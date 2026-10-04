"""Baseline-only independent exporter of the seven original phase vectors.

No original counters, vector collection, formatter, sorting or dump calls change.
Never install together with the storage candidate: the source hash rejects it.
"""
import hashlib

COMMIT = '65f3241a6cd486dffddaca251536f5c691af9a1f'
BASELINE_SHA256 = {'src/cpu/pred/btb/decoupled_bpred_stats.cc':
                   'd36d8982f32df1d185aab54f59d1049ee2b3fd03ee638a067213f912d4ebcc29'}

HELPER = r'''
// BPU_PHASE_VECTOR_ORACLE_V1: independent exporter, reads original vectors.
namespace {
void oracleCopy(const std::string &source, const std::string &destination)
{
    std::ifstream in(simout.resolve(source), std::ios::binary);
    std::ofstream out(simout.resolve(destination), std::ios::binary | std::ios::trunc);
    if (!in.good() || !out.good()) throw std::runtime_error("BPU oracle: copy open");
    std::array<char, 8192> buffer{};
    while (in.read(buffer.data(), buffer.size()) || in.gcount())
        out.write(buffer.data(), in.gcount());
    if (!in.eof() || in.bad()) throw std::runtime_error("BPU oracle: copy read");
    out.flush();
    if (!out.good()) throw std::runtime_error("BPU oracle: copy write");
    out.close();
    if (out.fail()) throw std::runtime_error("BPU oracle: copy close");
}
template<class Writer>
void oracleWrite(const std::string &prefix, const std::string &archive,
                 const std::string &file, Writer writer)
{
    std::ofstream out(simout.resolve(prefix + file), std::ios::binary | std::ios::trunc);
    if (!out.good()) throw std::runtime_error("BPU oracle: record open");
    writer(out);
    out.flush();
    if (!out.good()) throw std::runtime_error("BPU oracle: record write");
    out.close();
    if (out.fail()) throw std::runtime_error("BPU oracle: record close");
    oracleCopy(prefix + file, archive + file);
}
template<class BranchPhases, class TakenPhases>
void oracleBranches(std::ostream &out, const char *kind,
                    const BranchPhases &phases, const TakenPhases &taken)
{
    if (phases.size() != taken.size()) throw std::runtime_error("BPU oracle: phase mismatch");
    for (size_t id = 0; id < phases.size(); ++id) {
        out << "{\"schemaVersion\":1,\"kind\":" << std::quoted(kind)
            << ",\"phaseId\":" << id << ",\"records\":[";
        bool first = true;
        for (const auto &entry : phases[id]) {
            const auto &s = entry.second;
            if (!first) out << ',';
            first = false;
            out << '[' << entry.first.first << ',' << entry.first.second << ','
                << s.pc << ',' << s.branchType << ',' << s.totalCount << ','
                << s.mispredCount << ',' << s.dirWrongCount << ','
                << s.targetWrongCount << ',' << s.noPredCount << ']';
        }
        out << "],\"taken\":["; first = true;
        for (const auto &entry : taken[id]) {
            if (!first) out << ',';
            first = false; out << '[' << entry.first << ',' << entry.second << ']';
        }
        out << "]}\n";
    }
}
template<class Phases>
void oracleFsq(std::ostream &out, const char *kind, const Phases &phases)
{
    for (size_t id = 0; id < phases.size(); ++id) {
        out << "{\"schemaVersion\":1,\"kind\":" << std::quoted(kind)
            << ",\"phaseId\":" << id << ",\"records\":[";
        bool first = true;
        for (const auto value : phases[id]) {
            if (!first) out << ',';
            first = false; out << value;
        }
        out << "]}\n";
    }
}
template<class Phases>
void oracleBtb(std::ostream &out, const Phases &phases)
{
    for (size_t id = 0; id < phases.size(); ++id) {
        out << "{\"schemaVersion\":1,\"kind\":\"btb\",\"phaseId\":" << id
            << ",\"records\":[";
        bool first = true;
        for (const auto &item : phases[id]) {
            const auto &e = item.second.first;
            if (!first) out << ',';
            first = false;
            out << '[' << item.first << ',' << item.second.second << ',' << e.pc
                << ',' << e.target << ',' << int(e.resolved) << ',' << int(e.isCond)
                << ',' << int(e.isIndirect) << ',';
            // Default BranchInfo leaves this field undefined; never read it.
            if (e.valid) out << int(e.isDirect); else out << "null";
            out << ',' << int(e.isCall) << ',' << int(e.isReturn) << ',' << int(e.size)
                << ',' << int(e.valid) << ',' << int(e.alwaysTaken) << ',' << e.ctr
                << ',' << e.tag << ',' << e.source << ']';
        }
        out << "]}\n";
    }
}
} // anonymous namespace
'''

