// Linux/aarch64 userspace probe for the audited BM1684X timer-low register.
// This is an ioctl/MMIO completion measurement, NOT PCIe TLP wire latency.
// Only BMDEV_GET_REG is issued. No register write, timer reset, SDK init,
// PCI configuration write, BAR mapping, or arbitrary address is supported.
// Driver reference: bm_uapi.h / bm_fops.c / bm_io.c / bm_timer.c, SDK 0.5.1.
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <sched.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace {
constexpr uint32_t kTimerLow = 0x50010184u;
// The driver encodes sizeof(unsigned long), not sizeof(struct bm_reg).
constexpr unsigned long kGetReg = _IOWR('p', 0x3d, unsigned long);
struct BmReg { int reg_addr; int reg_value; };
static_assert(sizeof(int) == 4 && sizeof(unsigned long) == 8,
              "Use the native 64-bit Linux ABI matching the audited driver");
static_assert(sizeof(BmReg) == 8 && _IOC_SIZE(kGetReg) == 8,
              "Unexpected BMDEV_GET_REG ABI");
volatile sig_atomic_t stopped = 0;
void stop_handler(int) { stopped = 1; }

std::string quote(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') out << '\\' << char(c);
        else if (c < 0x20) out << "\\u" << std::hex << std::setw(4)
                              << std::setfill('0') << unsigned(c) << std::dec;
        else out << char(c);
    }
    out << '"';
    return out.str();
}
uint64_t now_ns() {
    timespec ts{};
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0)
        throw std::runtime_error("clock_gettime: " + std::string(strerror(errno)));
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}
void check_stop() {
    if (stopped) throw std::runtime_error("interrupted; partial samples retained");
}
void sleep_gap() {
    // A minimum requested 1 ms idle gap AFTER the preceding ioctl, not a
    // guaranteed 1 kHz schedule. The recorded timestamps show actual spacing.
    timespec left{0, 1000000};
    while (nanosleep(&left, &left) != 0) {
        if (errno != EINTR)
            throw std::runtime_error("nanosleep: " + std::string(strerror(errno)));
        check_stop();
    }
    check_stop();
}
int parse_uint(const std::string& text, int maximum) {
    if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("Expected a nonnegative integer: " + text);
    size_t end = 0;
    const unsigned long value = std::stoul(text, &end);
    if (end != text.size() || value > static_cast<unsigned long>(maximum))
        throw std::runtime_error("Argument outside permitted range: " + text);
    return static_cast<int>(value);
}
struct Fd {
    int value = -1;
    ~Fd() { if (value >= 0) close(value); }
};
struct Affinity {
    cpu_set_t original{};
    bool changed = false;
    void apply(int cpu) {
        if (sched_getaffinity(0, sizeof(original), &original) != 0)
            throw std::runtime_error("sched_getaffinity: " + std::string(strerror(errno)));
        if (cpu < 0) return;
        if (!CPU_ISSET(cpu, &original))
            throw std::runtime_error("Requested CPU is outside this process's allowed affinity");
        cpu_set_t selected;
        CPU_ZERO(&selected);
        CPU_SET(cpu, &selected);
        if (sched_setaffinity(0, sizeof(selected), &selected) != 0)
            throw std::runtime_error("sched_setaffinity: " + std::string(strerror(errno)));
        changed = true;
    }
    ~Affinity() {
        if (changed) sched_setaffinity(0, sizeof(original), &original);
    }
};
struct Sample {
    uint64_t start_ns, end_ns;
    uint32_t value;
    int cpu_before, cpu_after, result, error;
};
struct Run {
    std::string mode;
    unsigned expected = 0;
    bool complete = false;
    std::vector<Sample> samples;
};
std::string stats(std::vector<uint64_t> values) {
    if (values.empty()) return "null";
    std::sort(values.begin(), values.end());
    const auto percentile = [&](unsigned p) {
        // Nearest-rank percentiles, with ranks starting at one.
        const size_t rank = (values.size() * p + 99) / 100;
        return values[rank - 1];
    };
    const long double sum = std::accumulate(values.begin(), values.end(), 0.0L);
    std::ostringstream out;
    out << std::setprecision(12) << "{\"count\":" << values.size()
        << ",\"min_ns\":" << values.front() << ",\"mean_ns\":"
        << sum / values.size() << ",\"p50_ns\":" << percentile(50)
        << ",\"p95_ns\":" << percentile(95) << ",\"p99_ns\":" << percentile(99)
        << ",\"max_ns\":" << values.back() << '}';
    return out.str();
}
void measure(int fd, Run& run) {
    run.samples.reserve(run.expected);
    for (unsigned i = 0; i < run.expected; ++i) {
        check_stop();
        if (run.mode == "spaced" && i != 0) sleep_gap();
        BmReg reg{static_cast<int>(kTimerLow), 0};
        const int cpu_before = sched_getcpu();
        const uint64_t start = now_ns();
        const int rc = ioctl(fd, kGetReg, &reg);
        const int saved_errno = rc < 0 ? errno : 0;
        const uint64_t end = now_ns();
        const int cpu_after = sched_getcpu();
        run.samples.push_back({start, end, static_cast<uint32_t>(reg.reg_value),
                               cpu_before, cpu_after, rc, saved_errno});
        if (rc != 0)
            throw std::runtime_error("BMDEV_GET_REG returned " + std::to_string(rc)
                                     + ": " + strerror(saved_errno));
        if (end < start) throw std::runtime_error("CLOCK_MONOTONIC_RAW went backwards");
    }
    const auto first = run.samples.front().value;
    const bool changed = std::any_of(run.samples.begin(), run.samples.end(),
                                    [first](const Sample& s) { return s.value != first; });
    if (!changed) throw std::runtime_error("Timer value never changed; register read not verified");
    // Do not require strict monotonicity: 32-bit timer-low can wrap, and
    // counter granularity can yield repeated samples.
    run.complete = true;
}
std::string run_json(const Run& run) {
    std::vector<uint64_t> durations;
    unsigned transitions = 0, decreases = 0, migrated = 0;
    bool have_previous = false;
    uint32_t previous = 0;
    for (const auto& s : run.samples) {
        if (s.result != 0) continue;
        if (s.end_ns >= s.start_ns) durations.push_back(s.end_ns - s.start_ns);
        if (have_previous) {
            transitions += (s.value != previous);
            decreases += (s.value < previous);
        }
        have_previous = true;
        previous = s.value;
        migrated += (s.cpu_before != s.cpu_after);
    }
    std::ostringstream out;
    out << "{\"mode\":" << quote(run.mode) << ",\"expected_samples\":" << run.expected
        << ",\"requested_idle_gap_ns\":" << (run.mode == "spaced" ? 1000000 : 0)
        << ",\"complete\":" << (run.complete ? "true" : "false")
        << ",\"timer_value_changed\":" << (transitions ? "true" : "false")
        << ",\"value_transitions\":" << transitions
        << ",\"value_decreases_possible_wrap\":" << decreases
        << ",\"samples_with_cpu_migration\":" << migrated
        << ",\"ioctl_duration\":" << stats(durations)
        << ",\"sample_columns\":[\"start_raw_ns\",\"end_raw_ns\",\"duration_ns\","
           "\"timer_low_u32\",\"cpu_before\",\"cpu_after\",\"ioctl_result\",\"errno\"]"
        << ",\"samples\":[";
    for (size_t i = 0; i < run.samples.size(); ++i) {
        const auto& s = run.samples[i];
        if (i) out << ',';
        out << '[' << s.start_ns << ',' << s.end_ns << ','
            << (s.end_ns >= s.start_ns ? s.end_ns - s.start_ns : 0) << ',' << s.value
            << ',' << s.cpu_before << ',' << s.cpu_after << ',' << s.result << ',' << s.error << ']';
    }
    out << "]}";
    return out.str();
}
void write_new_file(const std::string& path, const std::string& content) {
    Fd output;
    output.value = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
    if (output.value < 0)
        throw std::runtime_error("Cannot create a new output file: " + std::string(strerror(errno)));
    size_t offset = 0;
    while (offset < content.size()) {
        const ssize_t n = write(output.value, content.data() + offset, content.size() - offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) throw std::runtime_error("Output write failed: " + std::string(strerror(errno)));
        offset += static_cast<size_t>(n);
    }
}
} // namespace

