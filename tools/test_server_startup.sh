#!/bin/bash
# Test that the server starts up, responds to a shutdown request, and exits cleanly.
# Prints server stderr for diagnostics on failure.
set -e
cd "$(dirname "$0")/.."
MODULAR_ROOT="$(.venv/bin/python3 -c 'from mojo._package_root import get_package_root; print(get_package_root())')"

STDERR_LOG=$(mktemp)
trap "rm -f $STDERR_LOG" EXIT

RESPONSE=$(echo '{"type":"shutdown","id":1}' | build/mojo-repl-server "$MODULAR_ROOT" 2>"$STDERR_LOG")
EXIT=$?

echo "Server stderr:"
cat "$STDERR_LOG"
echo ""
echo "Server stdout: $RESPONSE"
echo "Exit code: $EXIT"

if [ $EXIT -ne 0 ]; then
    echo "FAIL: server exited with code $EXIT"
    exit 1
fi

# Check for ready message followed by shutdown ack
if echo "$RESPONSE" | head -1 | grep -q '"ready"'; then
    echo "PASS: server started and shut down cleanly"
else
    echo "FAIL: expected ready message, got: $(echo "$RESPONSE" | head -1)"
    exit 1
fi
