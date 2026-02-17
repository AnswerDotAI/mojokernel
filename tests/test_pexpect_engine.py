import pytest
from mojokernel.engines.pexpect_engine import (
    PexpectEngine, _parse_output, _strip_ansi, _is_prompt_line, _PROMPT_PAT, _ECHO_RE)

# ── ANSI stripping ──

def test_strip_ansi_removes_escapes():
    assert _strip_ansi('\x1b[2m  1> \x1b[0m') == '  1> '

def test_strip_ansi_removes_cursor_codes():
    assert _strip_ansi('\x1b[1G\x1b[J\x1b[6Ghello') == 'hello'

def test_strip_ansi_preserves_plain_text():
    assert _strip_ansi('hello world') == 'hello world'

# ── Prompt detection ──

def test_prompt_pattern_matches():
    assert _PROMPT_PAT.search('42\n  2> ')
    assert _PROMPT_PAT.search('42\n  2>    2>   2> ')

def test_prompt_pattern_no_match_without_newline():
    assert not _PROMPT_PAT.search('  1> print(42)')

def test_is_prompt_line_starts_with_prompt():
    assert _is_prompt_line('  1> print(42)')

def test_is_prompt_line_embedded_prompt():
    assert _is_prompt_line('print(42)  1> print(42) ')

def test_is_prompt_line_continuation():
    assert _is_prompt_line('  2.    2.   2. ')

def test_is_prompt_line_plain_output():
    assert not _is_prompt_line('42')
    assert not _is_prompt_line('hello world')

# ── Output parsing: echo lines ──

def test_parse_strips_echo_line():
    raw = '  1> print(42)\n42\n  2> '
    r = _parse_output(raw)
    assert r.success
    assert 'print(42)' not in r.stdout
    assert '42' in r.stdout

def test_parse_strips_embedded_echo():
    raw = 'print(42)  1> print(42) \n42\n  2> '
    r = _parse_output(raw)
    assert r.success
    assert 'print(42)' not in r.stdout
    assert '42' in r.stdout

def test_parse_strips_continuation_prompts():
    raw = '  2. \n42\n  3> '
    r = _parse_output(raw)
    assert r.success
    assert '42' in r.stdout
    assert '2.' not in r.stdout

def test_parse_strips_continuation_echo():
    raw = '  2.     if n <= 1: return n\n42\n  3> '
    r = _parse_output(raw)
    assert 'if n' not in r.stdout
    assert '42' in r.stdout

def test_parse_strips_next_prompt():
    raw = '42\n  2> '
    r = _parse_output(raw)
    assert '2>' not in r.stdout
    assert '42' in r.stdout

# ── Output parsing: clean output ──

def test_parse_empty():
    r = _parse_output('')
    assert r.success
    assert r.stdout == ''

def test_parse_simple_output():
    r = _parse_output('42\n')
    assert r.success
    assert r.stdout.strip() == '42'

def test_parse_multiline_output():
    r = _parse_output('hello\nworld\n')
    assert 'hello' in r.stdout
    assert 'world' in r.stdout

# ── Output parsing: errors ──

def test_parse_error_has_failure_fields():
    raw = "error: use of unknown declaration 'x'\nprint(x)\n      ^~\n"
    r = _parse_output(raw)
    assert not r.success
    assert r.ename == 'MojoError'
    assert 'unknown declaration' in r.evalue

def test_parse_error_strips_null():
    raw = "error: bad\n(null)\n"
    r = _parse_output(raw)
    assert not r.success
    assert '(null)' not in '\n'.join(r.traceback)

def test_parse_error_strips_user_prefix():
    raw = "[User] error: something broke\n"
    r = _parse_output(raw)
    assert not r.success
    assert not r.evalue.startswith('[User]')

def test_parse_error_with_prompt_prefix():
    raw = "  3> error: use of unknown declaration 'x'\nprint(x)\n      ^~\n"
    r = _parse_output(raw)
    assert not r.success
    assert 'unknown declaration' in r.evalue

def test_parse_full_repl_output():
    raw = '  1> print(42)\n42\n  2> '
    r = _parse_output(raw)
    assert r.success
    assert r.stdout.strip() == '42'

def test_parse_full_repl_error():
    raw = "  1> print(x)\nerror: use of unknown declaration 'x'\nprint(x)\n      ^~\n(null)\n  2> "
    r = _parse_output(raw)
    assert not r.success
    assert 'unknown declaration' in r.evalue

# ── Integration tests (need mojo-repl binary) ──

@pytest.fixture(scope='module')
def engine():
    e = PexpectEngine()
    e.start()
    yield e
    e.shutdown()

def test_engine_starts_alive(engine):
    assert engine.alive

def test_simple_print(engine):
    r = engine.execute('print(42)')
    assert r.success
    assert '42' in r.stdout

def test_output_is_clean(engine):
    r = engine.execute('print("hello")')
    assert r.success
    lines = [l for l in r.stdout.strip().split('\n') if l.strip()]
    assert lines == ['hello']

def test_no_output_expression(engine):
    r = engine.execute('var _test_silent = 1')
    assert r.success

def test_state_persistence(engine):
    engine.execute('var _test_persist = 10')
    r = engine.execute('print(_test_persist)')
    assert r.success
    assert '10' in r.stdout

def test_multiline_fn(engine):
    engine.execute('fn _test_add(a: Int, b: Int) -> Int:\n    return a + b')
    r = engine.execute('print(_test_add(3, 4))')
    assert r.success
    assert '7' in r.stdout

def test_arithmetic(engine):
    r = engine.execute('print(3 * 7 + 1)')
    assert r.success
    assert '22' in r.stdout

def test_error_returns_failure(engine):
    r = engine.execute('print(_totally_undefined_xyz)')
    assert not r.success
    assert r.ename == 'MojoError'

def test_error_has_traceback(engine):
    r = engine.execute('print(_no_such_var_abc)')
    assert not r.success
    assert len(r.traceback) > 0

def test_recovery_after_error(engine):
    engine.execute('print(_bad_var_recovery)')
    r = engine.execute('print(999)')
    assert r.success
    assert '999' in r.stdout

def test_var_with_print_no_accumulation(engine):
    r = engine.execute('var _test_acc = String("hi")\nprint(_test_acc)')
    assert r.success
    assert 'hi' in r.stdout
    r2 = engine.execute('print(42)')
    assert r2.success
    assert r2.stdout.strip() == '42'

def test_empty_code(engine):
    r = engine.execute('')
    assert r.success
    assert r.stdout == ''

def test_whitespace_only_code(engine):
    r = engine.execute('   \n  \n  ')
    assert r.success
    assert r.stdout == ''
