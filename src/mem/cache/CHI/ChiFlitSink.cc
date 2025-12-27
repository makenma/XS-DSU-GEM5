#ifndef __CHIFLITSINK_HH__
#define __CHIFLITSINK_HH__

#include "mem/cache/CHI/ChiFlitSink.hh"

namespace gem5
{
ChiFlitSink::ChiFlitSink(const Params &p)
    : ClockedObject(p),
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

}

#endif