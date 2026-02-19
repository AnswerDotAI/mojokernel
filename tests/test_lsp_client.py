import sys, tempfile, time, pytest
from pathlib import Path
from mojokernel.lsp_client import (
    MojoLSPClient, completion_matches, completion_metadata, hover_text, identifier_span, lsp_position_to_offset,
    offset_to_lsp_position, signature_text,
)


def _fake_lsp_cmd():
    code = r'''
import json, sys

def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line: return None
        if line in (b"\r\n", b"\n"): break
        if b":" not in line: continue
        k,v = line.decode("ascii", "replace").split(":", 1)
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    if n <= 0: return None
    body = sys.stdin.buffer.read(n)
    return json.loads(body.decode("utf-8"))

def send(obj):
    payload = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()

while True:
    msg = read_msg()
    if msg is None: break
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"capabilities": {}}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": mid, "result": None})
    elif method == "textDocument/completion":
        send({
            "jsonrpc": "2.0", "id": mid, "result": {
                "isIncomplete": False, "items": [
                    {"label": "print", "kind": 3, "detail": "print(value: Any)"},
                    {"insertText": "println", "kind": 3},
                ],
            },
        })
    elif method == "textDocument/hover":
        send({"jsonrpc": "2.0", "id": mid, "result": {"contents": {"kind": "markdown", "value": "hover-info"}}})
    elif method == "textDocument/signatureHelp":
        send({
            "jsonrpc": "2.0", "id": mid, "result": {
                "signatures": [{"label": "print(value: Any)", "parameters": [{"label": "value: Any"}]}],
                "activeSignature": 0, "activeParameter": 0,
            },
        })
    elif method == "exit":
        break
'''
    return [sys.executable, '-u', '-c', code]


def _hanging_cmd(): return [sys.executable, '-u', '-c', 'import time; time.sleep(60)']


def _fake_lsp_cmd_ignores_did_change(report_change_support=False):
    cap = '{"textDocumentSync":{"change":2}}' if report_change_support else '{}'
    code = rf'''
import json, sys
doc = ""

def read_msg():
    headers = {{}}
    while True:
        line = sys.stdin.buffer.readline()
        if not line: return None
        if line in (b"\r\n", b"\n"): break
        if b":" not in line: continue
        k,v = line.decode("ascii", "replace").split(":", 1)
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    if n <= 0: return None
    return json.loads(sys.stdin.buffer.read(n).decode("utf-8"))

def send(obj):
    payload = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {{len(payload)}}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()

def line_length(txt, line):
    lines = txt.split("\n")
    return len(lines[line]) if 0 <= line < len(lines) else None

while True:
    msg = read_msg()
    if msg is None: break
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        send({{"jsonrpc":"2.0","id":mid,"result":{{"capabilities":{cap}}}}})
    elif method == "shutdown":
        send({{"jsonrpc":"2.0","id":mid,"result":None}})
    elif method == "textDocument/didOpen":
        doc = msg.get("params", {{}}).get("textDocument", {{}}).get("text", "")
    elif method == "textDocument/didClose":
        doc = ""
    elif method == "textDocument/didChange":
        pass
    elif method == "textDocument/completion":
        p = msg.get("params", {{}}).get("position", {{}})
        line, ch = p.get("line", 0), p.get("character", 0)
        ll = line_length(doc, line)
        if ll is None or ch > ll:
            send({{"jsonrpc":"2.0","id":mid,"error":{{"code":-32600,"message":"invalid request"}}}})
        else:
            send({{"jsonrpc":"2.0","id":mid,"result":{{"isIncomplete":False,"items":[{{"label":"print","kind":3}}]}}}})
    elif method == "exit":
        break
'''
    return [sys.executable, '-u', '-c', code]


def _fake_lsp_cmd_echo_profile_env():
    code = r'''
import json, os, sys
profile = os.environ.get("MODULAR_PROFILE_FILENAME", "")

def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line: return None
        if line in (b"\r\n", b"\n"): break
        if b":" not in line: continue
        k,v = line.decode("ascii", "replace").split(":", 1)
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    if n <= 0: return None
    return json.loads(sys.stdin.buffer.read(n).decode("utf-8"))

def send(obj):
    payload = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()

while True:
    msg = read_msg()
    if msg is None: break
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"capabilities":{}}})
    elif method == "shutdown":
        send({"jsonrpc":"2.0","id":mid,"result":None})
    elif method == "textDocument/completion":
        send({"jsonrpc":"2.0","id":mid,"result":{"isIncomplete":False,"items":[{"label":profile,"kind":3}]}})
    elif method == "exit":
        break
'''
    return [sys.executable, '-u', '-c', code]


