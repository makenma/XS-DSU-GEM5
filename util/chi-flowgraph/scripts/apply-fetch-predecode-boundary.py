"""Install the fetch-side predecode boundary recovery on the pinned model08 source.

The companion BPU change is separate. Preserve original sources and a diff;
refuse unrelated edits. This does not rebuild or replace an executing model.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

BASE = {
    'src/cpu/o3/fetch.cc': 'e4a71c1cfc01a190b0ea9e16946a0bc566c4ec20272697cb0f5a7909592be734',
    'src/cpu/o3/fetch.hh': 'fbe9dc45bf3fa582a2c44aa0bc0d1072d571d9fc2d2a73d7089b78716405ab8a',
}


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Expected exactly one anchor: ' + old[:100])
    return text.replace(old, new, 1)


def transform_cc(text):
    text = replace_once(text, '    assert(stream.startPC <= curr_pc && curr_pc < stream.predEndPC);', '''    panic_if(!(stream.startPC <= curr_pc && curr_pc < stream.predEndPC),
             "Fetch PC outside FTQ range: tid=%u pc=%#x start=%#x end=%#x "
             "predTaken=%u branch=%#x target=%#x ftq=%lu",
             tid, curr_pc, stream.startPC, stream.predEndPC, stream.predTaken,
             stream.predBranchInfo.pc, stream.predBranchInfo.target,
             inst->getFtqId());''')
    text = replace_once(text, '    decoder[tid]->reset();\n\n    // Clear the icache miss',
                        '    decoder[tid]->reset();\n    threads[tid].lastDecodedValid = false;\n\n    // Clear the icache miss')
    text = replace_once(text, 'bool\nFetch::processSingleInstruction(ThreadID tid, PCStateBase &pc,', '''bool
Fetch::recoverInvalidBranchSlots(ThreadID tid, PCStateBase &pc)
{
    if (isTraceMode()) {
        return false;
    }
    const auto &stream = dbpbtb->ftqFetchingTarget(tid);
    const Addr start = pc.instAddr();
    const Addr end = pc.as<RiscvISA::PCState>().npc();
    const auto &buffer = threads[tid];
    std::vector<Addr> invalid;
    auto inspect = [&](Addr branch_pc) {
        // Decode proves instruction boundaries, including the halfword
        // consumed from the next FTQ by a 32-bit instruction crossing a block.
        const bool inside_current = start < branch_pc && branch_pc < end;
        const bool inside_previous = buffer.lastDecodedValid &&
            start == buffer.lastDecodedEnd &&
            buffer.lastDecodedStart < branch_pc && branch_pc < buffer.lastDecodedEnd;
        if ((inside_current || inside_previous) &&
            std::find(invalid.begin(), invalid.end(), branch_pc) == invalid.end()) {
            invalid.push_back(branch_pc);
        }
    };
    for (const auto &entry : stream.predBTBEntries) {
        if (entry.valid) {
            inspect(entry.pc);
        }
    }
    if (stream.predTaken) {
        inspect(stream.predBranchInfo.pc);
    }
    if (invalid.empty()) {
        return false;
    }
    const auto target_id = dbpbtb->ftqHeadId(tid);
    for (Addr bad_pc : invalid) {
        DPRINTF(Fetch, "[tid:%u] Predecode invalid BTB slot %#x: decoded [%#x,%#x), "
                "previous [%#x,%#x) valid=%u, ftq=%lu prefix=%u; refetch %#x\\n",
                tid, bad_pc, start, end, buffer.lastDecodedStart, buffer.lastDecodedEnd,
                buffer.lastDecodedValid, target_id, ftqEntryFetchedInsts[tid], start);
    }
    dbpbtb->predecodeSquash(target_id, pc, invalid, tid, ftqEntryFetchedInsts[tid]);
    ftqEntryFetchedInsts[tid] = 0;

    // The current instruction has not been built yet. Keep every older
    // dynamic instruction, including this cycle's valid fetchQueue prefix.
    // No backend squash or prediction of a fabricated control instruction.
    threads[tid].cacheReq.cancelAllRequests();
    threads[tid].cacheReq.reset();
    if (retryTid == tid) {
        for (auto *packet : retryPkt) {
            delete packet;
        }
        retryPkt.clear();
        retryTid = InvalidThreadID;
        cacheBlocked = false;
    }
    threads[tid].valid = false;
    threads[tid].lastDecodedValid = false;
    decoder[tid]->reset();
    pc.as<RiscvISA::PCState>().set(start);
    return true;
}

bool
Fetch::processSingleInstruction(ThreadID tid, PCStateBase &pc,''')
    text = replace_once(text, '''        staticInst = dec_ptr->decode(pc);
        ++fetchStats.insts;''', '''        staticInst = dec_ptr->decode(pc);
        if (recoverInvalidBranchSlots(tid, pc)) {
            // End this fetch cycle and let the BPU predict from the proven
            // instruction boundary. No macro-op or DynInst exists yet.
            return true;
        }
        ++fetchStats.insts;''')
    text = replace_once(text, '''    // Update the main PC state for the next instruction.
    set(pc, *next_pc);''', '''    // Retain only a confirmed, sequentially completed instruction span.
    // A target entered by a taken branch is an independent boundary.
    if (newMacroop) {
        auto &buffer = threads[tid];
        buffer.lastDecodedStart = pc.instAddr();
        buffer.lastDecodedEnd = pc.as<RiscvISA::PCState>().npc();
        buffer.lastDecodedValid = !predictedBranch &&
            next_pc->instAddr() == buffer.lastDecodedEnd;
    }

    // Update the main PC state for the next instruction.
    set(pc, *next_pc);''')
    return text


def transform_hh(text):
    text = replace_once(text, '    void performInstructionFetch(ThreadID tid);',
                        '''    void performInstructionFetch(ThreadID tid);

    /** Reject BTB branch PCs inside confirmed real instruction bytes. */
    bool recoverInvalidBranchSlots(ThreadID tid, PCStateBase &pc);''')
    text = replace_once(text, '''        /** Size of the fetch buffer in bytes. Set by Fetch class during init. */''',
                        '''        /** Last sequentially decoded instruction, for cross-FTQ halfwords. */
        Addr lastDecodedStart = 0;
        Addr lastDecodedEnd = 0;
        bool lastDecodedValid = false;

        /** Size of the fetch buffer in bytes. Set by Fetch class during init. */''')
    text = replace_once(text, '''        void reset() {
            valid = false;
            startPC = 0;''', '''        void reset() {
            valid = false;
            lastDecodedValid = false;
            startPC = 0;''')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--from-evidence', type=Path, help='Revise only the exact previous candidate captured here')
    args = parser.parse_args()
    planned = {}
    for name, expected in BASE.items():
        path = args.source / name
        raw = path.read_bytes()
        if args.from_evidence:
            if raw != (args.from_evidence / 'patched' / name).read_bytes():
                raise ValueError('Previous candidate changed independently: ' + name)
            raw = (args.from_evidence / 'original' / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Unexpected baseline: ' + name)
        old = raw.decode()
        new = transform_cc(old) if name.endswith('.cc') else transform_hh(old)
        planned[name] = (old, new)
    args.evidence.mkdir(parents=True, exist_ok=False)
    report = []
    for name, (old, new) in planned.items():
        for group, text in [('original', old), ('patched', new)]:
            backup = args.evidence / group / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(text)
        (args.source / name).write_text(new)
        report.append({'path': name, 'originalSha256': BASE[name],
                       'patchedSha256': hashlib.sha256(new.encode()).hexdigest()})
    (args.evidence / 'fetch.patch').write_text(''.join(''.join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile='a/' + name, tofile='b/' + name)) for name, (old, new) in planned.items()))
    (args.evidence / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
