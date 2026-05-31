#ifndef __CHIFLITSINK_HH__
#define __CHIFLITSINK_HH__

#include "mem/cache/CHI/ChiFlitSink.hh"

#include "base/cprintf.hh"
#include "base/trace.hh"
#include "debug/ChiFlitSink.hh"

namespace gem5
{

namespace Chi
{
ChiFlitSink::ChiFlitSink(const Params &p)
    : ClockedObject(p),
    Consumer(this),
      inPort(csprintf("%s.chi_side", name()), *this),
      numReq(0)
{
}


Port&
ChiFlitSink::getPort(const std::string &if_name, PortID idx)
{
    if (if_name == "chi_side") {
        return inPort;
    }
    return SimObject::getPort(if_name, idx);

}


void
ChiFlitSink::wakeup()
{
    for (int c = 0; c < static_cast<int>(ChannelType::NUM_CHANNELS); ++c) {
        const auto ch = static_cast<ChannelType>(c);

        while (inPort.hasRxFlit(ch)) {
            auto flit = inPort.getRxFlit(ch);
            if (!flit) {
                break;
            }
            ++numReq;
            std::visit([&](const auto &f) {
                DPRINTF(ChiFlitSink,
                        "sink RX ch=%u opcode=0x%x src=%u tgt=%u txn=%u "
                        "count=%llu\n",
                        static_cast<unsigned>(ch), f.opcode, f.srcid,
                        f.tgtid, f.txnid,
                        static_cast<unsigned long long>(numReq));
            }, *flit);
        }

        while (inPort.hasTxFlit(ch)) {
            auto flit = inPort.getTxFlit(ch);
            if (!flit) {
                break;
            }
            ++numReq;
            std::visit([&](const auto &f) {
                DPRINTF(ChiFlitSink,
                        "sink TX ch=%u opcode=0x%x src=%u tgt=%u txn=%u "
                        "count=%llu\n",
                        static_cast<unsigned>(ch), f.opcode, f.srcid,
                        f.tgtid, f.txnid,
                        static_cast<unsigned long long>(numReq));
            }, *flit);
        }
    }


}

void
ChiFlitSink::print(std::ostream& out) const
{
    out << "ChiFlitSink(" << name() << ")";
}

}
}

#endif
