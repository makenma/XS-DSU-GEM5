#include "dev/ai_mesh/mesh_moe_gate.hh"

#include <sstream>

#include "base/logging.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

void MoeInsertionGate::arm(const RegionSpec &spec)
{
    fatal_if(spec.layer_id == 0, "MoE region gate needs a layer id");
    fatal_if(spec.insert_after_command_id == 0 ||
             spec.resume_before_command_id == 0,
             "MoE region gate needs both static commands");
    fatal_if(spec.insert_after_command_id >= spec.resume_before_command_id,
             "MoE region gate commands are out of order");
    if (regions.count(spec.layer_id) != 0)
        fatal("core already has an armed MoE region for layer %u",
              spec.layer_id);
    Region region;
    region.spec = spec;
    region.phase = RegionPhase::ARMED;
    regions[spec.layer_id] = region;
    order.push_back(spec.layer_id);
}

void MoeInsertionGate::noteIssued(uint32_t command_id)
{
    for (uint32_t layer_id : order) {
        Region &region = regions[layer_id];
        if (region.phase != RegionPhase::ARMED)
            continue;
        if (command_id == region.spec.insert_after_command_id)
            region.phase = RegionPhase::REACHED;
    }
}

void MoeInsertionGate::release(uint32_t layer_id)
{
    auto it = regions.find(layer_id);
    fatal_if(it == regions.end(), "releasing an unarmed MoE region gate");
    fatal_if(it->second.phase == RegionPhase::RELEASED,
             "MoE region gate for layer %u already released", layer_id);
    fatal_if(it->second.phase != RegionPhase::REACHED,
             "MoE region gate for layer %u released before the insertion "
             "point", layer_id);
    it->second.phase = RegionPhase::RELEASED;
}

bool MoeInsertionGate::blockedIndex(uint32_t command_index) const
{
    for (uint32_t layer_id : order) {
        const Region &region = regions.at(layer_id);
        if (region.phase != RegionPhase::REACHED)
            continue;
        if (command_index >= region.spec.resume_before_index)
            return true;
    }
    return false;
}

bool MoeInsertionGate::layerArmed(uint32_t layer_id) const
{
    return regions.count(layer_id) != 0;
}

MoeInsertionGate::RegionPhase MoeInsertionGate::phase(uint32_t layer_id) const
{
    auto it = regions.find(layer_id);
    return it == regions.end() ? RegionPhase::IDLE : it->second.phase;
}

bool MoeInsertionGate::allReleased() const
{
    for (uint32_t layer_id : order)
        if (regions.at(layer_id).phase != RegionPhase::RELEASED)
            return false;
    return !order.empty();
}

std::string MoeInsertionGate::describe() const
{
    std::ostringstream out;
    for (uint32_t layer_id : order) {
        const Region &region = regions.at(layer_id);
        out << "layer=" << layer_id
            << " region=" << region.spec.region_id
            << " insert_after=" << region.spec.insert_after_command_id
            << " resume_before=" << region.spec.resume_before_command_id
            << " resume_index=" << region.spec.resume_before_index
            << " phase=" << static_cast<int>(region.phase) << "; ";
    }
    return out.str();
}

} // namespace ai_mesh
} // namespace gem5
