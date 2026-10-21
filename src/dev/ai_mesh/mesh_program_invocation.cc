#include "dev/ai_mesh/mesh_program_invocation.hh"

namespace gem5
{
namespace ai_mesh
{

bool
MeshProgramInvocation::dmaAddresses(
    const DecodedDmaDescriptor &descriptor, const RuntimeObjectKey &key,
    std::array<uint64_t, 2> &addresses, MeshLoadError &error) const
{
    const auto reject = [&](const char *message) {
        error = {"E_RELOCATION", message};
        return false;
    };
    if (key.kind != mesh_abi::MeshObjectKind::DESCRIPTOR ||
        key.ordinal != descriptor.descriptor_id)
        return reject("DMA identity does not name its descriptor");
    if (key.domain == mesh_abi::MeshObjectDomain::STATIC) {
        const auto found = admission().context().descriptors().find(key.ordinal);
        if (found == admission().context().descriptors().end() ||
            !binding_.hasEndpoint(key.ordinal) ||
            mesh_abi::encodeDmaDescriptor(descriptor) !=
                mesh_abi::encodeDmaDescriptor(*found->second))
            return reject("static DMA differs from its admitted invocation");
        addresses = {binding_.endpointAddress(key.ordinal, true),
                     binding_.endpointAddress(key.ordinal, false)};
        return true;
    }
    if (key.domain != mesh_abi::MeshObjectDomain::MOE_OVERLAY &&
        key.domain != mesh_abi::MeshObjectDomain::WEIGHT_FILL)
        return reject("DMA identity has no execution address owner");
    if (key.domain == mesh_abi::MeshObjectDomain::WEIGHT_FILL &&
        descriptor.kind != mesh_abi::kDmaKindLOAD)
        return reject("weight fill must be a read execution");
    std::array<uint64_t, 2> resolved{};
    for (bool source : {true, false}) {
        if (source && descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL)
            continue;
        const auto &endpoint = source ? descriptor.src : descriptor.dst;
        const auto *region = admission().arch().region(endpoint.region_id);
        if (!region)
            return reject("runtime DMA endpoint has no admitted region");
        const bool local = endpoint.memory_space == mesh_abi::kMemorySpaceCORE_SRAM ||
                           endpoint.memory_space == mesh_abi::kMemorySpacePEER_SRAM;
        if (local != region->perCore())
            return reject("runtime DMA endpoint role differs from its region");
        bool overflow = false;
        const uint64_t base = region->tileBase(endpoint.owner_core, overflow);
        if (overflow || endpoint.offset_bytes > UINT64_MAX - base)
            return reject("runtime DMA endpoint address overflows");
        const uint64_t capacity = local ? admission().arch().sram_bytes : region->bytes;
        if (endpoint.offset_bytes > capacity)
            return reject("runtime DMA endpoint escapes its region");
        if (descriptor.useful_bytes) {
            const uint64_t stride = source ? descriptor.src_stride_bytes : descriptor.dst_stride_bytes;
            const uint64_t available = capacity - endpoint.offset_bytes;
            if (!descriptor.rows || !descriptor.row_bytes || descriptor.row_bytes > available ||
                (descriptor.rows > 1 && stride > (available - descriptor.row_bytes) / (descriptor.rows - 1)))
                return reject("runtime DMA rows escape their region");
        }
        resolved[source ? 0 : 1] = base + endpoint.offset_bytes;
    }
    addresses = resolved;
    return true;
}

bool
prepareProgramInvocation(
    std::shared_ptr<const MeshProgramAdmission> admission,
    uint32_t entrypoint_id, uint32_t profile_id,
    const std::vector<DispatchBinding> &bindings,
    std::shared_ptr<const MeshProgramInvocation> &out, MeshLoadError &error)
{
    size_t matching_variants = 0;
    auto reject = [&](const char *message) {
        error = {"E_RELOCATION", message,
                 {{"entrypoint_id", std::to_string(entrypoint_id)},
                  {"profile_id", std::to_string(profile_id)},
                  {"matching_variants", std::to_string(matching_variants)}}};
        return false;
    };
    if (!admission)
        return reject("invocation requires an admitted program");
    const mesh_abi::semantic_abi::ProgramVariant *selected = nullptr;
    uint64_t variant_id = 0;
    for (const auto &[id, variant] : admission->context().variants()) {
        if ((entrypoint_id && variant->entrypoint_id != entrypoint_id) ||
            (profile_id && variant->profile_id != profile_id))
            continue;
        ++matching_variants;
        selected = variant;
        variant_id = id;
    }
    if (!selected)
        return reject("invocation selects no Program variant");
    if (matching_variants != 1)
        return reject("invocation selects multiple Program variants");
    std::shared_ptr<MeshProgramInvocation> prepared(new MeshProgramInvocation);
    prepared->admission_ = std::move(admission);
    prepared->view_ = prepared->admission().executionViews().forInstance(
        selected->entrypoint_id, selected->profile_id);
    if (!prepared->view_)
        return reject("selected variant has no admitted execution view");
    if (!resolveInvocationBinding(prepared->admission(), variant_id, bindings,
                                  prepared->binding_, error))
        return false;
    for (const auto core : prepared->admission().arch().core_ids) {
        CommandRom rom;
        if (!buildCommandRom(prepared->admission(), variant_id, core, rom,
                             error))
            return false;
        prepared->roms_.emplace(core, std::move(rom));
    }
    for (const auto &descriptor : prepared->admission().program().transport.dma_descriptors) {
        if (descriptor.kind != mesh_abi::kDmaKindP2P_PUSH ||
            !prepared->binding().hasEndpoint(descriptor.descriptor_id))
            continue;
        auto &plan = prepared->transfers_[descriptor.transfer_id];
        plan.receiver_core = descriptor.dst.owner_core;
        plan.useful_bytes += descriptor.useful_bytes;
        if (descriptor.useful_bytes == 0)
            continue;
        const auto base = prepared->binding().endpointAddress(
            descriptor.descriptor_id, false);
        for (uint32_t row = 0; row < descriptor.rows; ++row)
            plan.ranges.emplace_back(base + uint64_t(row) * descriptor.dst_stride_bytes,
                                     base + uint64_t(row) * descriptor.dst_stride_bytes + descriptor.row_bytes);
    }
    for (const auto &[id, plan] : prepared->transfers_) {
        const auto *fact = prepared->admission().dma().p2pTransfer(id);
        if (!fact || plan.receiver_core != fact->destinationCore() ||
            plan.useful_bytes != fact->expectedBytes())
            return reject("invocation peer plan differs from admitted transfer");
    }
    out = std::move(prepared);
    return true;
}

}
}
