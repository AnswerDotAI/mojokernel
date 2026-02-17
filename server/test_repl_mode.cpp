// Test whether SetREPLEnabled(true) on EvaluateExpressionOptions
// gives us var persistence through EvaluateExpression.
#include <cstdlib>
#include <iostream>
#include <string>

#include <lldb/API/SBDebugger.h>
#include <lldb/API/SBTarget.h>
#include <lldb/API/SBProcess.h>
#include <lldb/API/SBBreakpoint.h>
#include <lldb/API/SBExpressionOptions.h>
#include <lldb/API/SBValue.h>
#include <lldb/API/SBLanguageRuntime.h>
#include <lldb/API/SBCommandInterpreter.h>
#include <lldb/API/SBCommandReturnObject.h>
#include <lldb/API/SBError.h>

// Internal header to access EvaluateExpressionOptions::SetREPLEnabled
#include <lldb/Target/Target.h>

using namespace lldb;
using namespace lldb_private;

static std::string drain(SBProcess &proc, size_t (SBProcess::*fn)(char*, size_t) const) {
    std::string out;
    char buf[65536];
    size_t n;
    while ((n = (proc.*fn)(buf, sizeof(buf))) > 0) out.append(buf, n);
    return out;
}

// Access the internal EvaluateExpressionOptions from SBExpressionOptions.
// SBExpressionOptions has a single member: unique_ptr<EvaluateExpressionOptions>
static EvaluateExpressionOptions& get_internal(SBExpressionOptions &opts) {
    auto *ptr = reinterpret_cast<std::unique_ptr<EvaluateExpressionOptions>*>(&opts);
    return **ptr;
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: test-repl-mode <modular-root>\n";
        return 1;
    }
    std::string root = argv[1];

    setenv("MODULAR_MAX_PACKAGE_ROOT", root.c_str(), 1);
    setenv("MODULAR_MOJO_MAX_PACKAGE_ROOT", root.c_str(), 1);
    setenv("MODULAR_MOJO_MAX_DRIVER_PATH", (root + "/bin/mojo").c_str(), 1);
    setenv("MODULAR_MOJO_MAX_IMPORT_PATH", (root + "/lib/mojo").c_str(), 1);

    SBDebugger::Initialize();
    auto debugger = SBDebugger::Create(false);
    debugger.SetScriptLanguage(eScriptLanguageNone);
    debugger.SetAsync(false);

    auto ci = debugger.GetCommandInterpreter();
    SBCommandReturnObject ret;
    ci.HandleCommand(("plugin load " + root + "/lib/libMojoLLDB.dylib").c_str(), ret);
    std::cout << "Plugin loaded: " << (ret.Succeeded() ? "yes" : "no") << "\n";

    auto mojo_lang = SBLanguageRuntime::GetLanguageTypeFromString("mojo");
    debugger.SetREPLLanguage(mojo_lang);

    SBError err;
    auto target = debugger.CreateTarget((root + "/lib/mojo-repl-entry-point").c_str(),
                                        "", "", true, err);
    auto bp = target.BreakpointCreateByName("mojo_repl_main");
    auto process = target.LaunchSimple(nullptr, nullptr, nullptr);
    std::cout << "Process state: " << process.GetState() << " (5=stopped)\n";
    drain(process, &SBProcess::GetSTDOUT);
    drain(process, &SBProcess::GetSTDERR);

    // Set up expression options with REPL mode enabled
    SBExpressionOptions opts;
    opts.SetLanguage(mojo_lang);
    opts.SetUnwindOnError(false);
    opts.SetGenerateDebugInfo(true);
    opts.SetTimeoutInMicroSeconds(0);

    // Access internal and enable REPL mode
    auto &internal = get_internal(opts);
    std::cout << "REPL enabled before: " << internal.GetREPLEnabled() << "\n";
    internal.SetREPLEnabled(true);
    std::cout << "REPL enabled after: " << internal.GetREPLEnabled() << "\n";

    // Test 1: Declare a variable
    std::cout << "\n--- Test 1: var x = 42 ---\n";
    auto v1 = target.EvaluateExpression("var x = 42", opts);
    auto out1 = drain(process, &SBProcess::GetSTDOUT);
    auto e1 = v1.GetError();
    std::cout << "Error: " << (e1.Fail() ? "yes" : "no")
              << " msg: " << (e1.GetCString() ? e1.GetCString() : "(null)")
              << " stdout: [" << out1 << "]\n";

    // Test 2: Use the variable — does it persist?
    std::cout << "\n--- Test 2: print(x) ---\n";
    auto v2 = target.EvaluateExpression("print(x)", opts);
    auto out2 = drain(process, &SBProcess::GetSTDOUT);
    auto e2 = v2.GetError();
    std::cout << "Error: " << (e2.Fail() ? "yes" : "no")
              << " msg: " << (e2.GetCString() ? e2.GetCString() : "(null)")
              << " stdout: [" << out2 << "]\n";

    // Test 3: Mutate and check
    std::cout << "\n--- Test 3: x = 99 ---\n";
    auto v3 = target.EvaluateExpression("x = 99", opts);
    auto out3 = drain(process, &SBProcess::GetSTDOUT);
    auto e3 = v3.GetError();
    std::cout << "Error: " << (e3.Fail() ? "yes" : "no")
              << " msg: " << (e3.GetCString() ? e3.GetCString() : "(null)")
              << " stdout: [" << out3 << "]\n";

    std::cout << "\n--- Test 4: print(x) after mutation ---\n";
    auto v4 = target.EvaluateExpression("print(x)", opts);
    auto out4 = drain(process, &SBProcess::GetSTDOUT);
    auto e4 = v4.GetError();
    std::cout << "Error: " << (e4.Fail() ? "yes" : "no")
              << " msg: " << (e4.GetCString() ? e4.GetCString() : "(null)")
              << " stdout: [" << out4 << "]\n";

    // Test 5: Function definition
    std::cout << "\n--- Test 5: fn add ---\n";
    auto v5 = target.EvaluateExpression("fn add(a: Int, b: Int) -> Int:\n    return a + b", opts);
    drain(process, &SBProcess::GetSTDOUT);

    std::cout << "\n--- Test 6: print(add(3,4)) ---\n";
    auto v6 = target.EvaluateExpression("print(add(3, 4))", opts);
    auto out6 = drain(process, &SBProcess::GetSTDOUT);
    std::cout << "stdout: [" << out6 << "]\n";

    process.Destroy();
    SBDebugger::Destroy(debugger);
    SBDebugger::Terminate();
    return 0;
}
