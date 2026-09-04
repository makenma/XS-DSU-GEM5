#include "dev/ai_mesh/mesh_ir_verifier.hh"

#include "dev/ai_mesh/mesh_splitter.hh"

#include <algorithm>
#include <functional>
#include <map>
#include <set>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
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

// Span of the endpoint on one side: the stride of THAT side shapes its
// own span (the local and remote strides are independent).
uint64_t endpointSpan(const DecodedDmaDescriptor &descriptor, bool src_side)
{
    if (descriptor.rows == 0 || descriptor.row_bytes == 0)
        return 0;
    if (descriptor.rows <= 1)
        return descriptor.row_bytes;
    const uint64_t stride = src_side ? descriptor.src_stride_bytes
                                     : descriptor.dst_stride_bytes;
    bool overflow = false;
    const uint64_t span =
        checkedMulAdd(stride, descriptor.rows - 1, descriptor.row_bytes,
                      overflow);
    return overflow ? UINT64_MAX : span;
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

bool verifyDecodedProgram(const DecodedProgram &program, const RuntimeArch &arch,
                          MeshLoadError &error)
{
    using namespace mesh_abi;

    if (program.arch_digest_hex != arch.arch_digest_hex)
        return fail("E_ARCH_DIGEST", "architecture digest mismatch", error);

    // command ids unique and sorted
    for (size_t i = 1; i < program.commands.size(); i++) {
        if (program.commands[i - 1].command_id >= program.commands[i].command_id)
            return fail("E_ABI_ORDER", "commands must be sorted by command_id", error);
    }

    std::map<uint32_t, size_t> command_index;
    for (size_t i = 0; i < program.commands.size(); i++)
        command_index[program.commands[i].command_id] = i;

    std::map<uint32_t, const DecodedEvent *> events;
    for (const auto &event : program.events) {
        if (events.count(event.event_id))
            return fail("E_ABI_DUPLICATE", "duplicate event id", error);
        events[event.event_id] = &event;
    }

    // Unique ids, string-table bounds and rank limits (mirrors Python).
    {
        std::set<uint32_t> tensor_ids;
        for (const auto &tensor : program.tensors) {
            if (!tensor_ids.insert(tensor.tensor_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate tensor_id", error);
            if (tensor.name_sid == 0 ||
                tensor.name_sid > program.strings.size())
                return fail("E_ABI_BOUNDS", "string id out of range", error);
            if (tensor.rank > 8)
                return fail("E_ABI_BOUNDS", "tensor rank > 8", error);
            if (tensor.layout == kLayoutKindBLOCKED_MNK &&
                tensor.layout_attr == 0)
                return fail("E_ABI_BOUNDS",
                            "BLOCKED_MNK tensor requires layout attr", error);
        }
        std::set<uint32_t> profile_ids;
        for (const auto &profile : program.profiles) {
            if (!profile_ids.insert(profile.profile_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate profile_id", error);
            if (profile.name_sid == 0 ||
                profile.name_sid > program.strings.size())
                return fail("E_ABI_BOUNDS", "string id out of range", error);
            if (profile.rank > 8)
                return fail("E_ABI_BOUNDS", "profile rank > 8", error);
        }
        std::set<uint32_t> entrypoint_ids;
        for (const auto &entrypoint : program.entrypoints) {
            if (!entrypoint_ids.insert(entrypoint.entrypoint_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate entrypoint_id",
                            error);
            if (entrypoint.name_sid == 0 ||
                entrypoint.name_sid > program.strings.size())
                return fail("E_ABI_BOUNDS", "string id out of range", error);
        }
        std::set<uint32_t> shard_ids;
        for (const auto &shard : program.shards)
            if (!shard_ids.insert(shard.shard_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate shard_id", error);
        std::set<uint32_t> allocation_ids;
        for (const auto &allocation : program.allocations)
            if (!allocation_ids.insert(allocation.allocation_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate allocation_id",
                            error);
        std::set<uint32_t> descriptor_ids;
        for (const auto &descriptor : program.descriptors)
            if (!descriptor_ids.insert(descriptor.descriptor_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate descriptor_id",
                            error);
        std::set<uint32_t> relocation_ids;
        std::set<std::string> relocation_symbols;
        for (const auto &relocation : program.relocations) {
            if (!relocation_ids.insert(relocation.relocation_id).second)
                return fail("E_ABI_DUPLICATE", "duplicate relocation_id",
                            error);
            if (relocation.symbol_sid == 0 ||
                relocation.symbol_sid > program.strings.size())
                return fail("E_ABI_BOUNDS", "string id out of range", error);
            if (!relocation_symbols.insert(
                    program.strings[relocation.symbol_sid - 1]).second)
                return fail("E_RELOCATION", "duplicate relocation symbol",
                            error);
        }
    }

    // opcode/engine closed set + waits/operands bounds
    for (const auto &command : program.commands) {
        if (!arch.hasCore(command.core_id))
            return fail("E_ABI_BOUNDS", "command core not in arch", error);
        if (opcodeEngine(command.opcode) == 0)
            return fail("E_ABI_ENUM", "unknown opcode", error);
        if (command.engine != opcodeEngine(command.opcode))
            return fail("E_ENGINE_MISMATCH", "opcode does not map to engine", error);
        if (!spanFits(command.wait_begin, command.wait_count,
                      program.waits.size()))
            return fail("E_ABI_BOUNDS", "wait range out of table", error);
        if (!spanFits(command.operand_begin, command.operand_count,
                      program.operands.size()))
            return fail("E_ABI_BOUNDS", "operand range out of table", error);
        for (uint16_t w = 0; w < command.wait_count; w++)
            if (!events.count(
                    program.waits[size_t(command.wait_begin) + w]))
                return fail("E_ABI_BOUNDS", "wait references unknown event", error);
        if (command.signal_event && !events.count(command.signal_event))
            return fail("E_ABI_BOUNDS", "signal event unknown", error);
    }

    // streams: unique, control stream with exactly one HALT, partition table
    std::map<std::pair<uint16_t, uint16_t>, bool> stream_keys;
    std::set<uint16_t> cores_seen;
    for (const auto &stream : program.streams) {
        if (stream.flags & ~(uint16_t(1) | uint16_t(2)))
            return fail("E_ABI_ENUM", "stream flags have unknown bits", error);
        auto key = std::make_pair(stream.core_id, stream.stream_id);
        if (stream_keys.count(key))
            return fail("E_ABI_DUPLICATE", "duplicate stream", error);
        stream_keys[key] = true;
        if (!arch.hasCore(stream.core_id))
            return fail("E_ABI_BOUNDS", "stream core not in arch", error);
        if (!spanFits(stream.command_begin, stream.command_count,
                      program.commands.size()))
            return fail("E_ABI_BOUNDS", "stream range out of table", error);
        cores_seen.insert(stream.core_id);
    }
    std::map<uint16_t, int> control_streams;
    for (const auto &stream : program.streams)
        if (stream.flags & kStreamFlagsIS_LOCAL_CONTROL)
            control_streams[stream.core_id]++;
    for (uint16_t core : cores_seen)
        if (control_streams[core] != 1)
            return fail("E_STREAM_CONTRACT", "core must have exactly one "
                        "local control stream", error);
    std::vector<uint32_t> covered;
    for (const auto &stream : program.streams) {
        int halts = 0;
        bool control = stream.flags & kStreamFlagsIS_LOCAL_CONTROL;
        for (uint32_t i = 0; i < stream.command_count; i++) {
            const auto &command =
                program.commands[size_t(stream.command_begin) + i];
            covered.push_back(command.command_id);
            if (command.core_id != stream.core_id || command.stream_id != stream.stream_id)
                return fail("E_STREAM_CONTRACT", "command core/stream mismatch", error);
            if (command.opcode == kOpcodeHALT) {
                if (!control)
                    return fail("E_STREAM_CONTRACT", "HALT outside control stream", error);
                halts++;
            }
        }
        if (control && halts != 1)
            return fail("E_STREAM_CONTRACT", "control stream must hold exactly one HALT", error);
    }
    if (covered.size() != program.commands.size())
        return fail("E_STREAM_CONTRACT", "streams must partition commands", error);
    std::sort(covered.begin(), covered.end());
    for (size_t i = 0; i < covered.size(); i++)
        if (covered[i] != program.commands[i].command_id)
            return fail("E_STREAM_CONTRACT", "stream ranges must partition commands", error);

    // events: producer rules (mirrors the Python verifier exactly)
    std::map<uint32_t, std::vector<uint32_t>> producers;
    std::map<uint32_t, std::vector<const DecodedCommand *>> producer_cmds;
    for (const auto &command : program.commands) {
        if (command.signal_event) {
            producers[command.signal_event].push_back(command.command_id);
            producer_cmds[command.signal_event].push_back(&command);
        }
    }
    for (const auto &descriptor : program.descriptors)
        producers[descriptor.completion_event].push_back(descriptor.command_id);
    for (const auto &kv : events) {
        const DecodedEvent &event = *kv.second;
        if (event.kind != kEventKindNORMAL && event.kind != kEventKindBARRIER)
            return fail("E_ABI_ENUM", "event kind not in closed set", error);
        if (event.expected_arrivals < 1)
            return fail("E_ABI_BOUNDS", "expected_arrivals must be >= 1", error);
        const auto &event_producers = producers[event.event_id];
        if (event.kind == kEventKindNORMAL) {
            if (event_producers.size() == 0)
                return fail("E_EVENT_NO_PRODUCER", "event has no producer", error);
            if (event_producers.size() > 1)
                return fail("E_EVENT_MULTIPLE_PRODUCERS", "multiple producers", error);
            if (event.producer_command_id != event_producers[0])
                return fail("E_EVENT_NO_PRODUCER",
                            "event producer_command_id mismatch", error);
        } else {
            for (const DecodedCommand *producer :
                 producer_cmds[event.event_id])
                if (producer->opcode != kOpcodeBARRIER)
                    return fail("E_EVENT_MULTIPLE_PRODUCERS",
                                "barrier event signaled by non-BARRIER "
                                "command", error);
            for (const auto &descriptor : program.descriptors)
                if (descriptor.completion_event == event.event_id)
                    return fail("E_EVENT_MULTIPLE_PRODUCERS",
                                "barrier event signaled by a DMA descriptor "
                                "completion", error);
            if (producer_cmds[event.event_id].size() !=
                event.expected_arrivals)
                return fail("E_ABI_BOUNDS",
                            "barrier arrivals != signaling BARRIER commands",
                            error);
        }
    }

    // allocations: CORE_SRAM only, in capacity, aligned, non-overlapping
    std::map<uint16_t, std::vector<const DecodedAllocation *>> per_core;
    for (const auto &allocation : program.allocations) {
        if (!arch.hasCore(allocation.owner_core))
            return fail("E_ABI_BOUNDS", "allocation core not in arch", error);
        if (allocation.memory_space != kMemorySpaceCORE_SRAM)
            return fail("E_ABI_BOUNDS", "V1 allocations must be CORE_SRAM",
                        error);
        if (allocation.alignment_bytes == 0 ||
            (allocation.alignment_bytes & (allocation.alignment_bytes - 1)))
            return fail("E_ABI_BOUNDS", "alignment must be power of two", error);
        if (allocation.offset_bytes % allocation.alignment_bytes)
            return fail("E_DMA_RANGE", "allocation offset misaligned", error);
        // Checked range: offset <= capacity && size <= capacity - offset.
        if (allocation.offset_bytes > arch.sram_bytes ||
            allocation.size_bytes > arch.sram_bytes - allocation.offset_bytes)
            return fail("E_SRAM_OOM", "allocation exceeds SRAM capacity", error);
        per_core[allocation.owner_core].push_back(&allocation);
    }
    for (auto &kv : per_core) {
        auto &list = kv.second;
        std::sort(list.begin(), list.end(),
                  [](const DecodedAllocation *a, const DecodedAllocation *b) {
                      return a->offset_bytes < b->offset_bytes;
                  });
        for (size_t i = 1; i < list.size(); i++)
            if (list[i]->offset_bytes < list[i - 1]->offset_bytes + list[i - 1]->size_bytes)
                return fail("E_DMA_RANGE", "SRAM allocations overlap", error);
    }

    // Shards: tensor/allocation references and checked span (mirrors Python).
    {
        std::set<uint32_t> tensor_ids;
        for (const auto &tensor : program.tensors)
            tensor_ids.insert(tensor.tensor_id);
        std::map<uint32_t, const DecodedAllocation *> allocation_by_id;
        for (const auto &allocation : program.allocations)
            allocation_by_id[allocation.allocation_id] = &allocation;
        for (const auto &shard : program.shards) {
            if (!tensor_ids.count(shard.tensor_id))
                return fail("E_ABI_BOUNDS", "shard tensor unknown", error);
            if (shard.allocation_id) {
                auto it = allocation_by_id.find(shard.allocation_id);
                if (it == allocation_by_id.end())
                    return fail("E_ABI_BOUNDS", "shard allocation unknown",
                                error);
                if (it->second->owner_core != shard.owner_core)
                    return fail("E_ABI_BOUNDS", "shard/allocation core "
                                "mismatch", error);
                if (shard.allocation_offset > it->second->size_bytes ||
                    shard.span_bytes >
                        it->second->size_bytes - shard.allocation_offset)
                    return fail("E_DMA_RANGE", "shard exceeds allocation",
                                error);
            }
            for (size_t d = 0; d < shard.local_shape.size(); d++)
                if (shard.valid_shape[d] > shard.local_shape[d])
                    return fail("E_ABI_BOUNDS",
                                "valid shape exceeds local shape", error);
        }
    }

    // descriptors: kind<->opcode, checked arithmetic, endpoint bounds
    std::set<uint32_t> transfers;
    for (const auto &descriptor : program.descriptors) {
        auto it = command_index.find(descriptor.command_id);
        if (it == command_index.end())
            return fail("E_ABI_BOUNDS", "descriptor command unknown", error);
        const auto &command = program.commands[it->second];
        uint16_t expected_kind = 0;
        switch (command.opcode) {
        case kOpcodeDMA_LOAD: expected_kind = kDmaKindLOAD; break;
        case kOpcodeDMA_STORE: expected_kind = kDmaKindSTORE; break;
        case kOpcodeDMA_P2P_PUSH: expected_kind = kDmaKindP2P_PUSH; break;
        case kOpcodeDMA_PREFETCH: expected_kind = kDmaKindPREFETCH; break;
        case kOpcodeDMA_FILL: expected_kind = kDmaKindLOCAL_FILL; break;
        default:
            return fail("E_ABI_ENUM", "command cannot own descriptor", error);
        }
        if (descriptor.kind != expected_kind)
            return fail("E_ABI_ENUM", "descriptor kind mismatch", error);
        if (command.core_id != descriptor.owner_core)
            return fail("E_ABI_BOUNDS", "descriptor owner mismatch", error);
        if (!events.count(descriptor.completion_event))
            return fail("E_ABI_BOUNDS", "descriptor completion event unknown",
                        error);
        bool overflow = false;
        uint64_t useful = checkedMul(descriptor.rows, descriptor.row_bytes, overflow);
        if (overflow || useful != descriptor.useful_bytes)
            return fail("E_ABI_OVERFLOW", "useful_bytes != rows * row_bytes", error);
        if (descriptor.max_burst_beats < 1 ||
            descriptor.max_burst_beats > arch.axi_max_burst_beats)
            return fail("E_DMA_RANGE", "max_burst_beats outside arch bounds", error);
        if (descriptor.physical_storage_bytes < descriptor.useful_bytes)
            return fail("E_DMA_RANGE", "physical < useful", error);
        if (descriptor.rows > 1) {
            if (descriptor.src_stride_bytes < descriptor.row_bytes)
                return fail("E_DMA_RANGE",
                            "src stride < row_bytes implies row overlap", error);
            if (descriptor.dst_stride_bytes < descriptor.row_bytes)
                return fail("E_DMA_RANGE",
                            "dst stride < row_bytes implies row overlap", error);
        }

        // Closed-set membership first (mirrors the Python verifier codes).
        for (const auto &endpoint : {descriptor.src, descriptor.dst}) {
            bool space_known = endpoint.memory_space >= 1 && endpoint.memory_space <= 4;
            if (!space_known)
                return fail("E_ABI_ENUM", "memory space not in closed set", error);
        }

        // Kind-specific endpoint direction rules (mirrors the Python
        // verifier; both sides must reject the same classes).
        auto is_remote = [](uint16_t space) -> bool {
            return space == kMemorySpaceHBM || space == kMemorySpaceHOST_SHARED;
        };
        if (descriptor.kind == kDmaKindLOAD || descriptor.kind == kDmaKindPREFETCH) {
            if (!is_remote(descriptor.src.memory_space))
                return fail("E_DMA_RANGE", "LOAD source must be HBM/HOST_SHARED", error);
            if (descriptor.dst.memory_space != kMemorySpaceCORE_SRAM)
                return fail("E_DMA_RANGE", "LOAD destination must be CORE_SRAM", error);
        } else if (descriptor.kind == kDmaKindSTORE) {
            if (descriptor.src.memory_space != kMemorySpaceCORE_SRAM)
                return fail("E_DMA_RANGE", "STORE source must be CORE_SRAM", error);
            if (!is_remote(descriptor.dst.memory_space))
                return fail("E_DMA_RANGE", "STORE destination must be HBM/HOST_SHARED", error);
        } else if (descriptor.kind == kDmaKindLOCAL_FILL) {
            if (descriptor.dst.memory_space != kMemorySpaceCORE_SRAM)
                return fail("E_DMA_RANGE", "FILL destination must be CORE_SRAM", error);
        }

        bool local = false;
        for (const auto &side : {std::pair<const DecodedDmaEndpoint *, bool>{&descriptor.src, true},
                                 std::pair<const DecodedDmaEndpoint *, bool>{&descriptor.dst, false}}) {
            const DecodedDmaEndpoint &endpoint = *side.first;
            const RuntimeArch::Region *region = arch.region(endpoint.region_id);
            if (!region)
                return fail("E_ABI_BOUNDS", "endpoint region unknown", error);
            if (endpoint.shard_id) {
                const DecodedShard *shard_of_endpoint = nullptr;
                for (const auto &shard : program.shards)
                    if (shard.shard_id == endpoint.shard_id)
                        shard_of_endpoint = &shard;
                if (shard_of_endpoint &&
                    shard_of_endpoint->tensor_id != endpoint.tensor_id)
                    return fail("E_ABI_BOUNDS",
                                "endpoint shard belongs to a different tensor",
                                error);
            }
            const uint64_t span = endpointSpan(descriptor, side.second);
            uint64_t capacity = region->is_sram_aperture ? region->tile_bytes : region->bytes;
            const bool endpoint_is_sram =
                endpoint.memory_space == kMemorySpaceCORE_SRAM ||
                endpoint.memory_space == kMemorySpacePEER_SRAM;
            if (endpoint_is_sram && !region->is_sram_aperture)
                return fail("E_DMA_RANGE",
                            "endpoint memory space requires an SRAM aperture "
                            "region", error);
            if (!endpoint_is_sram && region->is_sram_aperture)
                return fail("E_DMA_RANGE",
                            "endpoint memory space / region kind mismatch",
                            error);
            if (!endpoint_is_sram) {
                if (endpoint.memory_space == kMemorySpaceHBM &&
                    region->kind != RuntimeArch::Region::kKindHbm)
                    return fail("E_DMA_RANGE",
                                "HBM endpoint requires an HBM region", error);
                if (endpoint.memory_space == kMemorySpaceHOST_SHARED &&
                    region->kind != RuntimeArch::Region::kKindHostShared)
                    return fail("E_DMA_RANGE",
                                "HOST_SHARED endpoint requires a HOST_SHARED "
                                "region", error);
                if (endpoint.owner_core != 0xFFFF)
                    return fail("E_ABI_BOUNDS",
                                "remote endpoint owner must use the no-core "
                                "sentinel", error);
            }
            if (endpoint.memory_space == kMemorySpaceCORE_SRAM || endpoint.memory_space == kMemorySpacePEER_SRAM) {
                if (!arch.hasCore(endpoint.owner_core))
                    return fail("E_ABI_BOUNDS", "endpoint core not in arch", error);
                if (endpoint.shard_id == 0)
                    return fail("E_DMA_RANGE",
                                "endpoint has no shard view", error);
                const DecodedShard *endpoint_shard = nullptr;
                for (const auto &shard : program.shards)
                    if (shard.shard_id == endpoint.shard_id)
                        endpoint_shard = &shard;
                const DecodedAllocation *shard_allocation = nullptr;
                if (endpoint_shard)
                    for (const auto &allocation : program.allocations)
                        if (allocation.allocation_id ==
                            endpoint_shard->allocation_id)
                            shard_allocation = &allocation;
                if (!shard_allocation)
                    return fail("E_ABI_BOUNDS",
                                "endpoint shard allocation unknown", error);
                const uint64_t view_start =
                    shard_allocation->offset_bytes +
                    endpoint_shard->allocation_offset;
                const uint64_t view_end = view_start + endpoint_shard->span_bytes;
                if (endpoint.offset_bytes < view_start ||
                    endpoint.offset_bytes > view_end ||
                    span > view_end - endpoint.offset_bytes)
                    return fail("E_DMA_RANGE",
                                "endpoint span escapes its shard view", error);
                if (endpoint.owner_core == descriptor.owner_core)
                    local = true;
            }
            // Checked arithmetic: reject instead of wrapping on u64 overflow.
            if (endpoint.offset_bytes > capacity || span > capacity ||
                endpoint.offset_bytes > capacity - span)
                return fail("E_DMA_RANGE", "endpoint exceeds region capacity", error);
        }
        if (!local)
            return fail("E_DMA_RANGE", "no endpoint on issuing core", error);
        if (descriptor.kind == kDmaKindP2P_PUSH) {
            if (descriptor.dst.memory_space != kMemorySpacePEER_SRAM)
                return fail("E_DMA_RANGE", "P2P destination must be PEER_SRAM", error);
            if (descriptor.dst.owner_core == descriptor.owner_core)
                return fail("E_DMA_RANGE", "P2P to self", error);
            if (transfers.count(descriptor.transfer_id))
                return fail("E_ABI_DUPLICATE", "duplicate transfer_id", error);
            transfers.insert(descriptor.transfer_id);
        }
    }

    // Every DMA command owns exactly one descriptor (mirrors Python).
    {
        auto dmaOpcode = [](uint16_t opcode) -> bool {
            switch (opcode) {
            case kOpcodeDMA_LOAD:
            case kOpcodeDMA_STORE:
            case kOpcodeDMA_P2P_PUSH:
            case kOpcodeDMA_PREFETCH:
            case kOpcodeDMA_FILL:
                return true;
            default:
                return false;
            }
        };
        std::map<uint32_t, int> descriptors_per_command;
        for (const auto &descriptor : program.descriptors)
            descriptors_per_command[descriptor.command_id]++;
        for (const auto &command : program.commands) {
            if (!dmaOpcode(command.opcode))
                continue;
            const int count = descriptors_per_command[command.command_id];
            if (count == 0)
                return fail("E_ABI_BOUNDS", "DMA command has no descriptor",
                            error);
            if (count > 1)
                return fail("E_ABI_DUPLICATE",
                            "multiple descriptors for DMA command", error);
        }
    }

    // Operand tensor/shard/allocation integrity mirrors the Python verifier.
    {
        std::set<uint32_t> tensor_ids;
        for (const auto &tensor : program.tensors)
            tensor_ids.insert(tensor.tensor_id);
        std::map<uint32_t, const DecodedShard *> shard_by_id;
        for (const auto &shard : program.shards)
            shard_by_id[shard.shard_id] = &shard;
        std::set<uint32_t> allocation_ids;
        for (const auto &allocation : program.allocations)
            allocation_ids.insert(allocation.allocation_id);
        for (const auto &command : program.commands) {
            const size_t operand_end =
                size_t(command.operand_begin) + command.operand_count;
            for (size_t i = command.operand_begin; i < operand_end; i++) {
                const auto &operand = program.operands[i];
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
                    if (it->second->owner_core != command.core_id)
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
        for (const auto &command : program.commands) {
            auto &shards = operand_shards_of[command.command_id];
            const size_t operand_end =
                size_t(command.operand_begin) + command.operand_count;
            for (size_t i = command.operand_begin; i < operand_end; i++)
                if (program.operands[i].shard_id)
                    shards.insert(program.operands[i].shard_id);
        }
        for (const auto &descriptor : program.descriptors) {
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

    // RECV_WAIT transfer matching + the full opcode<->attr contract
    // (mirrors the Python verifier: kind binding, REPEAT subrange rules,
    // GEMM workload overflow).
    auto attr_of = [&](const DecodedCommand &command,
                       const char *who) -> const DecodedAttr * {
        if (command.attr_index == 0 || command.attr_index > program.attrs.size()) {
            fail("E_ABI_BOUNDS", "attr missing", error);
            (void)who;
            return nullptr;
        }
        return &program.attrs[command.attr_index - 1];
    };
    for (const auto &command : program.commands) {
        switch (command.opcode) {
        case kOpcodeREPEAT: {
            const DecodedAttr *attr = attr_of(command, "REPEAT");
            if (!attr)
                return false;
            if (attr->kind != kAttrKindREPEAT_V1)
                return fail("E_ABI_ENUM", "REPEAT requires REPEAT_V1 attr",
                            error);
            const auto &repeat_attr = attr->template as<mesh_abi::RepeatV1>();
            const uint32_t begin = repeat_attr.subrange_begin_stream_ordinal;
            const uint32_t count = repeat_attr.subrange_command_count;
            const uint32_t repeat = repeat_attr.repeat_count;
            if (count == 0 || repeat < 1)
                return fail("E_ABI_BOUNDS", "REPEAT count invalid", error);
            const DecodedStream *stream = nullptr;
            for (const auto &candidate : program.streams)
                if (candidate.core_id == command.core_id &&
                    candidate.stream_id == command.stream_id)
                    stream = &candidate;
            if (!stream)
                return fail("E_ABI_BOUNDS", "REPEAT stream unknown", error);
            // Ordinal of the REPEAT inside its own stream.
            uint32_t ordinal = 0;
            bool found_repeat = false;
            for (uint32_t i = 0; i < stream->command_count; i++) {
                const DecodedCommand &member =
                    program.commands[stream->command_begin + i];
                if (member.command_id == command.command_id) {
                    ordinal = i;
                    found_repeat = true;
                    break;
                }
            }
            if (!found_repeat ||
                !spanFits(begin, count, stream->command_count) ||
                uint64_t(begin) + count != ordinal)
                return fail("E_ABI_BOUNDS", "REPEAT subrange must immediately "
                            "precede REPEAT in its stream", error);
            for (uint64_t i = begin; i < uint64_t(begin) + count; i++) {
                const DecodedCommand &member =
                    program.commands[size_t(stream->command_begin) + i];
                if (member.opcode == kOpcodeHALT ||
                    member.opcode == kOpcodeREQUEST_BEGIN ||
                    member.opcode == kOpcodeREQUEST_END ||
                    member.opcode == kOpcodeBARRIER ||
                    member.opcode == kOpcodeDMA_P2P_PUSH ||
                    member.opcode == kOpcodeRECV_WAIT ||
                    member.opcode == kOpcodeREPEAT)
                    return fail("E_ABI_BOUNDS",
                                "forbidden opcode inside REPEAT subrange",
                                error);
            }
            break;
        }
        case kOpcodeAXI_FENCE: {
            const DecodedAttr *attr = attr_of(command, "AXI_FENCE");
            if (!attr)
                return false;
            if (attr->kind != kAttrKindFENCE_V1)
                return fail("E_ABI_ENUM", "AXI_FENCE requires FENCE_V1 attr",
                            error);
            break;
        }
        case kOpcodeRECV_WAIT: {
            const DecodedAttr *attr = attr_of(command, "RECV_WAIT");
            if (!attr)
                return false;
            if (attr->kind != kAttrKindRECV_WAIT_V1)
                return fail("E_ABI_ENUM", "RECV_WAIT attr kind mismatch", error);
            if (!transfers.count(
                    attr->template as<mesh_abi::RecvWaitV1>().transfer_id))
                return fail("E_P2P_UNMATCHED",
                            "RECV_WAIT transfer has no P2P push", error);
            break;
        }
        case kOpcodeDMA_LOAD:
        case kOpcodeDMA_STORE:
        case kOpcodeDMA_P2P_PUSH:
        case kOpcodeDMA_PREFETCH:
            if (command.attr_index != 0)
                return fail("E_ABI_ENUM", "DMA command must not carry attr",
                            error);
            break;
        case kOpcodeDMA_FILL: {
            const DecodedAttr *attr = attr_of(command, "DMA_FILL");
            if (!attr)
                return false;
            if (attr->kind != kAttrKindFILL_V1)
                return fail("E_ABI_ENUM", "DMA_FILL requires FILL_V1 attr",
                            error);
            break;
        }
        case kOpcodeGEMM:
        case kOpcodeBMM: {
            const DecodedAttr *attr = attr_of(command, "GEMM/BMM");
            if (!attr)
                return false;
            const uint16_t expected = command.opcode == kOpcodeGEMM
                ? kAttrKindGEMM_V1 : kAttrKindBMM_V1;
            if (attr->kind != expected)
                return fail("E_ABI_ENUM", "GEMM/BMM attr kind mismatch",
                            error);
            const uint32_t efficiency =
                command.opcode == kOpcodeGEMM
                    ? attr->as<mesh_abi::GemmV1>().efficiency_q16
                    : attr->as<mesh_abi::BmmV1>().efficiency_q16;
            if (efficiency < 1 || efficiency > 65536)
                return fail("E_ABI_BOUNDS",
                            "efficiency_q16 outside [1, 65536]", error);
            const uint16_t gemm_dtype =
                command.opcode == kOpcodeGEMM
                    ? attr->as<mesh_abi::GemmV1>().dtype
                    : attr->as<mesh_abi::BmmV1>().dtype;
            if (!(arch.tensor_dtype_mask >> (gemm_dtype - 1) & 1u))
                return fail("E_CAPABILITY_MISMATCH",
                            "GEMM/BMM dtype has no tensor throughput "
                            "capability", error);
            uint32_t dims[4] = {0, 0, 0, 0};
            if (command.opcode == kOpcodeGEMM) {
                const auto &gemm = attr->as<mesh_abi::GemmV1>();
                dims[0] = gemm.batch;
                dims[1] = gemm.m;
                dims[2] = gemm.n;
                dims[3] = gemm.k;
            } else {
                const auto &bmm = attr->as<mesh_abi::BmmV1>();
                dims[0] = bmm.batch;
                dims[1] = bmm.m;
                dims[2] = bmm.n;
                dims[3] = bmm.k;
            }
            uint64_t work = 1;
            bool overflow = false;
            for (const uint64_t value : dims) {
                work = checkedMul(work, value, overflow);
                if (overflow || work >= (uint64_t(1) << 63))
                    return fail("E_ABI_OVERFLOW",
                                "GEMM workload overflows u64", error);
            }
            break;
        }
        case kOpcodeELEMENTWISE: {
            const DecodedAttr *attr = attr_of(command, "ELEMENTWISE");
            if (!attr)
                return false;
            if (attr->kind != kAttrKindELEMENTWISE_V1)
                return fail("E_ABI_ENUM", "ELEMENTWISE attr kind mismatch",
                            error);
            if (attr->as<mesh_abi::ElementwiseV1>().ops_per_element < 1)
                return fail("E_ABI_BOUNDS", "ops_per_element must be >= 1",
                            error);
            if (!(arch.vector_dtype_mask >>
                  (attr->as<mesh_abi::ElementwiseV1>().dtype - 1) & 1u))
                return fail("E_CAPABILITY_MISMATCH",
                            "elementwise dtype has no vector capability",
                            error);
            break;
        }
        case kOpcodeLOCAL_REDUCE: {
            const DecodedAttr *attr = attr_of(command, "LOCAL_REDUCE");
            if (!attr)
                return false;
            if (attr->kind != kAttrKindREDUCE_V1)
                return fail("E_ABI_ENUM", "LOCAL_REDUCE attr kind mismatch",
                            error);
            if (attr->as<mesh_abi::ReduceV1>().fan_in < 2)
                return fail("E_ABI_BOUNDS", "fan_in must be >= 2", error);
            if (!(arch.reduce_dtype_mask >>
                  (attr->as<mesh_abi::ReduceV1>().dtype - 1) & 1u))
                return fail("E_CAPABILITY_MISMATCH",
                            "reduce dtype has no reduce capability", error);
            break;
        }
        case kOpcodeSOFTMAX:
        case kOpcodeNORM: {
            const DecodedAttr *attr = attr_of(command, "SOFTMAX/NORM");
            if (!attr)
                return false;
            const uint16_t expected = command.opcode == kOpcodeSOFTMAX
                ? kAttrKindSOFTMAX_V1 : kAttrKindNORM_V1;
            if (attr->kind != expected)
                return fail("E_ABI_ENUM", "SOFTMAX/NORM attr kind mismatch",
                            error);
            const uint16_t dtype =
                command.opcode == kOpcodeSOFTMAX
                    ? attr->as<mesh_abi::SoftmaxV1>().dtype
                    : attr->as<mesh_abi::NormV1>().dtype;
            if (!(arch.vector_dtype_mask >> (dtype - 1) & 1u))
                return fail("E_CAPABILITY_MISMATCH",
                            "vector reduction dtype has no vector capability",
                            error);
            return fail("E_ABI_BOUNDS",
                        "vector reduction opcode requires a concrete phase "
                        "plan the V1 ABI cannot express", error);
        }
        default:
            break; // control opcodes carry no attr contract
        }
    }

    // Relocations reference declared tensors and arch regions.
    {
        std::set<uint32_t> tensor_ids;
        for (const auto &tensor : program.tensors)
            tensor_ids.insert(tensor.tensor_id);
        for (const auto &relocation : program.relocations) {
            if (!tensor_ids.count(relocation.tensor_id))
                return fail("E_RELOCATION", "relocation tensor unknown",
                            error);
            const RuntimeArch::Region *region =
                arch.region(relocation.region_id);
            if (!region)
                return fail("E_RELOCATION", "relocation region unknown",
                            error);
            if (relocation.offset_bytes >= region->bytes)
                return fail("E_RELOCATION", "relocation offset outside "
                            "region", error);
        }
    }

    // Opcode operand contract: count bounds and READ_ONLY/result-RW
    // access for every compute/DMA opcode (mirrors Python).
    {
        auto contract = [](uint16_t opcode, int &low, int &high,
                           bool &compute) -> bool {
            switch (opcode) {
            case kOpcodeGEMM:
            case kOpcodeBMM: low = 3; high = 3; compute = true; return true;
            case kOpcodeELEMENTWISE: low = 1; high = 2; compute = true; return true;
            case kOpcodeLOCAL_REDUCE: low = 1; high = 8; compute = true; return true;
            case kOpcodeSOFTMAX:
            case kOpcodeNORM: low = 1; high = 1; compute = true; return true;
            case kOpcodeDMA_LOAD:
            case kOpcodeDMA_STORE:
            case kOpcodeDMA_P2P_PUSH:
            case kOpcodeDMA_PREFETCH:
            case kOpcodeDMA_FILL: low = 1; high = 8; compute = false; return true;
            case kOpcodeRECV_WAIT: low = 1; high = 1; compute = false; return true;
            default: return false;
            }
        };
        for (const auto &command : program.commands) {
            int low = 0, high = 0;
            bool compute = false;
            if (!contract(command.opcode, low, high, compute))
                continue;
            if (command.operand_count < low || command.operand_count > high)
                return fail("E_ABI_BOUNDS",
                            "operand count outside the opcode contract",
                            error);
            if (compute && command.operand_count) {
                const DecodedOperand &result =
                    program.operands[size_t(command.operand_begin) +
                                     command.operand_count - 1];
                if (result.access != kAccessKindREAD_WRITE)
                    return fail("E_ABI_BOUNDS",
                                "result operand must be READ_WRITE", error);
            }
        }
    }

    std::set<uint32_t> recv_wait_transfers;
    for (const auto &command : program.commands) {
        if (command.opcode != kOpcodeRECV_WAIT)
            continue;
        const DecodedAttr *attr = attr_of(command, "RECV_WAIT");
        if (!attr)
            return false;
        const uint32_t transfer_id =
            attr->template as<mesh_abi::RecvWaitV1>().transfer_id;
        if (!transfers.count(transfer_id))
            return fail("E_P2P_UNMATCHED",
                        "RECV_WAIT transfer has no matching P2P push", error);
        if (!recv_wait_transfers.insert(transfer_id).second)
            return fail("E_P2P_UNMATCHED",
                        "two RECV_WAIT commands reference one transfer",
                        error);
    }

    // Expected traffic recompute (remote-side plan), mirroring Python
    // field-for-field: one row per descriptor, command/kind/useful match,
    // burst/beat/segments plan, and B/R/W counts.
    {
        std::map<uint32_t, const DecodedTrafficRow *> rows_by_descriptor;
        for (const auto &row : program.traffic) {
            if (rows_by_descriptor.count(row.descriptor_id))
                return fail("E_ABI_DUPLICATE", "duplicate traffic row", error);
            rows_by_descriptor[row.descriptor_id] = &row;
        }
        for (const auto &descriptor : program.descriptors) {
            if (!rows_by_descriptor.count(descriptor.descriptor_id))
                return fail("E_TRAFFIC_MISMATCH",
                            "descriptor has no expected traffic row", error);
        }
        for (const auto &row : program.traffic) {
            const DecodedDmaDescriptor *descriptor = nullptr;
            for (const auto &candidate : program.descriptors)
                if (candidate.descriptor_id == row.descriptor_id)
                    descriptor = &candidate;
            if (!descriptor)
                return fail("E_TRAFFIC_MISMATCH", "traffic row references "
                            "unknown descriptor", error);
            if (row.command_id != descriptor->command_id)
                return fail("E_TRAFFIC_MISMATCH", "traffic row command mismatch",
                            error);
            if (row.kind != descriptor->kind)
                return fail("E_TRAFFIC_MISMATCH", "traffic row kind mismatch",
                            error);
            if (descriptor->useful_bytes != row.useful_bytes)
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
                    row.r_beats != 0 || row.w_beats != 0)
                    return fail("E_TRAFFIC_MISMATCH",
                                "FILL traffic row must be all zero", error);
                continue;
            }
            const uint32_t width = arch.axi_data_bytes;
            const bool is_read = descriptor->kind == 1 || descriptor->kind == 4;
            const DecodedDmaEndpoint &remote = is_read ? descriptor->src : descriptor->dst;
            const uint64_t stride =
                is_read ? descriptor->src_stride_bytes : descriptor->dst_stride_bytes;
            const uint32_t limit = std::min(
                uint32_t(descriptor->max_burst_beats), arch.axi_max_burst_beats);
            uint32_t bursts = 0;
            uint64_t beats = 0;
            for (uint32_t r = 0; r < descriptor->rows; r++) {
                const uint64_t base = remote.offset_bytes + uint64_t(r) * stride;
                const auto list = splitBursts(base, descriptor->row_bytes, width,
                                              limit);
                bursts += uint32_t(list.size());
                for (const auto &burst : list)
                    beats += burst.beats;
            }
            // One row segment per non-empty row (plan_descriptor's
            // SegmentPlan.segments; zero-byte descriptors have none).
            const uint32_t segments =
                descriptor->row_bytes > 0 ? descriptor->rows : 0;
            if (row.bursts != bursts || row.physical_beat_bytes != beats * width)
                return fail("E_TRAFFIC_MISMATCH", "traffic burst plan mismatch",
                            error);
            if (row.segments != segments)
                return fail("E_TRAFFIC_MISMATCH", "traffic segments mismatch",
                            error);
            if (row.aw_count != (is_write ? bursts : 0) ||
                row.ar_count != (is_write ? 0 : bursts))
                return fail("E_TRAFFIC_MISMATCH", "traffic AW/AR mismatch",
                            error);
            if (row.b_count != row.aw_count)
                return fail("E_TRAFFIC_MISMATCH", "traffic B count mismatch",
                            error);
            if (row.r_beats != (is_write ? 0 : beats))
                return fail("E_TRAFFIC_MISMATCH", "traffic R beat mismatch",
                            error);
            if (row.w_beats != (is_write ? beats : 0))
                return fail("E_TRAFFIC_MISMATCH", "traffic W beat mismatch",
                            error);
        }
    }

    // Lifecycle contract (mirrors Python E_LIFECYCLE).
    for (const auto &entrypoint : program.entrypoints) {
        const DecodedStream *stream = nullptr;
        for (const auto &candidate : program.streams)
            if (candidate.core_id == entrypoint.lifecycle_core_id &&
                candidate.stream_id == entrypoint.lifecycle_stream_id)
                stream = &candidate;
        if (!stream)
            return fail("E_LIFECYCLE", "lifecycle stream missing", error);
        if (!spanFits(stream->command_begin, stream->command_count,
                      program.commands.size()))
            return fail("E_ABI_BOUNDS", "stream range out of table", error);
        const size_t begin = size_t(stream->command_begin);
        const size_t end = begin + stream->command_count;
        const DecodedCommand *request_begin = nullptr;
        const DecodedCommand *request_end = nullptr;
        size_t begin_index = 0;
        size_t end_index = 0;
        for (size_t i = begin; i < end; i++) {
            const auto &command = program.commands[i];
            if (command.opcode == kOpcodeREQUEST_BEGIN) {
                if (request_begin)
                    return fail("E_LIFECYCLE", "lifecycle stream must hold "
                                "exactly one REQUEST_BEGIN and one "
                                "REQUEST_END", error);
                request_begin = &command;
                begin_index = i - begin;
            }
            if (command.opcode == kOpcodeREQUEST_END) {
                if (request_end)
                    return fail("E_LIFECYCLE", "lifecycle stream must hold "
                                "exactly one REQUEST_BEGIN and one "
                                "REQUEST_END", error);
                request_end = &command;
                end_index = i - begin;
            }
        }
        if (!request_begin || !request_end)
            return fail("E_LIFECYCLE", "lifecycle stream must hold exactly "
                        "one REQUEST_BEGIN and one REQUEST_END", error);
        if (begin_index != 0)
            return fail("E_LIFECYCLE", "REQUEST_BEGIN must dominate all "
                        "instance work", error);
        if (end_index < begin_index)
            return fail("E_LIFECYCLE", "REQUEST_END before REQUEST_BEGIN",
                        error);
        if (!request_end->signal_event)
            return fail("E_LIFECYCLE", "REQUEST_END must signal an event",
                        error);
        if (!request_begin->signal_event)
            return fail("E_LIFECYCLE", "REQUEST_BEGIN must signal an event",
                        error);
    }

    // acyclicity over command-level happens-before via events
    std::map<uint32_t, std::vector<uint32_t>> edges;
    for (const auto &command : program.commands) {
        auto &targets = edges[command.command_id];
        for (uint16_t w = 0; w < command.wait_count; w++)
            for (uint32_t producer :
                 producers[program.waits[size_t(command.wait_begin) + w]])
                targets.push_back(producer);
        std::sort(targets.begin(), targets.end());
        targets.erase(std::unique(targets.begin(), targets.end()), targets.end());
    }
    std::map<uint32_t, int> state;
    std::vector<uint32_t> stack;
    std::function<bool(uint32_t)> visit = [&](uint32_t node) -> bool {
        int &mark = state[node];
        if (mark == 1)
            return false; // cycle
        if (mark == 2)
            return true;
        mark = 1;
        stack.push_back(node);
        for (uint32_t target : edges[node])
            if (!visit(target))
                return false;
        stack.pop_back();
        mark = 2;
        return true;
    };
    for (const auto &kv : edges)
        if (!visit(kv.first))
            return fail("E_DEPENDENCY_CYCLE", "command dependency cycle", error);

    return true;
}

} // namespace ai_mesh
} // namespace gem5
