// Thin wrapper that enters Mojo REPL mode with clean output.
// Links against Modular's liblldb (unlike standalone lldb which crashes).
// Usage: mojo-repl <modular-root>
#include <cstdlib>
#include <iostream>
#include <string>

#include <lldb/API/SBDebugger.h>
#include <lldb/API/SBTarget.h>
#include <lldb/API/SBProcess.h>
#include <lldb/API/SBBreakpoint.h>
#include <lldb/API/SBLanguageRuntime.h>
#include <lldb/API/SBCommandInterpreter.h>
#include <lldb/API/SBCommandReturnObject.h>
#include <lldb/API/SBError.h>

using namespace lldb;

int main(int argc, char *argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: mojo-repl <modular-root>\n";
        return 1;
    }
    std::string root = argv[1];

    setenv("MODULAR_MAX_PACKAGE_ROOT", root.c_str(), 1);
    setenv("MODULAR_MOJO_MAX_PACKAGE_ROOT", root.c_str(), 1);
    setenv("MODULAR_MOJO_MAX_DRIVER_PATH", (root + "/bin/mojo").c_str(), 1);
    setenv("MODULAR_MOJO_MAX_IMPORT_PATH", (root + "/lib/mojo").c_str(), 1);

    SBDebugger::Initialize();
    auto dbg = SBDebugger::Create(true);
    if (!dbg.IsValid()) { std::cerr << "Failed to create debugger\n"; return 1; }

    // Suppress all noise
    auto ci = dbg.GetCommandInterpreter();
    SBCommandReturnObject r;
    ci.HandleCommand("settings set show-statusline false", r);
    ci.HandleCommand("settings set show-progress false", r);
    ci.HandleCommand("settings set use-color false", r);
    ci.HandleCommand("settings set show-autosuggestion false", r);
    ci.HandleCommand("settings set stop-line-count-before 0", r);
    ci.HandleCommand("settings set stop-line-count-after 0", r);

    // Load plugin
    ci.HandleCommand(("plugin load " + root + "/lib/libMojoLLDB.dylib").c_str(), r);
    if (!r.Succeeded()) { std::cerr << "Failed to load MojoLLDB plugin\n"; return 1; }

    auto mojo_lang = SBLanguageRuntime::GetLanguageTypeFromString("mojo");
    if (mojo_lang == eLanguageTypeUnknown) { std::cerr << "Mojo lang not found\n"; return 1; }
    dbg.SetREPLLanguage(mojo_lang);

    // Create target and launch
    SBError err;
    auto target = dbg.CreateTarget((root + "/lib/mojo-repl-entry-point").c_str(),
                                   "", "", true, err);
    if (!target.IsValid()) { std::cerr << "Failed to create target\n"; return 1; }

    auto bp = target.BreakpointCreateByName("mojo_repl_main");
    auto process = target.LaunchSimple(nullptr, nullptr, nullptr);
    if (!process.IsValid() || process.GetState() != eStateStopped) {
        std::cerr << "Failed to launch\n"; return 1;
    }

    // Enter REPL
    auto repl_err = dbg.RunREPL(mojo_lang, nullptr);
    if (repl_err.Fail())
        std::cerr << "REPL error: " << repl_err.GetCString() << "\n";

    process.Destroy();
    SBDebugger::Destroy(dbg);
    SBDebugger::Terminate();
    return 0;
}
