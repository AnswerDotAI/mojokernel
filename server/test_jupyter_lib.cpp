#include <cstdlib>
#include <dlfcn.h>
#include <iostream>
#include <string>

#include <lldb/API/SBDebugger.h>
#include <lldb/API/SBTarget.h>
#include <lldb/API/SBProcess.h>
#include <lldb/API/SBBreakpoint.h>
#include <lldb/API/SBExpressionOptions.h>
#include <lldb/API/SBValue.h>
#include <lldb/API/SBCommandInterpreter.h>
#include <lldb/API/SBCommandReturnObject.h>
#include <lldb/API/SBLanguageRuntime.h>
#include <lldb/API/SBError.h>

using namespace lldb;

static std::string drain(SBProcess &proc, size_t (SBProcess::*fn)(char*, size_t) const) {
    std::string out;
    char buf[65536];
    size_t n;
    while ((n = (proc.*fn)(buf, sizeof(buf))) > 0) out.append(buf, n);
    return out;
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: test-jupyter-lib <modular-root>\n";
        return 1;
    }
    std::string root = argv[1];
    auto entry_point = root + "/lib/mojo-repl-entry-point";
    auto plugin_path = root + "/lib/libMojoLLDB.dylib";
    auto jupyter_path = root + "/lib/libMojoJupyter.dylib";

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

    // Load MojoLLDB plugin
    ci.HandleCommand(("plugin load " + plugin_path).c_str(), ret);
    std::cout << "MojoLLDB loaded: " << (ret.Succeeded() ? "yes" : "no") << "\n";

    auto mojo_lang = SBLanguageRuntime::GetLanguageTypeFromString("mojo");
    debugger.SetREPLLanguage(mojo_lang);

    // Create target, breakpoint, launch
    SBError err;
    auto target = debugger.CreateTarget(entry_point.c_str(), "", "", true, err);
    auto bp = target.BreakpointCreateByName("mojo_repl_main");
    auto process = target.LaunchSimple(nullptr, nullptr, nullptr);
    std::cout << "Process state: " << process.GetState() << "\n";

    drain(process, &SBProcess::GetSTDOUT);
    drain(process, &SBProcess::GetSTDERR);

    // Load libMojoJupyter
    std::cout << "\n--- Loading libMojoJupyter.dylib ---\n";
    void *handle = dlopen(jupyter_path.c_str(), RTLD_NOW);
    if (!handle) {
        std::cout << "dlopen failed: " << dlerror() << "\n";
        return 1;
    }
    std::cout << "dlopen succeeded!\n";

    // Try evaluating with EvaluateExpression - does var persistence change?
    SBExpressionOptions opts;
    opts.SetLanguage(mojo_lang);
    opts.SetUnwindOnError(false);
    opts.SetGenerateDebugInfo(true);
    opts.SetTimeoutInMicroSeconds(0);

    std::cout << "\n--- Test 1: var declaration ---\n";
    auto v1 = target.EvaluateExpression("var _jtest = 42", opts);
    auto out1 = drain(process, &SBProcess::GetSTDOUT);
    auto e1 = v1.GetError();
    std::cout << "Error: " << (e1.Fail() ? "yes" : "no")
              << " msg: " << (e1.GetCString() ? e1.GetCString() : "(null)")
              << " stdout: [" << out1 << "]\n";

    std::cout << "\n--- Test 2: use var ---\n";
    auto v2 = target.EvaluateExpression("print(_jtest)", opts);
    auto out2 = drain(process, &SBProcess::GetSTDOUT);
    auto e2 = v2.GetError();
    std::cout << "Error: " << (e2.Fail() ? "yes" : "no")
              << " msg: " << (e2.GetCString() ? e2.GetCString() : "(null)")
              << " stdout: [" << out2 << "]\n";

    // Also try HandleCommand
    std::cout << "\n--- Test 3: HandleCommand var ---\n";
    SBCommandReturnObject r3;
    ci.HandleCommand("expression -l mojo -- var _jtest2 = 99", r3);
    auto out3 = drain(process, &SBProcess::GetSTDOUT);
    std::cout << "Succeeded: " << r3.Succeeded()
              << " output: [" << (r3.GetOutput() ? r3.GetOutput() : "") << "]"
              << " stdout: [" << out3 << "]\n";

    std::cout << "\n--- Test 4: HandleCommand use var ---\n";
    SBCommandReturnObject r4;
    ci.HandleCommand("expression -l mojo -- print(_jtest2)", r4);
    auto out4 = drain(process, &SBProcess::GetSTDOUT);
    std::cout << "Succeeded: " << r4.Succeeded()
              << " output: [" << (r4.GetOutput() ? r4.GetOutput() : "") << "]"
              << " stdout: [" << out4 << "]\n";

    process.Destroy();
    SBDebugger::Destroy(debugger);
    SBDebugger::Terminate();
    dlclose(handle);
    return 0;
}
