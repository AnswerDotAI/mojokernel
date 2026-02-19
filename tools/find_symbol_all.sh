#!/bin/bash
# Search for a symbol across all Modular dylibs (including local symbols).
# Usage: tools/find_symbol_all.sh <pattern>
set -e
cd "$(dirname "$0")/.."
ROOT="${MODROOT:=$(.venv/bin/python -c 'from mojo._package_root import get_package_root; print(get_package_root())')}"
PAT="${1:?Usage: find_symbol_all.sh <pattern>}"
for lib in "$ROOT/lib/"*.dylib; do
    matches=$(nm "$lib" 2>/dev/null | c++filt | grep -i "$PAT" || true)
    if [ -n "$matches" ]; then
        echo "=== $(basename "$lib") ==="
        echo "$matches"
    fi
done
