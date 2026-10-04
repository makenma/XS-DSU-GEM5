"""Installer safety and comparisons of actual old/new router method bodies.

Prepare the pinned review snapshot with apply-router-host-scan.py first. This
executes extracted production functions with queue/port storage, not a second
implementation of routing logic. Full simulator mode/golden runs are separate.
"""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'native/gem5'))
import router_host_scan_patch as change
spec = importlib.util.spec_from_file_location(
    'router_host_installer', PROJECT / 'scripts/apply-router-host-scan.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def function_body(text, signature):
    begin = text.index(signature)
    brace = text.index('{', begin)
    depth = 1
    cursor = brace + 1
    while depth:
        if text[cursor] == '{':
            depth += 1
        elif text[cursor] == '}':
            depth -= 1
        cursor += 1
    return text[brace:cursor]


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = PROJECT / 'build/router-host-scan-preview'
        cls.old = {p: (snapshot / 'original' / p).read_text() for p in (change.CC, change.HH)}
        cls.new = {p: (snapshot / 'patched' / p).read_text() for p in cls.old}
        if hashlib.sha256(cls.old[change.CC].encode()).hexdigest() != change.OBSERVED_CC_SHA256:
            raise ValueError('review snapshot is not the fixed observed router')
        if cls.new != change.patches(cls.old):
            raise ValueError('review snapshot does not match the current patch')

    def test_credit_wakeup_stage_order_and_arbitration_body_preserved(self):
        for function in ['wakeup', 'stepOneCycle', 'updateCreditFromIf',
                         'pickOutputWinnerByAge', 'pickOldestOut', 'updateOutAge',
                         'enqueueInbufFlit', 'enqueueInternalReq', 'unserialize']:
            with self.subTest(function=function):
                marker = 'ChiRouterRefModel::' + function + '('
                self.assertEqual(function_body(self.old[change.CC], marker),
                                 function_body(self.new[change.CC], marker))
        self.assertIn('const bool multiFlitOut;', self.new[change.HH])

    def test_bad_patch_context_refused(self):
        sources = dict(self.old)
        sources[change.CC] = sources[change.CC].replace('RoutedFlit head;', 'RoutedFlit independent;')
        with self.assertRaisesRegex(ValueError, 'context'):
            change.patches(sources)

    def test_actual_method_bodies_equal_in_both_modes_without_payload_copy(self):
        compiler = shutil.which('g++') or shutil.which('c++')
        self.assertIsNotNone(compiler, 'Run the focused C++ comparison in WSL')
        old = self.old[change.CC]
        new = self.new[change.CC]
        cpp = r'''
#include <array>
#include <cassert>
#include <deque>
#include <memory>
#include <random>
#include <vector>
#include <iostream>
enum class ChannelType { REQ, RSP, SNP, DAT };
struct Model {
    static constexpr int RefChannels=4, MaxRouters=2, InPortNum=12;
    static constexpr int OutPortNum=12, InternalNum=8, DefaultPNum=4;
    using InPort=int; using OutPort=int;
    enum class QueueDir { Rx, Tx };
    struct RoutedFlit {
        QueueDir dir=QueueDir::Rx; int out=0;
        std::vector<unsigned char> payload=std::vector<unsigned char>(32, 0xa5);
        inline static unsigned copies=0;
        RoutedFlit() = default;
        RoutedFlit(const RoutedFlit& x):dir(x.dir),out(x.out),payload(x.payload) { copies++; }
        RoutedFlit& operator=(const RoutedFlit& x) {
            dir=x.dir; out=x.out; payload=x.payload; copies++; return *this;
        }
    };
    struct Port {
        std::array<std::deque<int>,4> rx,tx;
        bool hasRxFlit(ChannelType c) const { return !rx[static_cast<int>(c)].empty(); }
        bool hasTxFlit(ChannelType c) const { return !tx[static_cast<int>(c)].empty(); }
    };
    const bool multiFlitOut;
    std::vector<std::unique_ptr<Port>> internalPorts,internalPeerPorts,devicePorts,localPorts;
    using Q=std::deque<RoutedFlit>;
    std::array<std::array<Q,24>,4> inputQ;
    std::array<std::array<Q,8>,4> inputFlitvQ;
    std::array<std::array<std::array<Q,12>,2>,4> inbufData,internalReqQ;
    std::array<std::array<std::array<std::array<Q,12>,12>,2>,4> inbufDataByOut,internalReqByOutQ;
    std::array<std::array<std::array<bool,12>,2>,4> outGrantVld{};
    std::array<std::array<std::array<Q,4>,2>,4> pCandQ;
    std::array<std::array<Q,4>,4> pPipeQ,pTxFifo;
    Model(bool mode):multiFlitOut(mode) {
        for (int i=0;i<8;i++) {
            internalPorts.emplace_back(std::make_unique<Port>());
            internalPeerPorts.emplace_back(std::make_unique<Port>());
        }
        for (int i=0;i<16;i++) {
            devicePorts.emplace_back(std::make_unique<Port>());
            localPorts.emplace_back(std::make_unique<Port>());
        }
    }
    int chIdx(ChannelType c) const { return static_cast<int>(c); }
    ChannelType idxToCh(int c) const { return static_cast<ChannelType>(c); }
    bool oldHasWork() const
''' + function_body(old, 'ChiRouterRefModel::hasWork(') + r'''
    bool newHasWork() const
''' + function_body(new, 'ChiRouterRefModel::hasWork(') + r'''
    bool oldPeek(ChannelType ch,int router,InPort ip,OutPort out,RoutedFlit& flit) const
''' + function_body(old, 'ChiRouterRefModel::peekInbufFlit(') + r'''
    bool newPeek(ChannelType ch,int router,InPort ip,OutPort out,QueueDir& dir) const
''' + function_body(new, 'ChiRouterRefModel::peekInbufDirection(') + r'''
};
int main() {
    unsigned checks=0;
    for (bool mode : {false,true}) {
        auto owned=std::make_unique<Model>(mode); auto& m=*owned;
        assert(!m.oldHasWork() && !m.newHasWork()); checks++;
        std::vector<Model::Q*> queues;
        for (int c=0;c<4;c++) {
            for (auto& q:m.inputQ[c]) queues.push_back(&q);
            for (auto& q:m.inputFlitvQ[c]) queues.push_back(&q);
            for (auto& q:m.pPipeQ[c]) queues.push_back(&q);
            for (auto& q:m.pTxFifo[c]) queues.push_back(&q);
            for (int r=0;r<2;r++) {
                for (auto& q:m.pCandQ[c][r]) queues.push_back(&q);
                for (int ip=0;ip<12;ip++) {
                    queues.push_back(&m.inbufData[c][r][ip]);
                    queues.push_back(&m.internalReqQ[c][r][ip]);
                    if(mode) for(int o=0;o<12;o++) {
                        queues.push_back(&m.inbufDataByOut[c][r][ip][o]);
                        queues.push_back(&m.internalReqByOutQ[c][r][ip][o]);
                    }
                }
            }
        }
        // Every reachable queue location can keep hasWork true, not only r0.
        for(auto* q:queues) {
            q->emplace_back(); assert(m.oldHasWork() && m.newHasWork()); checks++;
            q->clear();
        }
        for(int c=0;c<4;c++) for(int r=0;r<2;r++) for(int o=0;o<12;o++) {
            m.outGrantVld[c][r][o]=true; assert(m.oldHasWork() && m.newHasWork()); checks++;
            m.outGrantVld[c][r][o]=false;
        }
        for(auto* ports:{&m.internalPorts,&m.internalPeerPorts,&m.devicePorts,&m.localPorts})
            for(auto& p:*ports) for(int c=0;c<4;c++) for(auto* q:{&p->rx[c],&p->tx[c]}) {
                q->push_back(1); assert(m.oldHasWork() && m.newHasWork()); checks++; q->clear();
            }
        // Deterministic mixed occupancies; false mode never writes dead banks.
        std::mt19937 rng(0x434849);
        for(int i=0;i<4096;i++) {
            std::vector<Model::Q*> touched;
            for(unsigned j=0,n=rng()%8;j<n;j++) {
                auto* q=queues[rng()%queues.size()]; q->emplace_back(); touched.push_back(q);
            }
            assert(m.oldHasWork()==m.newHasWork()); checks++;
            for(auto* q:touched) q->clear();
        }
        for(int c=0;c<4;c++) for(int r=0;r<2;r++) for(int ip=0;ip<12;ip++)
            for(int o=0;o<12;o++) for(auto dir:{Model::QueueDir::Rx,Model::QueueDir::Tx}) {
                auto ch=static_cast<ChannelType>(c);
                auto& q=mode?m.inbufDataByOut[c][r][ip][o]:m.inbufData[c][r][ip];
                for(int state=0;state<3;state++) {
                    if(state) { q.emplace_back(); q.front().out=state==1?o:(o+1)%12; q.front().dir=dir; }
                    Model::RoutedFlit oldResult; Model::QueueDir newResult=Model::QueueDir::Rx;
                    Model::RoutedFlit::copies=0;
                    bool a=m.oldPeek(ch,r,ip,o,oldResult);
                    assert(Model::RoutedFlit::copies==(a?1u:0u));
                    Model::RoutedFlit::copies=0;
                    bool b=m.newPeek(ch,r,ip,o,newResult);
                    assert(a==b && (!a || oldResult.dir==newResult));
                    assert(Model::RoutedFlit::copies==0); checks++;
                    q.clear();
                }
            }
        assert(!m.oldHasWork() && !m.newHasWork()); checks++;
    }
    std::cout << checks << " original/generated method comparisons passed\n";
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'router.cc'
            binary = Path(directory) / 'router-test'
            source.write_text(cpp)
            subprocess.run([compiler, '-O2', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            print(result.stdout.strip())


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gem5 = self.root / 'gem5'
        self.baseline = {change.CC: b'baseline cc', change.HH: b'baseline hh'}
        self.observed = {change.CC: b'observed cc', change.HH: b'baseline hh'}
        self.desired = {p: data + b' optimized' for p, data in self.observed.items()}
        for p, data in self.observed.items():
            target = self.gem5 / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.compat = self.root / 'build/extended-installed' / change.CC
        self.compat.parent.mkdir(parents=True, exist_ok=True)
        self.compat.write_bytes(self.observed[change.CC])
        mocks = [patch.object(installer, 'PROJECT', self.root),
                 patch.object(installer, 'BASELINE_SHA256', {p: hashlib.sha256(s).hexdigest() for p,s in self.baseline.items()}),
                 patch.object(installer, 'OBSERVED_CC_SHA256', hashlib.sha256(self.observed[change.CC]).hexdigest()),
                 patch.object(installer, 'observer_source', return_value='observed cc'),
                 patch.object(installer, 'patches', return_value={p:s.decode() for p,s in self.desired.items()}),
                 patch.object(installer.subprocess, 'check_output', side_effect=self.git)]
        for mock in mocks:
            mock.start(); self.addCleanup(mock.stop)

    def git(self, args, **kwargs):
        if args[-2:] == ['rev-parse', 'HEAD']: return change.COMMIT + '\n'
        return self.baseline[args[-1].split(':',1)[1]]

    def main(self, *extra):
        with patch.object(sys, 'argv', ['install', '--gem5', str(self.gem5), *extra]):
            installer.main()

    def test_snapshot_only_keeps_source_and_observer_unchanged(self):
        self.main('--snapshot-only')
        self.assertEqual(self.compat.read_bytes(), self.observed[change.CC])
        for p, s in self.observed.items(): self.assertEqual((self.gem5/p).read_bytes(),s)

    def test_idempotence_and_full_observer_reinstall(self):
        self.main()
        mtimes = {p:(self.gem5/p).stat().st_mtime_ns for p in self.observed}
        self.main()
        self.assertEqual(mtimes,{p:(self.gem5/p).stat().st_mtime_ns for p in self.observed})
        self.assertEqual(self.compat.read_bytes(),self.desired[change.CC])
        # Existing observer can restore CC while our header remains installed.
        (self.gem5/change.CC).write_bytes(self.observed[change.CC])
        self.compat.write_bytes(self.observed[change.CC])
        self.main()
        for p,s in self.desired.items(): self.assertEqual((self.gem5/p).read_bytes(),s)

    def test_independent_source_or_snapshot_edit_blocks_all_writes(self):
        for target in [self.gem5/change.HH,self.compat]:
            before=target.read_bytes(); target.write_bytes(before+b' independent')
            with self.assertRaisesRegex(ValueError,'independent'):
                self.main()
            self.assertEqual((self.gem5/change.CC).read_bytes(),self.observed[change.CC])
            target.write_bytes(before)

    def test_wrong_head_or_pinned_hash_rejected(self):
        with patch.object(installer.subprocess,'check_output',return_value='wrong'):
            with self.assertRaisesRegex(ValueError,'commit'): installer.plan(self.gem5)
        with patch.object(installer,'BASELINE_SHA256',{change.CC:'0'*64}):
            with self.assertRaisesRegex(ValueError,'pinned'): installer.plan(self.gem5)


if __name__ == '__main__':
    unittest.main()
