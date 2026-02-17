// PTY-based Mojo REPL server. Runs RunREPL() in a background thread with
// I/O redirected through a PTY pair. Provides JSON protocol on stdin/stdout.
// This gives full var/let persistence (unlike HandleCommand approach).

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <atomic>
#include <regex>
#include <chrono>

#include <unistd.h>
#include <util.h>
#include <poll.h>
#include <termios.h>
#include <signal.h>
#include <fcntl.h>
#include <sys/ioctl.h>

#include <lldb/API/SBDebugger.h>
#include <lldb/API/SBTarget.h>
#include <lldb/API/SBProcess.h>
#include <lldb/API/SBBreakpoint.h>
#include <lldb/API/SBLanguageRuntime.h>
#include <lldb/API/SBCommandInterpreter.h>
#include <lldb/API/SBCommandReturnObject.h>
#include <lldb/API/SBFile.h>
#include <lldb/API/SBError.h>

#include "json.hpp"

using namespace lldb;
using json = nlohmann::json;

// --- ANSI/prompt patterns ---

static const std::regex ANSI_RE(R"(\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?[0-9;]*[A-Za-z])");
static const std::regex PROMPT_PAT(R"(\n\s*\d+>\s)");
static const std::regex PROMPT_LINE_RE(R"(^\s*\d+[>.]\s)");
static const std::regex ECHO_RE(R"(\s+\d+[>]\s)");
static const std::regex ERROR_RE(R"(error:)", std::regex::icase);

static std::string strip_ansi(const std::string &s) {
    return std::regex_replace(s, ANSI_RE, "");
}

static std::string replace_cr(const std::string &s) {
    std::string r;
    r.reserve(s.size());
    for (char c : s) if (c != '\r') r += c;
    return r;
}

// --- PTY helpers ---

static std::string read_pty(int fd, int timeout_ms) {
    std::string buf;
    char chunk[65536];
    struct pollfd pfd = {fd, POLLIN, 0};
    while (true) {
        int ret = poll(&pfd, 1, timeout_ms);
        if (ret <= 0) break;
        if (pfd.revents & POLLIN) {
            ssize_t n = read(fd, chunk, sizeof(chunk));
            if (n <= 0) break;
            buf.append(chunk, n);
        }
        if (pfd.revents & (POLLERR | POLLHUP)) break;
        timeout_ms = 100; // after first chunk, short timeout for more data
    }
    return buf;
}

static std::string read_until_prompt(int fd, int timeout_s = 30) {
    std::string buf;
    char chunk[65536];
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout_s);
    auto prompt_time = std::chrono::steady_clock::time_point{};
    struct pollfd pfd = {fd, POLLIN, 0};

    while (std::chrono::steady_clock::now() < deadline) {
        int remaining_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now()).count();
        if (remaining_ms <= 0) break;

        pfd.revents = 0;
        int ret = poll(&pfd, 1, std::min(remaining_ms, 1000));
        if (ret > 0 && (pfd.revents & POLLIN)) {
            ssize_t n = read(fd, chunk, sizeof(chunk));
            if (n > 0) {
                buf.append(chunk, n);
                auto clean = strip_ansi(buf);
                if (prompt_time == std::chrono::steady_clock::time_point{} &&
                    std::regex_search(clean, PROMPT_PAT)) {
                    prompt_time = std::chrono::steady_clock::now();
                }
            }
            if (n <= 0 && (pfd.revents & POLLHUP)) break;
        } else if (ret == 0) {
            // timeout on poll
            if (prompt_time != std::chrono::steady_clock::time_point{}) {
                auto elapsed = std::chrono::steady_clock::now() - prompt_time;
                if (elapsed > std::chrono::milliseconds(300)) return strip_ansi(buf);
            }
        }
        if (pfd.revents & (POLLERR | POLLHUP)) break;
    }

    if (prompt_time != std::chrono::steady_clock::time_point{})
        return strip_ansi(buf);
    return ""; // timeout
}

// --- Output parser (mirrors pexpect_engine.py logic) ---

static bool is_prompt_line(const std::string &line) {
    if (std::regex_search(line, PROMPT_LINE_RE)) return true;
    if (std::regex_search(line, ECHO_RE)) return true;
    return false;
}

