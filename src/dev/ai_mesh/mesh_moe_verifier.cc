#include "dev/ai_mesh/mesh_moe_verifier.hh"

#include <algorithm>
#include <cstdint>
#include <map>
#include <set>
#include <utility>
#include <vector>

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

bool powerOfTwo(uint64_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

uint32_t dtypeBytes(uint16_t dtype)
{
    switch (dtype) {
    case mesh_abi::kDtypeFP32: return 4;
    case mesh_abi::kDtypeFP16: return 2;
    case mesh_abi::kDtypeBF16: return 2;
    case mesh_abi::kDtypeINT8: return 1;
    case mesh_abi::kDtypeINT32: return 4;
    default: return 0;
    }
}

uint64_t tensorBytes(const mesh_abi::Tensor &tensor, bool &ok)
{
    uint32_t element = dtypeBytes(tensor.dtype);
    ok = tensor.rank >= 1 && tensor.rank <= 8 && element != 0;
    if (!ok)
        return 0;
    uint64_t total = element;
    for (uint16_t i = 0; i < tensor.rank; i++) {
        if (tensor.dims[i] == 0) {
            ok = false;
            return 0;
        }
        if (total > UINT64_MAX / tensor.dims[i]) {
            ok = false;
            return 0;
        }
        total *= tensor.dims[i];
    }
    return total;
}

const mesh_abi::MoeExpertSpec *expertOf(
    const DecodedProgram &program, const mesh_abi::MoeLayerSpec &layer,
    size_t offset)
{
    if (layer.expert_first > program.moe_expert_specs.size() ||
        layer.expert_count > program.moe_expert_specs.size() -
                                layer.expert_first)
        return nullptr;
    return &program.moe_expert_specs[layer.expert_first + offset];
}

const mesh_abi::MoeDynamicRegion *regionOf(
    const DecodedProgram &program, const mesh_abi::MoeLayerSpec &layer,
    size_t offset)
{
    if (layer.dynamic_region_first > program.moe_dynamic_regions.size() ||
        layer.dynamic_region_count > program.moe_dynamic_regions.size() -
                                       layer.dynamic_region_first)
        return nullptr;
    return &program.moe_dynamic_regions[layer.dynamic_region_first + offset];
}

bool verifyGroupBounds(const DecodedProgram &program,
                       const mesh_abi::MoeLayerSpec &layer,
                       MeshLoadError &error)
{
    uint64_t commands = 0;
    uint64_t descriptors = 0;
    uint64_t events = 0;
    uint64_t transfers = 0;
    uint64_t allocations = 0;
    for (uint32_t i = 0; i < layer.dynamic_region_count; i++) {
        const auto *region = regionOf(program, layer, i);
        commands += region->max_overlay_commands;
        descriptors += region->max_overlay_descriptors;
        events += region->max_overlay_events;
        transfers = std::max<uint64_t>(transfers,
                                       region->max_overlay_transfers);
        allocations += region->max_overlay_allocations;
    }
    events += 1;
    if (layer.max_materialized_commands < commands ||
        layer.max_materialized_descriptors < descriptors ||
        layer.max_materialized_events < events ||
        layer.max_materialized_transfers < transfers ||
        layer.max_dynamic_allocations < allocations)
        return fail("E_ABI_BOUNDS",
                    "group bound below per-region demand", error);
    return true;
}

bool verifyLayers(const DecodedProgram &program, MeshLoadError &error)
{
    const auto &layers = program.moe_layer_specs;
    uint64_t experts_offset = 0;
    uint64_t regions_offset = 0;
    std::vector<uint32_t> kernel_indices;
    for (size_t index = 0; index < layers.size(); index++) {
        const auto &layer = layers[index];
        if (layer.layer_id != index + 1)
            return fail("E_ABI_ORDER", "layer ids must be dense from 1",
                        error);
        if (layer.flags != 0)
            return fail("E_ABI_RESERVED", "layer flags must be zero", error);
        if (layer.expert_count < 1 || layer.top_k < 1 ||
            layer.top_k > layer.expert_count)
            return fail("E_ABI_BOUNDS", "layer top-k out of range", error);
        if (layer.dynamic_region_count < 1)
            return fail("E_ABI_BOUNDS", "layer needs at least one region",
                        error);
        if (layer.token_bytes == 0 || layer.output_token_bytes == 0 ||
            layer.capacity_factor_q16 == 0)
            return fail("E_ABI_BOUNDS", "layer byte size is zero", error);
        if (layer.max_tokens_per_frozen_batch == 0 ||
            layer.max_requests_per_batch == 0 || layer.max_routes == 0 ||
            layer.max_materialized_commands == 0 ||
            layer.max_materialized_events == 0 ||
            layer.max_materialized_descriptors == 0 ||
            layer.max_materialized_transfers == 0 ||
            layer.max_dynamic_allocations == 0)
            return fail("E_ABI_BOUNDS", "layer bound must be positive", error);
        if (uint64_t(layer.max_routes) <
            uint64_t(layer.top_k) * layer.max_tokens_per_frozen_batch)
            return fail("E_ABI_BOUNDS", "max_routes below top-k demand",
                        error);
        if (layer.expert_first != experts_offset)
            return fail("E_ABI_ORDER",
                        "expert slices must tile the expert table", error);
        if (layer.dynamic_region_first != regions_offset)
            return fail("E_ABI_ORDER",
                        "region slices must tile the region table", error);
        if (expertOf(program, layer, 0) == nullptr)
            return fail("E_ABI_BOUNDS", "expert slice out of table", error);
        if (regionOf(program, layer, 0) == nullptr)
            return fail("E_ABI_BOUNDS", "region slice out of table", error);
        experts_offset = uint64_t(layer.expert_first) + layer.expert_count;
        regions_offset = uint64_t(layer.dynamic_region_first) +
                         layer.dynamic_region_count;
        if (layer.kernel_spec_index >= program.moe_kernel_specs.size())
            return fail("E_ABI_BOUNDS", "kernel index out of table", error);
        kernel_indices.push_back(layer.kernel_spec_index);
        if (!verifyGroupBounds(program, layer, error))
            return false;
    }
    if (experts_offset != program.moe_expert_specs.size())
        return fail("E_ABI_ORDER", "expert table has unowned records", error);
    if (regions_offset != program.moe_dynamic_regions.size())
        return fail("E_ABI_ORDER", "region table has unowned records", error);
    std::sort(kernel_indices.begin(), kernel_indices.end());
    if (program.moe_kernel_specs.size() != layers.size())
        return fail("E_ABI_DUPLICATE",
                    "each layer must own exactly one kernel record", error);
    for (size_t i = 0; i < kernel_indices.size(); i++)
        if (kernel_indices[i] != i)
            return fail("E_ABI_DUPLICATE",
                        "each layer must own exactly one kernel record",
                        error);
    return true;
}

const mesh_abi::Tensor *weightTensor(const DecodedProgram &program,
                                     uint32_t symbol_id, MeshLoadError &error)
{
    const mesh_abi::Relocation *relocation = nullptr;
    for (const auto &candidate : program.relocations)
        if (candidate.symbol_sid == symbol_id)
            relocation = &candidate;
    if (relocation == nullptr) {
        fail("E_RELOCATION", "weight symbol is not relocated", error);
        return nullptr;
    }
    for (const auto &tensor : program.tensors)
        if (tensor.tensor_id == relocation->tensor_id) {
            if (tensor.role != mesh_abi::kTensorRoleWEIGHT ||
                tensor.storage_class != mesh_abi::kStorageClassEXTERNAL ||
                tensor.access != mesh_abi::kAccessKindREAD_ONLY ||
                (tensor.flags &
                 mesh_abi::kTensorFlagsHAS_CONTENT_SHA256) == 0) {
                fail("E_RELOCATION",
                     "MoE weight symbol must be read-only persistent external",
                     error);
                return nullptr;
            }
            return &tensor;
        }
    fail("E_RELOCATION", "weight symbol tensor is missing", error);
    return nullptr;
}

bool verifyExperts(const DecodedProgram &program, const RuntimeArch &arch,
                   MeshLoadError &error)
{
    for (const auto &layer : program.moe_layer_specs) {
        for (uint32_t ordinal = 0; ordinal < layer.expert_count; ordinal++) {
            const auto *expert = expertOf(program, layer, ordinal);
            if (expert->layer_id != layer.layer_id)
                return fail("E_ABI_BOUNDS", "expert layer mismatch", error);
            if (expert->expert_id != ordinal)
                return fail("E_ABI_ORDER",
                            "expert ids must be dense inside the layer",
                            error);
            if (expert->flags != 0)
                return fail("E_ABI_RESERVED", "expert flags must be zero",
                            error);
            if (!arch.hasCore(expert->core_id))
                return fail("E_ABI_BOUNDS", "expert core not in arch", error);
            if (expert->weight_bytes == 0)
                return fail("E_ABI_BOUNDS", "expert weight is empty", error);
            if (expert->weight_region_offset > UINT64_MAX -
                    expert->weight_bytes)
                return fail("E_ABI_OVERFLOW",
                            "expert weight range overflows", error);
            const auto *tensor =
                weightTensor(program, expert->weight_symbol_id, error);
            if (tensor == nullptr)
                return false;
            bool shaped = false;
            const uint64_t backing = tensorBytes(*tensor, shaped);
            if (!shaped)
                return fail("E_ABI_BOUNDS", "weight tensor shape is unusable",
                            error);
            if (expert->weight_region_offset + expert->weight_bytes > backing)
                return fail("E_ABI_BOUNDS",
                            "expert weight range escapes its backing", error);
            if (expert->weight_digest_index == UINT32_MAX ||
                expert->weight_digest_index >= program.content_digests.size())
                return fail("E_ABI_BOUNDS",
                            "weight digest index out of table", error);
            const auto &digest =
                program.content_digests[expert->weight_digest_index];
            bool zero = true;
            for (uint8_t byte : digest.digest)
                zero = zero && byte == 0;
            if (digest.object_kind != mesh_abi::kTensorRoleWEIGHT ||
                digest.object_id != tensor->tensor_id || zero ||
                digest.digest != tensor->content_sha256)
                return fail("E_ABI_BOUNDS",
                            "weight digest does not match its tensor", error);
        }
    }
    return true;
}

bool verifyKernelShape(const mesh_abi::MoeKernelSpec &kernel,
                       MeshLoadError &error)
{
    const uint32_t in_bytes = dtypeBytes(kernel.input_dtype);
    const uint32_t out_bytes = dtypeBytes(kernel.output_dtype);
    const uint64_t planes = kernel.expert_opcode == mesh_abi::kOpcodeGEMM
                                ? 1
                                : kernel.batch;
    if (uint64_t(kernel.input_token_bytes) != planes * kernel.k * in_bytes ||
        uint64_t(kernel.output_token_bytes) != planes * kernel.n * out_bytes)
        return fail("E_ABI_BOUNDS",
                    "kernel token bytes disagree with its shape", error);
    if (uint64_t(kernel.weight_operand_bytes) !=
        planes * kernel.k * kernel.n * in_bytes)
        return fail("E_ABI_BOUNDS",
                    "kernel weight bytes disagree with its shape", error);
    return true;
}

bool verifyKernels(const DecodedProgram &program, MeshLoadError &error)
{
    for (const auto &layer : program.moe_layer_specs) {
        const auto &kernel = program.moe_kernel_specs[layer.kernel_spec_index];
        if (kernel.layer_id != layer.layer_id)
            return fail("E_ABI_BOUNDS", "kernel layer mismatch", error);
        if (kernel.expert_opcode != mesh_abi::kOpcodeGEMM &&
            kernel.expert_opcode != mesh_abi::kOpcodeBMM)
            return fail("E_ABI_ENUM", "kernel opcode must be GEMM or BMM",
                        error);
        if (kernel.flags != 0)
            return fail("E_ABI_RESERVED", "kernel flags must be zero", error);
        if (kernel.batch == 0 || kernel.n == 0 || kernel.k == 0 ||
            kernel.max_m == 0 || !powerOfTwo(kernel.expert_result_alignment))
            return fail("E_ABI_BOUNDS", "kernel geometry is invalid", error);
        if (kernel.max_m < layer.max_tokens_per_frozen_batch)
            return fail("E_ABI_BOUNDS", "kernel max_m below batch bound",
                        error);
        if (kernel.input_token_bytes != layer.token_bytes ||
            kernel.output_token_bytes != layer.output_token_bytes)
            return fail("E_ABI_BOUNDS",
                        "kernel token bytes disagree with the layer", error);
        if (!verifyKernelShape(kernel, error))
            return false;
        for (uint32_t ordinal = 0; ordinal < layer.expert_count; ordinal++) {
            const auto *expert = expertOf(program, layer, ordinal);
            if (expert->weight_bytes != kernel.weight_operand_bytes)
                return fail("E_ABI_BOUNDS",
                            "expert weight disagrees with the kernel", error);
        }
    }
    return true;
}

std::map<uint32_t, std::vector<uint32_t>>
commandPrerequisites(const DecodedProgram &program)
{
    std::map<uint32_t, std::vector<uint32_t>> producers;
    for (const auto &command : program.commands)
        if (command.signal_event != 0)
            producers[command.signal_event].push_back(command.command_id);
    for (const auto &descriptor : program.descriptors)
        producers[descriptor.completion_event].push_back(descriptor.command_id);

    std::map<uint32_t, std::vector<uint32_t>> prerequisites;
    for (const auto &command : program.commands)
        prerequisites[command.command_id];
    for (const auto &stream : program.streams) {
        const size_t begin = stream.command_begin;
        for (size_t i = 1; i < stream.command_count; i++) {
            const uint32_t previous = program.commands[begin + i - 1].command_id;
            const uint32_t current = program.commands[begin + i].command_id;
            prerequisites[current].push_back(previous);
        }
    }
    for (const auto &command : program.commands) {
        for (uint16_t w = 0; w < command.wait_count; w++) {
            const uint32_t event_id =
                program.waits[size_t(command.wait_begin) + w];
            for (uint32_t producer : producers[event_id])
                prerequisites[command.command_id].push_back(producer);
        }
    }
    return prerequisites;
}

bool prerequisiteOf(
    const std::map<uint32_t, std::vector<uint32_t>> &prerequisites,
    uint32_t target, uint32_t prerequisite)
{
    std::set<uint32_t> seen;
    const auto entry = prerequisites.find(target);
    std::vector<uint32_t> pending =
        entry == prerequisites.end() ? std::vector<uint32_t>() : entry->second;
    while (!pending.empty()) {
        const uint32_t node = pending.back();
        pending.pop_back();
        if (seen.count(node) != 0)
            continue;
        seen.insert(node);
        const auto next = prerequisites.find(node);
        if (next != prerequisites.end())
            pending.insert(pending.end(), next->second.begin(),
                           next->second.end());
    }
    return seen.count(prerequisite) != 0;
}

const mesh_abi::Command *uniqueOpcode(const DecodedProgram &program,
                                      uint16_t opcode, int16_t core_id,
                                      size_t &count)
{
    const mesh_abi::Command *found = nullptr;
    count = 0;
    for (const auto &command : program.commands) {
        if (command.opcode != opcode)
            continue;
        if (core_id >= 0 && command.core_id != uint16_t(core_id))
            continue;
        count++;
        found = &command;
    }
    return found;
}

bool verifyRegionDomination(const DecodedProgram &program,
                            const mesh_abi::MoeDynamicRegion &region,
                            const mesh_abi::Command &insert,
                            const mesh_abi::Command &resume,
                            MeshLoadError &error)
{
    size_t begins = 0;
    size_t ends = 0;
    size_t halts = 0;
    const auto *begin =
        uniqueOpcode(program, mesh_abi::kOpcodeREQUEST_BEGIN, -1, begins);
    const auto *end =
        uniqueOpcode(program, mesh_abi::kOpcodeREQUEST_END, -1, ends);
    const auto *halt =
        uniqueOpcode(program, mesh_abi::kOpcodeHALT, region.core_id, halts);
    if (begins != 1 || ends != 1)
        return fail("E_LIFECYCLE", "instance lifecycle must be unique", error);
    if (halts != 1)
        return fail("E_LIFECYCLE",
                    "region core must hold exactly one local halt", error);
    const auto prerequisites = commandPrerequisites(program);
    if (!prerequisiteOf(prerequisites, insert.command_id, begin->command_id))
        return fail("E_LIFECYCLE",
                    "request begin must precede the region gate", error);
    if (!prerequisiteOf(prerequisites, halt->command_id, resume.command_id))
        return fail("E_LIFECYCLE",
                    "region gate must precede the local halt", error);
    if (!prerequisiteOf(prerequisites, end->command_id, resume.command_id))
        return fail("E_LIFECYCLE",
                    "region gate must precede request end", error);
    return true;
}

bool verifyRegionGate(const DecodedProgram &program,
                      const mesh_abi::MoeDynamicRegion &region,
                      MeshLoadError &error)
{
    const mesh_abi::Stream *stream = nullptr;
    for (const auto &candidate : program.streams)
        if (candidate.core_id == region.core_id &&
            candidate.stream_id == region.stream_id)
            stream = &candidate;
    if (stream == nullptr)
        return fail("E_STREAM_CONTRACT", "region stream missing", error);
    std::vector<const mesh_abi::Command *> commands;
    for (const auto &command : program.commands)
        if (command.core_id == region.core_id &&
            command.stream_id == region.stream_id)
            commands.push_back(&command);
    int64_t insert_index = -1;
    int64_t resume_index = -1;
    for (size_t i = 0; i < commands.size(); i++) {
        const auto *command = commands[i];
        if (command->command_id == region.insert_after_command_id)
            insert_index = i;
        if (command->command_id == region.resume_before_command_id)
            resume_index = i;
    }
    if (insert_index < 0 || resume_index < 0)
        return fail("E_ABI_BOUNDS", "region gate commands are missing", error);
    if (resume_index != insert_index + 1)
        return fail("E_ABI_BOUNDS", "region gate commands must be adjacent",
                    error);
    const auto *insert = commands[insert_index];
    if (insert->signal_event != region.entry_event_id)
        return fail("E_ABI_BOUNDS",
                    "region entry event must be the gate signal", error);
    const mesh_abi::Event *event = nullptr;
    for (const auto &candidate : program.events)
        if (candidate.event_id == region.entry_event_id)
            event = &candidate;
    if (event == nullptr || event->producer_command_id != insert->command_id)
        return fail("E_ABI_BOUNDS",
                    "region entry event has no matching producer", error);
    if (!verifyRegionDomination(program, region, *insert,
                                *commands[resume_index], error))
        return false;
    for (const auto *command : commands) {
        if (command->opcode != mesh_abi::kOpcodeREPEAT)
            continue;
        if (command->attr_index == 0 ||
            command->attr_index > program.attrs.size())
            return fail("E_ABI_BOUNDS", "REPEAT attr index out of table",
                        error);
        const auto &attr = program.attrs[command->attr_index - 1];
        const auto *repeat = std::get_if<mesh_abi::RepeatV1>(&attr.typed);
        if (repeat == nullptr)
            return fail("E_ABI_ENUM", "REPEAT requires REPEAT_V1 attr", error);
        const uint64_t begin = repeat->subrange_begin_stream_ordinal;
        const uint64_t count = repeat->subrange_command_count;
        if (uint64_t(insert_index) >= begin &&
            uint64_t(insert_index) < begin + count)
            return fail("E_ABI_BOUNDS",
                        "region gate may not sit inside a REPEAT subrange",
                        error);
        if (uint64_t(resume_index) >= begin &&
            uint64_t(resume_index) < begin + count)
            return fail("E_ABI_BOUNDS",
                        "region gate may not sit inside a REPEAT subrange",
                        error);
    }
    return true;
}

bool verifyScratchPartition(const RuntimeArch &arch,
                           const mesh_abi::MoeDynamicRegion &region,
                           MeshLoadError &error)
{
    if (region.scratch_offset + region.scratch_bytes > arch.sram_bytes)
        return fail("E_ABI_BOUNDS", "region scratch escapes SRAM", error);
    if (arch.partitions.empty())
        return fail("E_ARCH_PARTITION",
                    "MoE feature needs a partitioned architecture", error);
    const auto *scratch =
        arch.partition(mesh_abi::kSramPartitionKindRUNTIME_SCRATCH);
    if (scratch == nullptr)
        return fail("E_ARCH_PARTITION",
                    "MoE feature needs a RUNTIME_SCRATCH partition", error);
    if (region.scratch_offset < scratch->base ||
        region.scratch_offset + region.scratch_bytes >
            scratch->base + scratch->bytes)
        return fail("E_ABI_BOUNDS",
                    "region scratch escapes RUNTIME_SCRATCH", error);
    return true;
}

bool verifyRegions(const DecodedProgram &program, const RuntimeArch &arch,
                   MeshLoadError &error)
{
    std::map<uint16_t, std::vector<std::pair<uint64_t, uint64_t>>> scratch;
    std::vector<std::vector<uint16_t>> core_sets;
    for (const auto &layer : program.moe_layer_specs) {
        std::vector<uint16_t> cores;
        for (uint32_t ordinal = 0; ordinal < layer.dynamic_region_count;
             ordinal++) {
            const auto *region = regionOf(program, layer, ordinal);
            if (region->region_id != ordinal + 1)
                return fail("E_ABI_ORDER",
                            "region ids must be core-ascending ordinals",
                            error);
            if (region->layer_id != layer.layer_id)
                return fail("E_ABI_BOUNDS", "region layer mismatch", error);
            if (region->flags != 0)
                return fail("E_ABI_RESERVED", "region flags must be zero",
                            error);
            if (!arch.hasCore(region->core_id))
                return fail("E_ABI_BOUNDS", "region core not in arch", error);
            if (!cores.empty() && region->core_id <= cores.back())
                return fail("E_ABI_ORDER",
                            "region cores must ascend without duplicates",
                            error);
            cores.push_back(region->core_id);
            if (region->scratch_bytes == 0 ||
                !powerOfTwo(region->scratch_alignment))
                return fail("E_ABI_BOUNDS",
                            "region scratch geometry is invalid", error);
            if (!verifyScratchPartition(arch, *region, error))
                return false;
            auto &intervals = scratch[region->core_id];
            for (const auto &other : intervals)
                if (region->scratch_offset < other.second &&
                    other.first < region->scratch_offset +
                                      region->scratch_bytes)
                    return fail("E_ABI_BOUNDS",
                                "same-core scratch intervals overlap", error);
            intervals.emplace_back(region->scratch_offset,
                                   region->scratch_offset +
                                       region->scratch_bytes);
            if (!verifyRegionGate(program, *region, error))
                return false;
        }
        for (uint32_t ordinal = 0; ordinal < layer.expert_count; ordinal++) {
            const auto *expert = expertOf(program, layer, ordinal);
            if (std::find(cores.begin(), cores.end(), expert->core_id) ==
                cores.end())
                return fail("E_ABI_BOUNDS",
                            "expert core has no participating region", error);
        }
        core_sets.push_back(cores);
    }
    for (const auto &cores : core_sets)
        if (cores != core_sets.front())
            return fail("E_ABI_BOUNDS",
                        "every layer must share the same participating cores",
                        error);
    return true;
}

} // anonymous namespace

