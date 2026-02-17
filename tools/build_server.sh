#!/bin/bash
set -e
cd "$(dirname "$0")/.."
MODULAR_ROOT="$(.venv/bin/python3 -c 'from mojo._package_root import get_package_root; print(get_package_root())')"
LLVM_INCLUDE="${LLVM_INCLUDE:-/opt/homebrew/opt/llvm/include}"

mkdir -p build
c++ -std=c++17 \
    -I"$LLVM_INCLUDE" \
    -L"$MODULAR_ROOT/lib" \
    -llldb23.0.0git \
    -Wl,-rpath,"$MODULAR_ROOT/lib" \
    -o build/mojo-repl-server \
    server/repl_server.cpp

echo "Built build/mojo-repl-server"
