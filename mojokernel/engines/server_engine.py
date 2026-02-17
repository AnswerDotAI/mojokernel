import json,os,signal,subprocess
from pathlib import Path
from .base import ExecutionResult


def _find_modular_root():
    from mojo._package_root import get_package_root
    return get_package_root()


def _find_server_binary():
    """Find the mojo-repl-server binary, checking build dir then PATH."""
    build_dir = Path(__file__).resolve().parents[2] / "build"
    p = build_dir / "mojo-repl-server"
    if p.exists(): return str(p)
    import shutil
    return shutil.which("mojo-repl-server")


class ServerEngine:
    def __init__(self):
        self.proc = None
        self._next_id = 0

    def start(self):
        server_bin = _find_server_binary()
        if not server_bin:
            raise FileNotFoundError("mojo-repl-server not found. Run tools/build_server.sh first.")
        root = _find_modular_root()
        env = dict(os.environ)
        env.update({
            'MODULAR_MAX_PACKAGE_ROOT': root,
            'MODULAR_MOJO_MAX_PACKAGE_ROOT': root,
            'MODULAR_MOJO_MAX_DRIVER_PATH': os.path.join(root, 'bin', 'mojo'),
            'MODULAR_MOJO_MAX_IMPORT_PATH': os.path.join(root, 'lib', 'mojo'),
        })
        self.proc = subprocess.Popen(
            [server_bin, root],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env)

        # Wait for ready message
        ready = self._read_response()
        if ready.get('status') == 'error':
            raise RuntimeError(f"Server failed to start: {ready.get('message', 'unknown error')}")
        if ready.get('status') != 'ready':
            raise RuntimeError(f"Unexpected server response: {ready}")

    def _send(self, req):
        self._next_id += 1
        req['id'] = self._next_id
        line = json.dumps(req, separators=(',', ':')) + '\n'
        self.proc.stdin.write(line.encode())
        self.proc.stdin.flush()
        return self._read_response()

    def _read_response(self):
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read().decode() if self.proc.stderr else ''
            raise RuntimeError(f"Server process died. stderr: {stderr}")
        return json.loads(line)

    def execute(self, code):
        code = code.strip()
        if not code: return ExecutionResult()

        resp = self._send({'type': 'execute', 'code': code})

        if resp.get('status') == 'error':
            return ExecutionResult(
                stdout=resp.get('stdout', ''),
                stderr=resp.get('stderr', ''),
                success=False,
                ename=resp.get('ename', 'MojoError'),
                evalue=resp.get('evalue', ''),
                traceback=resp.get('traceback', []))

        return ExecutionResult(
            stdout=resp.get('stdout', ''),
            stderr=resp.get('stderr', ''))

    def interrupt(self):
        if self.proc and self.proc.poll() is None:
            os.kill(self.proc.pid, signal.SIGINT)

    def restart(self):
        self.shutdown()
        self.start()

    def shutdown(self):
        if self.proc and self.proc.poll() is None:
            try:
                self._send({'type': 'shutdown'})
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
                self.proc.wait()
        self.proc = None

    @property
    def alive(self): return self.proc is not None and self.proc.poll() is None
