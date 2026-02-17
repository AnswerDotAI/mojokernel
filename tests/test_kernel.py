import re, pytest, time
import jupyter_client
import mojokernel

def test_version():
    v = mojokernel.__version__
    assert re.match(r'\d+\.\d+\.\d+\.\d+', v)
    assert v != '0.0.0.0'

@pytest.fixture(scope='module')
def kc():
    km = jupyter_client.KernelManager(kernel_name='mojo')
    km.start_kernel()
    kc = km.client()
    kc.start_channels()
    kc.wait_for_ready(timeout=30)
    yield kc
    km.shutdown_kernel()

def _run(kc, code, timeout=15):
    """Execute code on kernel, return (stdout_texts, error_contents)."""
    kc.execute(code)
    stdouts, errors = [], []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = kc.get_iopub_msg(timeout=2)
            if msg['msg_type'] == 'stream' and msg['content']['name'] == 'stdout':
                stdouts.append(msg['content']['text'])
            elif msg['msg_type'] == 'error':
                errors.append(msg['content'])
            elif msg['msg_type'] == 'status' and msg['content']['execution_state'] == 'idle':
                break
        except: break
    return ''.join(stdouts), errors

# -- Kernel output --

def test_kernel_print(kc):
    stdout, errors = _run(kc, 'print(42)')
    assert '42' in stdout
    assert not errors

def test_kernel_output_clean(kc):
    stdout, _ = _run(kc, 'print("clean")')
    assert 'clean' in stdout
    assert '>' not in stdout

def test_kernel_state_persists(kc):
    _run(kc, 'var _ktest_v = 77')
    stdout, _ = _run(kc, 'print(_ktest_v)')
    assert '77' in stdout

def test_kernel_error(kc):
    _, errors = _run(kc, 'print(_ktest_undefined)')
    assert len(errors) == 1
    assert errors[0]['ename'] == 'MojoError'

def test_kernel_recovery_after_error(kc):
    _run(kc, 'print(_ktest_bad)')
    stdout, errors = _run(kc, 'print(123)')
    assert '123' in stdout
    assert not errors

def test_kernel_empty_code(kc):
    stdout, errors = _run(kc, '')
    assert stdout == ''
    assert not errors

def test_kernel_multiline(kc):
    _run(kc, 'fn _ktest_sq(n: Int) -> Int:\n    return n * n')
    stdout, _ = _run(kc, 'print(_ktest_sq(5))')
    assert '25' in stdout
