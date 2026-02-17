import re,os
import pexpect
from .base import ExecutionResult

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?[0-9;]*[A-Za-z]')
_PROMPT_PAT = re.compile(r'\n\s*\d+>\s')
_PROMPT_LINE_RE = re.compile(r'\s*\d+[>.]\s')
_ECHO_RE = re.compile(r'\s+\d+[>]\s')
_ERROR_RE = re.compile(r'error:', re.IGNORECASE)

_REPL_SETTINGS = [
    '-O', 'settings set show-statusline false',
    '-O', 'settings set show-progress false',
    '-O', 'settings set use-color false',
    '-O', 'settings set show-autosuggestion false',
    '-O', 'settings set auto-indent false',
]

def _strip_ansi(s): return _ANSI_RE.sub('', s)

def _find_mojo():
    import shutil
    return shutil.which('mojo')

def _is_prompt_line(line):
    if _PROMPT_LINE_RE.match(line): return True
    if _ECHO_RE.search(line): return True
    return False

def _parse_output(raw):
    raw = raw.replace('\r', '')
    lines = raw.split('\n')
    output, errors = [], []
    in_error = False
    for line in lines:
        if not line.strip(): continue
        stripped = re.sub(r'^\s*\d+[>.]\s*', '', line) if _PROMPT_LINE_RE.match(line) else line
        if _ERROR_RE.search(stripped): in_error = True
        if in_error:
            s = stripped.strip()
            if s and s != '(null)': errors.append(s)
            continue
        if _is_prompt_line(line): continue
        output.append(line)
    stdout = '\n'.join(output) + '\n' if output else ''
    if errors:
        evalue = errors[0]
        if evalue.startswith('[User] '): evalue = evalue[7:]
        return ExecutionResult(stdout=stdout, success=False,
            ename='MojoError', evalue=evalue, traceback=errors)
    return ExecutionResult(stdout=stdout)


class PexpectEngine:
    def __init__(self):
        self.child = None
        self._warmed = False

    def start(self):
        mojo = _find_mojo()
        if not mojo: raise FileNotFoundError("mojo not found on PATH")
        env = os.environ.copy()
        env['TERM'] = 'dumb'
        self.child = pexpect.spawn(
            mojo, ['repl'] + _REPL_SETTINGS,
            encoding='utf-8', echo=False, timeout=60, env=env)
        self.child.expect('delimited by a blank line', timeout=30)
        self._drain(timeout=3)

    def _drain(self, timeout=1):
        try:
            while True: self.child.read_nonblocking(100000, timeout=timeout)
        except (pexpect.TIMEOUT, pexpect.EOF): pass

    def _read_until_prompt(self, timeout):
        import time
        buf = ''
        deadline = time.time() + timeout
        prompt_time = 0
        while time.time() < deadline:
            try:
                chunk = self.child.read_nonblocking(100000, timeout=1)
                buf += chunk
                if not prompt_time and _PROMPT_PAT.search(_strip_ansi(buf)):
                    prompt_time = time.time()
            except pexpect.TIMEOUT:
                if prompt_time and time.time() - prompt_time > 0.3:
                    return _strip_ansi(buf)
            except pexpect.EOF: raise
        if prompt_time: return _strip_ansi(buf)
        return None

    def execute(self, code):
        if not self.child or not self.child.isalive():
            raise RuntimeError("REPL process not running")
        code = code.strip()
        if not code: return ExecutionResult()
        self._drain(timeout=0.1)
        for line in code.split('\n'):
            self.child.sendline(line)
        self.child.sendline('')
        try: raw = self._read_until_prompt(timeout=30)
        except pexpect.EOF:
            return ExecutionResult(stderr='REPL process died', success=False,
                ename='REPLError', evalue='REPL process died',
                traceback=['The Mojo REPL process terminated unexpectedly'])
        if raw is None:
            return ExecutionResult(stderr='Expression timed out', success=False,
                ename='TimeoutError', evalue='Expression timed out',
                traceback=['Expression evaluation timed out'])
        self._warmed = True
        return _parse_output(raw)

    def interrupt(self):
        if self.child and self.child.isalive(): self.child.sendintr()

    def restart(self):
        self.shutdown()
        self.start()
        self._warmed = False

    def shutdown(self):
        if self.child and self.child.isalive():
            try:
                self.child.sendline(':quit')
                self.child.expect(pexpect.EOF, timeout=5)
            except (pexpect.TIMEOUT, pexpect.EOF): pass
            self.child.close(force=True)
        self.child = None

    @property
    def alive(self): return self.child is not None and self.child.isalive()
