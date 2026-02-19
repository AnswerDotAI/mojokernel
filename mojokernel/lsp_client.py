import json, os, shutil, subprocess, sys, threading
from collections import deque
from pathlib import Path


class LSPError(RuntimeError): pass


def offset_to_lsp_position(text, offset):
    offset = max(0, min(len(text), offset))
    line = text.count('\n', 0, offset)
    prev = text.rfind('\n', 0, offset)
    char = offset if prev < 0 else offset - prev - 1
    return line, char


def lsp_position_to_offset(text, line, char):
    line = max(0, line)
    char = max(0, char)
    starts = [0]
    for i,c in enumerate(text):
        if c == '\n': starts.append(i+1)
    if line >= len(starts): return len(text)
    start = starts[line]
    end = starts[line+1]-1 if line+1 < len(starts) else len(text)
    return min(start + char, end)


def identifier_span(text, cursor_pos):
    cursor_pos = max(0, min(len(text), cursor_pos))
    start = cursor_pos
    while start and (text[start-1].isalnum() or text[start-1] == '_'): start -= 1
    end = cursor_pos
    while end < len(text) and (text[end].isalnum() or text[end] == '_'): end += 1
    return start, end


def _completion_items(payload):
    if isinstance(payload, dict): return payload.get('items') or []
    if isinstance(payload, list): return payload
    return []


def _completion_text(item):
    if not isinstance(item, dict): return None
    text = None
    if isinstance(item.get('textEdit'), dict): text = item['textEdit'].get('newText')
    if not text: text = item.get('insertText')
    if not text: text = item.get('label')
    if not isinstance(text, str): return None
    text = text.strip()
    if not text: return None
    return text.splitlines()[0]


_kind_to_jupyter_type = {2: 'function', 3: 'function', 4: 'function', 5: 'property', 6: 'instance', 7: 'class', 8: 'class', 9: 'module', 10: 'property', 12: 'instance', 14: 'keyword', 17: 'path', 19: 'path', 22: 'class', 24: 'keyword'}
def _completion_type(kind): return _kind_to_jupyter_type.get(kind, 'text') if isinstance(kind, int) else 'text'


def completion_matches(payload):
    items = _completion_items(payload)
    res, seen = [], set()
    for item in items:
        text = _completion_text(item)
        if not text: continue
        if text in seen: continue
        seen.add(text)
        res.append(text)
    return res


def completion_metadata(payload, start, end):
    items = _completion_items(payload)
    res, seen = [], set()
    for item in items:
        text = _completion_text(item)
        if not text or text in seen: continue
        seen.add(text)
        entry = dict(start=start, end=end, text=text, type=_completion_type(item.get('kind')))
        detail = item.get('detail')
        if isinstance(detail, str) and detail.strip(): entry['signature'] = detail.strip()
        res.append(entry)
    return res


def hover_text(payload):
    if not isinstance(payload, dict): return ''
    c = payload.get('contents')
    if isinstance(c, str): return c.strip()
    if isinstance(c, dict):
        v = c.get('value')
        return v.strip() if isinstance(v, str) else ''
    if isinstance(c, list):
        vals = []
        for o in c:
            if isinstance(o, str): vals.append(o)
            elif isinstance(o, dict) and isinstance(o.get('value'), str): vals.append(o['value'])
        return '\n\n'.join(o.strip() for o in vals if o and o.strip())
    return ''


def signature_text(payload):
    if not isinstance(payload, dict): return ''
    sigs = payload.get('signatures')
    if not isinstance(sigs, list) or not sigs: return ''
    sidx = payload.get('activeSignature')
    if not isinstance(sidx, int) or not 0 <= sidx < len(sigs): sidx = 0
    sig = sigs[sidx]
    if not isinstance(sig, dict): return ''
    label = sig.get('label')
    if not isinstance(label, str) or not label.strip(): return ''
    text = label.strip()
    pidx = payload.get('activeParameter')
    params = sig.get('parameters')
    if isinstance(pidx, int) and isinstance(params, list) and 0 <= pidx < len(params):
        p = params[pidx]
        pl = p.get('label') if isinstance(p, dict) else None
        if isinstance(pl, str) and pl.strip(): text += f"\n\nactive parameter: {pl.strip()}"
    return text


