#include "dev/ai_mesh/tensor_dma_engine.hh"

#include "dev/ai_mesh/mesh_hash.hh"

#include <algorithm>
#include <array>

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

using mesh_hash::digestHex;

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

bool
TensorDmaEngine::readFunctional(uint64_t address, uint64_t size, uint8_t *out)
{
    return transport->readSram(address, size, out);
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
        const bool remote_is_source =
            descriptor.kind == mesh_abi::kDmaKindLOAD ||
            descriptor.kind == mesh_abi::kDmaKindPREFETCH;
        DmaPlan plan = planDescriptorRemoteAt(
            owner->admittedEndpointAddress(descriptor.descriptor_id,
                                           remote_is_source),
            descriptor, transport->dataBusBytes());
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

    const std::optional<DescriptorKey> key =
        owner ? owner->frozenDescriptorKey(descriptor.command_id,
                                           descriptor.descriptor_id)
              : std::nullopt;
    fatal_if(!key, "admitted descriptor %u has no frozen execution identity",
             descriptor.descriptor_id);
    if (transport)
        transport->beginPayloadDigest(descriptor.descriptor_id);
    if (owner)
        owner->recordDescriptorSubmission(*key, curTick(), commit_tick);
    auto *event = new EngineEvent(this, descriptor, *key, commit_tick);
    schedule(event, commit_tick);
    outstanding++;
    DPRINTF(AiMesh,
            "core %u dma descriptor %u: kind=%u bytes=%llu bursts=%u beats=%llu commit=%llu\n",
            core_id, descriptor.descriptor_id, descriptor.kind,
            (unsigned long long)descriptor.useful_bytes, bursts,
            (unsigned long long)beats, (unsigned long long)commit_tick);
    return true;
}

