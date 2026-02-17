#!/usr/bin/env python3
"""Send code to the server and print responses.
Usage: tools/server_exec.py 'print(42)' 'var x = 1' 'print(x)'
"""
import json,os,subprocess,sys
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: server_exec.py <code> [<code> ...]", file=sys.stderr)
        sys.exit(1)
    root = Path(__file__).resolve().parents[1]
    server_bin = root / "build" / "mojo-repl-server"
    from mojo._package_root import get_package_root
    modular_root = get_package_root()
    env = {**os.environ, 'DYLD_LIBRARY_PATH': f'{modular_root}/lib', 'LD_LIBRARY_PATH': f'{modular_root}/lib'}
    proc = subprocess.Popen(
        [str(server_bin), modular_root],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    ready = json.loads(proc.stdout.readline())
    assert ready['status'] == 'ready', f"Server not ready: {ready}"
    for i, code in enumerate(sys.argv[1:], 1):
        req = json.dumps({'type': 'execute', 'id': i, 'code': code})
        proc.stdin.write((req + '\n').encode())
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        print(json.dumps(resp, indent=2))
    proc.stdin.write(b'{"type":"shutdown","id":999}\n')
    proc.stdin.flush()
    proc.wait(timeout=10)
    stderr = proc.stderr.read().decode()
    if stderr: print(f"--- server stderr ---\n{stderr}", file=sys.stderr)

if __name__ == '__main__': main()
