#!/bin/bash
set -e
cd "$(dirname "$0")/.."
pytest -q "$@"
