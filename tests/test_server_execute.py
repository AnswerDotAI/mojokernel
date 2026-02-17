"""Expression evaluation tests through the REPL server binary."""
import json,subprocess,pytest
from pathlib import Path

BUILD_DIR = Path(__file__).resolve().parents[1] / "build"

def _server_bin():
    p = BUILD_DIR / "mojo-repl-server"
    return p if p.exists() else None

def _modular_root():
    from mojo._package_root import get_package_root
    return get_package_root()

@pytest.fixture(scope='module')
def server():
    bin = _server_bin()
    if not bin: pytest.skip("Server binary not found")
    root = _modular_root()
    proc = subprocess.Popen(
        [str(bin), root],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    line = proc.stdout.readline()
    assert json.loads(line)['status'] == 'ready'
    yield proc
    proc.stdin.write(b'{"type":"shutdown","id":999}\n')
    proc.stdin.flush()
    proc.wait(timeout=10)

_id = 0
def _exec(server, code):
    global _id; _id += 1
    req = json.dumps({'type': 'execute', 'id': _id, 'code': code}, separators=(',', ':')) + '\n'
    server.stdin.write(req.encode())
    server.stdin.flush()
    return json.loads(server.stdout.readline())

# -- Expression evaluation --

def test_simple_print(server):
    r = _exec(server, 'print(42)')
    assert r['status'] == 'ok', f"Expected ok: {r}"
    assert '42' in r['stdout'], f"Expected 42 in stdout: {r}"

def test_arithmetic(server):
    r = _exec(server, 'print(3 * 7 + 1)')
    assert r['status'] == 'ok'
    assert '22' in r['stdout']

def test_var_persistence(server):
    r = _exec(server, 'var _srv_x = 10')
    assert r['status'] == 'ok'
    r = _exec(server, 'print(_srv_x)')
    assert r['status'] == 'ok'
    assert '10' in r['stdout']

def test_var_mutation(server):
    _exec(server, 'var _srv_m = 5')
    _exec(server, '_srv_m = 99')
    r = _exec(server, 'print(_srv_m)')
    assert r['status'] == 'ok'
    assert '99' in r['stdout']

def test_multiline_fn(server):
    _exec(server, 'fn _srv_sq(n: Int) -> Int:\n    return n * n')
    r = _exec(server, 'print(_srv_sq(5))')
    assert r['status'] == 'ok'
    assert '25' in r['stdout']

def test_struct(server):
    _exec(server, 'struct _SrvPt:\n    var x: Int\n    var y: Int\n    fn __init__(out self, x: Int, y: Int):\n        self.x = x\n        self.y = y')
    r = _exec(server, 'var _srv_pt = _SrvPt(3, 4)\nprint(_srv_pt.x)')
    assert r['status'] == 'ok'
    assert '3' in r['stdout']

def test_error_unknown_var(server):
    r = _exec(server, 'print(_srv_undefined_var)')
    assert r['status'] == 'error'
    assert r['ename'] == 'MojoError'
    assert len(r['traceback']) > 0

def test_recovery_after_error(server):
    _exec(server, 'print(_srv_bad_var)')
    r = _exec(server, 'print(123)')
    assert r['status'] == 'ok'
    assert '123' in r['stdout']

def test_no_output_statement(server):
    r = _exec(server, 'var _srv_silent = 99')
    assert r['status'] == 'ok'

def test_string_output(server):
    r = _exec(server, 'print("hello world")')
    assert r['status'] == 'ok'
    assert 'hello world' in r['stdout']

def test_multiline_output(server):
    r = _exec(server, 'print("a")\nprint("b")')
    assert r['status'] == 'ok'
    assert 'a' in r['stdout']
    assert 'b' in r['stdout']
