"""Protocol-level tests for the mojo-repl-server binary.
Spawn the server, send JSON requests, verify JSON responses.
"""
import json,os,subprocess,pytest
from pathlib import Path

SERVER_BIN = Path(__file__).resolve().parents[1] / "build" / "mojo-repl-server"

def _modular_root():
    from mojo._package_root import get_package_root
    return get_package_root()

@pytest.fixture(scope='module')
def server():
    if not SERVER_BIN.exists():
        pytest.skip(f"Server binary not found at {SERVER_BIN}. Run tools/build_server.sh first.")
    root = _modular_root()
    proc = subprocess.Popen(
        [str(SERVER_BIN), root],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Wait for ready
    line = proc.stdout.readline()
    assert line, "Server produced no output"
    ready = json.loads(line)
    assert ready['status'] == 'ready', f"Server not ready: {ready}"
    yield proc
    proc.stdin.write(b'{"type":"shutdown","id":999}\n')
    proc.stdin.flush()
    proc.wait(timeout=10)

def _send(server, req):
    line = json.dumps(req, separators=(',', ':')) + '\n'
    server.stdin.write(line.encode())
    server.stdin.flush()
    resp_line = server.stdout.readline()
    assert resp_line, "Server returned no response"
    return json.loads(resp_line)

# -- Protocol tests --

def test_execute_returns_ok(server):
    resp = _send(server, {'type': 'execute', 'id': 1, 'code': 'var _proto_x = 1'})
    assert resp['id'] == 1
    assert resp['status'] == 'ok', f"Expected ok, got: {resp}"
    assert 'stdout' in resp

def test_execute_with_print(server):
    resp = _send(server, {'type': 'execute', 'id': 2, 'code': 'print(42)'})
    assert resp['status'] == 'ok'
    assert '42' in resp['stdout']

def test_execute_error_response(server):
    resp = _send(server, {'type': 'execute', 'id': 3, 'code': 'print(_proto_undef_xyz)'})
    assert resp['status'] == 'error'
    assert resp['ename'] == 'MojoError'
    assert 'evalue' in resp
    assert isinstance(resp['traceback'], list)

def test_complete_stub(server):
    resp = _send(server, {'type': 'complete', 'id': 4, 'code': 'pri', 'cursor_pos': 3})
    assert resp['status'] == 'ok'
    assert 'completions' in resp

def test_empty_code(server):
    resp = _send(server, {'type': 'execute', 'id': 5, 'code': ''})
    assert resp['status'] == 'ok'
    assert resp['stdout'] == ''

def test_response_has_id(server):
    resp = _send(server, {'type': 'execute', 'id': 42, 'code': 'var _proto_y = 2'})
    assert resp['id'] == 42

def test_unknown_type(server):
    resp = _send(server, {'type': 'bogus', 'id': 6})
    assert resp['status'] == 'error'
    assert 'ProtocolError' in resp.get('ename', '')
