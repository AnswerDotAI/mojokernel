#!/bin/bash
# Print the modular root path
cd "$(dirname "$0")/.."
.venv/bin/python -c 'from mojo._package_root import get_package_root; print(get_package_root())'
