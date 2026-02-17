#!/bin/bash
set -e
cd "$(dirname "$0")/.."
MODULAR_ROOT="$(.venv/bin/python3 -c 'from mojo._package_root import get_package_root; print(get_package_root())')"
LLVM_INCLUDE="${LLVM_INCLUDE:-/opt/homebrew/opt/llvm/include}"

mkdir -p build
LLVM_LIB="${LLVM_LIB:-/opt/homebrew/opt/llvm/lib}"
BASE="-std=c++17 -I$LLVM_INCLUDE -L$MODULAR_ROOT/lib -llldb23.0.0git"
LLVM_LIBS="-L$LLVM_LIB -lLLVMSupport -lLLVMDemangle"

c++ $BASE $LLVM_LIBS -o build/mojo-repl-server server/repl_server.cpp
echo "Built build/mojo-repl-server"

c++ $BASE -o build/mojo-repl server/mojo_repl.cpp
echo "Built build/mojo-repl"

c++ $BASE -o build/mojo-repl-server-pty server/repl_server_pty.cpp
echo "Built build/mojo-repl-server-pty"

if [ -f server/test_jupyter_lib.cpp ]; then
    c++ $BASE -o build/test-jupyter-lib server/test_jupyter_lib.cpp
    echo "Built build/test-jupyter-lib"
fi
