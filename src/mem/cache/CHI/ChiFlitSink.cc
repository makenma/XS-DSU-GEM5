#ifndef __CHIFLITSINK_HH__
#define __CHIFLITSINK_HH__

#include "mem/cache/CHI/ChiFlitSink.hh"

namespace gem5
{

namespace Chi
{
ChiFlitSink::ChiFlitSink(const Params &p)
    : ClockedObject(p),
    Consumer(this),
      inPort(csprintf("%s.chi_side", static_cast<ruby::Consumer*>(this),name()), *this),
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
    //TODO: abstract class here wirte the whole process later
    std::cout<<"ChiFlitSink wakeup called"<<std::endl;




}

void
ChiFlitSink::print(std::ostream& out) const
{
    out << "ChiFlitSink(" << name() << ")";
}

}
}

#endif
