#ifndef __BASICCHICHICOMPONENT__HH__
#define __BASICCHICHICOMPONENT__HH__

#include <typeindex>
#include <variant>

#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/BasicChiComponent.hh"
#include "sim/clocked_object.hh"

namespace gem5::Chi
{


class BasicChiComponent : public ClockedObject
{
    public:

    enum class NodeType : uint8_t
    {
        RNI,
        RNF,
        HNI,
        HNF,
        ROUTER,
        SN,
        UNKNOWN
    };

    enum ChiPipelineStage : uint8_t
    {
        STAGE_H0 = 0,
        STAGE_H1,
        STAGE_H2,
        STAGE_H3,
        STAGE_H4,
        STAGE_H5,
        STAGE_H6,
        STAGE_H7,
        STAGE_H8,
        STAGE_H9,
        STAGE_H10,
        STAGE_H11,
        STAGE_H12,
        STAGE_H13,
        STAGE_H14,
        STAGE_HX,
        STAGE_HX1,
        STAGE_HX2,
        STAGE_HX3,
        STAGE_DONE,
        NUM_PIPELINE_STAGES
    };




    PARAMS(BasicChiComponent);
    BasicChiComponent(const Params &p);

    void init();

    void print(std::ostream& out) const;

    protected:

    uint16_t m_x = 0;
    uint16_t m_y = 0;
    uint16_t m_port = 0;
    uint16_t m_device = 0;

    NodeType m_type = NodeType::UNKNOWN;

    private:
        static const char* nodeTypeStr(NodeType t);
        static NodeType parseNodeType(const std::string& s);


};

inline std::ostream&
operator<<(std::ostream& out, const BasicChiComponent& obj)
{
    obj.print(out);
    out << std::flush;
    return out;
}


}// namespace gem5::chi



#endif
