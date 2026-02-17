#!/bin/bash
# Start the repl server in foreground for manual testing.
# Send JSON requests via stdin, see responses on stdout.
# Example: echo '{"type":"execute","id":1,"code":"print(42)"}' | tools/start_server.sh
cd "$(dirname "$0")/.."
exec build/mojo-repl-server "$@"
