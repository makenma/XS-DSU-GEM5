"""Extract the actual candidate phase methods into a small C++ boundary harness.

Only FTQ/inst interfaces and the journal sink are test doubles. The four tested
method bodies and BranchStats are taken verbatim from guarded patched sources;
this is a component test, not a CPU simulation. This script does not compile.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'native/gem5'))
import bpu_phase_storage_patch as patch


def extract(text, marker):
    start = text.index(marker)
    brace = text.index('{', start)
    depth = 1
    cursor = brace + 1
    while depth:
        depth += (text[cursor] == '{') - (text[cursor] == '}')
        cursor += 1
    return text[start:cursor]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=ROOT / 'build/bpu-phase-storage-original')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    originals = {}
    for name, expected in patch.BASELINE_SHA256.items():
        data = (args.baseline / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('baseline SHA mismatch: ' + name)
        originals[name] = data.decode()
    candidate = patch.patches(originals)
    hh, stats = candidate[patch.HH], candidate[patch.STATS]
    methods = []
    for return_type, name in (
            ('void', 'processFetchDistributions'),
            ('std::unordered_map<Addr, std::pair<BTBEntry, uint64_t>>', 'processBTBEntries'),
            ('bool', 'processPhase'), ('void', 'notifyInstCommit')):
        methods.append(return_type + '\n' + extract(stats, 'DecoupledBPUWithBTB::' + name + '('))
    branch = extract(hh, '    struct BranchStats') + ';'
    scaffold = r'''
#include "BpuPhaseSpool.hh"
#include <cassert>
#include <iostream>
#include <map>
#include <unordered_map>
#define DPRINTF(...) do {} while (0)
namespace phase_storage = gem5::branch_prediction::btb_pred::phase_storage;
using Addr = uint64_t;
enum MispredType { DIR_WRONG, TARGET_WRONG, NO_PRED };
struct BTBEntry {
    uint64_t pc=0,target=0,tag=0;
    bool resolved=false,isCond=false,isIndirect=false,isDirect=false,isCall=false,isReturn=false;
    int size=4,ctr=0,source=-1;
    bool valid=true,alwaysTaken=false;
    int getType() { return 0; }
};
struct Inst { int ftqId=0,threadNumber=0; };
using DynInstPtr = std::shared_ptr<Inst>;
struct FTQ {
    struct Target { int commitInstNum=0; } target;
    Target &get(int, int) { return target; }
};
struct Captured {
    struct Frame { uint64_t id; std::string raw,row; };
    std::vector<Frame> frames;
    void append(uint64_t id, const std::string &raw, const std::string &row)
    { frames.push_back({id,raw,row}); }
};
class DecoupledBPUWithBTB {
  public:
@BRANCH@
    using BranchKey = std::pair<Addr,int>;
    using BranchStatsMap = std::map<BranchKey,BranchStats>;
    FTQ ftq;
    uint64_t numInstCommitted=0,phaseSizeByInst=100000,phaseIdToDump=1,subPhaseIdToDump=1;
    int subPhaseRatio=10,maxInstsNum=2;
    uint64_t subPhaseSizeByInst() { return phaseSizeByInst/subPhaseRatio; }
    BranchStatsMap topMispredictsByBranch,lastPhaseTopMispredictsByBranch,lastSubPhaseTopMispredictsByBranch;
    std::unordered_map<Addr,uint64_t> currentPhaseTakenBranches,currentSubPhaseTakenBranches;
    std::vector<uint64_t> commitFsqEntryHasInstsVector{0,0,0},lastPhaseFsqEntryNumCommittedInstDist{0,0,0};
    std::vector<uint64_t> commitFsqEntryFetchedInstsVector{0,0,0},lastPhaseFsqEntryNumFetchedInstDist{0,0,0};
    std::unordered_map<Addr,std::pair<BTBEntry,uint64_t>> totalBTBEntries,lastPhaseBTBEntries;
    std::array<Captured,5> phaseHistory;
    bool processPhase(bool,uint64_t,uint64_t &,BranchStatsMap &,std::unordered_map<Addr,uint64_t> &);
    void processFetchDistributions(std::vector<uint64_t> &,std::vector<uint64_t> &);
    std::unordered_map<Addr,std::pair<BTBEntry,uint64_t>> processBTBEntries();
    void notifyInstCommit(const DynInstPtr &);
};
@METHODS@
int main() {
    DecoupledBPUWithBTB bpu;
    const auto inst=std::make_shared<Inst>();
    bpu.numInstCommitted=2147483646ULL;
    bpu.phaseIdToDump=21475; bpu.subPhaseIdToDump=214749;
    bpu.notifyInstCommit(inst); bpu.notifyInstCommit(inst);
    assert(bpu.numInstCommitted==2147483648ULL);
    for (const auto &sink:bpu.phaseHistory) assert(sink.frames.empty());
    // Reach a real production-sized phase boundary above 2^31, with nonzero deltas.
    bpu.numInstCommitted=2147499999ULL; bpu.subPhaseIdToDump=214750;
    DecoupledBPUWithBTB::BranchStats old(4096,1),current(4096,1);
    constexpr uint64_t Base=2147483645ULL;
    old.totalCount=Base; old.mispredCount=Base;
    current.totalCount=Base+7; current.mispredCount=Base+2; current.dirWrongCount=2;
    bpu.topMispredictsByBranch[{4096,1}]=current;
    bpu.lastPhaseTopMispredictsByBranch[{4096,1}]=old;
    bpu.lastSubPhaseTopMispredictsByBranch[{4096,1}]=old;
    bpu.currentPhaseTakenBranches[4096]=7; bpu.currentSubPhaseTakenBranches[4096]=7;
    bpu.commitFsqEntryHasInstsVector={Base+1,Base+3,Base+7}; bpu.lastPhaseFsqEntryNumCommittedInstDist={Base,Base,Base};
    bpu.commitFsqEntryFetchedInstsVector={Base+1,Base+2,Base+3}; bpu.lastPhaseFsqEntryNumFetchedInstDist={Base,Base,Base};
    bpu.totalBTBEntries[4096]={BTBEntry{},Base+7}; bpu.lastPhaseBTBEntries[4096]={BTBEntry{},Base};
    bpu.notifyInstCommit(inst);
    assert(bpu.numInstCommitted==2147500000ULL && bpu.phaseIdToDump==21476 && bpu.subPhaseIdToDump==214751);
    for(const auto &sink:bpu.phaseHistory) assert(sink.frames.size()==1);
    assert(bpu.phaseHistory[0].frames[0].id==21474 && bpu.phaseHistory[1].frames[0].id==214749);
    assert(bpu.phaseHistory[0].frames[0].raw.find("[4096,1,4096,1,7,2,2,0,0]")!=std::string::npos);
    assert(bpu.phaseHistory[2].frames[0].raw.find("[1,3,7]")!=std::string::npos);
    assert(bpu.phaseHistory[3].frames[0].raw.find("[1,2,3]")!=std::string::npos);
    assert(bpu.phaseHistory[4].frames[0].raw.find("[4096,7,")!=std::string::npos);
    assert(bpu.currentPhaseTakenBranches.empty() && bpu.currentSubPhaseTakenBranches.empty());
    // No new completed phase: notify must not duplicate prior snapshots.
    bpu.notifyInstCommit(inst);
    for(const auto &sink:bpu.phaseHistory) assert(sink.frames.size()==1);
    // Adjacent phase must subtract the newly installed previous snapshot.
    bpu.numInstCommitted=2147599999ULL; bpu.subPhaseIdToDump=214760;
    bpu.topMispredictsByBranch[{4096,1}].incrementTotal();
    bpu.topMispredictsByBranch[{4096,1}].incrementMispred(TARGET_WRONG);
    ++bpu.commitFsqEntryHasInstsVector[2]; ++bpu.commitFsqEntryFetchedInstsVector[1];
    ++bpu.totalBTBEntries[4096].second;
    bpu.notifyInstCommit(inst);
    assert(bpu.phaseHistory[0].frames[1].raw.find("[4096,1,4096,1,1,1,0,1,0]")!=std::string::npos);
    assert(bpu.phaseHistory[2].frames[1].raw.find("[0,0,1]")!=std::string::npos);
    assert(bpu.phaseHistory[4].frames[1].raw.find("[4096,1,")!=std::string::npos);
    DecoupledBPUWithBTB::BranchStats counter;
    counter.totalCount=2147483647ULL; counter.mispredCount=2147483647ULL;
    counter.dirWrongCount=2147483647ULL; counter.incrementTotal(); counter.incrementMispred(DIR_WRONG);
    assert(counter.totalCount==2147483648ULL && counter.mispredCount==2147483648ULL && counter.dirWrongCount==2147483648ULL);
    for (const int count : {0,1,2147482,2147483}) {
        counter.mispredCount=count; counter.totalCount=10000000;
        assert(counter.getMispredRate()==double(count*1000)/10000000);
    }
    for (const uint64_t count : std::array<uint64_t,3>{{2147484ULL,2147483648ULL,UINT64_MAX}}) {
        counter.mispredCount=count; counter.totalCount=count;
        const unsigned __int128 product=static_cast<unsigned __int128>(count)*1000;
        assert(counter.getMispredRate()==static_cast<double>(product)/count);
    }
    counter.totalCount=0; assert(counter.getMispredRate()==0);
    // Exercise the actual phase ID path across INT_MAX without billions of calls.
    DecoupledBPUWithBTB large;
    large.phaseSizeByInst=1; large.subPhaseRatio=1;
    large.numInstCommitted=2147483647ULL;
    large.phaseIdToDump=2147483648ULL; large.subPhaseIdToDump=2147483648ULL;
    large.notifyInstCommit(inst);
    for(const auto &sink:large.phaseHistory) assert(sink.frames[0].id==2147483647ULL);
    large.notifyInstCommit(inst);
    for(const auto &sink:large.phaseHistory) assert(sink.frames[1].id==2147483648ULL);
    std::cout << "{\"schemaVersion\":1,\"status\":\"PASS\",\"cases\":{"
        "\"actualNotifyAcrossInt32\":true,\"actualPhaseIdAcrossInt32\":true,"
        "\"actualPhaseDelta\":true,\"actualFsqDelta\":true,\"actualBtbDelta\":true,"
        "\"incompleteTailNotAppended\":true,\"statisticsCountersAcrossInt32\":true,"
        "\"mispredRateWideIntermediate\":true,\"adjacentPhaseDifference\":true,"
        "\"legacyDefinedRateRange\":true,\"zeroRateDenominator\":true}}\n";
}
'''
    output = scaffold.replace('@BRANCH@', branch).replace('@METHODS@', '\n\n'.join(methods))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding='utf-8', newline='\n')
    manifest = {
        'schemaVersion': 1, 'scope': 'actual extracted methods; FTQ and journal interface doubles; not CPU execution',
        'baselineSha256': patch.BASELINE_SHA256,
        'candidateSha256': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in candidate.items()},
        'methodSha256': [hashlib.sha256(text.encode()).hexdigest() for text in methods],
        'harnessSha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'compiled': False, 'executed': False,
    }
    args.output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
