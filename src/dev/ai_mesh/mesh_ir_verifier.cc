#include "dev/ai_mesh/mesh_ir_verifier.hh"

#include "dev/ai_mesh/mesh_splitter.hh"

#include <algorithm>
#include <map>
#include <memory>
#include <set>
#include <utility>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_ir_backing.hh"
#include "dev/ai_mesh/mesh_ir_control_dependency_verifier.hh"
#include "dev/ai_mesh/mesh_ir_computation_verifier.hh"
#include "dev/ai_mesh/mesh_ir_compute_verifier.hh"
#include "dev/ai_mesh/mesh_ir_dma_verifier.hh"
#include "dev/ai_mesh/mesh_ir_intrinsic_dependency_facts.hh"
#include "dev/ai_mesh/mesh_ir_intrinsic_memory_verifier.hh"
#include "dev/ai_mesh/mesh_ir_lifetime_verifier.hh"
#include "dev/ai_mesh/mesh_ir_region.hh"
#include "dev/ai_mesh/mesh_ir_semantic_context.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{
bool fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

std::string
hexDigest(const std::array<uint8_t, 32> &digest)
{
    static constexpr char Hex[] = "0123456789abcdef";
    std::string result;
    result.reserve(digest.size() * 2);
    for (const uint8_t value : digest) {
        result.push_back(Hex[value >> 4]);
        result.push_back(Hex[value & 0xf]);
    }
    return result;
}

template <typename Record>
const Record *
semanticRow(
    const mesh_abi::semantic_abi::SemanticRef &reference,
    const std::vector<Record> &rows)
{
    using Traits = mesh_abi::semantic_abi::SemanticRecordTraits<Record>;
    if (reference.section_type != Traits::section_type ||
        reference.row_id == 0 || reference.row_id > rows.size())
        return nullptr;
    return &rows[reference.row_id - 1];
}

} // anonymous namespace

bool RuntimeArch::hasCore(uint16_t core_id) const
{
    return std::find(core_ids.begin(), core_ids.end(), core_id) != core_ids.end();
}

const RuntimeArch::Region *RuntimeArch::region(uint16_t region_id) const
{
    for (const auto &candidate : regions)
        if (candidate.region_id == region_id)
            return &candidate;
    return nullptr;
}

uint64_t
RuntimeArch::Region::tileBase(uint16_t owner_core, bool &overflow) const
{
    overflow = false;
    if (!perCore())
        return base;
    const uint64_t stride = tile_stride ? tile_stride : tile_bytes;
    if (stride != 0 && owner_core > (~uint64_t(0) - base) / stride) {
        overflow = true;
        return 0;
    }
    return base + uint64_t(owner_core) * stride;
}

