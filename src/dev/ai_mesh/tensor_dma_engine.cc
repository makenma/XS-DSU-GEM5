#include "dev/ai_mesh/tensor_dma_engine.hh"

#include <algorithm>

#include <algorithm>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"
#include "dev/ai_mesh/mock_axi_transport.hh"
#include "params/TensorDmaEngine.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

TensorDmaEngine::TensorDmaEngine(const Params &p)
    : DmaEngineBase(p),
      core_id(p.core_id),
      setup_cycles(p.setup_cycles),
      descriptor_queue_depth(p.descriptor_queue_depth),
      max_outstanding(p.max_outstanding),
      transport(p.transport)
{}

void
TensorDmaEngine::bindOwner(MeshDummyCore *core, const RuntimeArch *arch_)
{
    owner = core;
    arch = arch_;
}

const std::map<uint32_t, ActualTraffic> &
TensorDmaEngine::actualTraffic() const
{
    return transport->actualTraffic();
}

bool TensorDmaEngine::submit(const DecodedDmaDescriptor &descriptor, Tick issue_tick)
{
    if (outstanding >= max_outstanding || outstanding >= descriptor_queue_depth)
        return false;

    // Completion = engine setup + transport burst latency (base latency per
    // burst + one cycle per beat, from the transport's own clock model) +
    // the SRAM service stall of the LOCAL endpoint.  Burst counts come from
    // the REMOTE-side plan (spec 7.2 shaping address).
    Tick commit_tick = issue_tick + clockPeriod();
    uint32_t bursts = 0;
    uint64_t beats = 0;
    Tick sram_stall = 0;
    if (owner && descriptor.useful_bytes > 0) {
        // Local SRAM port direction: LOAD/PREFETCH/FILL write the local
        // tile; STORE/P2P read their local source from it.  Zero-length
        // descriptors make no SRAM reservation.
        const bool local_write =
            descriptor.kind == mesh_abi::kDmaKindLOAD ||
            descriptor.kind == mesh_abi::kDmaKindPREFETCH ||
            descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL;
        if (descriptor.kind == mesh_abi::kDmaKindSTORE ||
            descriptor.kind == mesh_abi::kDmaKindP2P_PUSH)
            owner->checkDmaSourceValidity(descriptor.command_id);
        sram_stall = owner->reserveDmaSram(descriptor, local_write);
    }
    if (descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL) {
        Cycles cycles = setup_cycles + Cycles((descriptor.useful_bytes + 31) / 32);
        commit_tick = issue_tick + clockPeriod() * cycles + sram_stall;
    } else if (descriptor.useful_bytes > 0) {
        DmaPlan plan = planDescriptorRemote(descriptor, transport->dataBusBytes());
        bursts = plan.bursts;
        beats = plan.beats;
        const uint64_t cycles =
            dmaTransferCycles(uint64_t(setup_cycles),
                              uint64_t(transport->burstBaseLatencyCycles()),
                              bursts, beats);
        commit_tick = issue_tick + clockPeriod() * Cycles(cycles) + sram_stall;
    } else {
        // Zero-length descriptor: setup latency only, zero traffic.
        commit_tick = issue_tick + clockPeriod() * Cycles(setup_cycles) + sram_stall;
    }

    if (transport)
        transport->beginPayloadDigest(descriptor.descriptor_id);
    auto *event = new EngineEvent(this, descriptor, commit_tick);
    schedule(event, commit_tick);
    outstanding++;
    DPRINTF(AiMesh,
            "core %u dma descriptor %u: kind=%u bytes=%llu bursts=%u beats=%llu commit=%llu\n",
            core_id, descriptor.descriptor_id, descriptor.kind,
            (unsigned long long)descriptor.useful_bytes, bursts,
            (unsigned long long)beats, (unsigned long long)commit_tick);
    return true;
}

namespace
{
uint64_t tileStrideOf(const RuntimeArch::Region &region)
{
    return region.tile_stride ? region.tile_stride : region.tile_bytes;
}
} // anonymous namespace

