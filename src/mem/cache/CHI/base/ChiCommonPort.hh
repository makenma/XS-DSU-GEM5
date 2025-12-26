#ifndef __CHICOMMONPORT__HH__
#define __CHICOMMONPORT__HH__

#include <queue>

#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/port.hh"

namespace gem5
{

class ChiCommonPort: public Port
{

    static constexpr size_t NUM_CH =
        static_cast<size_t>(ChannelType::NUM_CHANNELS);

    public:
        explicit ChiCommonPort( const std::string& name,
                                PortID id=InvalidPortID,
                                const std::array<uint8_t,NUM_CH>& init=
                           std::array<uint8_t, NUM_CH>{4,8,4,8} )
            :Port(name,id),
            CreditValue(init),
            CreditLimit(init)
        {}

        // =========================
        // Credit check function
        // =========================
        void checkCredit(ChannelType ch) const
        {
            const size_t idx = static_cast<size_t>(ch);
            assert(idx < NUM_CH);
            assert(CreditValue[idx] <= CreditLimit[idx] &&
                   "CHI credit overflow!");
        }

        void checkAllCredits() const
        {
            for (size_t i = 0; i < NUM_CH; ++i) {
                assert(CreditValue[i] <= CreditLimit[i] &&
                       "CHI credit overflow!");
            }
        }

    private:
        std::array<uint8_t, NUM_CH> CreditValue{};
        std::array<uint8_t, NUM_CH> CreditLimit{};

        std::queue<RawReq> req;
        std::queue<RawRsp> rsp;
        std::queue<RawSnp> snp;
        std::queue<RawDat> dat;






}



}//namespace gem5

#endif