def _fake_lsp_cmd_with_stdout_noise():
    code = r'''
import json, sys

def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line: return None
        if line in (b"\r\n", b"\n"): break
        if b":" not in line: continue
        k,v = line.decode("ascii", "replace").split(":", 1)
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    if n <= 0: return None
    return json.loads(sys.stdin.buffer.read(n).decode("utf-8"))

def send(obj):
    payload = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()

while True:
    msg = read_msg()
    if msg is None: break
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"capabilities":{}}})
    elif method == "shutdown":
        send({"jsonrpc":"2.0","id":mid,"result":None})
    elif method == "textDocument/completion":
        sys.stdout.buffer.write(b"noise-before-header\n\n")
        sys.stdout.buffer.flush()
        send({"jsonrpc":"2.0","id":mid,"result":{"isIncomplete":False,"items":[{"label":"print","kind":3}]}})
    elif method == "exit":
        break
'''
    return [sys.executable, '-u', '-c', code]


class _FakeStdout:
    def __init__(self, lines, chunks): self._lines,self._chunks = list(lines),list(chunks)

    def readline(self):
        if self._lines: return self._lines.pop(0)
        return b''

    def read(self, _n):
        if self._chunks: return self._chunks.pop(0)
        return b''


class _FakeProc:
    def __init__(self, stdout): self.stdout = stdout


class _ProcStub:
    def __init__(self, rc=None, pid=123): self._rc,self.pid = rc,pid
    def poll(self): return self._rc


class _ThreadStub:
    def __init__(self, alive=True): self._alive = alive
    def is_alive(self): return self._alive


def test_lsp_client_round_trip_and_restart():
    c = MojoLSPClient(cmd=_fake_lsp_cmd(), request_timeout=1.0, shutdown_timeout=0.3)
    c.start()
    pid1 = c.pid
    assert c.is_running
    payload = c.complete('pri', 3)
    assert completion_matches(payload) == ['print', 'println']
    assert completion_metadata(payload, 0, 3)[0] == dict(start=0, end=3, text='print', type='function', signature='print(value: Any)')
    txt = hover_text(c.hover('x', 1))
    assert txt == 'hover-info'
    sig = signature_text(c.signature_help('print(', 6))
    assert sig == 'print(value: Any)\n\nactive parameter: value: Any'
    c.restart()
    assert c.is_running
    assert c.pid != pid1
    c.shutdown()
    assert not c.is_running


def test_lsp_client_failed_initialize_cleans_up():
    c = MojoLSPClient(cmd=_hanging_cmd(), request_timeout=0.1, shutdown_timeout=0.1)
    with pytest.raises(Exception): c.start()
    for _ in range(20):
        if not c.is_running: break
        time.sleep(0.02)
    assert not c.is_running


def test_position_helpers():
    text = "abc\ndefg\nx"
    assert offset_to_lsp_position(text, 0) == (0, 0)
    assert offset_to_lsp_position(text, 5) == (1, 1)
    assert lsp_position_to_offset(text, 1, 1) == 5
    assert identifier_span("hello_world()", 7) == (0, 11)


def test_lsp_client_reopens_when_did_change_not_supported():
    c = MojoLSPClient(cmd=_fake_lsp_cmd_ignores_did_change(False), request_timeout=1.0, shutdown_timeout=0.2)
    c.start()
    assert completion_matches(c.complete('pri', 3)) == ['print']
    assert completion_matches(c.complete('print', 5)) == ['print']
    c.shutdown()


def test_lsp_client_retries_when_server_ignores_did_change():
    c = MojoLSPClient(cmd=_fake_lsp_cmd_ignores_did_change(True), request_timeout=1.0, shutdown_timeout=0.2)
    c.start()
    assert completion_matches(c.complete('pri', 3)) == ['print']
    assert completion_matches(c.complete('print', 5)) == ['print']
    c.shutdown()


