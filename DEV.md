# mojokernel internals

How the Mojo REPL works under the hood, and how each engine exploits it.

## Background: Mojo runs on LLDB

Mojo doesn't have a standalone interpreter. All Mojo code execution -- including the REPL -- happens through LLDB's expression evaluation infrastructure. When you run `mojo repl`, it:

1. Initializes LLDB via `SBDebugger::Initialize()`
2. Loads `libMojoLLDB.dylib` (Modular's LLDB plugin that adds Mojo language support)
3. Creates a target from `mojo-repl-entry-point` (a small binary with a `mojo_repl_main` breakpoint)
4. Launches the target and stops at the breakpoint
5. Calls `SBDebugger::RunREPL(mojo_lang)` to enter interactive REPL mode

The critical challenge for a Jupyter kernel is **variable persistence**. Each call to LLDB's `expression` command creates a new scope -- `var x = 42` in one call is invisible to the next.

## How Mojo REPL achieves variable persistence

Discovered by running `strings` on `libMojoLLDB.dylib`, the REPL uses a context struct pattern:

```mojo
struct __mojo_repl_context__:
    var `x`: __mojo_repl_UnsafePointer[mut=True, __mojo_repl_UnsafePointer[mut=True, Int]]
    pass
```

Each variable declared in the REPL gets a field in this struct as a double-indirection `UnsafePointer`. Every expression is then wrapped in:

```mojo
def __mojo_repl_expr_impl__(mut __mojo_repl_arg: __mojo_repl_context__,
                             mut `x`: Int) -> None:
    var __mojo_repl_expr_failed = True
    @parameter
    def __mojo_repl_expr_body__() -> None:
        # user code goes here
        pass
    __mojo_repl_expr_body__()
    __mojo_repl_expr_failed = False
```

The context struct accumulates fields as you declare variables. Each expression receives the accumulated context as a parameter, giving it access to all previously declared variables. LLDB's `AddPersistentVariable` stores the compiled results across evaluations.

This mechanism is triggered by a single boolean flag on LLDB's internal `EvaluateExpressionOptions` class: `m_repl = true`.

## The SetREPLEnabled discovery

LLDB's public SB API (`SBExpressionOptions`) does not expose any REPL mode flag. The Swift Jupyter kernel (`links/swift-jupyter/`) uses `SetREPLMode(True)` -- but that's a Swift-specific addition to the SB API that doesn't exist in Modular's LLDB.

However, the internal LLDB header (`lldb/Target/Target.h`) defines:

```cpp
class EvaluateExpressionOptions {
    // ...
    bool m_repl = false;
    // ...
    bool GetREPLEnabled() const { return m_repl; }
    void SetREPLEnabled(bool b) { m_repl = b; }
};
```

And `SBExpressionOptions` wraps this with a single member:

```cpp
class SBExpressionOptions {
    // ...
private:
    std::unique_ptr<lldb_private::EvaluateExpressionOptions> m_opaque_up;
};
```

The C++ server accesses the internal options via `reinterpret_cast`:

```cpp
static lldb_private::EvaluateExpressionOptions& get_internal(SBExpressionOptions &opts) {
    return **reinterpret_cast<std::unique_ptr<lldb_private::EvaluateExpressionOptions>*>(&opts);
}
```

This works because `SBExpressionOptions` has exactly one data member (the `unique_ptr`), so its address is the address of that member. We reinterpret it, dereference the `unique_ptr`, and call `SetREPLEnabled(true)`.

With this flag set, `SBTarget::EvaluateExpression()` activates the context struct mechanism and variables persist across calls.

Note: `EvaluateExpression` with Mojo always reports `GetError().Fail() == true` with the message "unknown error", even on success. Real errors have actual error messages. The server distinguishes them by checking if the error message is literally "unknown error".

## C++ server engine (`server/repl_server.cpp`)

The server is a single-process C++ binary:

1. **Startup**: Initialize LLDB, load `libMojoLLDB.dylib`, create target from `mojo-repl-entry-point`, set breakpoint on `mojo_repl_main`, launch and stop at breakpoint.

2. **Configure expression options**:
   ```cpp
   SBExpressionOptions opts;
   opts.SetLanguage(mojo_lang);        // language type 51
   opts.SetUnwindOnError(false);
   opts.SetGenerateDebugInfo(true);
   opts.SetTimeoutInMicroSeconds(0);   // no timeout
   get_internal(opts).SetREPLEnabled(true);  // the key flag
   ```

3. **JSON protocol loop**: Read JSON from stdin, call `target.EvaluateExpression(code, opts)`, drain stdout/stderr from the LLDB process, return JSON on stdout.

4. **Stdout capture**: When Mojo code calls `print()`, the output goes to the LLDB process's stdout. We drain it with `SBProcess::GetSTDOUT()` after each evaluation.

### Build requirements

The server includes `lldb/Target/Target.h` (LLDB internal header) which pulls in LLVM types. This requires linking against brew's LLVM support libraries in addition to Modular's liblldb:

```
-llldb23.0.0git     (from Modular)
-lLLVMSupport       (from brew LLVM)
-lLLVMDemangle      (from brew LLVM)
```

### JSON protocol

```
→ {"type":"execute","code":"var x = 42","id":1}
← {"id":1,"status":"ok","stdout":"","stderr":"","value":""}

→ {"type":"execute","code":"print(x)","id":2}
← {"id":2,"status":"ok","stdout":"42\r\n","stderr":"","value":""}

→ {"type":"execute","code":"print(bad)","id":3}
← {"id":3,"status":"error","stdout":"","stderr":"","ename":"MojoError",
   "evalue":"use of unknown declaration 'bad'","traceback":["..."]}

→ {"type":"shutdown","id":99}
← {"id":99,"status":"ok"}
```

## Pexpect engine (`mojokernel/engines/pexpect_engine.py`)

The pexpect engine spawns `mojo repl` with noise-suppressing LLDB settings:

```
mojo repl \
  -O 'settings set show-statusline false' \
  -O 'settings set show-progress false' \
  -O 'settings set use-color false' \
  -O 'settings set show-autosuggestion false' \
  -O 'settings set auto-indent false'
```

It sets `TERM=dumb` to minimize terminal escape sequences (though editline still produces some ANSI codes that must be stripped).

### Execution flow

1. **Send code**: Each line sent individually via `sendline()`, followed by a blank line to submit.
2. **Read output**: Read from the PTY until a prompt pattern (`\n\s*\d+>\s`) is detected. After the first prompt match, wait 300ms of silence ("settle time") to ensure all output has arrived.
3. **Parse output**: Strip ANSI codes, filter prompt lines (`\d+[>.]\s`) and echo lines, detect `error:` to split output from error messages.

### Why 300ms settle time?

PTY data arrives in chunks. The prompt pattern might appear in the middle of a chunk, with more output still buffered. The settle time ensures we've received everything before returning.

### Error detection

The parser scans for lines containing `error:` (case-insensitive). Once found, all subsequent lines are treated as error output. This matches how the Mojo compiler reports errors through the REPL.

### Testing note

`tests/test_pexpect_engine.py` is marked `@pytest.mark.slow`.

The pexpect fallback engine is not currently our active execution path, so normal development test runs should skip these tests:

```bash
tools/test.sh
```

Run them only when explicitly working on fallback behavior:

```bash
INCLUDE_SLOW=1 tools/test.sh -m slow
```

### Debug exploration tools

Use these scripts to capture reproducible behavior snapshots for offline debugging:

```bash
tools/explore_lsp.py
tools/explore_kernel_client.py
```

Each writes a timestamped JSON report under `meta/` with raw request/response payloads.

`MojoLSPClient` sets `MODULAR_PROFILE_FILENAME` to a temp path by default, so LSP profiling artifacts don't land in the project directory. Set `MODULAR_PROFILE_FILENAME` explicitly to override this.

For live kernel diagnostics, set `MOJO_KERNEL_LSP_DIAG=1` before starting Jupyter. Completion replies will include `_mojokernel_debug` metadata (per-stage success/failure, elapsed ms, and LSP health snapshot on errors), and kernel logs will include LSP warning details/restarts. If needed, tune LSP request timeout with `MOJO_LSP_REQUEST_TIMEOUT` (seconds).

## PTY server backup (`server/repl_server_pty.cpp`)

This is a C++ version of the pexpect approach. It:

1. Creates a PTY pair with `openpty()`
2. Redirects LLDB's stdin/stdout/stderr to the PTY slave via `SetInputFileHandle()`/`SetOutputFileHandle()`
3. Runs `SBDebugger::RunREPL()` in a detached `std::thread`
4. Communicates with the REPL through the PTY master using the same prompt detection and output parsing as the pexpect engine
5. Exposes the same JSON protocol on stdin/stdout as the main server

This exists as a fallback. If Modular changes the internal layout of `EvaluateExpressionOptions` (breaking the `reinterpret_cast`), the PTY server will still work because it uses only public LLDB APIs.

## Why not HandleCommand?

The original server used `ci.HandleCommand("expression -l mojo -- " + code)`. This works for `fn`/`struct`/`trait` definitions (which compile into the persistent LLDB module) but NOT for `var`/`let` declarations. Each `HandleCommand` creates a new expression scope -- variables are local to that scope and disappear after evaluation.

The `m_repl` flag changes this by telling the expression evaluator to use the REPL's context struct mechanism, which is what makes `EvaluateExpression` work like the interactive REPL.