class _Pending:
    def __init__(self):
        self.event = threading.Event()
        self.msg = None
        self.err = None


class MojoLSPClient:
    def __init__(self, cmd=None, include_dirs=None, root_uri=None, env=None, request_timeout=2.0, shutdown_timeout=1.0, logger=None):
        self.cmd = list(cmd) if cmd else None
        self.include_dirs = list(include_dirs or [])
        self.root_uri = root_uri or Path.cwd().resolve().as_uri()
        self.env = env
        self.request_timeout = request_timeout
        self.shutdown_timeout = shutdown_timeout
        self.logger = logger
        self._proc = None
        self._reader = None
        self._stderr_reader = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending = {}
        self._next_id = 1
        self._doc_uri = 'file:///__mojokernel__/session.mojo'
        self._doc_text = ''
        self._doc_version = 0
        self._doc_open = False
        self._stderr_tail = deque(maxlen=20)

    @property
    def pid(self): return None if not self._proc else self._proc.pid

    @property
    def is_running(self): return bool(self._proc and self._proc.poll() is None)

    def _log(self, msg):
        if not self.logger: return
        try: self.logger(msg)
        except Exception: pass

    def _find_server(self):
        if p:=os.environ.get('MOJO_LSP_SERVER'): return p
        if p:=shutil.which('mojo-lsp-server'): return p
        p = Path(sys.executable).resolve().parent/'mojo-lsp-server'
        if p.exists(): return str(p)
        raise FileNotFoundError("mojo-lsp-server not found (set MOJO_LSP_SERVER or install Mojo SDK)")

    def _build_cmd(self):
        if self.cmd: return self.cmd
        cmd = [self._find_server()]
        for o in self.include_dirs: cmd += ['-I', o]
        return cmd

    def start(self):
        if self.is_running: return
        cmd = self._build_cmd()
        env = os.environ.copy()
        if self.env: env.update(self.env)
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, env=env)
        self._reader = threading.Thread(target=self._reader_loop, name='mojo-lsp-reader', daemon=True)
        self._stderr_reader = threading.Thread(target=self._stderr_loop, name='mojo-lsp-stderr', daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        try:
            params = dict(processId=os.getpid(), rootUri=self.root_uri, capabilities={}, clientInfo=dict(name='mojokernel', version='0'))
            self._request('initialize', params, timeout=self.request_timeout)
            self._notify('initialized', {})
        except Exception:
            self.shutdown()
            raise

    def restart(self):
        self.shutdown()
        self.start()

    def shutdown(self):
        proc = self._proc
        if not proc: return
        try:
            if proc.poll() is None:
                try: self._request('shutdown', None, timeout=self.shutdown_timeout)
                except Exception: pass
                try: self._notify('exit', None)
                except Exception: pass
                try: proc.wait(timeout=self.shutdown_timeout)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try: proc.wait(timeout=self.shutdown_timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=self.shutdown_timeout)
        finally:
            self._close_streams(proc)
            self._proc = None
            self._doc_open = False
            self._doc_text = ''
            self._doc_version = 0
            self._fail_pending(RuntimeError("LSP client shut down"))
            self._join_thread(self._reader)
            self._join_thread(self._stderr_reader)
            self._reader = None
            self._stderr_reader = None

    def update_document(self, text):
        if not self.is_running: self.start()
        text = text or ''
        if not self._doc_open:
            self._doc_open = True
            self._doc_version = 1
            self._doc_text = text
            td = dict(uri=self._doc_uri, languageId='mojo', version=self._doc_version, text=text)
            self._notify('textDocument/didOpen', dict(textDocument=td))
            return
        if text == self._doc_text: return
        self._doc_version += 1
        self._doc_text = text
        self._notify('textDocument/didChange', dict(textDocument=dict(uri=self._doc_uri, version=self._doc_version), contentChanges=[dict(text=text)]))

    def complete(self, text, cursor_offset, timeout=None):
        self.update_document(text)
        line, char = offset_to_lsp_position(text, cursor_offset)
        params = dict(textDocument=dict(uri=self._doc_uri), position=dict(line=line, character=char))
        return self._request('textDocument/completion', params, timeout=timeout)

    def hover(self, text, cursor_offset, timeout=None):
        self.update_document(text)
        line, char = offset_to_lsp_position(text, cursor_offset)
        params = dict(textDocument=dict(uri=self._doc_uri), position=dict(line=line, character=char))
        return self._request('textDocument/hover', params, timeout=timeout)

    def signature_help(self, text, cursor_offset, timeout=None):
        self.update_document(text)
        line, char = offset_to_lsp_position(text, cursor_offset)
        params = dict(textDocument=dict(uri=self._doc_uri), position=dict(line=line, character=char))
        return self._request('textDocument/signatureHelp', params, timeout=timeout)

    def _join_thread(self, t):
        if not t or t is threading.current_thread(): return
        t.join(timeout=0.2)

    def _close_streams(self, proc):
        for s in (proc.stdin, proc.stdout, proc.stderr):
            if not s: continue
            try: s.close()
            except Exception: pass

    def _request(self, method, params, timeout=None):
        timeout = self.request_timeout if timeout is None else timeout
        if not self.is_running: raise RuntimeError("LSP process not running")
        pending = _Pending()
        with self._pending_lock:
            req_id = self._next_id
            self._next_id += 1
            self._pending[req_id] = pending
        try:
            self._send(dict(jsonrpc='2.0', id=req_id, method=method, params=params))
            if not pending.event.wait(timeout):
                with self._pending_lock: self._pending.pop(req_id, None)
                raise TimeoutError(f"LSP request timed out: {method}")
            if pending.err: raise pending.err
            msg = pending.msg or {}
            if msg.get('error'): raise LSPError(msg['error'])
            return msg.get('result')
        finally:
            with self._pending_lock: self._pending.pop(req_id, None)

    def _notify(self, method, params):
        if not self.is_running: raise RuntimeError("LSP process not running")
        self._send(dict(jsonrpc='2.0', method=method, params=params))

    def _send(self, msg):
        proc = self._proc
        if not proc or not proc.stdin: raise RuntimeError("LSP stdin closed")
        payload = json.dumps(msg).encode('utf-8')
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode('ascii')
        with self._write_lock:
            proc.stdin.write(header)
            proc.stdin.write(payload)
            proc.stdin.flush()

    def _reader_loop(self):
        err = None
        try:
            while True:
                msg = self._read_message()
                if msg is None: break
                self._handle_message(msg)
        except Exception as e: err = e
        finally:
            if not err and self._stderr_tail: err = RuntimeError('\n'.join(self._stderr_tail))
            self._fail_pending(err or RuntimeError("LSP reader stopped"))

    def _stderr_loop(self):
        proc = self._proc
        if not proc or not proc.stderr: return
        while True:
            line = proc.stderr.readline()
            if not line: break
            txt = line.decode('utf-8', errors='replace').rstrip()
            if txt: self._stderr_tail.append(txt)
            if txt: self._log(f"[mojo-lsp] {txt}")

    def _read_message(self):
        proc = self._proc
        if not proc or not proc.stdout: return None
        headers = {}
        while True:
            line = proc.stdout.readline()
            if not line: return None
            if line in (b'\r\n', b'\n'): break
            if b':' not in line: continue
            k,v = line.decode('ascii', errors='replace').split(':', 1)
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get('content-length', '0'))
        if n <= 0: return None
        body = proc.stdout.read(n)
        if len(body) != n: return None
        return json.loads(body.decode('utf-8'))

    def _handle_message(self, msg):
        if 'id' in msg and ('result' in msg or 'error' in msg):
            with self._pending_lock: pending = self._pending.get(msg['id'])
            if not pending: return
            pending.msg = msg
            pending.event.set()
            return
        if 'id' in msg and 'method' in msg:
            self._send(dict(jsonrpc='2.0', id=msg['id'], error=dict(code=-32601, message='Method not found')))

    def _fail_pending(self, err):
        with self._pending_lock: items = list(self._pending.values())
        for pending in items:
            pending.err = err
            pending.event.set()
