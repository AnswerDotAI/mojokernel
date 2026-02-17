import os
from ipykernel.kernelbase import Kernel

class MojoKernel(Kernel):
    implementation = 'mojokernel'
    implementation_version = '0.1.0'
    language = 'mojo'
    language_version = '0.26'
    language_info = {
        'mimetype': 'text/x-mojo',
        'name': 'mojo',
        'file_extension': '.mojo',
        'pygments_lexer': 'python',
        'codemirror_mode': 'python',
    }
    banner = 'Mojo Jupyter Kernel'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if os.environ.get('MOJO_KERNEL_ENGINE') == 'server':
            from .engines.server_engine import ServerEngine
            self.engine = ServerEngine()
        else:
            from .engines.pexpect_engine import PexpectEngine
            self.engine = PexpectEngine()
        self.engine.start()

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        code = code.strip()
        if not code:
            return {'status': 'ok', 'execution_count': self.execution_count,
                    'payload': [], 'user_expressions': {}}

        result = self.engine.execute(code)

        if not silent and result.stdout:
            self.send_response(self.iopub_socket, 'stream',
                             {'name': 'stdout', 'text': result.stdout})
        if not silent and result.stderr:
            self.send_response(self.iopub_socket, 'stream',
                             {'name': 'stderr', 'text': result.stderr})

        if result.success:
            return {'status': 'ok', 'execution_count': self.execution_count,
                    'payload': [], 'user_expressions': {}}
        else:
            if not silent:
                self.send_response(self.iopub_socket, 'error', {
                    'ename': result.ename, 'evalue': result.evalue,
                    'traceback': result.traceback})
            return {'status': 'error', 'execution_count': self.execution_count,
                    'ename': result.ename, 'evalue': result.evalue,
                    'traceback': result.traceback}

    def do_shutdown(self, restart):
        if restart: self.engine.restart()
        else: self.engine.shutdown()
        return {'status': 'ok', 'restart': restart}

    def do_interrupt(self):
        self.engine.interrupt()

    def do_is_complete(self, code):
        code = code.strip()
        if not code: return {'status': 'complete'}
        lines = code.split('\n')
        last = lines[-1].strip()
        if last.endswith(':') or last.endswith('\\'):
            return {'status': 'incomplete', 'indent': '    '}
        return {'status': 'complete'}

if __name__ == '__main__':
    from ipykernel.kernelapp import IPKernelApp
    IPKernelApp.launch_instance(kernel_class=MojoKernel)
