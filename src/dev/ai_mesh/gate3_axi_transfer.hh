#ifndef DEV_AI_MESH_GATE3_AXI_TRANSFER_HH
#define DEV_AI_MESH_GATE3_AXI_TRANSFER_HH

#include <cstdint>
#include <vector>

#include "mem/axi/axi_types.hh"

namespace gem5
{
namespace ai_mesh
{

struct Gate3AxiSegment
{
    axi::AxiAddressRequest request;
    uint64_t logicalOffset = 0;
    uint64_t logicalBytes = 0;
};

class Gate3AxiTransferPlanner
{
  public:
    Gate3AxiTransferPlanner(uint32_t dataBusBytes,
                            uint16_t maxBurstBeats);

    std::vector<Gate3AxiSegment> plan(
        uint64_t address, uint64_t bytes, uint32_t axiId, uint8_t qos,
        uint32_t maxBeatBytes = 0) const;
    std::vector<axi::AxiWBeat> packWrite(
        const std::vector<uint8_t> &data,
        const Gate3AxiSegment &segment) const;
    void appendReadBeat(const Gate3AxiSegment &segment, uint16_t beatIndex,
                        const std::vector<uint8_t> &functionalData,
                        std::vector<uint8_t> &output) const;

  private:
    uint32_t dataBusBytes;
    uint16_t maxBurstBeats;
};

}
}

#endif
