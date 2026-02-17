#!/bin/bash
# Search for symbols in a Modular dylib (demangled).
# Usage: tools/dylib_symbols.sh <libname> [grep-pattern]
# Example: tools/dylib_symbols.sh libMojoJupyter.dylib Jupyter
set -e
cd "$(dirname "$0")/.."
ROOT="${MODROOT:=$(.venv/bin/python3 -c 'from mojo._package_root import get_package_root; print(get_package_root())')}"
LIB="${1:?Usage: dylib_symbols.sh <libname> [pattern]}"
if [ -n "$2" ]; then
    nm -g "$ROOT/lib/$LIB" | c++filt | grep -i "$2"
else
    nm -g "$ROOT/lib/$LIB" | c++filt
fi