EXPORT = r'''
    // Additional baseline oracle; the original reports above are already closed.
    static std::map<std::string, uint64_t> oracleDumpIds;
    const uint64_t oracleDumpId = oracleDumpIds[name()]++;
    const std::string oraclePrefix = name() + ".";
    const std::string oracleArchive = name() + ".dump-" + std::to_string(oracleDumpId) + ".";
    for (const char *file : {"topMisPredicts.csv", "topMispredictsByBranch.csv",
            "topMisrateByBranch.csv", "topMispredictByPhase.csv", "topMispredictBySubPhase.csv",
            "topMisPredictHist.csv", "misPredIndirectStream.csv",
            "fsqEntryCommittedInstNumDistsByPhase.csv", "fsqEntryFetchedInstNumDistsByPhase.csv",
            "btbEntriesByPhase.csv"}) {
        oracleCopy(file, oraclePrefix + file);
        oracleCopy(file, oracleArchive + file);
    }
    oracleWrite(oraclePrefix, oracleArchive, "topMispredictByPhase.csv.records.jsonl",
        [&](std::ostream &out) { oracleBranches(out, "branch-phase", topMispredictsByBranchByPhase, takenBranchesByPhase); });
    oracleWrite(oraclePrefix, oracleArchive, "topMispredictBySubPhase.csv.records.jsonl",
        [&](std::ostream &out) { oracleBranches(out, "branch-subphase", topMispredictsByBranchBySubPhase, takenBranchesBySubPhase); });
    oracleWrite(oraclePrefix, oracleArchive, "fsqEntryCommittedInstNumDistsByPhase.csv.records.jsonl",
        [&](std::ostream &out) { oracleFsq(out, "fsq-committed", fsqEntryNumCommittedInstDistByPhase); });
    oracleWrite(oraclePrefix, oracleArchive, "fsqEntryFetchedInstNumDistsByPhase.csv.records.jsonl",
        [&](std::ostream &out) { oracleFsq(out, "fsq-fetched", fsqEntryNumFetchedInstDistByPhase); });
    oracleWrite(oraclePrefix, oracleArchive, "btbEntriesByPhase.csv.records.jsonl",
        [&](std::ostream &out) { oracleBtb(out, BTBEntriesByPhase); });
    oracleWrite(oraclePrefix, oracleArchive, "phase-manifest.json", [&](std::ostream &out) {
        out << "{\"schemaVersion\":1,\"bpuName\":" << std::quoted(name())
            << ",\"numInstCommitted\":" << numInstCommitted
            << ",\"phaseSizeByInst\":" << phaseSizeByInst
            << ",\"subPhaseSize\":" << phaseSizeByInst / subPhaseRatio
            << ",\"mainPhases\":" << topMispredictsByBranchByPhase.size()
            << ",\"subPhases\":" << topMispredictsByBranchBySubPhase.size()
            << ",\"dumpId\":" << oracleDumpId << ",\"completeRecords\":true"
            << ",\"partialMainInsts\":" << numInstCommitted % phaseSizeByInst
            << ",\"partialSubInsts\":" << numInstCommitted % (phaseSizeByInst / subPhaseRatio)
            << "}\n";
    });
'''


def prepare(path, data):
    before = '#include <algorithm>\n'
    after = before + '#include <array>\n#include <fstream>\n#include <iomanip>\n#include <map>\n#include <stdexcept>\n'
    entry = 'void\nDecoupledBPUWithBTB::initDB()'
    exit_anchor = '        bpdb.save_db(simout.resolve("bp.db").c_str());\n    }\n}'
    edits = [(before, after), (entry, HELPER + '\n' + entry),
             (exit_anchor, exit_anchor[:-1] + EXPORT + '}')]
    text = data.decode('utf-8')
    if hashlib.sha256(data).hexdigest() != BASELINE_SHA256[path]:
        restored = text
        for old, new in reversed(edits):
            if restored.count(new) != 1:
                raise ValueError('unknown source or storage candidate: ' + path)
            restored = restored.replace(new, old, 1)
        if hashlib.sha256(restored.encode()).hexdigest() != BASELINE_SHA256[path]:
            raise ValueError('refusing independent source changes: ' + path)
        text = restored
    original = text.encode()
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError('oracle exact context mismatch: ' + path)
        text = text.replace(old, new, 1)
    return original, text.encode()
