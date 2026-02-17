#!/bin/bash
# Build and run C++ server unit tests.
# Each test is a small binary in server/tests/ that exits 0 on success.
set -e
cd "$(dirname "$0")/.."
MODULAR_ROOT="$(.venv/bin/python3 -c 'from mojo._package_root import get_package_root; print(get_package_root())')"
LLVM_INCLUDE="${LLVM_INCLUDE:-/path/to/llvm/include}"

mkdir -p build/tests
failed=0
for src in server/tests/test_*.cpp; do
    [ -f "$src" ] || { echo "No C++ tests found in server/tests/"; exit 0; }
    name=$(basename "$src" .cpp)
    echo "Building $name..."
    c++ -std=c++17 \
        -I"$LLVM_INCLUDE" -Iserver \
        -L"$MODULAR_ROOT/lib" \
        -llldb23.0.0git \
        -Wl,-rpath,"$MODULAR_ROOT/lib" \
        -o "build/tests/$name" \
        "$src"
    echo -n "Running $name... "
    if "build/tests/$name"; then
        echo "ok"
    else
        echo "FAILED"
        failed=$((failed + 1))
    fi
done
[ $failed -eq 0 ] && echo "All C++ tests passed" || { echo "$failed test(s) failed"; exit 1; }
