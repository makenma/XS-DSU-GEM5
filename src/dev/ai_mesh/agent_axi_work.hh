#ifndef DEV_AI_MESH_AGENT_AXI_WORK_HH
#define DEV_AI_MESH_AGENT_AXI_WORK_HH

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_runtime_types.hh"
#include "dev/ai_mesh/gate3_axi_transfer.hh"
#include "mem/axi/axi_types.hh"

namespace gem5
{
namespace ai_mesh
{

template <size_t N>
std::vector<uint8_t>
toVector(const std::array<uint8_t, N> &data)
{
    return std::vector<uint8_t>(data.begin(), data.end());
}


struct Gate3WriteWork
{
    std::string object;
    std::string control;
    std::string direction;
    std::optional<uint64_t> absoluteSeq;
    std::optional<uint64_t> requestId;
    std::optional<uint64_t> cookie;
    uint64_t address = 0;
    uint32_t axiId = 0;
    uint8_t qos = 0;
    axi::AxiAddressRequest request;
    std::vector<axi::AxiWBeat> beats;
    std::vector<uint64_t> semanticBytes;
    std::vector<Gate3AxiSegment> segments;
    std::vector<uint8_t> data;
    size_t segmentIndex = 0;
    size_t nextBeat = 0;
    bool awAccepted = false;
    uint64_t txn = 0;
};

struct Gate3ReadWork
{
    std::string object;
    std::string control;
    std::string direction;
    std::optional<uint64_t> absoluteSeq;
    std::optional<uint64_t> requestId;
    std::optional<uint64_t> cookie;
    uint64_t address = 0;
    uint32_t axiId = 0;
    uint8_t qos = 0;
    axi::AxiAddressRequest request;
    std::vector<Gate3AxiSegment> segments;
    size_t segmentIndex = 0;
    uint16_t beatIndex = 0;
    uint16_t beatCount = 0;
    uint64_t bytes = 0;
    uint64_t bytesConsumed = 0;
    std::vector<uint8_t> data;
    bool accepted = false;
    bool sawError = false;
    uint64_t txn = 0;
    std::optional<SqIntakeId> sqIntakeId;
};

}
}
#endif
