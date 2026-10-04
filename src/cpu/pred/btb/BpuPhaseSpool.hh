// Lossless host-only phase storage. No simulated events, clocks or predictors.
#ifndef __CPU_PRED_BTB_BPU_PHASE_SPOOL_HH__
#define __CPU_PRED_BTB_BPU_PHASE_SPOOL_HH__

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace gem5 { namespace branch_prediction { namespace btb_pred {
namespace phase_storage {

inline constexpr std::array<const char *, 5> FileNames = {{
    "topMispredictByPhase.csv", "topMispredictBySubPhase.csv",
    "fsqEntryCommittedInstNumDistsByPhase.csv",
    "fsqEntryFetchedInstNumDistsByPhase.csv", "btbEntriesByPhase.csv"
}};

inline void require(bool ok, const std::string &what)
{
    if (!ok) throw std::runtime_error("BPU phase storage: " + what);
}

inline uint64_t nextCount(uint64_t value)
{
    require(value != std::numeric_limits<uint64_t>::max(), "counter overflow");
    return value + 1;
}

inline double perMille(uint64_t mispredictions, uint64_t total)
{
    // Keep the old integer-product -> double-conversion -> division order.
    // The 128-bit product covers the complete uint64_t count domain.
    const unsigned __int128 product = static_cast<unsigned __int128>(mispredictions) * 1000;
    return total > 0 ? static_cast<double>(product) / total : 0;
}

// Both outputs are explicit checked host files. The legacy name is intentionally
// still overwritten by each BPU exit callback; the instance name preserves all.
class Report
{
    std::string path, mirror, archive;
    std::ofstream output;
    bool closed = false;
  public:
    Report(const std::string &p, const std::string &m = "", const std::string &a = "") :
        path(p), mirror(m), archive(a),
        output(p, std::ios::binary | std::ios::trunc)
    { require(output.good(), "open report " + path); }
    std::ostream *stream() { return &output; }
    void close()
    {
        if (closed) return;
        output.flush();
        require(output.good(), "flush report " + path);
        output.close();
        require(!output.fail(), "close report " + path);
        closed = true;
        for (const auto &destination : {mirror, archive}) {
            if (destination.empty()) continue;
            std::ifstream input(path, std::ios::binary);
            require(input.good(), "open report mirror source " + path);
            Report target(destination);
            std::array<char, 8192> buffer{};
            while (input.read(buffer.data(), buffer.size()) || input.gcount())
                target.stream()->write(buffer.data(), input.gcount());
            require(input.eof() && !input.bad(), "read report " + path);
            input.clear();
            input.close();
            require(!input.fail(), "close report reader " + path);
            target.close();
        }
    }
    ~Report() noexcept
    {
        if (!closed) {
            try { close(); }
            catch (const std::exception &e) {
                std::fprintf(stderr, "%s\n", e.what()); std::abort();
            }
        }
    }
};

// Five journals replace seven growing vectors (branch+takens share a frame).
// Format: magic BPUJ0001; repeated LE u64 phase, rawBytes, rowBytes, FNV64,
// followed by the exact UTF-8 JSON record and original formatted CSV row.
// No C++ object representation/padding/pointers or phase-sized index is saved.
class Spool
{
    static constexpr uint64_t MaxFrameBytes = 128ULL * 1024 * 1024;
    std::string path;
    std::ofstream output;
    uint64_t records = 0;
    bool initialized = false;

    static void hashByte(uint64_t &hash, unsigned char value)
    { hash = (hash ^ value) * 1099511628211ULL; }
    static void hashWord(uint64_t &hash, uint64_t value)
    { for (int i = 0; i < 8; ++i) hashByte(hash, (value >> (i * 8)) & 255); }
    static uint64_t checksum(uint64_t id, const std::string &raw,
                             const std::string &row)
    {
        uint64_t hash = 14695981039346656037ULL;
        hashWord(hash, id); hashWord(hash, raw.size()); hashWord(hash, row.size());
        for (unsigned char value : raw) hashByte(hash, value);
        for (unsigned char value : row) hashByte(hash, value);
        return hash;
    }
    static void writeWord(std::ostream &out, uint64_t value)
    { for (int i = 0; i < 8; ++i) out.put(char((value >> (i * 8)) & 255)); }
    static uint64_t readWord(std::istream &in)
    {
        uint64_t value = 0;
        for (int i = 0; i < 8; ++i) {
            const auto byte = in.get();
            require(byte != std::char_traits<char>::eof(), "truncated frame header");
            value |= uint64_t(static_cast<unsigned char>(byte)) << (i * 8);
        }
        return value;
    }
    void open()
    {
        require(!path.empty(), "unconfigured journal");
        if (output.is_open()) return;
        output.clear();
        output.open(path, std::ios::binary |
                    (initialized ? std::ios::app : std::ios::trunc));
        require(output.good(), "open journal " + path);
        if (!initialized) {
            output.write("BPUJ0001", 8);
            output.flush();
            require(output.good(), "write journal header " + path);
            initialized = true;
        }
    }
  public:
    void configure(const std::string &p)
    {
        require(path.empty(), "journal configured twice");
        path = p; open();
    }
    uint64_t size() const { return records; }
    void append(uint64_t id, const std::string &raw, const std::string &row)
    {
        require(id == records, "noncontiguous phase in " + path);
        require(raw.size() <= MaxFrameBytes && row.size() <= MaxFrameBytes,
                "oversized phase in " + path);
        const uint64_t after = nextCount(records);
        open();
        writeWord(output, id); writeWord(output, raw.size());
        writeWord(output, row.size()); writeWord(output, checksum(id, raw, row));
        output.write(raw.data(), raw.size()); output.write(row.data(), row.size());
        output.flush();
        require(output.good(), "write/flush phase " + path);
        records = after;
    }
    template<class Visitor> void replay(Visitor visitor)
    {
        open(); output.flush();
        require(output.good(), "flush journal for replay " + path);
        std::ifstream input(path, std::ios::binary);
        require(input.good(), "open journal for replay " + path);
        char magic[8]; input.read(magic, 8);
        require(input.good() && std::string(magic, 8) == "BPUJ0001",
                "bad journal header " + path);
        for (uint64_t expected = 0; expected < records; ++expected) {
            const uint64_t id = readWord(input), rawSize = readWord(input);
            const uint64_t rowSize = readWord(input), hash = readWord(input);
            require(id == expected && rawSize <= MaxFrameBytes && rowSize <= MaxFrameBytes,
                    "bad phase header " + path);
            std::string raw(rawSize, '\0'), row(rowSize, '\0');
            input.read(raw.data(), rawSize); input.read(row.data(), rowSize);
            require(input.good(), "truncated phase " + path);
            require(checksum(id, raw, row) == hash, "phase checksum mismatch " + path);
            visitor(id, raw, row);
        }
        require(input.peek() == std::char_traits<char>::eof() && !input.bad(),
                "extra bytes after final phase " + path);
        input.clear(); input.close();
        require(!input.fail(), "close journal reader " + path);
    }
    void close()
    {
        if (!output.is_open()) return;
        output.flush(); require(output.good(), "flush journal " + path);
        output.close(); require(!output.fail(), "close journal " + path);
    }
    ~Spool() noexcept
    {
        try { close(); }
        catch (const std::exception &e) {
            std::fprintf(stderr, "%s\n", e.what()); std::abort();
        }
    }
};

inline std::string branchHeader(bool sub)
{
    std::ostringstream out;
    out << (sub ? "subPhaseID" : "phaseID")
        << ",numBranches,numEverTakenBranches,totalMispredicts";
    for (int i = 0; i < 5; ++i)
        out << ",topMispPC_" << i << ",type_" << i << ",misCnt_" << i;
    return out.str();
}

template<class BranchMap, class TakenMap>
std::pair<std::string, std::string> branchRecord(
    bool sub, uint64_t phase, const BranchMap &data, const TakenMap &taken)
{
    std::ostringstream raw, out;
    raw << "{\"schemaVersion\":1,\"kind\":\"branch-"
        << (sub ? "subphase" : "phase") << "\",\"phaseId\":" << phase
        << ",\"records\":[";
    bool first = true;
    std::vector<std::pair<typename BranchMap::key_type,
                          typename BranchMap::mapped_type>> phaseRecords;
    uint64_t totalMispredicts = 0;
    for (const auto &record : data) {
        phaseRecords.push_back(record);
        const auto &s = record.second;
        if (!first) raw << ',';
        first = false;
        raw << '[' << record.first.first << ',' << record.first.second << ','
            << s.pc << ',' << s.branchType << ',' << s.totalCount << ','
            << s.mispredCount << ',' << s.dirWrongCount << ','
            << s.targetWrongCount << ',' << s.noPredCount << ']';
        totalMispredicts += s.mispredCount;
    }
    raw << "],\"taken\":["; first = true;
    for (const auto &record : taken) {
        if (!first) raw << ',';
        first = false; raw << '[' << record.first << ',' << record.second << ']';
    }
    raw << "]}";
    out << phase << ',' << int(data.size()) << ',' << int(taken.size())
        << ',' << totalMispredicts;
    std::sort(phaseRecords.begin(), phaseRecords.end(), [](const auto &a, const auto &b) {
        return a.second.mispredCount > b.second.mispredCount;
    });
    for (size_t i = 0; i < 5 && i < phaseRecords.size(); ++i) {
        const auto &s = phaseRecords[i].second;
        out << ',' << std::hex << s.pc << ',' << std::dec << s.branchType
            << ',' << s.mispredCount;
    }
    out << std::dec << std::endl;
    return {raw.str(), out.str()};
}

inline std::string fsqHeader(int maxInsts)
{
    std::ostringstream out; out << "phaseID";
    for (int i = 0; i <= maxInsts; ++i) out << ',' << i;
    out << ",average"; return out.str();
}

inline std::pair<std::string, std::string> fsqRecord(
    bool fetched, uint64_t phase, const std::vector<uint64_t> &dist,
    int maxInsts, uint64_t phaseSize)
{
    require(dist.size() == size_t(maxInsts + 1), "invalid FSQ bin count");
    std::ostringstream raw, out;
    raw << "{\"schemaVersion\":1,\"kind\":\"fsq-"
        << (fetched ? "fetched" : "committed") << "\",\"phaseId\":" << phase
        << ",\"records\":[";
    out << phase; uint64_t numFsqEntries = 0;
    for (int i = 0; i <= maxInsts; ++i) {
        numFsqEntries += dist[i];
        if (i) raw << ',';
        raw << dist[i]; out << ',' << dist[i];
    }
    raw << "]}";
    out << ',' << (double)phaseSize / (double)numFsqEntries << std::endl;
    return {raw.str(), out.str()};
}

inline std::string btbHeader()
{ return "phaseID,numBTBEntries,entry_0_pc,entry_0_type,entry_1_pc,entry_1_type"; }

template<class BtbMap>
std::pair<std::string, std::string> btbRecord(uint64_t phase, const BtbMap &data)
{
    using Entry = typename BtbMap::mapped_type::first_type;
    std::ostringstream raw, out;
    raw << "{\"schemaVersion\":1,\"kind\":\"btb\",\"phaseId\":" << phase
        << ",\"records\":[";
    std::vector<std::tuple<typename BtbMap::key_type, Entry,
                          typename BtbMap::mapped_type::second_type>> entries;
    bool first = true;
    for (const auto &item : data) {
        const auto &e = item.second.first;
        entries.emplace_back(item.first, e, item.second.second);
        if (!first) raw << ',';
        first = false;
        raw << '[' << item.first << ',' << item.second.second << ',' << e.pc
            << ',' << e.target << ',' << int(e.resolved) << ',' << int(e.isCond)
            << ',' << int(e.isIndirect) << ',';
        // BranchInfo's default constructor leaves isDirect indeterminate.
        // The invalid default MBTB entry has no defined value to preserve.
        if (e.valid) raw << int(e.isDirect); else raw << "null";
        raw << ',' << int(e.isCall) << ',' << int(e.isReturn) << ',' << int(e.size)
            << ',' << int(e.valid) << ',' << int(e.alwaysTaken) << ',' << e.ctr
            << ',' << e.tag << ',' << e.source << ']';
    }
    raw << "]}";
    out << std::dec << phase << ',' << data.size();
    std::sort(entries.begin(), entries.end(), [](const auto &a, const auto &b) {
        return std::get<2>(a) > std::get<2>(b);
    });
    for (size_t i = 0; i < 2 && i < entries.size(); ++i) {
        auto entry = std::get<1>(entries[i]);
        out << ',' << std::hex << std::get<0>(entries[i])
            << ',' << std::dec << entry.getType();
    }
    out << std::endl;
    return {raw.str(), out.str()};
}

} // namespace phase_storage
}}} // namespace gem5::branch_prediction::btb_pred
#endif
