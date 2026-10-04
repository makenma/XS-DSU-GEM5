// Standalone C++17 test of the production helper, not a simulated workload.
// Build with C++17 and native/gem5 on the include path.
// Run with a writable, disposable output directory argument.
#include "BpuPhaseSpool.hh"
#include <filesystem>
#include <iostream>
#include <map>
#include <unordered_map>

namespace ps = gem5::branch_prediction::btb_pred::phase_storage;
namespace fs = std::filesystem;

void check(bool condition, const char *message)
{ if (!condition) throw std::runtime_error(message); }

template<class F> void rejects(F action)
{
    bool rejected = false;
    try { action(); } catch (const std::runtime_error &) { rejected = true; }
    check(rejected, "expected explicit rejection");
}

std::string bytes(const fs::path &path)
{
    std::ifstream input(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

void save(const fs::path &path, const std::string &data)
{
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.write(data.data(), data.size()); output.close();
    check(!output.fail(), "test file write failed");
}

struct Stats {
    uint64_t pc;
    int branchType, totalCount, mispredCount, dirWrongCount, targetWrongCount, noPredCount;
};
using BranchMap = std::map<std::pair<uint64_t, int>, Stats>;

// Independent old formatter, kept structurally identical to the pinned source.
std::string legacyBranch(int id, const BranchMap &data, size_t taken)
{
    std::ostringstream out;
    std::vector<std::pair<BranchMap::key_type, Stats>> records;
    for (const auto &record : data) records.push_back(record);
    int total = 0;
    for (const auto &record : records) total += record.second.mispredCount;
    out << id << ',' << int(data.size()) << ',' << int(taken) << ',' << total;
    std::sort(records.begin(), records.end(), [](const auto &a, const auto &b) {
        return a.second.mispredCount > b.second.mispredCount;
    });
    for (size_t i = 0; i < 5 && i < records.size(); ++i) {
        const auto &s = records[i].second;
        out << ',' << std::hex << s.pc << ',' << std::dec << s.branchType << ',' << s.mispredCount;
    }
    out << std::dec << std::endl;
    return out.str();
}

struct Entry {
    uint64_t pc = 0, target = 0;
    bool resolved = false, isCond = false, isIndirect = false;
    bool isDirect; // Intentionally indeterminate on invalid entries, like gem5.
    bool isCall = false, isReturn = false;
    int size = 4;
    bool valid = false, alwaysTaken = false;
    int ctr = 0;
    uint64_t tag = 0;
    int source = -1;
    int getType() { return isCond ? 1 : isIndirect ? 2 : 0; }
};
using BtbMap = std::unordered_map<uint64_t, std::pair<Entry, int>>;

std::string legacyBtb(int id, const BtbMap &data)
{
    std::ostringstream out;
    out << std::dec << id << ',' << data.size();
    std::vector<std::tuple<uint64_t, Entry, int>> entries;
    for (const auto &entry : data)
        entries.push_back(std::make_tuple(entry.first, entry.second.first, entry.second.second));
    std::sort(entries.begin(), entries.end(), [](const auto &a, const auto &b) {
        return std::get<2>(a) > std::get<2>(b);
    });
    for (size_t i = 0; i <= 1 && i < entries.size(); ++i) {
        Entry entry = std::get<1>(entries[i]);
        out << ',' << std::hex << std::get<0>(entries[i]) << ',' << std::dec << entry.getType();
    }
    out << std::endl;
    return out.str();
}

void formatterTests()
{
    BranchMap data;
    std::unordered_map<uint64_t, int> taken;
    check(ps::branchRecord(false, 0, data, taken).second == "0,0,0,0\n", "empty branch phase");
    for (int i = 0; i < 27; ++i) {
        const uint64_t pc = 0x1000 + (i / 2) * 4;
        data[{pc, i % 2}] = {pc, i % 2, 10, i % 3, 1, 2, 3};
        taken[pc] = i;
    }
    const auto record = ps::branchRecord(false, 7, data, taken);
    check(record.second == legacyBranch(7, data, taken.size()), "branch tie order/format changed");
    // Last low-ranking entry is in raw output even though CSV only contains top five.
    check(record.first.find("[4148,0,4148,0,10,2,1,2,3]") != std::string::npos,
          "complete raw branch fields missing");
    check(ps::branchRecord(true, 2147483648ULL, data, taken).second.rfind("2147483648,", 0) == 0,
          "phase ID truncated at int boundary");
    check(ps::fsqRecord(false, 0, {0, 0, 0}, 2, 100000).second == "0,0,0,0,inf\n",
          "zero-FSQ legacy inf changed");
    check(ps::fsqRecord(true, 2, {2, 3, 5}, 2, 100000).second == "2,2,3,5,10000\n",
          "FSQ bins/average changed");
    rejects([] { ps::fsqRecord(false, 0, {1}, 2, 100000); });

    BtbMap btb;
    for (int i = 0; i < 25; ++i) {
        Entry e; e.valid = true; e.pc = i + 0x100; e.target = 0x200 + i;
        e.isDirect = true; e.ctr = -2; e.tag = 0x123456789abcdefULL;
        e.source = 3; e.isCond = i % 2;
        btb.emplace(e.pc, std::make_pair(e, i % 3));
    }
    check(ps::btbRecord(7, btb).second == legacyBtb(7, btb), "BTB tied iteration order changed");
    BtbMap invalid;
    invalid[17].first.tag = 19;
    const auto invalidRaw = ps::btbRecord(0, invalid).first;
    check(invalidRaw.find(",null,") != std::string::npos, "invalid isDirect must be unknown");
    check(ps::nextCount(2147483647ULL) == 2147483648ULL, "commit counter int overflow");
    rejects([] { ps::nextCount(UINT64_MAX); });
}

void journalTests(const fs::path &dir)
{
    ps::Spool a, b;
    a.configure((dir / "cpu0.spool").string());
    b.configure((dir / "cpu1.spool").string());
    rejects([&] { a.append(1, "bad", "bad"); });
    constexpr uint64_t Count = 20000;
    for (uint64_t i = 0; i < Count; ++i) {
        const auto value = std::to_string(i);
        a.append(i, "raw" + value, "row" + value + "\n");
    }
    rejects([&] { a.append(Count - 1, "duplicate", "duplicate"); });
    b.append(0, "other CPU", "independent\n");
    for (int dump = 0; dump < 2; ++dump) {
        uint64_t visited = 0;
        a.replay([&](uint64_t id, const std::string &raw, const std::string &row) {
            check(id == visited++, "journal order changed");
            check(raw == "raw" + std::to_string(id), "journal raw changed");
            check(row == "row" + std::to_string(id) + "\n", "journal CSV changed");
        });
        check(visited == Count, "repeated dump lost/duplicated records");
        a.close();
    }
    a.append(Count, "after dump", "tail\n");
    uint64_t n = 0;
    a.replay([&](uint64_t, const std::string &, const std::string &) { ++n; });
    check(n == Count + 1 && b.size() == 1, "append after dump or per-core isolation failed");
    a.close(); b.close();

    ps::Spool corrupt;
    const auto path = dir / "corrupt.spool";
    corrupt.configure(path.string()); corrupt.append(0, "complete raw", "row\n"); corrupt.close();
    const auto original = bytes(path);
    std::vector<std::string> bad;
    auto changed = original; changed[0] = 'X'; bad.push_back(changed);
    bad.push_back(original.substr(0, 12)); // Truncated fixed header.
    bad.push_back(original.substr(0, original.size() - 1)); // Truncated body.
    changed = original; changed[40] ^= 1; bad.push_back(changed); // Checksum mismatch.
    changed = original; changed[16] = char(255); changed[23] = char(127); bad.push_back(changed);
    bad.push_back(original + "extra");
    for (const auto &damage : bad) {
        save(path, damage);
        rejects([&] { corrupt.replay([](uint64_t, const std::string &, const std::string &) {}); });
        corrupt.close();
    }
    save(path, original);
    corrupt.replay([](uint64_t, const std::string &, const std::string &) {});
    corrupt.close();
    rejects([&] { ps::Spool badPath; badPath.configure((dir / "missing" / "bad").string()); });

    const auto reportPath = dir / "legacy.csv";
    ps::Report report(reportPath.string(), (dir / "cpu.csv").string(), (dir / "dump-0.csv").string());
    *report.stream() << "phaseID\n0\n"; report.close(); report.close();
    check(bytes(reportPath) == bytes(dir / "cpu.csv") && bytes(reportPath) == bytes(dir / "dump-0.csv"),
          "legacy/latest/archive differ");
    rejects([&] { ps::Report badPath((dir / "missing" / "report").string()); });
}

int main(int argc, char **argv)
{
    if (argc == 2 && std::string(argv[1]) == "--write-error") {
        ps::Spool spool; spool.configure("/dev/full"); // Must terminate nonzero, never pass.
        return 0;
    }
    if (argc != 2) return 2;
    try {
        fs::create_directories(argv[1]);
        formatterTests(); journalTests(argv[1]);
        std::cout << "{\"schemaVersion\":1,\"status\":\"PASS\",\"cases\":{"
            "\"replayTwice\":true,\"replayAfterClose\":true,\"appendAfterReplay\":true,"
            "\"corruptionRejected\":true,\"truncationRejected\":true,"
            "\"duplicatePhaseRejected\":true,\"phaseCounterAcrossInt32\":true,"
            "\"legacyTieOrder\":true,\"completeRawRecords\":true,"
            "\"perCoreIsolation\":true,\"counterOverflowRejected\":true}}\n";
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n'; return 1;
    }
    return 0;
}
