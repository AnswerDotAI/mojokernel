#!/bin/bash
set -e
cd "$(dirname "$0")/.."
pip install -e .
python3 -m mojokernel install --sys-prefix
jupyter kernelspec list | grep -i mojo
echo "Mojo kernel installed"
