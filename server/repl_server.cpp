#include <cstdio>
#include <lldb/API/SBDebugger.h>
#include <lldb/API/SBTarget.h>
#include <lldb/API/SBProcess.h>
#include <lldb/API/SBThread.h>
#include <lldb/API/SBExpressionOptions.h>
#include <lldb/API/SBValue.h>
#include <lldb/API/SBError.h>
#include <lldb/API/SBLanguageRuntime.h>

int main() {
    lldb::SBDebugger::Initialize();
    auto debugger = lldb::SBDebugger::Create(false);
    if (!debugger.IsValid()) {
        fprintf(stderr, "Failed to create SBDebugger\n");
        return 1;
    }
    fprintf(stderr, "SBDebugger created successfully\n");

    printf("{\"status\":\"ok\",\"message\":\"mojo-repl-server ready\"}\n");

    lldb::SBDebugger::Destroy(debugger);
    lldb::SBDebugger::Terminate();
    return 0;
}
