#ifndef __HOMENODEFULL__HH__
#define __HOMENODEFULL__HH__

#include "mem/cache/CHI/HomeLinkLayer.hh"
#include "mem/cache/CHI/HomePocq.hh"
#include "mem/cache/CHI/SlcSf.hh"
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
        SlcSf &slcSf() { return *slcsf; }
        HomePocq &pocqModel() { return pocq; }
        std::size_t pocqActiveEntries() const { return pocq.activeEntries(); }
        uint32_t nodeId() const {
            return (m_x << 7) | (m_y << 4) | (m_port << 2) | m_device;
        }
        uint32_t snNodeId() const { return snId; }
        uint32_t rnfNodeId(uint32_t index) const;
        uint32_t rnfIndex(uint32_t node) const;
        DrainState drain() override;
        void serialize(CheckpointOut &cp) const override;
        void unserialize(CheckpointIn &cp) override;
        bool transmit(ChannelType channel, const FlitVariant &flit) {
            return linklayer.transmit(channel, flit);
        }
    private:
        // HomePocq 必须先于 HomeLinkLayer 构造，LinkLayer 保存它的
        // 非 owning 指针并在 H1 做准入。
        SlcSf *slcsf;
        uint32_t snId;
        std::vector<uint32_t> rnfNodeIds;
        HomePocq        pocq;
        HomeLinkLayer   linklayer;
        ChiCommonPort   rxport;
        EventFunctionWrapper drainEvent;
        bool checkpointReady();
        void pollDrain();






};

}//namespace gem5

#endif
