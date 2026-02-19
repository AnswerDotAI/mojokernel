import os
from pathlib import Path
from ipykernel.kernelbase import Kernel
from .lsp_client import MojoLSPClient, completion_matches, hover_text, identifier_span


class MojoKernel(Kernel):
    implementation = 'mojokernel'
    implementation_version = '0.1.0'
    language = 'mojo'
    language_version = '0.26'
    language_info = dict(mimetype='text/x-mojo', name='mojo', file_extension='.mojo', pygments_lexer='python', codemirror_mode='python')
    banner = 'Mojo Jupyter Kernel'

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
                self.lsp = MojoLSPClient(include_dirs=include_dirs, root_uri=Path.cwd().resolve().as_uri(), logger=self.log.debug)
                self.lsp.start()
            except Exception as e:
                self.log.warning(f"Mojo LSP unavailable, completions disabled: {e}")
                self.lsp = None

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
        if not self.lsp: return dict(status='ok', matches=[], cursor_start=start, cursor_end=end, metadata={})
        text = self._lsp_preamble + code
        pos = len(self._lsp_preamble) + cursor_pos
        try: matches = completion_matches(self.lsp.complete(text, pos))
        except Exception as e:
            self.log.debug(f"Completion failed: {e}")
            matches = []
        return dict(status='ok', matches=matches, cursor_start=start, cursor_end=end, metadata={})

    def do_inspect(self, code, cursor_pos, detail_level=0, omit_sections=()):
        cursor_pos = len(code) if cursor_pos is None else cursor_pos
        if not self.lsp: return dict(status='ok', found=False, data={}, metadata={})
        text = self._lsp_preamble + code
        pos = len(self._lsp_preamble) + cursor_pos
        try: txt = hover_text(self.lsp.hover(text, pos))
        except Exception as e:
            self.log.debug(f"Inspect failed: {e}")
            txt = ''
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
