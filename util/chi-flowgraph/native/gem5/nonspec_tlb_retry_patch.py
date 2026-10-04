"""A commit-authorized delayed-translation replay for pinned KMH O3.

Keep this separate from ordinary speculative memory replay. The caller is the
completed-translation deferred list, not the cache-port retry paths.
"""

COMMIT = '65f3241a6cd486dffddaca251536f5c691af9a1f'
BASELINE_SHA256 = {
    'src/cpu/o3/inst_queue.cc':
        '98ec56e59d6083c4166c6806bcfc5ee028148ff3822059eddb798a58116fdcbc',
    'src/cpu/o3/issue_queue.hh':
        'edd89b0734e06a5c239fc89c614aafd93b35fcfe5d581c82d6de66dc8b5bbf25',
    'src/cpu/o3/issue_queue.cc':
        'b0695d50a7f3dd8e14391f0ad75896034d398783d4f93a4c4499a2a26b6bcf34',
}

DEFERRED_BEFORE = '''    while ((mem_inst = getDeferredMemInstToExecute())) {
        mem_inst->issueQue->retryMem(mem_inst);
    }
'''

DEFERRED_AFTER = '''    while ((mem_inst = getDeferredMemInstToExecute())) {
        if (mem_inst->isNonSpeculative()) {
            // The deferred list also releases squashed entries. They must
            // not regain the ROB-head permission of a live instruction.
            if (mem_inst->isSquashed()) {
                continue;
            }
            mem_inst->issueQue->retryCommitAuthorizedMem(mem_inst);
        } else {
            mem_inst->issueQue->retryMem(mem_inst);
        }
    }
'''

RETRY_METHOD = '''void
IssueQue::retryCommitAuthorizedMem(const DynInstPtr& inst)
{
    // scheduleNonSpec already granted ROB-head permission and removed the
    // instruction from nonSpecInsts. Resume that same instruction after its
    // translation, including a completed translation fault, without granting
    // fresh permission or putting it in the speculative ready queues.
    panic_if(inst->issueQue != this || !inst->isNonSpeculative() ||
             !inst->isMemRef() || !inst->isAtCommit() ||
             !inst->translationCompleted() || inst->isSquashed() ||
             inst->inPipe() || inst->isExecuted(),
             "Invalid commit-authorized memory replay [sn:%llu] PC %s: "
             "nonSpec=%d memRef=%d atCommit=%d translated=%d "
             "squashed=%d inPipe=%d executed=%d",
             inst->seqNum, inst->pcState(), inst->isNonSpeculative(),
             inst->isMemRef(), inst->isAtCommit(),
             inst->translationCompleted(), inst->isSquashed(),
             inst->inPipe(), inst->isExecuted());
    iqstats->retryMem++;
    DPRINTF(Schedule, "retry commit-authorized memory [sn:%llu] PC %s "
            "fault=%d\\n", inst->seqNum, inst->pcState(), inst->faulted());
    // issueToFu applies the existing replay bandwidth and load/store port
    // limits. Re-entering the LSQ pipe clears its replay flags; AtCommit,
    // savedRequest and the translation fault remain intact.
    replayQ.push(inst);
}

'''


def replace_once(source, before, after, path):
    if source.count(before) != 1:
        raise ValueError('unexpected or ambiguous patch context: ' + path)
    return source.replace(before, after, 1)


def patches(baseline):
    """Return desired text; the installer separately validates full hashes."""
    cc = 'src/cpu/o3/issue_queue.cc'
    hh = 'src/cpu/o3/issue_queue.hh'
    iq = 'src/cpu/o3/inst_queue.cc'
    return {
        iq: replace_once(baseline(iq), DEFERRED_BEFORE, DEFERRED_AFTER, iq),
        hh: replace_once(
            baseline(hh),
            '    void retryMem(const DynInstPtr& inst);\n',
            '    void retryMem(const DynInstPtr& inst);\n'
            '    /** Resume an already-authorized non-speculative TLB miss. */\n'
            '    void retryCommitAuthorizedMem(const DynInstPtr& inst);\n', hh),
        cc: replace_once(baseline(cc), 'bool\nIssueQue::idle()\n',
                         RETRY_METHOD + 'bool\nIssueQue::idle()\n', cc),
    }