static json parse_output(const std::string &raw) {
    auto clean = replace_cr(raw);
    std::istringstream ss(clean);
    std::string line;
    std::vector<std::string> output, errors;
    bool in_error = false;

    while (std::getline(ss, line)) {
        if (line.empty()) continue;
        // Strip prompt prefix if present
        std::string stripped = line;
        if (std::regex_search(line, PROMPT_LINE_RE))
            stripped = std::regex_replace(line, std::regex(R"(^\s*\d+[>.]\s*)"), "");
        if (std::regex_search(stripped, ERROR_RE)) in_error = true;
        if (in_error) {
            auto s = stripped;
            // trim
            s.erase(0, s.find_first_not_of(" \t"));
            s.erase(s.find_last_not_of(" \t") + 1);
            if (!s.empty() && s != "(null)") errors.push_back(s);
            continue;
        }
        if (is_prompt_line(line)) continue;
        output.push_back(line);
    }

    std::string stdout_str;
    for (auto &l : output) stdout_str += l + "\n";

    if (!errors.empty()) {
        std::string evalue = errors[0];
        if (evalue.substr(0, 7) == "[User] ") evalue = evalue.substr(7);
        return {{"status", "error"}, {"stdout", stdout_str},
                {"stderr", ""}, {"ename", "MojoError"},
                {"evalue", evalue}, {"traceback", errors}};
    }
    return {{"status", "ok"}, {"stdout", stdout_str}, {"stderr", ""}, {"value", ""}};
}

// --- Main ---