void TensorDmaEngine::completeDescriptor(const DecodedDmaDescriptor &descriptor,
                                         const DescriptorKey &key,
                                         Tick commit_tick)
{
    const MockAxiTransport::DescriptorFault fault =
        transport->takeDescriptorFault(descriptor.descriptor_id);
    if (fault == MockAxiTransport::DescriptorFault::Lost)
        return; // watchdog fault injection: completion never arrives
    outstanding--;
    const Tick completion_tick = curTick();
    TrafficContribution contribution;
    const DmaStatus fault_status =
        descriptor.kind == mesh_abi::kDmaKindLOAD ||
                descriptor.kind == mesh_abi::kDmaKindPREFETCH
            ? DmaStatus::AXI_READ_ERROR
            : DmaStatus::AXI_WRITE_ERROR;
    if (fault == MockAxiTransport::DescriptorFault::Error) {
        // The injected response failure is delivered at the scheduled
        // completion point before any functional target write: the descriptor
        // commits no bytes, keeps no landing or transfer-success evidence, and
        // records only the zero-traffic AXI row so the accounting stays
        // complete.  Other descriptors of the group still write for real.
        transport->accountInjectedError(descriptor.descriptor_id);
        if (owner) {
            const uint64_t target_base =
                owner->admittedEndpointAddress(descriptor.descriptor_id, false);
            mesh_hash::Sha256 before_hash;
            mesh_hash::Sha256 after_hash;
            bool sampled = true;
            std::vector<uint8_t> span(descriptor.row_bytes);
            for (uint32_t row = 0; row < descriptor.rows && sampled; row++) {
                const uint64_t address =
                    target_base + uint64_t(row) * descriptor.dst_stride_bytes;
                if (!transport->readSram(address, descriptor.row_bytes,
                                         span.data())) {
                    sampled = false;
                    break;
                }
                before_hash.update(span.data(), span.size());
            }
            for (uint32_t row = 0; row < descriptor.rows && sampled; row++) {
                const uint64_t address =
                    target_base + uint64_t(row) * descriptor.dst_stride_bytes;
                if (!transport->readSram(address, descriptor.row_bytes,
                                         span.data())) {
                    sampled = false;
                    break;
                }
                after_hash.update(span.data(), span.size());
            }
            if (sampled)
                owner->recordFaultTarget(key, digestHex(before_hash.digest()),
                                         digestHex(after_hash.digest()));
        }
        if (owner)
            owner->recordDescriptorCompletion(key, completion_tick, false, 0,
                                              fault_status, contribution);
        if (owner)
            owner->onDmaCompleted(descriptor.command_id,
                                  descriptor.descriptor_id,
                                  descriptor.completion_event, commit_tick,
                                  fault_status);
        return;
    }
    const bool capture_transfer =
        descriptor.kind == mesh_abi::kDmaKindP2P_PUSH &&
        descriptor.useful_bytes > 0;
    std::string source_hex;
    std::string target_hex;
    std::string initial_hex;

    if (descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL) {
        // Local pattern fill: SRAM writes only, no AXI transaction and no B
        // response (spec 4.3); the scheduled commit enforces the core edge.
        // The pattern is a little-endian byte sequence that repeats across the
        // whole descriptor payload, and the destination is the admitted
        // endpoint address, exactly like every other DMA kind.
        auto it = fill_patterns.find(descriptor.command_id);
        fatal_if(it == fill_patterns.end(), "fill pattern not bound for command %u",
                 descriptor.command_id);
        const uint64_t pattern = it->second;
        const uint64_t payload_bytes =
            uint64_t(descriptor.row_bytes) * descriptor.rows;
        std::vector<uint8_t> bytes(payload_bytes);
        for (uint64_t i = 0; i < payload_bytes; i++)
            bytes[i] = static_cast<uint8_t>((pattern >> (8 * (i % 8))) & 0xFF);
        const uint64_t dst_base =
            owner->admittedEndpointAddress(descriptor.descriptor_id, false);
        const uint64_t dst_end =
            dst_base + uint64_t(descriptor.rows - 1) *
                           descriptor.dst_stride_bytes +
            descriptor.row_bytes;
        struct RangeSpec
        {
            std::string kind;
            uint64_t address = 0;
            uint64_t size = 0;
        };
        std::vector<RangeSpec> specs;
        const std::optional<DestinationStorageObservation> storage =
            owner->admittedDestinationStorage(descriptor.descriptor_id);
        fatal_if(!storage,
                 "fill destination storage is unresolved for descriptor %u",
                 descriptor.descriptor_id);
        if (dst_base > storage->base)
            specs.push_back(
                {"object-prefix", storage->base, dst_base - storage->base});
        if (dst_base >= 8)
            specs.push_back({"left", dst_base - 8, 8});
        specs.push_back({"right", dst_end, 8});
        for (uint32_t row = 0; row + 1 < descriptor.rows; row++) {
            const uint64_t gap_begin = dst_base +
                uint64_t(row) * descriptor.dst_stride_bytes +
                descriptor.row_bytes;
            const uint64_t gap_end = dst_base +
                uint64_t(row + 1) * descriptor.dst_stride_bytes;
            if (gap_end > gap_begin)
                specs.push_back({"row-gap", gap_begin, gap_end - gap_begin});
        }
        const uint64_t allocation_end = storage->base + storage->bytes;
        if (allocation_end > dst_end)
            specs.push_back(
                {"allocation-tail", dst_end, allocation_end - dst_end});
        std::vector<std::vector<uint8_t>> before_bytes(specs.size());
        std::vector<bool> available(specs.size(), false);
        for (size_t index = 0; index < specs.size(); index++) {
            if (specs[index].size == 0)
                continue;
            before_bytes[index].resize(specs[index].size);
            available[index] = transport->readSram(
                specs[index].address, specs[index].size,
                before_bytes[index].data());
        }
        for (uint32_t row = 0; row < descriptor.rows; row++) {
            const uint64_t dst_addr =
                dst_base + uint64_t(row) * descriptor.dst_stride_bytes;
            const bool ok = transport->writeSram(
                dst_addr, descriptor.row_bytes,
                bytes.data() + uint64_t(row) * descriptor.row_bytes);
            fatal_if(!ok, "fill destination write out of bounds");
        }
        std::vector<uint8_t> landed(payload_bytes);
        for (uint32_t row = 0; row < descriptor.rows; row++) {
            const bool ok = transport->readSram(
                dst_base + uint64_t(row) * descriptor.dst_stride_bytes,
                descriptor.row_bytes,
                landed.data() + uint64_t(row) * descriptor.row_bytes);
            fatal_if(!ok, "fill destination read back out of bounds");
        }
        mesh_hash::Sha256 landing;
        landing.update(landed.data(), landed.size());
        std::vector<SentinelRangeObservation> sentinels;
        for (size_t index = 0; index < specs.size(); index++) {
            if (specs[index].size == 0)
                continue;
            SentinelRangeObservation range;
            range.kind = specs[index].kind;
            range.address = specs[index].address;
            range.size = specs[index].size;
            range.available = available[index];
            if (range.available) {
                std::vector<uint8_t> after(range.size);
                if (transport->readSram(range.address, range.size,
                                        after.data())) {
                    mesh_hash::Sha256 before_hash;
                    before_hash.update(before_bytes[index].data(),
                                       range.size);
                    mesh_hash::Sha256 after_hash;
                    after_hash.update(after.data(), range.size);
                    range.before_digest = digestHex(before_hash.digest());
                    range.after_digest = digestHex(after_hash.digest());
                } else {
                    range.available = false;
                }
            }
            sentinels.push_back(std::move(range));
        }
        if (owner) {
            const std::optional<DescriptorKey> landing_key =
                owner->frozenDescriptorKey(descriptor.command_id,
                                           descriptor.descriptor_id);
            if (landing_key)
                owner->recordFillLanding(*landing_key,
                                         digestHex(landing.digest()), *storage,
                                         sentinels);
        }
        transport->notePayload(descriptor.descriptor_id, bytes.data(),
                               payload_bytes);
        contribution = transport->accountFill(descriptor.descriptor_id,
                                              descriptor.useful_bytes);
    }

    if (descriptor.kind != mesh_abi::kDmaKindLOCAL_FILL) {
        const RuntimeArch::Region *src_region = arch->region(descriptor.src.region_id);
        const RuntimeArch::Region *dst_region = arch->region(descriptor.dst.region_id);
        fatal_if(!src_region || !dst_region, "dma descriptor regions unresolved");

        const bool remote_is_source =
            descriptor.kind == mesh_abi::kDmaKindLOAD ||
            descriptor.kind == mesh_abi::kDmaKindPREFETCH;
        DmaPlan plan = planDescriptorRemoteAt(
            owner->admittedEndpointAddress(descriptor.descriptor_id,
                                           remote_is_source),
            descriptor, transport->dataBusBytes());
        if (descriptor.useful_bytes == 0)
            plan.bursts = 0;
        const bool source_is_local =
            descriptor.src.memory_space == mesh_abi::kMemorySpaceCORE_SRAM ||
            descriptor.src.memory_space == mesh_abi::kMemorySpacePEER_SRAM;
        const bool capture_source_rows =
            descriptor.useful_bytes > 0 && source_is_local;
        std::vector<uint8_t> buffer(descriptor.row_bytes);
        std::vector<uint8_t> target(descriptor.row_bytes);
        mesh_hash::Sha256 source_digest;
        mesh_hash::Sha256 target_digest;
        mesh_hash::Sha256 initial_digest;
        std::vector<ContentRowObservation> source_rows;
        const uint64_t src_base =
            owner->admittedEndpointAddress(descriptor.descriptor_id, true);
        const uint64_t dst_base =
            owner->admittedEndpointAddress(descriptor.descriptor_id, false);
        const bool dst_is_sram =
            descriptor.dst.memory_space == mesh_abi::kMemorySpaceCORE_SRAM ||
            descriptor.dst.memory_space == mesh_abi::kMemorySpacePEER_SRAM;
        for (uint32_t row = 0; row < descriptor.rows; row++) {
            const uint64_t src_addr =
                src_base + uint64_t(row) * descriptor.src_stride_bytes;
            const uint64_t dst_addr =
                dst_base + uint64_t(row) * descriptor.dst_stride_bytes;
            bool ok = true;
            if (descriptor.src.memory_space == mesh_abi::kMemorySpaceCORE_SRAM ||
                descriptor.src.memory_space == mesh_abi::kMemorySpacePEER_SRAM)
                ok = transport->readSram(src_addr, descriptor.row_bytes,
                                         buffer.data());
            else
                ok = transport->readHbm(src_addr, descriptor.row_bytes,
                                        buffer.data());
            fatal_if(!ok, "dma source read out of bounds");
            transport->notePayload(descriptor.descriptor_id, buffer.data(),
                                   descriptor.row_bytes);
            if (capture_transfer)
                source_digest.update(buffer.data(), descriptor.row_bytes);
            if (capture_source_rows) {
                mesh_hash::Sha256 row_hash;
                row_hash.update(buffer.data(), descriptor.row_bytes);
                ContentRowObservation row;
                row.address = src_addr;
                row.size = descriptor.row_bytes;
                row.digest = digestHex(row_hash.digest());
                source_rows.push_back(row);
            }

            if (capture_transfer) {
                if (dst_is_sram)
                    ok = transport->readSram(dst_addr, descriptor.row_bytes,
                                             target.data());
                else
                    ok = transport->readHbm(dst_addr, descriptor.row_bytes,
                                            target.data());
                fatal_if(!ok, "dma destination read back out of bounds");
                initial_digest.update(target.data(), descriptor.row_bytes);
            }

            if (dst_is_sram)
                ok = transport->writeSram(dst_addr, descriptor.row_bytes,
                                          buffer.data());
            else
                ok = transport->writeHbm(dst_addr, descriptor.row_bytes,
                                         buffer.data());
            fatal_if(!ok, "dma destination write out of bounds");

            if (capture_transfer) {
                if (dst_is_sram)
                    ok = transport->readSram(dst_addr, descriptor.row_bytes,
                                             target.data());
                else
                    ok = transport->readHbm(dst_addr, descriptor.row_bytes,
                                            target.data());
                fatal_if(!ok, "dma destination read back out of bounds");
                target_digest.update(target.data(), descriptor.row_bytes);
            }
        }

        if (descriptor.useful_bytes == 0)
            plan.bursts = 0;
        if (capture_transfer) {
            source_hex = digestHex(source_digest.digest());
            target_hex = digestHex(target_digest.digest());
            initial_hex = digestHex(initial_digest.digest());
        }
        if (capture_source_rows && owner)
            owner->recordDescriptorSourceRows(key, source_rows);
        if (descriptor.kind == mesh_abi::kDmaKindLOAD ||
            descriptor.kind == mesh_abi::kDmaKindPREFETCH)
            contribution = transport->accountRead(descriptor.descriptor_id,
                                                  descriptor.useful_bytes,
                                                  plan.bursts);
        else if (descriptor.kind == mesh_abi::kDmaKindSTORE)
            contribution = transport->accountWrite(descriptor.descriptor_id,
                                                   descriptor.useful_bytes,
                                                   plan.bursts);
        else if (descriptor.kind == mesh_abi::kDmaKindP2P_PUSH)
            contribution = transport->accountP2p(descriptor.descriptor_id,
                                                 descriptor.useful_bytes,
                                                 plan.bursts);
    }

    if (owner)
        owner->recordDescriptorCompletion(
            key, completion_tick, true, curTick(), DmaStatus::OK,
            contribution);
    if (owner)
        owner->onDmaCompleted(descriptor.command_id, descriptor.descriptor_id,
                              descriptor.completion_event, commit_tick,
                              DmaStatus::OK);
    if (owner && capture_transfer) {
        MeshDummyCore::TransferCommitContent content;
        content.source_digest = source_hex;
        content.target_digest = target_hex;
        content.target_initial_digest = initial_hex;
        owner->recordTransferCommit(descriptor, content);
    }
}

} // namespace ai_mesh
} // namespace gem5
