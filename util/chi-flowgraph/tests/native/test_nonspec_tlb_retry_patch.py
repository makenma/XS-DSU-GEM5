"""Patch/installer and extracted C++ control-flow tests, not a CPU simulation.

The C++ test executes the exact generated retry method and deferred-list routing
against small DynInst/queue doubles. Actual delayed page walks need the separate
full-system CBO reproducer; SE translates synchronously in this pinned gem5.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'native/gem5'))
import nonspec_tlb_retry_patch as change

spec = importlib.util.spec_from_file_location(
    'apply_nonspec_tlb', PROJECT / 'scripts/apply-nonspec-tlb-retry.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gem5 = self.root / 'gem5'
        self.original = {
            'src/cpu/o3/inst_queue.cc': change.DEFERRED_BEFORE.encode(),
            'src/cpu/o3/issue_queue.hh': b'    void retryMem(const DynInstPtr& inst);\n',
            'src/cpu/o3/issue_queue.cc': b'void\nIssueQue::retryMem(const DynInstPtr& inst)\n'
                b'{\n    assert(!inst->isNonSpeculative());\n}\nbool\nIssueQue::idle()\n{}\n',
        }
        for name, data in self.original.items():
            path = self.gem5 / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.hashes = {p: hashlib.sha256(s).hexdigest() for p, s in self.original.items()}
        self.patchers = [
            patch.object(installer, 'BASELINE_SHA256', self.hashes),
            patch.object(installer, 'PROJECT', self.root),
            patch.object(installer.subprocess, 'check_output', side_effect=self.git),
        ]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)

    def git(self, args, **kwargs):
        if args[-2:] == ['rev-parse', 'HEAD']:
            return change.COMMIT + '\n'
        return self.original[args[-1].split(':', 1)[1]]

    def main(self, *extra):
        with patch.object(sys, 'argv', ['install', '--gem5', str(self.gem5), *extra]):
            installer.main()

    def test_check_and_snapshot_leave_checkout_unchanged(self):
        self.main('--check')
        self.assertFalse((self.root / 'build').exists())
        self.main('--snapshot-only')
        for name, data in self.original.items():
            self.assertEqual((self.gem5 / name).read_bytes(), data)
        manifest = json.loads((self.root / 'build/nonspec-tlb-preview/manifest.json').read_text())
        self.assertFalse(manifest['checkoutModified'])
        self.assertEqual(len(manifest['files']), 3)

    def test_install_idempotent_with_exact_snapshots(self):
        self.main()
        times = {p: (self.gem5 / p).stat().st_mtime_ns for p in self.original}
        self.main()
        self.assertEqual(times, {p: (self.gem5 / p).stat().st_mtime_ns for p in self.original})
        manifest = json.loads((self.root / 'build/nonspec-tlb-installed/manifest.json').read_text())
        self.assertTrue(manifest['checkoutModified'])
        for item in manifest['files']:
            actual = (self.gem5 / item['path']).read_bytes()
            self.assertEqual(hashlib.sha256(actual).hexdigest(), item['patchedSha256'])

    def test_unknown_edit_refused_before_any_write(self):
        name = 'src/cpu/o3/issue_queue.cc'
        (self.gem5 / name).write_bytes(self.original[name] + b'// independent\n')
        before = {p: (self.gem5 / p).read_bytes() for p in self.original}
        with self.assertRaisesRegex(ValueError, 'independent edits'):
            self.main()
        self.assertEqual(before, {p: (self.gem5 / p).read_bytes() for p in self.original})
        self.assertFalse((self.root / 'build').exists())

    def test_wrong_head_and_pinned_hash_refused(self):
        with patch.object(installer.subprocess, 'check_output', return_value='wrong\n'):
            with self.assertRaisesRegex(ValueError, 'unexpected gem5 commit'):
                installer.plan(self.gem5)
        self.hashes['src/cpu/o3/inst_queue.cc'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'pinned source hash'):
            installer.plan(self.gem5)

    def test_missing_and_duplicate_context_refused(self):
        for text in ['', change.DEFERRED_BEFORE * 2]:
            with self.subTest(text=text[:20]):
                with self.assertRaisesRegex(ValueError, 'context'):
                    change.patches(lambda p: text if p.endswith('inst_queue.cc') else
                                   self.original[p].decode())

    def test_ordinary_assert_and_other_routes_unchanged(self):
        result = change.patches(lambda p: self.original[p].decode())
        cc = result['src/cpu/o3/issue_queue.cc']
        before = self.original['src/cpu/o3/issue_queue.cc'].decode()
        self.assertEqual(cc.replace(change.RETRY_METHOD, ''), before)
        self.assertIn('assert(!inst->isNonSpeculative());', cc)
        self.assertIn('else {\n            mem_inst->issueQue->retryMem(mem_inst);',
                      result['src/cpu/o3/inst_queue.cc'])


class ExtractedCppTests(unittest.TestCase):
    def test_authorization_fault_squash_and_ordinary_routing(self):
        compiler = shutil.which('g++') or shutil.which('c++')
        if compiler is None:
            self.skipTest('C++ compiler unavailable; run this test in WSL')
        cpp = r'''
#include <cassert>
#include <deque>
#include <memory>
#include <queue>
#include <stdexcept>
#include <iostream>
#define panic_if(condition, ...) do { if (condition) throw std::runtime_error("invariant"); } while (0)
#define DPRINTF(...) do {} while (0)
struct IssueQue;
struct Inst {
    IssueQue* issueQue = nullptr;
    bool nonSpec = true, memRef = true, atCommit = true, translated = true;
    bool squashed = false, inPipeline = false, executed = false;
    bool fault = false, replayNeeded = true;
    void* savedRequest = reinterpret_cast<void*>(0x1234);
    bool isNonSpeculative() const { return nonSpec; }
    bool isMemRef() const { return memRef; }
    bool isAtCommit() const { return atCommit; }
    bool translationCompleted() const { return translated; }
    bool isSquashed() const { return squashed; }
    bool inPipe() const { return inPipeline; }
    bool isExecuted() const { return executed; }
};
using DynInstPtr = std::shared_ptr<Inst>;
struct IssueQue {
    struct Stats { unsigned retryMem = 0; } stats;
    Stats* iqstats = &stats;
    std::queue<DynInstPtr> replayQ;
    unsigned ordinary = 0;
    void retryMem(const DynInstPtr& inst) {
        assert(!inst->isNonSpeculative());
        ordinary++;
        stats.retryMem++;
        replayQ.push(inst);
    }
    void retryCommitAuthorizedMem(const DynInstPtr& inst);
};
''' + change.RETRY_METHOD + r'''
struct InstructionQueue {
    std::deque<DynInstPtr> deferred;
    DynInstPtr getDeferredMemInstToExecute() {
        if (deferred.empty()) return nullptr;
        auto inst = deferred.front(); deferred.pop_front(); return inst;
    }
    void resume() {
        DynInstPtr mem_inst;
''' + change.DEFERRED_AFTER + r'''
    }
};
DynInstPtr makeInst(IssueQue& queue) {
    auto inst = std::make_shared<Inst>(); inst->issueQue = &queue; return inst;
}
int main() {
    // The generated method itself, including every rejection guard.
    for (unsigned mode = 0; mode != 8; mode++) {
        IssueQue queue, other;
        auto inst = makeInst(queue);
        switch (mode) {
          case 0: inst->nonSpec = false; break;
          case 1: inst->memRef = false; break;
          case 2: inst->atCommit = false; break;
          case 3: inst->translated = false; break;
          case 4: inst->squashed = true; break;
          case 5: inst->inPipeline = true; break;
          case 6: inst->executed = true; break;
          case 7: inst->issueQue = &other; break;
        }
        bool rejected = false;
        try { queue.retryCommitAuthorizedMem(inst); }
        catch (const std::runtime_error&) { rejected = true; }
        assert(rejected && queue.replayQ.empty() && queue.stats.retryMem == 0);
    }
    // A completed translation fault is resumed unchanged for precise commit.
    for (bool fault : {false, true}) {
        IssueQue queue;
        auto inst = makeInst(queue); inst->fault = fault;
        auto request = inst->savedRequest;
        InstructionQueue iq; iq.deferred.push_back(inst); iq.resume();
        assert(queue.replayQ.size() == 1 && queue.replayQ.front() == inst);
        assert(queue.stats.retryMem == 1 && queue.ordinary == 0);
        assert(inst->fault == fault && inst->savedRequest == request);
        assert(inst->atCommit && inst->nonSpec && inst->replayNeeded);
        assert(!inst->inPipeline && !inst->executed && iq.deferred.empty());
        iq.resume(); assert(queue.replayQ.size() == 1);
    }
    // Squashed static nonSpec must be discarded even before TLB completion.
    // Ordinary squashed replay keeps the original accounting/queue behavior.
    IssueQue queue;
    auto squashed = makeInst(queue); squashed->squashed = true;
    squashed->atCommit = false; squashed->translated = false;
    auto ordinary = makeInst(queue); ordinary->nonSpec = false;
    auto ordinarySquashed = makeInst(queue);
    ordinarySquashed->nonSpec = false; ordinarySquashed->squashed = true;
    InstructionQueue iq;
    iq.deferred = {squashed, ordinary, ordinarySquashed}; iq.resume();
    assert(queue.replayQ.size() == 2 && queue.stats.retryMem == 2 && queue.ordinary == 2);
    assert(queue.replayQ.front() == ordinary); queue.replayQ.pop();
    assert(queue.replayQ.front() == ordinarySquashed);
    std::cout << "extracted patch control-flow checks passed\n";
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'retry.cc'
            binary = Path(directory) / 'retry-test'
            source.write_text(cpp, encoding='utf-8')
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