def test_lsp_client_sets_profile_filename_by_default():
    c = MojoLSPClient(cmd=_fake_lsp_cmd_echo_profile_env(), request_timeout=1.0, shutdown_timeout=0.2)
    c.start()
    got = completion_matches(c.complete('x', 1))[0]
    assert got
    gp,tp = Path(got).resolve(),Path(tempfile.gettempdir()).resolve()
    assert tp == gp.parent or tp in gp.parents
    c.shutdown()


def test_lsp_client_ignores_non_lsp_stdout_noise():
    c = MojoLSPClient(cmd=_fake_lsp_cmd_with_stdout_noise(), request_timeout=1.0, shutdown_timeout=0.2)
    c.start()
    assert completion_matches(c.complete('pri', 3)) == ['print']
    st = c.debug_state()
    assert st['reader_alive']
    c.shutdown()


def test_lsp_client_read_message_handles_fragmented_body_reads():
    body = b'{"jsonrpc":"2.0","id":7,"result":{"ok":true}}'
    head = f"Content-Length: {len(body)}\r\n".encode('ascii')
    stdout = _FakeStdout([head, b'\r\n'], [body[:9], body[9:21], body[21:]])
    c = MojoLSPClient(cmd=[])
    c._proc = _FakeProc(stdout)
    msg = c._read_message()
    assert msg == dict(jsonrpc='2.0', id=7, result=dict(ok=True))


def test_lsp_client_complete_restarts_when_reader_dead():
    c = MojoLSPClient(cmd=[])
    c._proc,c._reader,c._stderr_reader = _ProcStub(),_ThreadStub(False),_ThreadStub(True)
    restarts = []

    def restart():
        restarts.append('restart')
        c._proc,c._reader,c._stderr_reader = _ProcStub(),_ThreadStub(True),_ThreadStub(True)

    c.restart = restart
    c._text_document_request = lambda method, text, pos, timeout=None: dict(isIncomplete=False, items=[dict(label='print', kind=3)])
    out = c.complete('pri', 3)
    assert completion_matches(out) == ['print']
    assert restarts == ['restart']


def test_lsp_client_complete_restarts_once_on_reader_stopped_error():
    c = MojoLSPClient(cmd=[])
    c._proc,c._reader,c._stderr_reader = _ProcStub(),_ThreadStub(True),_ThreadStub(True)
    restarts,count = [],0

    def restart():
        restarts.append('restart')
        c._proc,c._reader,c._stderr_reader = _ProcStub(),_ThreadStub(True),_ThreadStub(True)

    def req(method, text, pos, timeout=None):
        nonlocal count
        count += 1
        if count == 1: raise RuntimeError('LSP reader stopped')
        return dict(isIncomplete=False, items=[dict(label='print', kind=3)])

    c.restart = restart
    c._text_document_request = req
    out = c.complete('pri', 3)
    assert completion_matches(out) == ['print']
    assert restarts == ['restart']
    assert count == 2


def test_lsp_client_complete_timeout_does_not_restart():
    c = MojoLSPClient(cmd=[])
    c._proc,c._reader,c._stderr_reader = _ProcStub(),_ThreadStub(True),_ThreadStub(True)
    restarts = []
    c.restart = lambda: restarts.append('restart')
    c._text_document_request = lambda method, text, pos, timeout=None: (_ for _ in ()).throw(TimeoutError('LSP request timed out: textDocument/completion'))
    with pytest.raises(TimeoutError): c.complete('pri', 3)
    assert restarts == []


def test_lsp_client_debug_state_compact_truncates_large_fields():
    c = MojoLSPClient(cmd=[])
    c._proc,c._reader,c._stderr_reader = _ProcStub(),_ThreadStub(True),_ThreadStub(True)
    c._last_reader_error = 'x' * 500
    c._stderr_tail.extend(['a' * 300, 'b' * 300, 'c' * 300])
    out = c.debug_state(compact=True)
    assert len(out['last_reader_error']) <= 180
    assert len(out['stderr_tail']) == 2
    assert all(len(o) <= 120 for o in out['stderr_tail'])
