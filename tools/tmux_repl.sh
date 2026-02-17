#!/bin/bash
# Start mojo-repl in a tmux pane.
# Usage: tools/tmux_repl.sh [wait_secs]
PANE="%3"
WAIT="${1:-10}"
tmux send-keys -t "$PANE" './build/mojo-repl $MODROOT' Enter
sleep "$WAIT"
tmux capture-pane -t "$PANE" -p | tail -10