bool validateRuntimeArch(const RuntimeArch &arch, MeshLoadError &error)
{
    if (arch.core_ids.empty())
        return fail("E_ABI_BOUNDS", "runtime architecture has no cores", error);
    std::set<uint32_t> core_ids;
    for (uint32_t core_id : arch.core_ids)
        if (!core_ids.insert(core_id).second)
            return fail("E_ABI_BOUNDS", "runtime architecture repeats a core id",
                        error);
    if (arch.sram_bytes == 0 || arch.sram_banks == 0)
        return fail("E_ABI_BOUNDS", "runtime SRAM geometry is empty", error);
    if (arch.sram_alignment == 0 ||
        (arch.sram_alignment & (arch.sram_alignment - 1)) != 0)
        return fail("E_ABI_BOUNDS",
                    "runtime SRAM alignment is not a power of two", error);
    if (arch.axi_data_bytes != 8 && arch.axi_data_bytes != 16 &&
        arch.axi_data_bytes != 32 && arch.axi_data_bytes != 64)
        return fail("E_ABI_ENUM", "runtime AXI data width is unsupported", error);
    if (arch.axi_max_burst_beats == 0 || arch.axi_max_burst_beats > 256)
        return fail("E_ABI_BOUNDS", "runtime AXI burst limit is out of range",
                    error);
    if (arch.axi_address_bits == 0 || arch.axi_address_bits > 64)
        return fail("E_ABI_BOUNDS", "runtime AXI address width is out of range",
                    error);
    const uint64_t address_limit =
        arch.axi_address_bits == 64 ? 0 : (uint64_t(1) << arch.axi_address_bits);
    std::set<uint32_t> region_ids;
    for (const auto &region : arch.regions) {
        if (!region_ids.insert(region.region_id).second)
            return fail("E_ABI_BOUNDS",
                        "runtime architecture repeats a region id", error);
        if (region.bytes == 0)
            return fail("E_ABI_BOUNDS", "runtime region is empty", error);
        if (region.kind != RuntimeArch::Region::kKindHbm &&
            region.kind != RuntimeArch::Region::kKindHostShared &&
            region.kind != RuntimeArch::Region::kKindSramAperture)
            return fail("E_ABI_ENUM", "runtime region kind is unknown", error);
        if (region.base > UINT64_MAX - region.bytes)
            return fail("E_ABI_BOUNDS", "runtime region overflows u64", error);
        if (address_limit != 0 && region.base + region.bytes > address_limit)
            return fail("E_ABI_BOUNDS",
                        "runtime region exceeds the AXI address width", error);
        if (region.is_sram_aperture ||
            region.kind == RuntimeArch::Region::kKindSramAperture) {
            if (region.tile_bytes == 0 ||
                region.tile_stride < region.tile_bytes)
                return fail("E_ABI_BOUNDS",
                            "runtime SRAM aperture tile is invalid", error);
            if ((region.tile_stride & (region.tile_stride - 1)) != 0)
                return fail("E_ABI_BOUNDS",
                            "runtime SRAM aperture stride is not a power of two",
                            error);
        }
    }
    for (size_t i = 0; i < arch.regions.size(); i++)
        for (size_t j = i + 1; j < arch.regions.size(); j++) {
            const auto &a = arch.regions[i];
            const auto &b = arch.regions[j];
            if (a.base < b.base + b.bytes && b.base < a.base + a.bytes)
                return fail("E_ABI_BOUNDS", "runtime regions overlap", error);
        }
    return true;
}

