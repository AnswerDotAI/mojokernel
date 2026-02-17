#!/bin/bash
cd "$(dirname "$0")/.."
rm -rf build/ dist/ *.egg-info .pytest_cache
find . -name '__pycache__' -type d -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null
echo "Cleaned"
