#ifndef __BASICCHICHICOMPONENT__CC__
#define __BASICCHICHICOMPONENT__CC__



#include "mem/cache/CHI/base/BasicChiComponent.hh"

namespace gem5::Chi
{

BasicChiComponent::BasicChiComponent(const Params &p)
  : ClockedObject(p),
    m_x(p.x),
    m_y(p.y),
    m_port(p.port),
    m_device(p.device)
{
    m_type = parseNodeType(p.node_type);
}

BasicChiComponent::NodeType
BasicChiComponent::parseNodeType(const std::string& s)
{
    if (s == "rni") return BasicChiComponent::NodeType::RNI;
    if (s == "rnf") return BasicChiComponent::NodeType::RNF;
    if (s == "hni") return BasicChiComponent::NodeType::HNI;
    if (s == "hnf") return BasicChiComponent::NodeType::HNF;
    if (s == "router") return BasicChiComponent::NodeType::ROUTER;
    if (s == "sn") return BasicChiComponent::NodeType::SN;
    return BasicChiComponent::NodeType::UNKNOWN;
}

const char*
BasicChiComponent::nodeTypeStr(NodeType t)
{
    switch (t) {
      case NodeType::RNI:    return "rni";
      case NodeType::RNF:    return "rnf";
      case NodeType::HNI:    return "hni";
      case NodeType::HNF:    return "hnf";
      case NodeType::ROUTER: return "router";
      case NodeType::SN:     return "sn";
      default:               return "unknown";
    }
}



void
BasicChiComponent::init()
{
    ClockedObject::init();
}

void
BasicChiComponent::print(std::ostream& out) const
{
    out << name()
        << " type=" << nodeTypeStr(m_type)
        << " coord={x:" << m_x
        << " y:" << m_y
        << " port:" << m_port
        << " device:" << m_device
        << "}";
}

} // namespace gem5::chi

#endif