bool admitProgram(std::shared_ptr<const DecodedProgram> source,
                  const RuntimeArch &arch, MeshProgramAdmission &admission,
                  MeshLoadError &error)
{
    using namespace mesh_abi;

    if (!source)
        return fail("E_ABI_BOUNDS", "program image is missing", error);
    if (!validateRuntimeArch(arch, error))
        return false;
    admission.program_ = std::move(source);
    admission.arch_ = &arch;
    const DecodedProgram &program = *admission.program_;

    if (!mesh_binary_detail::validateProgramSemanticChecksum(program, error))
        return false;
    if (hexDigest(program.header.arch_digest) != arch.arch_digest_hex)
        return fail("E_ARCH_DIGEST", "architecture digest mismatch", error);
    if (!buildProgramSemanticContext(program, admission.context_, error))
        return false;
    const ProgramSemanticContext &context = admission.context_;
    if (!verifyProgramGeometry(program, context, admission.geometry_, error))
        return false;
    const VerifiedProgramGeometry &geometry = admission.geometry_;
    if (!verifyProgramBacking(program, arch, context, geometry,
                              admission.backing_, error))
        return false;
    const VerifiedProgramBacking &backing = admission.backing_;
    if (!verifyProgramCommandProjection(program, arch, context,
                                        admission.projection_, error))
        return false;
    const VerifiedCommandProjectionFacts &projection = admission.projection_;
    if (!verifyProgramComputationDomain(program, context, geometry,
                                        admission.matrix_facts_, error))
        return false;
    if (!verifyDmaSemanticDomain(program, arch, context, geometry, backing,
                                 projection, admission.dma_, error))
        return false;
    if (!verifyProgramIntrinsicDependencyFacts(
            program, context, admission.intrinsic_, error))
        return false;
    if (!verifyProgramIntrinsicMemoryDomain(
            program, context, geometry, admission.intrinsic_,
            admission.matrix_facts_, error))
        return false;
    if (!verifyProgramComputeDomain(program, arch, context, geometry, error))
        return false;
    if (!verifyProgramControlDependencyDomain(
            program, arch, context, geometry, backing, projection,
            admission.dma_, admission.intrinsic_, admission.control_, error))
        return false;
    const VerifiedControlDependencyFacts &controlFacts = admission.control_;
    if (!verifyProgramLifetimeDomain(
            program, arch, context, geometry, backing, admission.intrinsic_,
            error))
        return false;

    std::set<uint32_t> tensor_ids;

    // Unique ids, string-table bounds and rank limits (mirrors Python).
    {
        for (const auto &tensor : program.transport.tensors) {
            if (!tensor_ids.insert(tensor.tensor_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate tensor_id", error);
            if (tensor.name_sid == 0 ||
                tensor.name_sid > program.transport.strings.size())
                return fail("E_ABI_BOUNDS", "string id out of range", error);
            if (tensor.rank > 8)
                return fail("E_ABI_BOUNDS", "tensor rank > 8", error);
            if (tensor.layout == kLayoutKindBLOCKED_MNK &&
                tensor.layout_attr == 0)
                return fail("E_ABI_BOUNDS",
                            "BLOCKED_MNK tensor requires layout attr", error);
        }
        std::set<uint32_t> profile_ids;
        for (const auto &profile : program.transport.profiles) {
            if (!profile_ids.insert(profile.profile_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate profile_id", error);
            if (profile.name_sid == 0 ||
                profile.name_sid > program.transport.strings.size())
                return fail("E_ABI_BOUNDS", "string id out of range", error);
            if (profile.rank > 8)
                return fail("E_ABI_BOUNDS", "profile rank > 8", error);
        }
        std::set<uint32_t> entrypoint_ids;
        for (const auto &entrypoint : program.transport.entrypoints) {
            if (!entrypoint_ids.insert(entrypoint.entrypoint_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate entrypoint_id",
                            error);
            if (entrypoint.name_sid == 0 ||
                entrypoint.name_sid > program.transport.strings.size())
                return fail("E_ABI_BOUNDS", "string id out of range", error);
        }
        std::set<uint32_t> shard_ids;
        for (const auto &shard : program.transport.shards)
            if (!shard_ids.insert(shard.shard_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate shard_id", error);
        std::set<uint32_t> descriptor_ids;
        for (const auto &descriptor : program.transport.dma_descriptors)
            if (!descriptor_ids.insert(descriptor.descriptor_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate descriptor_id",
                            error);
    }

    for (const auto &command : program.transport.commands) {
        if (!arch.hasCore(command.core_id))
            return fail("E_ABI_BOUNDS", "command core not in arch", error);
        if (opcodeEngine(command.opcode) == 0)
            return fail("E_ABI_ENUM", "unknown opcode", error);
        const bool semantic_compute =
            command.opcode == kOpcodeGEMM ||
            command.opcode == kOpcodeBMM ||
            command.opcode == kOpcodeELEMENTWISE ||
            command.opcode == kOpcodeLOCAL_REDUCE ||
            command.opcode == kOpcodeSOFTMAX ||
            command.opcode == kOpcodeNORM;
        if (!semantic_compute &&
            command.engine != opcodeEngine(command.opcode))
            return fail("E_ENGINE_MISMATCH", "opcode does not map to engine", error);
        if (!spanFits(command.operand_begin, command.operand_count,
                      program.transport.command_operands.size()))
            return fail("E_ABI_BOUNDS", "operand range out of table", error);
    }

    for (const auto &shard : program.transport.shards)
        if (!tensor_ids.count(shard.tensor_id))
            return fail("E_ABI_BOUNDS", "shard tensor unknown", error);
    for (const auto &relocation : program.transport.relocations)
        if (!tensor_ids.count(relocation.tensor_id))
            return fail("E_RELOCATION", "relocation tensor unknown", error);

    // Operand tensor/shard/allocation integrity mirrors the Python verifier.
    {
        std::map<uint32_t, const DecodedShard *> shard_by_id;
        for (const auto &shard : program.transport.shards)
            shard_by_id[shard.shard_id] = &shard;
        std::set<uint32_t> allocation_ids;
        for (const auto &allocation : program.transport.allocations)
            allocation_ids.insert(allocation.allocation_id);
        for (const auto &command : program.transport.commands) {
            const size_t operand_end =
                size_t(command.operand_begin) + command.operand_count;
            for (size_t i = command.operand_begin; i < operand_end; i++) {
                const auto &operand = program.transport.command_operands[i];
                if (!tensor_ids.count(operand.tensor_id))
                    return fail("E_ABI_BOUNDS", "operand tensor unknown", error);
                if (operand.allocation_id &&
                    !allocation_ids.count(operand.allocation_id))
                    return fail("E_ABI_BOUNDS", "operand allocation unknown", error);
                if (operand.shard_id) {
                    auto it = shard_by_id.find(operand.shard_id);
                    if (it != shard_by_id.end() && operand.allocation_id &&
                        it->second->allocation_id != operand.allocation_id)
                        return fail("E_ABI_BOUNDS",
                                    "operand allocation does not match its "
                                    "shard allocation", error);
                }
                if (operand.shard_id) {
                    auto it = shard_by_id.find(operand.shard_id);
                    if (it == shard_by_id.end())
                        return fail("E_ABI_BOUNDS", "operand shard unknown", error);
                    if (it->second->tensor_id != operand.tensor_id)
                        return fail("E_ABI_BOUNDS",
                                    "operand shard belongs to another tensor", error);
                    const bool compute =
                        command.opcode == kOpcodeGEMM ||
                        command.opcode == kOpcodeBMM ||
                        command.opcode == kOpcodeELEMENTWISE ||
                        command.opcode == kOpcodeLOCAL_REDUCE ||
                        command.opcode == kOpcodeSOFTMAX ||
                        command.opcode == kOpcodeNORM;
                    if (compute && operand.allocation_id != 0 &&
                        it->second->owner_core != command.core_id)
                        return fail("E_ABI_BOUNDS", "operand shard owned by another core",
                                    error);
                }
            }
        }
    }

    // Descriptor local endpoint <-> command operand binding: the runtime
    // pins and validates command operands while the descriptor moves bytes
    // at its local endpoint; both must designate the same SRAM shard.
    {
        std::map<uint32_t, std::set<uint32_t>> operand_shards_of;
        for (const auto &command : program.transport.commands) {
            auto &shards = operand_shards_of[command.command_id];
            const size_t operand_end =
                size_t(command.operand_begin) + command.operand_count;
            for (size_t i = command.operand_begin; i < operand_end; i++)
                if (program.transport.command_operands[i].shard_id)
                    shards.insert(program.transport.command_operands[i].shard_id);
        }
        for (const auto &descriptor : program.transport.dma_descriptors) {
            const DecodedDmaEndpoint *local = nullptr;
            for (const DecodedDmaEndpoint *endpoint :
                 {&descriptor.src, &descriptor.dst})
                if ((endpoint->memory_space == kMemorySpaceCORE_SRAM ||
                     endpoint->memory_space == kMemorySpacePEER_SRAM) &&
                    endpoint->owner_core == descriptor.owner_core)
                    local = endpoint;
            if (!local || local->shard_id == 0)
                continue;
            const auto &shards = operand_shards_of[descriptor.command_id];
            if (!shards.count(local->shard_id))
                return fail("E_DMA_RANGE",
                            "descriptor local endpoint shard is not a "
                            "command operand", error);
        }
    }

    {
        using namespace mesh_abi::semantic_abi;
        const ProgramSemantics &root =
            program.semantic_tables.program_semantics_rows.front();
        const TrafficReport *report = semanticRow(
            root.intrinsic_traffic,
            program.semantic_tables.traffic_report_rows);
        std::map<uint32_t, const DescriptorTraffic *> semantic_traffic;
        if (!report)
            return fail("E_TRAFFIC_MISMATCH",
                        "intrinsic traffic report is missing", error);
        const size_t semantic_begin = report->descriptors.begin;
        const size_t semantic_end = semantic_begin + report->descriptors.count;
        for (size_t i = semantic_begin; i < semantic_end; ++i) {
            const DescriptorTraffic *traffic = semanticRow(
                program.semantic_references[i],
                program.semantic_tables.descriptor_traffic_rows);
            if (!traffic)
                return fail("E_TRAFFIC_MISMATCH",
                            "descriptor traffic record is invalid", error);
            const DescriptorIdentity *identity = semanticRow(
                traffic->identity,
                program.semantic_tables.descriptor_identity_rows);
            if (!identity)
                return fail("E_TRAFFIC_MISMATCH",
                            "descriptor traffic identity is invalid", error);
            if (!semantic_traffic.emplace(
                    uint32_t(identity->descriptor_id), traffic).second)
                return fail("E_ABI_DUPLICATE",
                            "duplicate descriptor traffic identity", error);
        }
        std::map<uint32_t, const DecodedTrafficRow *> rows_by_descriptor;
        for (const auto &row : program.transport.expected_traffic) {
            if (rows_by_descriptor.count(row.descriptor_id))
                return fail("E_ABI_DUPLICATE", "duplicate traffic row", error);
            rows_by_descriptor[row.descriptor_id] = &row;
        }
        for (const auto &descriptor : program.transport.dma_descriptors) {
            if (!rows_by_descriptor.count(descriptor.descriptor_id))
                return fail("E_TRAFFIC_MISMATCH",
                            "descriptor has no expected traffic row", error);
        }
        for (const auto &row : program.transport.expected_traffic) {
            const DecodedDmaDescriptor *descriptor = nullptr;
            for (const auto &candidate : program.transport.dma_descriptors)
                if (candidate.descriptor_id == row.descriptor_id)
                    descriptor = &candidate;
            if (!descriptor)
                return fail("E_TRAFFIC_MISMATCH", "traffic row references "
                            "unknown descriptor", error);
            auto semantic_it = semantic_traffic.find(descriptor->descriptor_id);
            if (semantic_it == semantic_traffic.end() ||
                semantic_it->second->execution_count == 0)
                return fail("E_TRAFFIC_MISMATCH",
                            "descriptor execution record is missing", error);
            const DescriptorTraffic &semantic = *semantic_it->second;
            const ScheduledCommandFact *commandFact = controlFacts.scheduled(
                descriptor->command_id);
            if (!commandFact ||
                semantic.execution_count != commandFact->executionCount())
                return fail("E_TRAFFIC_MISMATCH",
                            "descriptor execution count is not admitted", error);
            const DescriptorIdentity *identity = semanticRow(
                semantic.identity,
                program.semantic_tables.descriptor_identity_rows);
            if (!identity || identity->entrypoint_id != row.entrypoint_id ||
                identity->profile_id != row.profile_id ||
                identity->command_id != row.command_id ||
                identity->descriptor_id != row.descriptor_id ||
                identity->issuing_core != descriptor->owner_core)
                return fail("E_TRAFFIC_MISMATCH",
                            "traffic identity projection differs", error);
            if (row.command_id != descriptor->command_id)
                return fail("E_TRAFFIC_MISMATCH", "traffic row command mismatch",
                            error);
            if (row.kind != descriptor->kind)
                return fail("E_TRAFFIC_MISMATCH", "traffic row kind mismatch",
                            error);
            const uint64_t src_address =
                admission.dma_.sourceAddress(descriptor->descriptor_id);
            const uint64_t dst_address =
                admission.dma_.destinationAddress(descriptor->descriptor_id);
            const bool is_read = descriptor->kind == kDmaKindLOAD ||
                descriptor->kind == kDmaKindPREFETCH;
            const uint64_t remote_address =
                is_read ? src_address : dst_address;
            const uint64_t remote_stride = is_read
                ? descriptor->src_stride_bytes
                : descriptor->dst_stride_bytes;
            if (static_cast<uint32_t>(semantic.src_memory_space) !=
                    descriptor->src.memory_space ||
                static_cast<uint32_t>(semantic.dst_memory_space) !=
                    descriptor->dst.memory_space ||
                semantic.src_address != src_address ||
                semantic.dst_address != dst_address ||
                semantic.remote_address != remote_address ||
                semantic.rows != descriptor->rows ||
                semantic.row_bytes != descriptor->row_bytes ||
                semantic.remote_stride_bytes != remote_stride ||
                semantic.max_burst_beats != std::min(
                    uint32_t(descriptor->max_burst_beats),
                    arch.axi_max_burst_beats))
                return fail("E_TRAFFIC_MISMATCH",
                            "traffic descriptor projection differs", error);
            bool traffic_overflow = false;
            const uint64_t execution_useful = checkedMul(
                descriptor->useful_bytes, commandFact->executionCount(),
                traffic_overflow);
            if (traffic_overflow || row.useful_bytes != execution_useful ||
                row.useful_bytes != semantic.useful_bytes)
                return fail("E_TRAFFIC_MISMATCH", "traffic useful mismatch",
                            error);
            const bool is_write = descriptor->kind == 2 || descriptor->kind == 3;
            if (row.b_count != row.aw_count)
                return fail("E_TRAFFIC_MISMATCH", "B count must equal AW count",
                            error);
            if (descriptor->kind == 5 /* LOCAL_FILL */) {
                if (row.bursts != 0 || row.physical_beat_bytes != 0 ||
                    row.segments != 0 || row.aw_count != 0 ||
                    row.ar_count != 0 || row.b_count != 0 ||
                    row.r_beats != 0 || row.w_beats != 0 ||
                    row.min_flits != semantic.flits)
                    return fail("E_TRAFFIC_MISMATCH",
                                "FILL traffic row must be all zero", error);
                continue;
            }
            const uint32_t width = arch.axi_data_bytes;
            const uint32_t limit = std::min(
                uint32_t(descriptor->max_burst_beats), arch.axi_max_burst_beats);
            uint64_t bursts = 0;
            uint64_t beats = 0;
            for (uint32_t r = 0; r < descriptor->rows; r++) {
                bool base_overflow = false;
                const uint64_t base = checkedMulAdd(
                    r, remote_stride, remote_address, base_overflow);
                if (base_overflow)
                    return fail("E_ABI_OVERFLOW",
                                "traffic row address overflows u64", error);
                const auto list = splitBursts(base, descriptor->row_bytes, width,
                                              limit);
                if (list.size() > UINT64_MAX - bursts)
                    return fail("E_ABI_OVERFLOW",
                                "traffic burst count overflows u64", error);
                bursts += list.size();
                for (const auto &burst : list) {
                    if (burst.beats > UINT64_MAX - beats)
                        return fail("E_ABI_OVERFLOW",
                                    "traffic beat count overflows u64", error);
                    beats += burst.beats;
                }
            }
            // One row segment per non-empty row (plan_descriptor's
            // SegmentPlan.segments; zero-byte descriptors have none).
            const uint32_t segments =
                descriptor->row_bytes > 0 ? descriptor->rows : 0;
            const uint64_t executions = commandFact->executionCount();
            const uint64_t total_bursts = checkedMul(
                bursts, executions, traffic_overflow);
            const uint64_t total_beat_bytes = checkedMul(
                checkedMul(beats, width, traffic_overflow), executions,
                traffic_overflow);
            const uint64_t total_segments = checkedMul(
                segments, executions, traffic_overflow);
            if (traffic_overflow || row.bursts != total_bursts ||
                row.bursts != semantic.bursts ||
                row.physical_beat_bytes != total_beat_bytes ||
                row.physical_beat_bytes != semantic.physical_beat_bytes)
                return fail("E_TRAFFIC_MISMATCH", "traffic burst plan mismatch",
                            error);
            if (row.segments != total_segments ||
                row.segments != semantic.segments)
                return fail("E_TRAFFIC_MISMATCH", "traffic segments mismatch",
                            error);
            if (row.aw_count != (is_write ? total_bursts : 0) ||
                row.ar_count != (is_write ? 0 : total_bursts))
                return fail("E_TRAFFIC_MISMATCH", "traffic AW/AR mismatch",
                            error);
            if (row.b_count != row.aw_count)
                return fail("E_TRAFFIC_MISMATCH", "traffic B count mismatch",
                            error);
            const uint64_t total_beats = checkedMul(
                beats, executions, traffic_overflow);
            if (traffic_overflow || row.r_beats != total_beats)
                return fail("E_TRAFFIC_MISMATCH", "traffic R beat mismatch",
                            error);
            if (row.w_beats != total_beats)
                return fail("E_TRAFFIC_MISMATCH", "traffic W beat mismatch",
                            error);
            if (row.min_flits != semantic.flits)
                return fail("E_TRAFFIC_MISMATCH", "traffic flit mismatch",
                            error);
        }
    }

    for (const auto &entry : context.states()) {
        const mesh_abi::semantic_abi::TensorState &state = *entry.second;
        if (state.version != 0 ||
            state.origin !=
                mesh_abi::semantic_abi::StateOrigin::PRE_RESIDENT)
            continue;
        const mesh_abi::semantic_abi::ProgramVariant *owner =
            context.owner("states", state.state_id);
        if (owner == nullptr)
            return fail("E_ABI_BOUNDS", "state has no owning variant", error);
        const mesh_abi::Allocation *allocation = backing.local(state.object_id);
        if (allocation == nullptr)
            return fail("E_TENSOR_NOT_RESIDENT",
                        "pre-resident state has no local allocation", error);
        std::vector<uint32_t> &ids =
            admission.initial_residents_[owner->variant_id]
                                        [allocation->owner_core];
        if (std::find(ids.begin(), ids.end(), allocation->allocation_id) ==
            ids.end())
            ids.push_back(allocation->allocation_id);
    }
    for (auto &variant : admission.initial_residents_)
        for (auto &core : variant.second)
            std::sort(core.second.begin(), core.second.end());

    return true;
}

} // namespace ai_mesh
} // namespace gem5
