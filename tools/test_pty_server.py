#!/usr/bin/env python
"""Test the PTY-based REPL server for var persistence and basic functionality."""
import subprocess, json, sys, time

def get_modroot():
    from mojo._package_root import get_package_root
    return str(get_package_root())

def send(proc, req):
    line = json.dumps(req) + '\n'
    proc.stdin.write(line)
    proc.stdin.flush()
    resp = proc.stdout.readline()
    return json.loads(resp)

def main():
    root = get_modroot()
    cmd = ['build/mojo-repl-server-pty', root]
    print(f"Starting PTY server...")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, bufsize=1)

    # Read ready message
    ready = proc.stdout.readline()
    print(f"Ready: {ready.strip()}")
    r = json.loads(ready)
    assert r['status'] == 'ready', f"Not ready: {r}"

    tests_passed = 0
    tests_failed = 0

    def test(name, req, check):
        nonlocal tests_passed, tests_failed
        resp = send(proc, req)
        try:
            check(resp)
            print(f"  PASS: {name}")
            tests_passed += 1
        except AssertionError as e:
            print(f"  FAIL: {name}: {e}")
            print(f"    Response: {json.dumps(resp, indent=2)}")
            tests_failed += 1

    # Basic print
    test("print(42)", {"type": "execute", "code": "print(42)", "id": 1},
         lambda r: (assert_eq(r['status'], 'ok'), assert_in('42', r['stdout'])))

    # Var declaration
    test("var x = 10", {"type": "execute", "code": "var x = 10", "id": 2},
         lambda r: assert_eq(r['status'], 'ok'))

    # VAR PERSISTENCE - the key test!
    test("print(x) [persistence]", {"type": "execute", "code": "print(x)", "id": 3},
         lambda r: (assert_eq(r['status'], 'ok'), assert_in('10', r['stdout'])))

    # Var mutation persistence
    test("x = 20", {"type": "execute", "code": "x = 20", "id": 4},
         lambda r: assert_eq(r['status'], 'ok'))

    test("print(x) after mutation", {"type": "execute", "code": "print(x)", "id": 5},
         lambda r: (assert_eq(r['status'], 'ok'), assert_in('20', r['stdout'])))

    # Function definition
    test("fn add", {"type": "execute", "code": "fn add(a: Int, b: Int) -> Int:\n    return a + b", "id": 6},
         lambda r: assert_eq(r['status'], 'ok'))

    test("print(add(3,4))", {"type": "execute", "code": "print(add(3, 4))", "id": 7},
         lambda r: (assert_eq(r['status'], 'ok'), assert_in('7', r['stdout'])))

    # Struct definition
    test("struct Point", {"type": "execute", "code": "struct Point:\n    var x: Int\n    var y: Int\n    fn __init__(out self, x: Int, y: Int):\n        self.x = x\n        self.y = y", "id": 8},
         lambda r: assert_eq(r['status'], 'ok'))

    test("use Point", {"type": "execute", "code": "var p = Point(3, 4)\nprint(p.x)", "id": 9},
         lambda r: (assert_eq(r['status'], 'ok'), assert_in('3', r['stdout'])))

    # Error handling
    test("undefined var", {"type": "execute", "code": "print(undefined_var_xyz)", "id": 10},
         lambda r: assert_eq(r['status'], 'error'))

    # Shutdown
    resp = send(proc, {"type": "shutdown", "id": 99})
    print(f"\nShutdown: {resp}")
    proc.wait(timeout=10)

    print(f"\n{'='*40}")
    print(f"Results: {tests_passed} passed, {tests_failed} failed")
    return 0 if tests_failed == 0 else 1

def assert_eq(a, b):
    assert a == b, f"{a!r} != {b!r}"

def assert_in(needle, haystack):
    assert needle in haystack, f"{needle!r} not in {haystack!r}"

if __name__ == '__main__':
    sys.exit(main())
