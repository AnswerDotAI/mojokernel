#!/bin/bash
set -e
jupyter kernelspec remove -y mojo 2>/dev/null || true
echo "Mojo kernel uninstalled"
