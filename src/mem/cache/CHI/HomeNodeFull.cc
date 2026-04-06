#ifndef __HOMENODEFULL__CC__
#define __HOMENODEFULL__CC__

#include "mem/cache/CHI/HomeNodeFull.hh"

#include <iostream>

#include "debug/HomeLinkLayer.hh"

namespace gem5::Chi
{


HomeNodeFull::HomeNodeFull(const HomeNodeFullParams& p)
    : BasicChiComponent(p),
    Consumer(this),
    linklayer( this),
    rxport(p.name + ".rxport", static_cast<ruby::Consumer*>(this),/*PortID*/ 0)
{
    std::cout<<"HomeNodeFull constructed with name: "<<p.name<<std::endl;
    std::cout << "consumer ptr=" << (void*)static_cast<ruby::Consumer*>(this) << "\n";

    linklayer.setRxPort(&rxport);
}

void
HomeNodeFull::wakeup()
{
    //TODO: abstract class here wirte the whole process later
    std::cout<<"HomeNodeFull wakeup called"<<std::endl;
    linklayer.wakeup();




}

void
HomeNodeFull::print(std::ostream& out) const
{
    out << "HomeNodeFull(" << name() << ")";
}

Port&
HomeNodeFull::getPort(const std::string& if_name, PortID idx)
{
    if (if_name == "rxport") {
         std::cout<<"HomeNodeFull get rxport"<<std::endl;
        return rxport;
    }
    return BasicChiComponent::getPort(if_name, idx);
}




}

// namespace gem5{
// Chi::HomeNodeFull*
// HomeNodeFullParams::create() const
// {
//     return new Chi::HomeNodeFull(*this);
// }
// }

#endif
