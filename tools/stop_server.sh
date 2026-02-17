#!/bin/bash
# Stop any running repl server instances
pkill -f mojo-repl-server 2>/dev/null && echo "Stopped mojo-repl-server" || echo "No server running"
