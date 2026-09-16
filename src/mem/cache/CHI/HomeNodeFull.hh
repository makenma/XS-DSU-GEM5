#ifndef __HOMENODEFULL__HH__
#define __HOMENODEFULL__HH__

#include "mem/cache/CHI/HomeLinkLayer.hh"
#include "mem/cache/CHI/HomePocq.hh"
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
        void releasePocqQos(PoolPriority priority);
        //HomeNodeFull* create() const;
    private:
        // HomePocq 必须先于 HomeLinkLayer 构造，LinkLayer 保存它的
        // 非 owning 指针并在 H1 做准入。
        HomePocq        pocq;
        HomeLinkLayer   linklayer;
        ChiCommonPort   rxport;






};

}//namespace gem5

#endif
