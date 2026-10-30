// Standalone host-performance workload. No gem5 timing is approximated here:
// the production model consumes the same requests and clock edges as in HNF.
#include "mem/cache/CHI/SlcSfModel.hh"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>

using namespace gem5::Chi;

namespace
{
struct Digest
{
    uint64_t value = 14695981039346656037ULL;
    void add(uint64_t n) {
        for (unsigned i = 0; i < 8; ++i) {
            value = (value ^ (n & 255)) * 1099511628211ULL;
            n >>= 8;
        }
    }
    void state(SlcState s) { add(unsigned(s.mesi)); add(s.unique); }
    void seq(const SlcSfSeqRequest &s) {
        add(s.id); add(s.createdCycle); add(s.readyCycle); add(s.addr); add(s.ns);
        state(s.state); add(s.rnf); add(s.broadcast);
        for (bool bit : s.targets) add(bit);
    }
    void result(const SlcSfResult &r) {
        add(r.token); add(r.srcid); add(r.txnid); add(r.cycle);
        add(unsigned(r.kind)); add(unsigned(r.replay)); add(r.beat); add(r.victim);
        const auto &l = r.lookup;
        add(l.slcHit); add(l.sfHit); add(l.seqHit);
        state(l.slcState); state(l.sfState); add(l.slcRnf); add(l.sfRnf);
        for (bool bit : l.snoopTargets) add(bit);
        add(l.snoopOpcode); add(l.broadcast); add(l.selfCancel);
        add(l.memoryRead); add(l.noFill); add(l.dataExpected); add(l.victim);
        add(l.victimAddr); add(l.victimNs); state(l.victimState);
        for (auto byte : r.data) add(byte);
    }
};
}

int main(int argc, char **argv)
{
    const uint64_t iterations = argc > 1 ? std::stoull(argv[1]) : 1000000;
    const bool sparse = argc > 2 && std::string(argv[2]) == "sparse";
    SlcSfModel::Config cfg;
    cfg.rnfCount = 4; cfg.sfSets = 4096;
    SlcSfModel model(cfg);
    Digest hash;
    uint64_t accepted = 0, replayed = 0, committed = 0, results = 0;
    uint64_t token = 1, cycle = 0, seqId = 0, seqStart = 0;
    auto consume = [&] {
        while (auto r = model.popResult()) {
            hash.result(*r); ++results;
            replayed += r->kind == SlcSfResultKind::Replay;
            committed += r->kind == SlcSfResultKind::CommitDone;
        }
    };
    const auto start = std::chrono::steady_clock::now();
    for (uint64_t i = 0; i < iterations; ++i) {
        cycle += sparse ? 4096 : 1;
        model.advance(cycle);
        consume();
        if (seqId && cycle >= seqStart + 19) {
            if (auto s = model.seqSnapshot(seqId)) hash.seq(*s);
            model.completeSeq(seqId); seqId = 0;
        }
        if (auto s = model.pendingSeq()) {
            hash.add(cycle); hash.seq(*s);
            model.acceptSeq(s->id, false);
            seqId = s->id; seqStart = cycle;
        }
        SlcSfRequest req;
        req.token = token; req.txnid = token; req.srcid = token & 3;
        // Four neighboring sets, many tags: both history replays and eviction
        // pressure remain present after warmup, instead of only cold misses.
        req.addr = (((token * 17) % 96) * 4096 + (token / 11) % 4) * 64;
        req.ns = (token % 7) != 0; req.rnf = token & 3;
        req.opcode = token % 3 == 0 ? 3 : 7;
        switch (token % 7) {
          case 0: case 1:
            req.op = SlcSfOp::Fill;
            req.state = {token & 1 ? SlcMesi::Modified : SlcMesi::Exclusive, false};
            for (unsigned b = 0; b < 64; ++b) req.data[b] = token + b * 3;
            break;
          case 2:
            req.op = SlcSfOp::UpdateSf;
            req.state = {SlcMesi::Exclusive, true}; break;
          case 3: req.op = SlcSfOp::FlushBoth; break;
          default: break;
        }
        req.victimConflict = token % 47 == 0;
        const bool ok = model.tryAccept(req, cycle);
        hash.add(cycle); hash.add(ok); hash.add(model.seqOccupancy());
        if (ok) { ++accepted; ++token; }
    }
    for (unsigned drain = 0; drain < 512 && model.hasWork(); ++drain) {
        model.advance(++cycle); consume();
        if (seqId && cycle >= seqStart + 19) {
            model.completeSeq(seqId); seqId = 0;
        }
        if (auto s = model.pendingSeq()) {
            model.acceptSeq(s->id, false); seqId = s->id; seqStart = cycle;
        }
    }
    if (model.hasWork()) { std::cerr << "workload did not drain\n"; return 1; }
    const auto end = std::chrono::steady_clock::now();
    // Include final architectural arrays and policy state, outside timed work.
    for (std::size_t s = 0; s < cfg.slcSets; ++s)
        for (std::size_t w = 0; w < cfg.slcWays; ++w) {
            const auto &t = model.l3TagRam().at(s, w);
            hash.add(t.paTag); hash.add(t.ns); hash.state(t.state());
            hash.add(t.rnfId); hash.add(t.rrpv);
            for (auto b : model.l3DataRam().at(s, w).bytes) hash.add(b);
        }
    for (std::size_t s = 0; s < cfg.sfSets; ++s)
        for (std::size_t w = 0; w < cfg.sfWays; ++w) {
            const auto &t = model.sfTagRam().at(s, w);
            hash.add(t.paTag); hash.add(t.ns); hash.state(t.state());
            hash.add(t.rnfId); hash.add(t.slcIsSource);
            for (bool b : t.sfRnfVec) hash.add(b);
        }
    hash.add(model.replacementPsel()); hash.add(model.replacementBrripCounter());
    std::cout << "{\"mode\":\"" << (sparse ? "sparse" : "dense")
              << "\",\"iterations\":" << iterations << ",\"cycles\":" << cycle
              << ",\"accepted\":" << accepted << ",\"replayed\":" << replayed
              << ",\"committed\":" << committed << ",\"results\":" << results
              << ",\"digest\":\"" << std::hex << hash.value << std::dec
              << "\",\"seconds\":" << std::chrono::duration<double>(end - start).count()
              << "}\n";
}
