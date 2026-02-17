#!/bin/bash
cd "$(dirname "$0")/.."
MODULAR_ROOT="$(.venv/bin/python3 -c 'from mojo._package_root import get_package_root; print(get_package_root())')"
echo "Modular root: $MODULAR_ROOT"
echo "Mojo version: $("$MODULAR_ROOT/bin/mojo" --version 2>&1)"
echo ""
echo "Key binaries:"
for f in mojo mojo-lldb mojo-lsp-server; do
    [ -f "$MODULAR_ROOT/bin/$f" ] && echo "  [ok] bin/$f" || echo "  [!!] bin/$f MISSING"
done
echo ""
echo "Key libraries:"
for f in mojo-repl-entry-point libMojoLLDB.dylib liblldb23.0.0git.dylib libMojoJupyter.dylib; do
    [ -f "$MODULAR_ROOT/lib/$f" ] && echo "  [ok] lib/$f ($(du -h "$MODULAR_ROOT/lib/$f" | cut -f1))" || echo "  [!!] lib/$f MISSING"
done
