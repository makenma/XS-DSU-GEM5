#ifndef __HOMENODEFULL__HH__
#define __HOMENODEFULL__HH__

#include "mem/cache/CHI/HnfCoherencyController.hh"
#include "mem/cache/CHI/HomeLinkLayer.hh"
#include "mem/cache/CHI/SlcSnoopFilter.hh"
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
        bool hasLinkWork() const;
        DrainState drain() override;
        void drainResume() override;
        bool drainD1Complete() const { return d1Complete; }
        bool drainD2Active() const { return d2Active; }
        //HomeNodeFull* create() const;
    private:
        SlcSnoopFilter* const slcsf;
        HnfCoherencyController cc;
        HomeLinkLayer   linklayer;
        ChiCommonPort   rxport;
        bool d1Active = false;
        bool d1Complete = false;
        bool d2Active = false;

        void advanceDrain();






};

}//namespace gem5

#endif
