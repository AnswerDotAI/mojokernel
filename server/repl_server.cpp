// Mojo REPL server using EvaluateExpression with REPL mode enabled.
// This gives full var/let persistence without PTY or text parsing.
// JSON protocol on stdin/stdout.

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
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

// Internal header for EvaluateExpressionOptions::SetREPLEnabled
#include <lldb/Target/Target.h>

#include "json.hpp"

using namespace lldb;
using json = nlohmann::json;

[[noreturn]] static void die(const std::string &msg) {
    std::cerr << msg << "\n";
    std::cout << json{{"status", "error"}, {"message", msg}} << "\n" << std::flush;
    std::exit(1);
}

static std::string drain(SBProcess &proc, size_t (SBProcess::*fn)(char*, size_t) const) {
    std::string out;
    char buf[65536];
    size_t n;
    while ((n = (proc.*fn)(buf, sizeof(buf))) > 0) out.append(buf, n);
    return out;
}

static std::vector<std::string> split_lines(const std::string &s) {
    std::vector<std::string> lines;
    std::istringstream ss(s);
    for (std::string line; std::getline(ss, line);)
        if (!line.empty()) lines.push_back(line);
    return lines;
}

// Access internal EvaluateExpressionOptions from SBExpressionOptions.
// SBExpressionOptions has a single member: unique_ptr<EvaluateExpressionOptions>.
static lldb_private::EvaluateExpressionOptions& get_internal(SBExpressionOptions &opts) {
    return **reinterpret_cast<std::unique_ptr<lldb_private::EvaluateExpressionOptions>*>(&opts);
}

static json handle_execute(const std::string &code,
                           SBTarget &target,
                           SBProcess &process,
                           SBExpressionOptions &opts) {
    if (code.empty())
        return {{"status", "ok"}, {"stdout", ""}, {"stderr", ""}, {"value", ""}};

    auto result = target.EvaluateExpression(code.c_str(), opts);
    auto out = drain(process, &SBProcess::GetSTDOUT);
    auto serr = drain(process, &SBProcess::GetSTDERR);

    auto err = result.GetError();
    // Mojo EvaluateExpression always reports "unknown error" even on success.
    // Real errors have actual error messages.
    bool is_real_error = err.Fail() && err.GetCString() &&
                         std::string(err.GetCString()) != "unknown error";

    if (is_real_error) {
        std::string emsg = err.GetCString();
        auto tb = split_lines(emsg);
        return {{"status", "error"}, {"stdout", out}, {"stderr", serr},
                {"ename", "MojoError"},
                {"evalue", tb.empty() ? emsg : tb[0]},
                {"traceback", tb}};
    }

    std::string val;
    if (result.GetValue()) val = result.GetValue();
    return {{"status", "ok"}, {"stdout", out}, {"stderr", serr}, {"value", val}};
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: mojo-repl-server <modular-root>\n";
        return 1;
    }
    std::string root = argv[1];
    auto entry_point = root + "/lib/mojo-repl-entry-point";
    auto plugin_path = root + "/lib/libMojoLLDB.dylib";

    setenv("MODULAR_MAX_PACKAGE_ROOT", root.c_str(), 1);
    setenv("MODULAR_MOJO_MAX_PACKAGE_ROOT", root.c_str(), 1);
    setenv("MODULAR_MOJO_MAX_DRIVER_PATH", (root + "/bin/mojo").c_str(), 1);
    setenv("MODULAR_MOJO_MAX_IMPORT_PATH", (root + "/lib/mojo").c_str(), 1);

    SBDebugger::Initialize();
    auto debugger = SBDebugger::Create(false);
    if (!debugger.IsValid()) die("Failed to create SBDebugger");

    debugger.SetScriptLanguage(eScriptLanguageNone);
    debugger.SetAsync(false);

    auto ci = debugger.GetCommandInterpreter();
    SBCommandReturnObject cmd_result;
    ci.HandleCommand(("plugin load " + plugin_path).c_str(), cmd_result);
    if (!cmd_result.Succeeded()) {
        std::string msg = "Failed to load MojoLLDB plugin";
        if (cmd_result.GetError()) msg += std::string(": ") + cmd_result.GetError();
        die(msg);
    }
    std::cerr << "Loaded MojoLLDB plugin\n";

    auto mojo_lang = SBLanguageRuntime::GetLanguageTypeFromString("mojo");
    if (mojo_lang == eLanguageTypeUnknown)
        die("Mojo language not recognized - is libMojoLLDB loaded correctly?");
    debugger.SetREPLLanguage(mojo_lang);
    std::cerr << "Mojo language type: " << static_cast<int>(mojo_lang) << "\n";

    SBError target_err;
    auto target = debugger.CreateTarget(entry_point.c_str(), "", "", true, target_err);
    if (!target.IsValid()) {
        std::string msg = "Failed to create target: " + entry_point;
        if (target_err.Fail()) msg += std::string(": ") + target_err.GetCString();
        die(msg);
    }

    auto bp = target.BreakpointCreateByName("mojo_repl_main");
    if (!bp.IsValid()) die("Failed to create breakpoint at mojo_repl_main");
    std::cerr << "Breakpoint set, " << bp.GetNumLocations() << " location(s)\n";

    auto process = target.LaunchSimple(nullptr, nullptr, nullptr);
    if (!process.IsValid()) die("Failed to launch target process");
    if (process.GetState() != eStateStopped)
        die("Process not stopped after launch (state=" + std::to_string(process.GetState()) + ")");
    std::cerr << "Process launched and stopped at breakpoint\n";

    drain(process, &SBProcess::GetSTDOUT);
    drain(process, &SBProcess::GetSTDERR);

    // Set up expression options with REPL mode for var persistence
    SBExpressionOptions opts;
    opts.SetLanguage(mojo_lang);
    opts.SetUnwindOnError(false);
    opts.SetGenerateDebugInfo(true);
    opts.SetTimeoutInMicroSeconds(0);
    get_internal(opts).SetREPLEnabled(true);
    std::cerr << "REPL mode enabled\n";

    std::cout << json{{"status", "ready"}} << "\n" << std::flush;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;

        json req;
        try { req = json::parse(line); }
        catch (const json::parse_error &e) {
            std::cout << json{{"id", 0}, {"status", "error"},
                {"ename", "ProtocolError"}, {"evalue", e.what()}, {"traceback", json::array()}}
                << "\n" << std::flush;
            continue;
        }

        auto type = req.value("type", "");
        auto id = req.value("id", 0);

        json resp;
        if (type == "execute") {
            resp = handle_execute(req.value("code", ""), target, process, opts);
        } else if (type == "complete") {
            resp = {{"status", "ok"}, {"completions", json::array()}};
        } else if (type == "interrupt") {
            process.SendAsyncInterrupt();
            resp = {{"status", "ok"}};
        } else if (type == "shutdown") {
            std::cout << json{{"id", id}, {"status", "ok"}} << "\n" << std::flush;
            break;
        } else {
            resp = {{"status", "error"}, {"ename", "ProtocolError"},
                    {"evalue", "unknown request type: " + type}, {"traceback", json::array()}};
        }

        resp["id"] = id;
        std::cout << resp << "\n" << std::flush;
    }

    process.Destroy();
    SBDebugger::Destroy(debugger);
    SBDebugger::Terminate();
    return 0;
}
