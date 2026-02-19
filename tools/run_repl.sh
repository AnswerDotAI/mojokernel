#!/bin/bash
# Launch mojo repl with noise-suppression flags for manual testing
cd "$(dirname "$0")/.."
MODULAR_ROOT="$(.venv/bin/python -c 'from mojo._package_root import get_package_root; print(get_package_root())')"
exec "$MODULAR_ROOT/bin/mojo" repl \
    -O "settings set show-statusline false" \
    -O "settings set show-progress false" \
    -O "settings set use-color false" \
    -O "settings set show-autosuggestion false" \
    "$@"
