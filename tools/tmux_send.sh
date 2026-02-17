#!/bin/bash
# Send a command to a tmux pane and capture its output.
# Usage: tools/tmux_send.sh <command> [wait_secs]
set -e
PANE="%3"
CMD="${1}"
WAIT="${2:-2}"

tmux send-keys -t "$PANE" "$CMD" Enter
sleep "$WAIT"
tmux capture-pane -t "$PANE" -p | tail -20