[[noreturn]] static void die(const std::string &msg) {
    std::cerr << msg << "\n";
    std::cout << json{{"status", "error"}, {"message", msg}} << "\n" << std::flush;
    std::exit(1);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: mojo-repl-server-pty <modular-root>\n";
        return 1;
    }
    std::string root = argv[1];
    auto entry_point = root + "/lib/mojo-repl-entry-point";
    auto plugin_path = root + "/lib/libMojoLLDB.dylib";

    setenv("MODULAR_MAX_PACKAGE_ROOT", root.c_str(), 1);
    setenv("MODULAR_MOJO_MAX_PACKAGE_ROOT", root.c_str(), 1);
    setenv("MODULAR_MOJO_MAX_DRIVER_PATH", (root + "/bin/mojo").c_str(), 1);
    setenv("MODULAR_MOJO_MAX_IMPORT_PATH", (root + "/lib/mojo").c_str(), 1);

    // Create PTY pair
    int master_fd, slave_fd;
    if (openpty(&master_fd, &slave_fd, nullptr, nullptr, nullptr) < 0)
        die("openpty() failed");

    // Set PTY to raw-ish mode (no local echo, no line buffering)
    struct termios tios;
    tcgetattr(slave_fd, &tios);
    tios.c_lflag &= ~(ECHO | ECHOE | ECHOK | ECHONL | ICANON);
    tios.c_iflag &= ~(ICRNL | INLCR | IGNCR);
    tios.c_oflag &= ~OPOST;
    tios.c_cc[VMIN] = 1;
    tios.c_cc[VTIME] = 0;
    tcsetattr(slave_fd, TCSANOW, &tios);

    // Set window size (prevents editline issues)
    struct winsize ws = {80, 120, 0, 0};
    ioctl(slave_fd, TIOCSWINSZ, &ws);

    // Initialize LLDB
    SBDebugger::Initialize();
    auto debugger = SBDebugger::Create(true);
    if (!debugger.IsValid()) die("Failed to create SBDebugger");

    // Redirect debugger I/O to PTY slave
    FILE *slave_in = fdopen(dup(slave_fd), "r");
    FILE *slave_out = fdopen(dup(slave_fd), "w");
    if (!slave_in || !slave_out) die("fdopen failed");
    debugger.SetInputFileHandle(slave_in, true);
    debugger.SetOutputFileHandle(slave_out, true);
    debugger.SetErrorFileHandle(fdopen(dup(slave_fd), "w"), true);

    auto ci = debugger.GetCommandInterpreter();
    SBCommandReturnObject ret;

    // Apply REPL settings
    ci.HandleCommand("settings set show-statusline false", ret);
    ci.HandleCommand("settings set show-progress false", ret);
    ci.HandleCommand("settings set use-color false", ret);
    ci.HandleCommand("settings set show-autosuggestion false", ret);
    ci.HandleCommand("settings set auto-indent false", ret);
    ci.HandleCommand("settings set stop-line-count-before 0", ret);
    ci.HandleCommand("settings set stop-line-count-after 0", ret);

    // Load MojoLLDB plugin
    ci.HandleCommand(("plugin load " + plugin_path).c_str(), ret);
    if (!ret.Succeeded()) die("Failed to load MojoLLDB plugin");
    std::cerr << "Loaded MojoLLDB plugin\n";

    auto mojo_lang = SBLanguageRuntime::GetLanguageTypeFromString("mojo");
    if (mojo_lang == eLanguageTypeUnknown) die("Mojo language not recognized");
    debugger.SetREPLLanguage(mojo_lang);
    std::cerr << "Mojo language type: " << static_cast<int>(mojo_lang) << "\n";

    // Create target + breakpoint + launch
    SBError target_err;
    auto target = debugger.CreateTarget(entry_point.c_str(), "", "", true, target_err);
    if (!target.IsValid()) die("Failed to create target");

    auto bp = target.BreakpointCreateByName("mojo_repl_main");
    if (!bp.IsValid()) die("Failed to create breakpoint");
    std::cerr << "Breakpoint set, " << bp.GetNumLocations() << " location(s)\n";

    auto process = target.LaunchSimple(nullptr, nullptr, nullptr);
    if (!process.IsValid()) die("Failed to launch process");
    if (process.GetState() != eStateStopped) die("Process not stopped at breakpoint");
    std::cerr << "Process launched and stopped at breakpoint\n";

    // Drain any startup output from PTY
    read_pty(master_fd, 500);

    // Run REPL in background thread
    std::atomic<bool> repl_running{true};
    std::thread repl_thread([&]() {
        auto err = debugger.RunREPL(mojo_lang, nullptr);
        if (err.Fail())
            std::cerr << "RunREPL error: " << (err.GetCString() ? err.GetCString() : "unknown") << "\n";
        repl_running = false;
    });
    repl_thread.detach();

    // Wait for initial REPL prompt
    std::cerr << "Waiting for REPL prompt...\n";
    auto initial = read_until_prompt(master_fd, 30);
    if (initial.empty()) die("Timed out waiting for REPL prompt");
    std::cerr << "REPL ready\n";

    // Signal readiness
    std::cout << json{{"status", "ready"}} << "\n" << std::flush;

    // Main JSON protocol loop
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        if (!repl_running) {
            std::cout << json{{"id", 0}, {"status", "error"},
                {"ename", "REPLError"}, {"evalue", "REPL process died"},
                {"traceback", json::array({"REPL process terminated unexpectedly"})}}
                << "\n" << std::flush;
            break;
        }

        json req;
        try { req = json::parse(line); }
        catch (const json::parse_error &e) {
            std::cout << json{{"id", 0}, {"status", "error"},
                {"ename", "ProtocolError"}, {"evalue", e.what()},
                {"traceback", json::array()}} << "\n" << std::flush;
            continue;
        }

        auto type = req.value("type", "");
        auto id = req.value("id", 0);
        json resp;

        if (type == "execute") {
            auto code = req.value("code", "");
            if (code.empty()) {
                resp = {{"status", "ok"}, {"stdout", ""}, {"stderr", ""}, {"value", ""}};
            } else {
                // Drain any stale PTY data
                read_pty(master_fd, 50);

                // Send each line to REPL, then blank line to submit
                std::istringstream code_ss(code);
                std::string code_line;
                while (std::getline(code_ss, code_line)) {
                    code_line += "\n";
                    write(master_fd, code_line.c_str(), code_line.size());
                    usleep(5000); // 5ms between lines for editline
                }
                // Blank line to submit
                write(master_fd, "\n", 1);

                // Read until next prompt
                auto raw = read_until_prompt(master_fd, 30);
                if (raw.empty()) {
                    resp = {{"status", "error"}, {"stdout", ""}, {"stderr", ""},
                            {"ename", "TimeoutError"}, {"evalue", "Expression timed out"},
                            {"traceback", json::array({"Expression evaluation timed out"})}};
                } else {
                    resp = parse_output(raw);
                }
            }
        } else if (type == "complete") {
            resp = {{"status", "ok"}, {"completions", json::array()}};
        } else if (type == "interrupt") {
            // Send Ctrl-C through PTY
            char ctrl_c = 3;
            write(master_fd, &ctrl_c, 1);
            read_pty(master_fd, 500);
            resp = {{"status", "ok"}};
        } else if (type == "shutdown") {
            // Send :quit to REPL
            write(master_fd, ":quit\n", 6);
            std::cout << json{{"id", id}, {"status", "ok"}} << "\n" << std::flush;
            break;
        } else {
            resp = {{"status", "error"}, {"ename", "ProtocolError"},
                    {"evalue", "unknown request type: " + type},
                    {"traceback", json::array()}};
        }

        resp["id"] = id;
        std::cout << resp << "\n" << std::flush;
    }

    close(master_fd);
    close(slave_fd);
    SBDebugger::Terminate();
    return 0;
}
