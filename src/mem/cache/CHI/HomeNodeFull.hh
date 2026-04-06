#ifndef __HOMENODEFULL__HH__
#define __HOMENODEFULL__HH__

#include "mem/cache/CHI/HomeLinkLayer.hh"
#include "mem/cache/CHI/base/BasicChiComponent.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/HomeNodeFull.hh"

namespace gem5::Chi
{





class HomeNodeFull : public BasicChiComponent , public ruby::Consumer
{
    public:
        HomeNodeFull(const HomeNodeFullParams& p);

        void wakeup() override ;
        void print(std::ostream& out) const override;
        Port& getPort(const std::string& if_name,
                  PortID idx = InvalidPortID) override;
        //HomeNodeFull* create() const;
    private:
        HomeLinkLayer   linklayer;
        ChiCommonPort   rxport;






};

}//namespace gem5

#endif
