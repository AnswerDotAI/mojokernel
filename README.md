# mojokernel

A Jupyter kernel for [Mojo](https://www.modular.com/mojo). Supports full variable persistence, function/struct definitions, and error handling across notebook cells.

**NB**: This is *not* an official Modular project. It's created by Jeremy Howard from Answer.AI and uses undocumented private symbols from mojo in ways that might turn out to be flaky or disappear in a future version. If Modular releases an official kernel that provides the same functionality, we'll archive this one and update these docs to point at that instead.

## Install

We recommend [uv](https://docs.astral.sh/uv/) which handles the Mojo package index automatically:

```bash
uv pip install mojokernel
mojokernel install --sys-prefix
```

With pip, install Mojo first (it's hosted on a separate index that pip can't discover automatically):

```bash
pip install modular --extra-index-url https://modular.gateway.scarf.sh/simple/
pip install mojokernel
mojokernel install --sys-prefix
```

Then select "Mojo" in Jupyter's kernel picker.

The pre-built wheel includes a compiled C++ server that talks directly to Mojo's LLDB -- no text parsing, fast and reliable. If the server binary isn't available for your platform, the kernel falls back to a pexpect-based engine that wraps `mojo repl`.

### Pexpect fallback

The pexpect engine is used automatically if the C++ server binary isn't present (e.g. when installing from the sdist). It spawns `mojo repl` and parses its output. To force pexpect mode:

```bash
MOJO_KERNEL_ENGINE=pexpect jupyter lab
```

### Building from source

To build the C++ server yourself (e.g. for development or an unsupported platform):

```bash
brew install llvm   # macOS; on Linux: apt install llvm-18-dev liblldb-18-dev
tools/build_server.sh
```

### PTY server (backup)

A third engine variant (`server/repl_server_pty.cpp`) uses `SBDebugger::RunREPL()` with PTY I/O redirection. This is a fallback in case the `EvaluateExpression` approach breaks in a future Modular release.

## Releases

mojokernel wheels are built against a specific Mojo version. Install the version matching your Mojo:

```bash
# Check your Mojo version
mojo --version          # e.g. "Mojo 0.26.1.0 (156d3ac6)" → version 26.1.0

# Install matching mojokernel
uv pip install mojokernel==26.1.0
```

### Publishing a new release

The wheel version is determined automatically from the latest Mojo release (via `mojokernel/_version.py`). No manual version bumping needed.

When Mojo publishes a new version:

1. Go to Actions > "Build and publish wheels" > Run workflow
2. Set `publish: true` to upload to PyPI

The CI installs the latest Mojo, builds the C++ server, and produces platform wheels tagged with the detected Mojo version. Wheels are built for macOS (Apple Silicon) and Linux (x86_64).

## Testing

```bash
uv pip install -e .
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
  explore_lsp.py         -- run LSP probes and write report to meta/
  explore_kernel_client.py -- run jupyter-client probes and write report to meta/
  test.sh                -- run pytest
```
