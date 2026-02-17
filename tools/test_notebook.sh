#!/bin/bash
set -e
cd "$(dirname "$0")/.."
outdir=$(mktemp -d)
jupyter nbconvert --to notebook --execute tests/notebooks/*.ipynb --output-dir "$outdir"
echo "All test notebooks passed (output in $outdir)"