void TensorDmaEngine::completeDescriptor(const DecodedDmaDescriptor &descriptor,
                                         Tick commit_tick)
{
    outstanding--;

    if (descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL) {
        // Local pattern fill: SRAM writes only, no AXI transaction and no B
        // response (spec 4.3); the scheduled commit enforces the core edge.
        const RuntimeArch::Region *dst_region = arch->region(descriptor.dst.region_id);
        fatal_if(!dst_region, "fill destination region unresolved");
        uint64_t pattern = 0;
        auto it = fill_patterns.find(descriptor.command_id);
        fatal_if(it == fill_patterns.end(), "fill pattern not bound for command %u",
                 descriptor.command_id);
        pattern = it->second;
        std::vector<uint8_t> bytes(descriptor.row_bytes);
        for (uint64_t i = 0; i < descriptor.row_bytes; i++)
            bytes[i] = static_cast<uint8_t>((pattern >> (8 * (i % 8))) & 0xFF);
        for (uint32_t row = 0; row < descriptor.rows; row++) {
            uint64_t dst_off =
                descriptor.dst.offset_bytes + uint64_t(row) * descriptor.dst_stride_bytes;
            bool ok = transport->writeSram(
                dst_region->base +
                    uint64_t(descriptor.dst.owner_core) *
                        (dst_region->tile_stride ? dst_region->tile_stride
                                                 : dst_region->tile_bytes) +
                    dst_off,
                descriptor.row_bytes, bytes.data());
            fatal_if(!ok, "fill destination write out of bounds");
        }
        transport->notePayload(descriptor.descriptor_id, bytes.data(), bytes.size());
        transport->accountFill(descriptor.descriptor_id, descriptor.useful_bytes);
    }

    if (descriptor.kind != mesh_abi::kDmaKindLOCAL_FILL) {
        const RuntimeArch::Region *src_region = arch->region(descriptor.src.region_id);
        const RuntimeArch::Region *dst_region = arch->region(descriptor.dst.region_id);
        fatal_if(!src_region || !dst_region, "dma descriptor regions unresolved");

        DmaPlan plan = planDescriptorRemote(descriptor, transport->dataBusBytes());
        if (descriptor.useful_bytes == 0)
            plan.bursts = 0;
        std::vector<uint8_t> buffer(descriptor.row_bytes);
        for (uint32_t row = 0; row < descriptor.rows; row++) {
            uint64_t src_off =
                descriptor.src.offset_bytes + uint64_t(row) * descriptor.src_stride_bytes;
            uint64_t dst_off =
                descriptor.dst.offset_bytes + uint64_t(row) * descriptor.dst_stride_bytes;
            bool ok = true;
            if (descriptor.src.memory_space == mesh_abi::kMemorySpaceCORE_SRAM)
                ok = transport->readSram(src_region->base +
                                             uint64_t(descriptor.src.owner_core) *
                                                 tileStrideOf(*src_region) + src_off,
                                         descriptor.row_bytes, buffer.data());
            else
                ok = transport->readHbm(src_region->base + src_off, descriptor.row_bytes,
                                        buffer.data());
            fatal_if(!ok, "dma source read out of bounds");
            transport->notePayload(descriptor.descriptor_id, buffer.data(),
                                   descriptor.row_bytes);

            if (descriptor.dst.memory_space == mesh_abi::kMemorySpaceCORE_SRAM ||
                descriptor.dst.memory_space == mesh_abi::kMemorySpacePEER_SRAM)
                ok = transport->writeSram(dst_region->base +
                                              uint64_t(descriptor.dst.owner_core) *
                                                  tileStrideOf(*dst_region) + dst_off,
                                          descriptor.row_bytes, buffer.data());
            else
                ok = transport->writeHbm(dst_region->base + dst_off, descriptor.row_bytes,
                                         buffer.data());
            fatal_if(!ok, "dma destination write out of bounds");
        }

        if (descriptor.useful_bytes == 0)
            plan.bursts = 0;
        if (descriptor.kind == mesh_abi::kDmaKindLOAD ||
            descriptor.kind == mesh_abi::kDmaKindPREFETCH)
            transport->accountRead(descriptor.descriptor_id, descriptor.useful_bytes,
                                   plan.bursts);
        else if (descriptor.kind == mesh_abi::kDmaKindSTORE)
            transport->accountWrite(descriptor.descriptor_id, descriptor.useful_bytes,
                                    plan.bursts);
        else if (descriptor.kind == mesh_abi::kDmaKindP2P_PUSH)
            transport->accountP2p(descriptor.descriptor_id, descriptor.useful_bytes,
                                  plan.bursts);
    }

    if (owner)
        owner->onDmaCompleted(descriptor.command_id, descriptor.completion_event,
                              commit_tick, DmaStatus::OK);
    if (descriptor.kind == mesh_abi::kDmaKindP2P_PUSH && owner)
        owner->notifyPeerCommit(descriptor.dst.owner_core, descriptor.transfer_id);
}

} // namespace ai_mesh
} // namespace gem5
