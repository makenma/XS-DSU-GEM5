#include "mem/cache/CHI/ChiRouterTester.hh"

#include <iostream>

#include "base/logging.hh"
#include "mem/cache/CHI/ChiRouterRefModel.hh"
#include "sim/sim_exit.hh"

namespace gem5::Chi
{
namespace {
constexpr std::array<unsigned, 4> Credits{4, 8, 4, 8};
constexpr uint32_t node(unsigned x, unsigned y, unsigned p, unsigned d) {
    return (x << 7) | (y << 4) | (p << 2) | d;
}
}

ChiRouterTester::ChiRouterTester(const Params &p)
    : ClockedObject(p), ruby::Consumer(this), router(p.router),
      tickEvent([this] { tick(); }, name() + ".tick"),
      scenario(p.scenario), repetitions(p.repetitions), limit(p.max_cycles),
      earlySchedule(p.early_schedule)
{
    for (unsigned i = 0; i < 24; ++i) {
        sources.emplace_back(std::make_unique<ChiCommonPort>(
            name() + ".sources" + std::to_string(i), this, i));
        sinks.emplace_back(std::make_unique<ChiCommonPort>(
            name() + ".sinks" + std::to_string(i), this, i));
    }
    if (!p.trace_file.empty()) {
        trace.open(p.trace_file);
        fatal_if(!trace, "Cannot create router trace %s", p.trace_file);
        trace << "event,cycle,transaction,port\n";
    }
    setup();
}

void
ChiRouterTester::setup()
{
    auto add = [&](unsigned source, unsigned output, unsigned ch,
                   uint64_t due, bool tx) {
        const unsigned p = output < 16 ? output / 4 : 0;
        const unsigned d = output < 16 ? output % 4 : 0;
        const unsigned dir = output < 16 ? 4 : (output - 16) % 4;
        const unsigned x = dir == 0 ? 2 : dir == 2 ? 0 : 1;
        const unsigned y = dir == 3 ? 2 : dir == 1 ? 0 : 1;
        transactions.push_back({source, output, ch, node(x, y, p, d),
                                due, 0, false, false, tx});
        pending[source][ch].push_back(transactions.size() - 1);
    };
    if (scenario == "alias") {
        for (unsigned ch = 0; ch < 4; ++ch) {
            add(20, 17, ch, 1, false);
            add(16, 16, ch, 2, false);
        }
    } else if (scenario == "timing") {
        for (unsigned ch = 0; ch < 4; ++ch) {
            add(0, 16, ch, 1, false);
            add(16, 16, ch, 25, false);
            add(0, 12, ch, 50, true);
            add(16, 12, ch, 75, true);
        }
    } else {
        fatal_if(scenario != "congestion" && scenario != "idle" &&
                 scenario != "throughput", "Unknown router test %s", scenario);
        for (unsigned n = 0; n < repetitions; ++n)
            for (unsigned src = 0; src < 24; ++src)
                for (unsigned ch = 0; ch < 4; ++ch) {
                    // One hot local output exercises candidate/merge/FIFO
                    // backpressure. Other scenarios spread over all ports.
                    const unsigned out = scenario == "congestion" ? 12 :
                        (src * 7 + n * 3 + ch) % 20;
                    const uint64_t due = scenario == "idle" ?
                        1 + (n / 2) * 250 : 1 + n;
                    add(src, out, ch, due, (src + n) & 1);
                }
    }
}

void ChiRouterTester::startup() { wakeup(); }
void ChiRouterTester::wakeup() {
    if (!finished && !tickEvent.scheduled())
        schedule(tickEvent, clockEdge(Cycles(1)));
}
void ChiRouterTester::print(std::ostream &out) const { out << name(); }
Port &ChiRouterTester::getPort(const std::string &name, PortID idx) {
    panic_if(idx < 0 || idx >= 24, "Router tester port index %d", idx);
    if (name == "sources") return *sources[idx];
    if (name == "sinks") return *sinks[idx];
    return ClockedObject::getPort(name, idx);
}

void
ChiRouterTester::record(char event, unsigned id, unsigned port)
{
    for (uint64_t value : {uint64_t(event), cycle, uint64_t(id), uint64_t(port)}) {
        digest ^= value;
        digest *= 1099511628211ULL;
    }
    if (trace) trace << event << ',' << cycle << ',' << id << ',' << port << '\n';
}

void
ChiRouterTester::inject(unsigned id)
{
    auto &tr = transactions[id];
    const auto ch = static_cast<ChannelType>(tr.channel);
    if (!(tr.tx ? sources[tr.source]->peerHasTxCredit(ch) :
                  sources[tr.source]->peerHasRxCredit(ch)))
        return;
    std::array<uint8_t, 64> data;
    for (unsigned b = 0; b < data.size(); ++b)
        data[b] = (id * 13 + b * 7) & 255;
    FlitVariant flit;
    switch (ch) {
      case REQ: flit = RawReq{}; break;
      case RSP: flit = RawRsp{}; break;
      case SNP: flit = RawSnp{}; break;
      case DAT: {
        RawDat dat;
        dat.data = {data.data(), data.size()};
        flit = dat;
        break;
      }
      default: panic("Invalid CHI channel");
    }
    std::visit([&](auto &f) {
        f.srcid = tr.source < 16 ? node(1, 1, tr.source / 4, tr.source % 4) :
            node(3, 3, (tr.source - 16) / 4, (tr.source - 16) % 4);
        f.tgtid = tr.target;
        f.txnid = id;
    }, flit);
    bool sent = tr.tx ? sources[tr.source]->enqueueTx(ch, flit) :
                        sources[tr.source]->enqueueRx(ch, flit);
    data.fill(0xff); // The receiving port must have frozen borrowed DAT bytes.
    if (sent) {
        tr.accepted = cycle;
        tr.sent = true;
        ++accepted;
        record('A', id, tr.source);
    }
}

void
ChiRouterTester::drain(unsigned output, ChannelType ch, bool tx)
{
    auto &port = *sinks[output];
    if (!(tx ? port.hasTxFlit(ch, true) : port.hasRxFlit(ch, true)))
        return;
    const auto flit = tx ? port.getTxFlit(ch) : port.getRxFlit(ch);
    panic_if(!flit, "Lost router output");
    const unsigned id = std::visit([](const auto &f) { return f.txnid; }, *flit);
    panic_if(id >= transactions.size(), "Unexpected router transaction %u", id);
    auto &tr = transactions[id];
    // A dual lane routes a directional output to its corresponding EXT wire.
    const bool output_match = output == tr.output ||
        (output >= 20 && tr.output >= 16 && output - 4 == tr.output);
    panic_if(!tr.sent || tr.received || !output_match || tr.channel != ch ||
             tr.tx != tx, "Router misdelivery/duplicate transaction %u", id);
    if (const auto *dat = std::get_if<RawDat>(&*flit)) {
        panic_if(dat->payloadSize() != 64, "Router DAT length changed");
        for (unsigned b = 0; b < 64; ++b)
            panic_if(dat->payloadData()[b] != ((id * 13 + b * 7) & 255),
                     "Router DAT ownership/content corrupt id=%u byte=%u", id, b);
    }
    if (scenario == "timing") {
        const unsigned expected = (tr.source < 16 ? 5 : 4) +
                                  (tr.output < 16 ? 1 : 0);
        panic_if(cycle - tr.accepted != expected,
                 "Router timing id=%u got=%llu expected=%u", id,
                 cycle - tr.accepted, expected);
    }
    tr.received = true;
    ++received;
    record('D', id, output);
}

void
ChiRouterTester::tick()
{
    cycle = curCycle();
    if (earlySchedule)
        schedule(tickEvent, clockEdge(Cycles(1)));
    panic_if(cycle > limit, "Router watchdog accepted=%u received=%u total=%u",
             accepted, received, transactions.size());
    if (scenario == "congestion" && cycle == 199) {
        // All wire credits plus finite local input/merge/output slots bound
        // accepted traffic while every sink withholds its credits.
        panic_if(accepted > 1600 || received != 0,
                 "Unbounded router buffering accepted=%u received=%u",
                 accepted, received);
    }
    if (scenario != "congestion" || cycle >= 200)
        for (unsigned out = 0; out < 24; ++out)
            for (unsigned ch = 0; ch < 4; ++ch) {
                drain(out, static_cast<ChannelType>(ch), false);
                drain(out, static_cast<ChannelType>(ch), true);
            }
    for (unsigned src = 0; src < sources.size(); ++src)
        for (unsigned ch = 0; ch < 4; ++ch)
            panic_if(sources[src]->hasRxFlit(static_cast<ChannelType>(ch)) ||
                     sources[src]->hasTxFlit(static_cast<ChannelType>(ch)),
                     "Router redirected blocked local output to source endpoint %u channel %u",
                     src, ch);
    // At most one injection per physical source/channel per cycle. Retrying
    // a blocked source preserves its flit order.
    for (auto &source : pending)
        for (auto &channel : source) {
            if (channel.empty()) continue;
            const unsigned id = channel.front();
            if (transactions[id].due > cycle) continue;
            inject(id);
            if (transactions[id].sent) channel.pop_front();
        }
    if (received == transactions.size() && router->idle()) {
        // Verify that every wire credit was returned exactly once, including
        // TX input and input credits retained while internal buffers stall.
        for (unsigned src = 0; src < 24; ++src) {
            auto &ingress = dynamic_cast<ChiCommonPort &>(router->getPort(
                src < 16 ? "device_ports" : "internal_peer_ports",
                src < 16 ? src : src - 16));
            for (unsigned ch = 0; ch < 4; ++ch)
                panic_if(ingress.availableRxCredit(static_cast<ChannelType>(ch)) != Credits[ch] ||
                         ingress.availableTxCredit(static_cast<ChannelType>(ch)) != Credits[ch],
                         "Router leaked ingress credit source=%u ch=%u", src, ch);
            for (unsigned ch = 0; ch < 4; ++ch)
                panic_if(sinks[src]->availableRxCredit(static_cast<ChannelType>(ch)) != Credits[ch] ||
                         sinks[src]->availableTxCredit(static_cast<ChannelType>(ch)) != Credits[ch],
                         "Router leaked egress credit output=%u ch=%u", src, ch);
        }
        finished = true;
        if (tickEvent.scheduled()) deschedule(tickEvent);
        if (trace) trace.flush();
        std::cout << "CHI_ROUTER_RESULT scenario=" << scenario << " flits="
                  << received << " cycles=" << cycle << " digest=" << digest << '\n';
        exitSimLoop("CHI router regression passed");
        return;
    }
    wakeup();
}
} // namespace gem5::Chi
