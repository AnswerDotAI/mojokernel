#!/bin/bash
set -e
cd "$(dirname "$0")/.."
pip install -e .
python3 -m mojokernel.install
jupyter kernelspec list | grep -i mojo
echo "Mojo kernel installed"
