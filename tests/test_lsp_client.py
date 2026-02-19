import sys, time, pytest
from mojokernel.lsp_client import MojoLSPClient, completion_matches, hover_text, identifier_span, offset_to_lsp_position, lsp_position_to_offset


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
        send({"jsonrpc": "2.0", "id": mid, "result": {"isIncomplete": False, "items": [{"label": "print"}, {"insertText": "println"}]}})
    elif method == "textDocument/hover":
        send({"jsonrpc": "2.0", "id": mid, "result": {"contents": {"kind": "markdown", "value": "hover-info"}}})
    elif method == "exit":
        break
'''
    return [sys.executable, '-u', '-c', code]


def _hanging_cmd(): return [sys.executable, '-u', '-c', 'import time; time.sleep(60)']


def test_lsp_client_round_trip_and_restart():
    c = MojoLSPClient(cmd=_fake_lsp_cmd(), request_timeout=1.0, shutdown_timeout=0.3)
    c.start()
    pid1 = c.pid
    assert c.is_running
    payload = c.complete('pri', 3)
    assert completion_matches(payload) == ['print', 'println']
    txt = hover_text(c.hover('x', 1))
    assert txt == 'hover-info'
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
