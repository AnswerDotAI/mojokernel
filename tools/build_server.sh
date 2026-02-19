#!/bin/bash
set -e
cd "$(dirname "$0")/.."

if [ -n "${MODULAR_ROOT:-}" ] && [ ! -d "$MODULAR_ROOT/lib" ]; then
    echo "Warning: MODULAR_ROOT=$MODULAR_ROOT is invalid, auto-detecting from python" >&2
    unset MODULAR_ROOT
fi
MODULAR_ROOT="${MODULAR_ROOT:-$(python -c 'from mojo._package_root import get_package_root; print(get_package_root())')}"
if [ ! -d "$MODULAR_ROOT/lib" ]; then
    echo "Error: MODULAR_ROOT/lib not found at $MODULAR_ROOT/lib" >&2
    exit 1
fi

shopt -s nullglob

if [ "$(uname)" = "Darwin" ]; then
    LLVM_INCLUDE="${LLVM_INCLUDE:-/opt/homebrew/opt/llvm/include}"
    LLVM_LIB="${LLVM_LIB:-/opt/homebrew/opt/llvm/lib}"
    libs=("$MODULAR_ROOT"/lib/liblldb*.dylib)
    if [ ${#libs[@]} -eq 0 ]; then
        echo "Error: no liblldb*.dylib found under $MODULAR_ROOT/lib" >&2
        exit 1
    fi
    LLDB_LIB=$(basename "${libs[0]}" .dylib | sed 's/^lib//')
else
    LLVM_INCLUDE="${LLVM_INCLUDE:-/usr/lib/llvm-18/include}"
    LLVM_LIB="${LLVM_LIB:-/usr/lib/llvm-18/lib}"
    libs=("$MODULAR_ROOT"/lib/liblldb*.so)
    if [ ${#libs[@]} -eq 0 ]; then
        echo "Error: no liblldb*.so found under $MODULAR_ROOT/lib" >&2
        exit 1
    fi
    LLDB_LIB=$(basename "${libs[0]}" | sed 's/^lib//;s/\.so$//')
fi

echo "MODULAR_ROOT=$MODULAR_ROOT"
echo "LLDB_LIB=$LLDB_LIB"

mkdir -p build
CFLAGS="-std=c++17 -I$LLVM_INCLUDE"
BASE_LD="-L$MODULAR_ROOT/lib -l$LLDB_LIB"

c++ $CFLAGS server/repl_server.cpp $BASE_LD -L$LLVM_LIB -lLLVMSupport -lLLVMDemangle -o build/mojo-repl-server
echo "Built build/mojo-repl-server"

mkdir -p mojokernel/bin
cp build/mojo-repl-server mojokernel/bin/
echo "Copied to mojokernel/bin/"

c++ $CFLAGS server/mojo_repl.cpp $BASE_LD -o build/mojo-repl
echo "Built build/mojo-repl"

c++ $CFLAGS server/repl_server_pty.cpp $BASE_LD -o build/mojo-repl-server-pty
echo "Built build/mojo-repl-server-pty"

if [ -f server/test_jupyter_lib.cpp ]; then
    c++ $CFLAGS server/test_jupyter_lib.cpp $BASE_LD -o build/test-jupyter-lib
    echo "Built build/test-jupyter-lib"
fi
