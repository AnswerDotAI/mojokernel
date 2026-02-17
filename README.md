# mojokernel

A Jupyter kernel for [Mojo](https://www.modular.com/mojo). Supports full variable persistence, function/struct definitions, and error handling across notebook cells.

## Install

Requires Mojo to be installed via `modular`.

```bash
pip install -e .
python -m mojokernel install --sys-prefix
```

Then select "Mojo" in Jupyter's kernel picker.

## Engines

mojokernel has two execution engines. Both support full `var`/`let` persistence, mutation, `fn`/`struct`/`trait` definitions, and proper error reporting.

### Pexpect engine (default)

Spawns `mojo repl` as a subprocess and communicates via PTY. This is the default because it requires no compilation step -- it works as soon as `mojo` is on your PATH.

- Uses `pexpect` to manage the REPL process
- Parses REPL output to extract results and detect errors
- Suppresses REPL UI noise via LLDB settings (`show-statusline`, `use-color`, etc.)

### C++ server engine

A compiled C++ binary that links directly against Modular's LLDB and uses `SBTarget::EvaluateExpression()` with REPL mode enabled. Communicates via JSON protocol on stdin/stdout.

- No text parsing -- structured API responses
- ~4x faster than pexpect for expression evaluation
- Requires compilation: `tools/build_server.sh`
- Requires brew LLVM headers: `brew install llvm`

To use the server engine:

```bash
tools/build_server.sh
MOJO_KERNEL_ENGINE=server jupyter lab
```

### PTY server (backup)

A third engine variant (`server/repl_server_pty.cpp`) uses `SBDebugger::RunREPL()` in a background thread with I/O redirected through a PTY pair. This is a fallback in case the `EvaluateExpression` approach breaks in a future Modular release. It works identically to the pexpect engine but as a self-contained C++ binary.

## Building the C++ server

```bash
brew install llvm
tools/build_server.sh
```

This produces:
- `build/mojo-repl-server` -- the main server (EvaluateExpression + REPL mode)
- `build/mojo-repl` -- thin REPL wrapper used by pexpect engine tests
- `build/mojo-repl-server-pty` -- PTY-based backup server

## Testing

```bash
pip install -e .
pytest -q
```

## Architecture

```
mojokernel/
  kernel.py              -- Jupyter kernel (ipykernel subclass)
  engines/
    base.py              -- ExecutionResult dataclass
    pexpect_engine.py    -- pexpect-based engine (default)
    server_engine.py     -- C++ server engine client
server/
  repl_server.cpp        -- C++ server (EvaluateExpression + REPL mode)
  repl_server_pty.cpp    -- PTY-based backup server
  mojo_repl.cpp          -- thin REPL wrapper (RunREPL)
  json.hpp               -- nlohmann/json
tests/
  test_pexpect_engine.py -- pexpect engine tests
  test_server_execute.py -- server engine tests
  test_kernel.py         -- kernel integration tests
tools/
  build_server.sh        -- compile C++ binaries
  server_exec.py         -- send code to server (debugging tool)
  test.sh                -- run pytest
```
