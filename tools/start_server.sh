#!/bin/bash
# Start the repl server in foreground for manual testing.
# Send JSON requests via stdin, see responses on stdout.
# Example: echo '{"type":"execute","id":1,"code":"print(42)"}' | tools/start_server.sh
cd "$(dirname "$0")/.."
MODULAR_ROOT="$(.venv/bin/python3 -c 'from mojo._package_root import get_package_root; print(get_package_root())')"
exec build/mojo-repl-server "$MODULAR_ROOT" "$@"
