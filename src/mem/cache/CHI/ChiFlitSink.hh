
#ifndef __CHIFLITSINK__HH__
#define __CHIFLITSINK__HH__


#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "sim/clocked_object.hh"
#include "sim/sim_object.hh"

namespace gem5
{

namespace Chi
{

class ChiFlitSink : public ClockedObject,public ruby::Consumer
{
  public:
    ChiFlitSink(const Params &p);
    void wakeup() override;
    void print(std::ostream& out) const override;

    class InPort : public ChiCommonPort
    {
      public:
        InPort(const std::string &name, ChiFlitSink &owner)
            : ChiCommonPort(name,static_cast<ruby::Consumer*>(&owner),1), owner(owner) {}
        // 假设你有类似 recvFlit/recvTiming 之类的接口

      private:
        ChiFlitSink &owner;
    };

    InPort inPort;

    Port &getPort(const std::string &if_name, PortID idx=InvalidPortID) override;


  private:
    uint64_t numReq = 0;
};


}

} // namespace gem5

#endif