int main(int argc, char** argv) {
    int device = -1, cpu = -1, exit_code = 0;
    std::string mode = "both", output_path, error, device_path;
    std::vector<Run> runs;
    std::vector<uint64_t> clock_baseline;
    utsname host{};
    timespec resolution{};
    Affinity affinity;
    std::signal(SIGINT, stop_handler);
    std::signal(SIGTERM, stop_handler);
    try {
        for (int i = 1; i < argc; ++i) {
            const std::string key = argv[i];
            if (key == "--help") {
                std::cout << "Usage: mmio_read_probe --device INDEX [--mode continuous|spaced|both] "
                             "[--cpu CPU] [--output NEW_FILE.json]\n"
                             "continuous: 10000 reads; spaced: 1000 reads with a requested 1ms idle gap.\n"
                             "Fixed read-only timer address 0x50010184; no register writes.\n"
                             "JSON includes every raw sample. Output defaults to stdout; existing files are not overwritten.\n";
                return 0;
            }
            if (i + 1 >= argc) throw std::runtime_error("Missing value for " + key);
            const std::string value = argv[++i];
            if (key == "--device") device = parse_uint(value, 255);
            else if (key == "--cpu") cpu = parse_uint(value, CPU_SETSIZE - 1);
            else if (key == "--mode") mode = value;
            else if (key == "--output") output_path = value;
            else throw std::runtime_error("Unknown argument: " + key);
        }
        if (device < 0) throw std::runtime_error("--device is required");
        if (mode != "continuous" && mode != "spaced" && mode != "both")
            throw std::runtime_error("--mode must be continuous, spaced, or both");
        device_path = "/dev/bm-sophon" + std::to_string(device);
        if (uname(&host) != 0) throw std::runtime_error("uname failed");
        if (clock_getres(CLOCK_MONOTONIC_RAW, &resolution) != 0)
            throw std::runtime_error("clock_getres failed");
        affinity.apply(cpu);
        // Check output collision before doing any device reads. Creation later
        // still uses O_EXCL to guard against a race and to retain prior results.
        if (!output_path.empty()) {
            struct stat st{};
            if (lstat(output_path.c_str(), &st) == 0)
                throw std::runtime_error("Output path already exists; refusing to overwrite");
            if (errno != ENOENT) throw std::runtime_error("Cannot inspect output path");
        }
        Fd input;
        input.value = open(device_path.c_str(), O_RDONLY | O_CLOEXEC);
        if (input.value < 0)
            throw std::runtime_error("Device open failed: " + std::string(strerror(errno)));
        clock_baseline.reserve(10000);
        for (unsigned i = 0; i < 10000; ++i) {
            check_stop();
            const uint64_t a = now_ns(), b = now_ns();
            if (b < a) throw std::runtime_error("Clock baseline went backwards");
            clock_baseline.push_back(b - a);
        }
        runs.reserve(2);
        for (const std::string& selected : {std::string("continuous"), std::string("spaced")}) {
            if (mode != "both" && mode != selected) continue;
            runs.push_back(Run{});
            auto& run = runs.back();
            run.mode = selected;
            run.expected = selected == "continuous" ? 10000 : 1000;
            measure(input.value, run);
        }
    } catch (const std::exception& e) {
        error = e.what();
        exit_code = 1;
    }
    std::ostringstream report;
    report << "{\"schema\":1,\"probe\":\"bm1684x_timer_mmio_read\",\"success\":"
           << (exit_code == 0 ? "true" : "false") << ",\"error\":" << quote(error)
           << ",\"measurement_scope\":\"Userspace ioctl elapsed time including kernel software and MMIO read completion; not single-TLP wire latency\""
           << ",\"clock\":\"CLOCK_MONOTONIC_RAW\",\"clock_resolution_ns\":"
           << uint64_t(resolution.tv_sec) * 1000000000ull + uint64_t(resolution.tv_nsec)
           << ",\"timer_address\":\"0x50010184\",\"ioctl_request_decimal\":" << kGetReg
           << ",\"abi_unsigned_long_bytes\":" << sizeof(unsigned long)
           << ",\"register_writes_issued\":0,\"device_index\":" << device
           << ",\"device_path\":" << quote(device_path) << ",\"requested_cpu\":" << cpu
           << ",\"kernel\":" << quote(host.release) << ",\"machine\":" << quote(host.machine)
           << ",\"pid\":" << getpid() << ",\"allowed_cpus_before\":[";
    bool first_cpu = true;
    for (int i = 0; i < CPU_SETSIZE; ++i) if (CPU_ISSET(i, &affinity.original)) {
        if (!first_cpu) report << ',';
        report << i;
        first_cpu = false;
    }
    report << "],\"clock_pair_baseline\":" << stats(clock_baseline)
           << ",\"baseline_subtracted\":false,\"clock_pair_baseline_samples_ns\":[";
    for (size_t i = 0; i < clock_baseline.size(); ++i) {
        if (i) report << ',';
        report << clock_baseline[i];
    }
    report << "],\"runs\":[";
    for (size_t i = 0; i < runs.size(); ++i) {
        if (i) report << ',';
        report << run_json(runs[i]);
    }
    report << "]}\n";
    try {
        if (output_path.empty()) std::cout << report.str();
        else write_new_file(output_path, report.str());
        if (!std::cout) throw std::runtime_error("stdout write failed");
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        // Preserve partial measurement/error information even if the requested
        // file cannot be created. stdout can be captured independently.
        if (!output_path.empty()) std::cout << report.str();
        return 2;
    }
    if (!error.empty()) std::cerr << error << '\n';
    return exit_code;
}
