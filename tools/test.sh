#!/bin/bash
set -e
cd "$(dirname "$0")/.."
if [ "${INCLUDE_SLOW:-0}" = "1" ]; then
    pytest -q "$@"
else
    pytest -q -m "not slow" "$@"
fi
