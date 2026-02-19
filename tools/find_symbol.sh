#!/bin/bash
# Search for a symbol across all Modular dylibs.
# Usage: tools/find_symbol.sh <pattern>
set -e
cd "$(dirname "$0")/.."
ROOT="${MODROOT:=$(.venv/bin/python -c 'from mojo._package_root import get_package_root; print(get_package_root())')}"
PAT="${1:?Usage: find_symbol.sh <pattern>}"
for lib in "$ROOT/lib/"*.dylib; do
    matches=$(nm -g "$lib" 2>/dev/null | c++filt | grep -v '^ *U ' | grep -i "$PAT" || true)
    if [ -n "$matches" ]; then
        echo "=== $(basename "$lib") ==="
        echo "$matches"
    fi
done
