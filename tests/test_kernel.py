import logging, re, pytest, time
import jupyter_client
import mojokernel
from mojokernel.kernel import MojoKernel
from mojokernel.lsp_client import LSPError

def test_version():
    v = mojokernel.__version__
    assert re.match(r'\d+\.\d+\.\d+', v)
    assert v != '0.0.0'

def test_version_from_mojo():
    from mojokernel._version import _get
    v = _get()
    assert re.match(r'\d+\.\d+\.\d+$', v)

def test_version_from_env(monkeypatch):
    monkeypatch.setenv('MOJO_VERSION', '99.0.0')
    from mojokernel._version import _get
    assert _get() == '99.0.0'

def test_version_env_overrides_mojo(monkeypatch):
    monkeypatch.setenv('MOJO_VERSION', '1.2.3')
    from mojokernel._version import _get
    assert _get() == '1.2.3'

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
    kc.execute(code)
    stdouts, errors = [], []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try: msg = kc.get_iopub_msg(timeout=2)
        except: break
        if msg['msg_type'] == 'stream' and msg['content']['name'] == 'stdout': stdouts.append(msg['content']['text'])
        elif msg['msg_type'] == 'error': errors.append(msg['content'])
        elif msg['msg_type'] == 'status' and msg['content']['execution_state'] == 'idle': break
    return ''.join(stdouts), errors

# -- Kernel output --

def test_kernel_smoke_output_state_and_multiline(kc):
    stdout, errors = _run(kc, 'print(42)')
    assert '42' in stdout
    assert not errors

    stdout, _ = _run(kc, 'print("clean")')
    assert 'clean' in stdout
    assert '>' not in stdout

    _run(kc, 'var _ktest_v = 77')
    stdout, _ = _run(kc, 'print(_ktest_v)')
    assert '77' in stdout

    _run(kc, 'fn _ktest_sq(n: Int) -> Int:\n    return n * n')
    stdout, _ = _run(kc, 'print(_ktest_sq(5))')
    assert '25' in stdout

def test_kernel_error_and_recovery(kc):
    _, errors = _run(kc, 'print(_ktest_undefined)')
    assert len(errors) == 1
    assert errors[0]['ename'] == 'MojoError'

    stdout, errors = _run(kc, 'print(123)')
    assert '123' in stdout
    assert not errors

def test_kernel_empty_code(kc):
    stdout, errors = _run(kc, '')
    assert stdout == ''
    assert not errors

def test_kernel_completion_and_signature_e2e(kc):
    _run(kc, 'fn _ktest_sig(a: Int, b: Int) -> Int:\n    return a + b')
    comp = kc.complete('_ktest_s', len('_ktest_s'), reply=True, timeout=10)['content']
    assert comp['status'] == 'ok'
    assert '_ktest_sig' in comp.get('matches', [])
    assert comp['cursor_start'] == 0
    assert comp['cursor_end'] == len('_ktest_s')

    insp = kc.inspect('_ktest_sig(', len('_ktest_sig('), detail_level=0, reply=True, timeout=10)['content']
    assert insp['status'] == 'ok'
    assert insp['found']
    txt = insp.get('data', {}).get('text/plain', '')
    assert '_ktest_sig(' in txt


class _WrapScopeOnlyLSP:
    def __init__(self): self.calls = []

    def _is_wrapped(self, text): return text.startswith('fn __mojokernel_cell__():\n')

    def complete(self, text, cursor_offset):
        self.calls.append(dict(kind='complete', text=text, cursor=cursor_offset))
        if not self._is_wrapped(text): return dict(isIncomplete=False, items=[])
        return dict(isIncomplete=False, items=[dict(label='sort', kind=2)])

    def signature_help(self, text, cursor_offset):
        self.calls.append(dict(kind='signature', text=text, cursor=cursor_offset))
        if not self._is_wrapped(text): return dict(signatures=[])
        return dict(signatures=[dict(label='sort()')], activeSignature=0)

    def hover(self, text, cursor_offset):
        self.calls.append(dict(kind='hover', text=text, cursor=cursor_offset))
        return None


class _WrapScopeOutdatedOnceLSP(_WrapScopeOnlyLSP):
    def __init__(self):
        super().__init__()
        self._wrapped_calls = 0

    def complete(self, text, cursor_offset):
        self.calls.append(dict(kind='complete', text=text, cursor=cursor_offset))
        if not self._is_wrapped(text): return dict(isIncomplete=False, items=[])
        self._wrapped_calls += 1
        if self._wrapped_calls == 2: raise LSPError(dict(code=-32801, message='outdated request'))
        return dict(isIncomplete=False, items=[dict(label='sort', kind=2)])


class _WrapScopeOutdatedBurstLSP(_WrapScopeOnlyLSP):
    def __init__(self, fail_count=3):
        super().__init__()
        self._fail_count = fail_count

    def complete(self, text, cursor_offset):
        self.calls.append(dict(kind='complete', text=text, cursor=cursor_offset))
        if not self._is_wrapped(text): return dict(isIncomplete=False, items=[])
        if self._fail_count > 0:
            self._fail_count -= 1
            raise LSPError(dict(code=-32801, message='outdated request'))
        return dict(isIncomplete=False, items=[dict(label='sort', kind=2)])


class _AlwaysTimeoutLSP:
    def complete(self, text, cursor_offset): raise TimeoutError('LSP request timed out: textDocument/completion')
    def signature_help(self, text, cursor_offset): return dict(signatures=[])
    def hover(self, text, cursor_offset): return None


