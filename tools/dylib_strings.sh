#!/bin/bash
# Search for strings in a Modular dylib.
# Usage: tools/dylib_strings.sh <libname> [grep-pattern]
# Example: tools/dylib_strings.sh libMojoLLDB.dylib repl
set -e
cd "$(dirname "$0")/.."
ROOT="${MODROOT:=$(.venv/bin/python -c 'from mojo._package_root import get_package_root; print(get_package_root())')}"
LIB="${1:?Usage: dylib_strings.sh <libname> [pattern]}"
if [ -n "$2" ]; then
    strings "$ROOT/lib/$LIB" | grep -i "$2"
else
    strings "$ROOT/lib/$LIB"
fi
