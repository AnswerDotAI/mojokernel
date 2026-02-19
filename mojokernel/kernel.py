import os
import re
import time
from pathlib import Path
from ipykernel.kernelbase import Kernel
from .lsp_client import LSPError, MojoLSPClient, completion_matches, completion_metadata, hover_text, identifier_span, signature_text


class MojoKernel(Kernel):
    implementation = 'mojokernel'
    implementation_version = '0.1.0'
    language = 'mojo'
    language_version = '0.26'
    language_info = dict(mimetype='text/x-mojo', name='mojo', file_extension='.mojo', pygments_lexer='python', codemirror_mode='python')
    banner = 'Mojo Jupyter Kernel'
    _builtin_signatures = {'print': 'print(value: Any)'}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if os.environ.get('MOJO_KERNEL_ENGINE') == 'pexpect':
            from .engines.pexpect_engine import PexpectEngine
            self.engine = PexpectEngine()
        else:
            from .engines.server_engine import ServerEngine, _find_server_binary
            if _find_server_binary(): self.engine = ServerEngine()
            else:
                from .engines.pexpect_engine import PexpectEngine
                self.engine = PexpectEngine()
        self.engine.start()
        self._lsp_preamble = ''
        self.lsp = None
        v = os.environ.get('MOJO_KERNEL_LSP', '1').lower()
        if v not in ('0', 'false', 'no', 'off'):
            try:
                include_dirs = [o for o in os.environ.get('MOJO_LSP_INCLUDE_DIRS', '').split(os.pathsep) if o]
                lsp_timeout = float(os.environ.get('MOJO_LSP_REQUEST_TIMEOUT', '2'))
                lsp_shutdown = float(os.environ.get('MOJO_LSP_SHUTDOWN_TIMEOUT', '1'))
                root_uri = Path.cwd().resolve().as_uri()
                self.lsp = MojoLSPClient(include_dirs=include_dirs, root_uri=root_uri, request_timeout=lsp_timeout, shutdown_timeout=lsp_shutdown, logger=self.log.debug)
                self.lsp.start()
            except Exception as e:
                self.log.warning(f"Mojo LSP unavailable, completions disabled: {e}")
                self.lsp = None

    def _known_symbols(self, extra=''):
        text = self._lsp_preamble + '\n' + extra
        syms = {k: dict(type='function', signature=v) for k,v in self._builtin_signatures.items()}
        for m in re.finditer(r'(?m)^\s*fn\s+([A-Za-z_]\w*)\s*\(([^)]*)\)', text):
            name,args = m.group(1),m.group(2).strip()
            syms[name] = dict(type='function', signature=f'{name}({args})')
        for m in re.finditer(r'(?m)^\s*struct\s+([A-Za-z_]\w*)', text): syms[m.group(1)] = dict(type='class')
        for m in re.finditer(r'(?m)\b(?:var|let)\s+([A-Za-z_]\w*)', text): syms.setdefault(m.group(1), dict(type='instance'))
        return syms

    def _fallback_complete(self, code, cursor_pos, start, end):
        prefix = code[start:cursor_pos]
        if not prefix: return [], {}
        syms = self._known_symbols(code)
        matches = [o for o in syms.keys() if o.startswith(prefix)]
        typed = []
        for o in matches:
            entry = dict(start=start, end=end, text=o, type=syms[o].get('type', 'text'))
            sig = syms[o].get('signature')
            if sig: entry['signature'] = sig
            typed.append(entry)
        return matches,dict(_jupyter_types_experimental=typed) if typed else {}

    def _inspect_target(self, code, cursor_pos):
        cursor_pos = max(0, min(len(code), cursor_pos))
        i = cursor_pos - 1
        while i >= 0 and code[i].isspace(): i -= 1
        if i >= 0 and code[i] == '(':
            j = i
            while j > 0 and (code[j-1].isalnum() or code[j-1] == '_'): j -= 1
            return code[j:i]
        start,end = identifier_span(code, cursor_pos)
        return code[start:end]

    def _fallback_inspect_text(self, code, cursor_pos):
        target = self._inspect_target(code, cursor_pos)
        if not target: return ''
        syms = self._known_symbols(code)
        if target in syms and syms[target].get('signature'): return syms[target]['signature']
        if target in syms: return target
        return ''

    def _wrap_for_lsp(self, text, cursor_pos, fn_name='__mojokernel_cell__'):
        marker = '__MOJOKERNEL_CURSOR__'
        if marker in text: marker = '__MOJOKERNEL_CURSOR2__'
        cursor_pos = max(0, min(len(text), cursor_pos))
        marked = text[:cursor_pos] + marker + text[cursor_pos:]
        body = '\n'.join(f'    {o}' if o else '' for o in marked.split('\n'))
        wrapped = f'fn {fn_name}():\n{body}'
        wpos = wrapped.find(marker)
        return wrapped.replace(marker, ''), wpos

    def _is_outdated_lsp_error(self, e):
        if not isinstance(e, LSPError): return False
        d = e.args[0] if e.args else None
        return isinstance(d, dict) and d.get('code') == -32801

    def _lsp_state(self):
        if not self.lsp: return {}
        try: return self.lsp.debug_state(compact=True)
        except Exception as e: return dict(error=self._diag_err(e))

    def _is_member_completion(self, code, cursor_pos, start):
        if cursor_pos > 0 and code[cursor_pos-1] == '.': return True
        return start > 0 and code[start-1] == '.'

    def _lsp_complete(self, text, pos, start, end, prefix=''):
        try: payload = self.lsp.complete(text, pos)
        except Exception as e:
            if not self._is_outdated_lsp_error(e): raise
            payload = self.lsp.complete(text, pos)
        matches = completion_matches(payload, prefix=prefix)
        typed = completion_metadata(payload, start, end, prefix=prefix)
        return matches,dict(_jupyter_types_experimental=typed) if typed else {}

    def _lsp_inspect(self, text, pos):
        txt = ''
        try: txt = signature_text(self.lsp.signature_help(text, pos))
        except Exception as e: self.log.debug(f"Signature help failed: {e}")
        if txt: return txt
        try: return hover_text(self.lsp.hover(text, pos))
        except Exception as e:
            self.log.debug(f"Inspect failed: {e}")
            return ''

    def _diag_on(self):
        v = os.environ.get('MOJO_KERNEL_LSP_DIAG', '').lower()
        return v not in ('', '0', 'false', 'no', 'off')

    def _diag_meta(self, metadata, diag, force=False):
        if not diag: return metadata
        if not force and not self._diag_on() and all(o.get('ok', False) for o in diag): return metadata
        m = dict(metadata or {})
        m['_mojokernel_debug'] = diag[-12:]
        return m

    def _diag_err(self, e, n=220):
        s = repr(e)
        return s if len(s) <= n else s[:n-3] + '...'

    def do_execute(self, code, silent, store_history=True, user_expressions=None, allow_stdin=False):
        code = code.strip()
        if not code: return dict(status='ok', execution_count=self.execution_count, payload=[], user_expressions={})
        result = self.engine.execute(code)

        if not silent and result.stdout: self.send_response(self.iopub_socket, 'stream', dict(name='stdout', text=result.stdout))
        if not silent and result.stderr: self.send_response(self.iopub_socket, 'stream', dict(name='stderr', text=result.stderr))

        if result.success:
            if self.lsp: self._lsp_preamble += code + '\n'
            return dict(status='ok', execution_count=self.execution_count, payload=[], user_expressions={})

        if not silent: self.send_response(self.iopub_socket, 'error', dict(ename=result.ename, evalue=result.evalue, traceback=result.traceback))
        return dict(status='error', execution_count=self.execution_count, ename=result.ename, evalue=result.evalue, traceback=result.traceback)

    def do_complete(self, code, cursor_pos):
        cursor_pos = len(code) if cursor_pos is None else cursor_pos
        start,end = identifier_span(code, cursor_pos)
        metadata = {}
        matches = []
        diag = []
        if self.lsp:
            text = self._lsp_preamble + code
            pos = len(self._lsp_preamble) + cursor_pos
            is_member = self._is_member_completion(code, cursor_pos, start)
            wtext,wpos = self._wrap_for_lsp(text, pos)
            prefix = code[start:cursor_pos]

            def try_complete(stage, t, p):
                nonlocal matches,metadata
                t0 = time.time()
                try:
                    matches,metadata = self._lsp_complete(t, p, start, end, prefix=prefix)
                    diag.append(dict(stage=stage, ok=True, matches=len(matches), elapsed_ms=round(1000 * (time.time() - t0), 1)))
                    return True
                except Exception as e:
                    es = self._diag_err(e)
                    st = self._lsp_state()
                    entry = dict(stage=stage, ok=False, error=es, elapsed_ms=round(1000 * (time.time() - t0), 1), lsp=st)
                    if self._is_outdated_lsp_error(e):
                        entry['stale'] = True
                        self.log.debug(f"{stage} stale request: {es}")
                    else: self.log.warning(f"{stage} failed: {es}; lsp={st}")
                    diag.append(entry)
                    return False

            if is_member:
                try_complete('lsp_wrapped', wtext, wpos)
                if not matches: try_complete('lsp_raw', text, pos)
            else:
                try_complete('lsp_raw', text, pos)
                if not matches: try_complete('lsp_wrapped', wtext, wpos)
        force_diag = bool(self.lsp and not matches and self._is_member_completion(code, cursor_pos, start))
        if not matches: matches,metadata = self._fallback_complete(code, cursor_pos, start, end)
        if matches and diag and diag[-1].get('stage') != 'fallback': diag.append(dict(stage='final', ok=True, matches=len(matches)))
        elif not matches: diag.append(dict(stage='final', ok=False, matches=0))
        metadata = self._diag_meta(metadata, diag, force=force_diag)
        return dict(status='ok', matches=matches, cursor_start=start, cursor_end=end, metadata=metadata)

    def do_inspect(self, code, cursor_pos, detail_level=0, omit_sections=()):
        cursor_pos = len(code) if cursor_pos is None else cursor_pos
        txt = ''
        if self.lsp:
            text = self._lsp_preamble + code
            pos = len(self._lsp_preamble) + cursor_pos
            txt = self._lsp_inspect(text, pos)
            if not txt:
                try:
                    wtext,wpos = self._wrap_for_lsp(text, pos)
                    txt = self._lsp_inspect(wtext, wpos)
                except Exception as e: self.log.debug(f"Wrapped inspect failed: {e}")
        if not txt: txt = self._fallback_inspect_text(code, cursor_pos)
        if not txt: return dict(status='ok', found=False, data={}, metadata={})
        return dict(status='ok', found=True, data={'text/plain': txt}, metadata={})

    def do_shutdown(self, restart):
        if self.lsp:
            try: self.lsp.restart() if restart else self.lsp.shutdown()
            except Exception as e: self.log.debug(f"LSP shutdown failed: {e}")
        self.engine.restart() if restart else self.engine.shutdown()
        return dict(status='ok', restart=restart)

    def do_interrupt(self): self.engine.interrupt()

    def do_is_complete(self, code):
        code = code.strip()
        if not code: return dict(status='complete')
        lines = code.split('\n')
        last = lines[-1].strip()
        if last.endswith(':') or last.endswith('\\'): return dict(status='incomplete', indent='    ')
        return dict(status='complete')


if __name__ == '__main__':
    from ipykernel.kernelapp import IPKernelApp
    IPKernelApp.launch_instance(kernel_class=MojoKernel)