class _TimeoutThenRecoverLSP(_WrapScopeOnlyLSP):
    def __init__(self):
        super().__init__()
        self.restart_calls = 0
        self._timed_out = False

    def complete(self, text, cursor_offset):
        self.calls.append(dict(kind='complete', text=text, cursor=cursor_offset))
        if not self._timed_out:
            self._timed_out = True
            raise TimeoutError('LSP request timed out: textDocument/completion')
        if not self._is_wrapped(text): return dict(isIncomplete=False, items=[])
        return dict(isIncomplete=False, items=[dict(label='sort', kind=2)])

    def restart(self): self.restart_calls += 1


class _UnfilteredLSP:
    def complete(self, text, cursor_offset):
        items = [dict(label='print', kind=3), dict(label='Int', kind=7), dict(label='len', kind=3), dict(insertText='println', kind=3), dict(label='pri_helper', kind=3)]
        return dict(isIncomplete=False, items=items)

    def signature_help(self, text, cursor_offset): return dict(signatures=[])
    def hover(self, text, cursor_offset): return None


class _DeadReaderRecoverLSP(_WrapScopeOnlyLSP):
    def __init__(self):
        super().__init__()
        self.restart_calls = 0
        self.is_running = True
        self.reader_alive = False

    def restart(self):
        self.restart_calls += 1
        self.is_running = True
        self.reader_alive = True

    def debug_state(self): return dict(is_running=self.is_running, reader_alive=self.reader_alive, restart_calls=self.restart_calls)


def _mk_kernel_for_lsp(lsp):
    k = MojoKernel.__new__(MojoKernel)
    k.lsp = lsp
    k._lsp_preamble = ''
    k.log = logging.getLogger('test-kernel-lsp')
    return k


def test_do_complete_uses_wrapped_scope_fallback_for_member_completion():
    lsp = _WrapScopeOnlyLSP()
    k = _mk_kernel_for_lsp(lsp)
    code = 'var list = [2, 3, 5]\nlist.'
    out = k.do_complete(code, len(code))
    assert out['status'] == 'ok'
    assert 'sort' in out['matches']
    comp_calls = [o for o in lsp.calls if o['kind'] == 'complete']
    assert comp_calls
    assert any(o['text'].startswith('fn __mojokernel_cell__():\n') for o in comp_calls)


def test_do_inspect_uses_wrapped_scope_fallback_for_signature():
    lsp = _WrapScopeOnlyLSP()
    k = _mk_kernel_for_lsp(lsp)
    code = 'var list = [2, 3, 5]\nlist.sort('
    out = k.do_inspect(code, len(code))
    assert out['status'] == 'ok'
    assert out['found']
    assert out['data'].get('text/plain') == 'sort()'
    sig_calls = [o for o in lsp.calls if o['kind'] == 'signature']
    assert len(sig_calls) >= 2
    assert not sig_calls[0]['text'].startswith('fn __mojokernel_cell__():\n')
    assert sig_calls[1]['text'].startswith('fn __mojokernel_cell__():\n')


def test_do_complete_repeated_member_completion_still_returns_matches():
    lsp = _WrapScopeOutdatedOnceLSP()
    k = _mk_kernel_for_lsp(lsp)
    code = 'var list = [2, 3, 5]\nlist.'
    first = k.do_complete(code, len(code))
    assert 'sort' in first.get('matches', [])
    second = k.do_complete(code, len(code))
    assert 'sort' in second.get('matches', [])


def test_do_complete_filters_lsp_matches_by_prefix():
    k = _mk_kernel_for_lsp(_UnfilteredLSP())
    out = k.do_complete('pri', 3)
    assert out.get('matches', []) == ['print', 'println', 'pri_helper']
    typed = out.get('metadata', {}).get('_jupyter_types_experimental', [])
    assert [o.get('text') for o in typed] == ['print', 'println', 'pri_helper']


def test_do_complete_member_prefix_uses_wrapped_first():
    lsp = _WrapScopeOnlyLSP()
    k = _mk_kernel_for_lsp(lsp)
    code = 'var list = [2, 3, 5]\nlist.s'
    out = k.do_complete(code, len(code))
    assert 'sort' in out.get('matches', [])
    comp_calls = [o for o in lsp.calls if o['kind'] == 'complete']
    assert comp_calls[0]['text'].startswith('fn __mojokernel_cell__():\n')


def test_do_complete_outdated_burst_falls_back_without_retry_loop():
    lsp = _WrapScopeOutdatedBurstLSP(3)
    k = _mk_kernel_for_lsp(lsp)
    code = 'var list = [2, 3, 5]\nlist.'
    out = k.do_complete(code, len(code))
    assert out.get('matches', []) == []


def test_do_complete_includes_debug_metadata_when_member_completion_fails():
    k = _mk_kernel_for_lsp(_AlwaysTimeoutLSP())
    out = k.do_complete('a.', 2)
    dbg = out.get('metadata', {}).get('_mojokernel_debug', [])
    assert out['status'] == 'ok'
    assert out.get('matches', []) == []
    assert any(o.get('stage') == 'lsp_wrapped' and not o.get('ok', True) for o in dbg)


def test_do_complete_timeout_does_not_restart_lsp():
    lsp = _TimeoutThenRecoverLSP()
    k = _mk_kernel_for_lsp(lsp)
    out = k.do_complete('var list = [2, 3, 5]\nlist.', len('var list = [2, 3, 5]\nlist.'))
    assert out.get('matches', []) == []
    assert lsp.restart_calls == 0


def test_do_complete_restarts_when_lsp_reader_dead():
    lsp = _DeadReaderRecoverLSP()
    k = _mk_kernel_for_lsp(lsp)
    out = k.do_complete('var list = [2, 3, 5]\nlist.s', len('var list = [2, 3, 5]\nlist.s'))
    assert 'sort' in out.get('matches', [])
    assert lsp.restart_calls == 1