bool verifyMoeV1(const DecodedProgram &program, const RuntimeArch &arch,
                 MeshLoadError &error)
{
    using namespace mesh_abi;
    const bool enabled =
        (program.required_features & kFeatureDynamicMoeV1) != 0;
    const bool any_table = !program.moe_layer_specs.empty() ||
                           !program.moe_expert_specs.empty() ||
                           !program.moe_dynamic_regions.empty() ||
                           !program.moe_kernel_specs.empty() ||
                           !program.content_digests.empty();
    if ((program.required_features & ~kKnownFeatureMask) != 0)
        return fail("E_ABI_FEATURE", "unknown required feature bits", error);
    if (!enabled) {
        if (any_table)
            return fail("E_ABI_FEATURE",
                        "MoE sections without the feature bit", error);
        return true;
    }
    if (program.moe_layer_specs.empty() || program.moe_expert_specs.empty() ||
        program.moe_dynamic_regions.empty() ||
        program.moe_kernel_specs.empty() || program.content_digests.empty())
        return fail("E_ABI_BOUNDS",
                    "MoE feature requires every conditional section", error);
    return verifyLayers(program, error) &&
           verifyExperts(program, arch, error) &&
           verifyRegions(program, arch, error) &&
           verifyKernels(program, error);
}

} // namespace ai_mesh
} // namespace gem5
