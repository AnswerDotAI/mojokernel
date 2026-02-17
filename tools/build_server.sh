#!/bin/bash
set -e
cd "$(dirname "$0")/.."
MODULAR_ROOT="${MODULAR_ROOT:-$(python3 -c 'from mojo._package_root import get_package_root; print(get_package_root())')}"

if [ "$(uname)" = "Darwin" ]; then
    LLVM_INCLUDE="${LLVM_INCLUDE:-/opt/homebrew/opt/llvm/include}"
    LLVM_LIB="${LLVM_LIB:-/opt/homebrew/opt/llvm/lib}"
    LLDB_LIB=$(basename "$MODULAR_ROOT"/lib/liblldb*.dylib .dylib | sed 's/^lib//')
else
    LLVM_INCLUDE="${LLVM_INCLUDE:-/usr/lib/llvm-18/include}"
    LLVM_LIB="${LLVM_LIB:-/usr/lib/llvm-18/lib}"
    LLDB_LIB=$(basename "$MODULAR_ROOT"/lib/liblldb*.so | sed 's/^lib//;s/\.so$//')
fi

echo "MODULAR_ROOT=$MODULAR_ROOT"
echo "LLDB_LIB=$LLDB_LIB"

mkdir -p build
CFLAGS="-std=c++17 -I$LLVM_INCLUDE"
LDFLAGS="-L$MODULAR_ROOT/lib -l$LLDB_LIB -L$LLVM_LIB -lLLVMSupport -lLLVMDemangle"

c++ $CFLAGS server/repl_server.cpp $LDFLAGS -o build/mojo-repl-server
echo "Built build/mojo-repl-server"

mkdir -p mojokernel/bin
cp build/mojo-repl-server mojokernel/bin/
echo "Copied to mojokernel/bin/"

c++ $CFLAGS server/mojo_repl.cpp -L$MODULAR_ROOT/lib -l$LLDB_LIB -o build/mojo-repl
echo "Built build/mojo-repl"

c++ $CFLAGS server/repl_server_pty.cpp -L$MODULAR_ROOT/lib -l$LLDB_LIB -o build/mojo-repl-server-pty
echo "Built build/mojo-repl-server-pty"

if [ -f server/test_jupyter_lib.cpp ]; then
    c++ $CFLAGS server/test_jupyter_lib.cpp -L$MODULAR_ROOT/lib -l$LLDB_LIB -o build/test-jupyter-lib
    echo "Built build/test-jupyter-lib"
fi
