#include "dev/ai_mesh/mesh_moe_materializer.hh"

#include <algorithm>
#include <cstring>
#include <map>
#include <optional>
#include <stdexcept>

#include "dev/ai_mesh/agent_sha256.hh"

namespace gem5::ai_mesh
{
namespace
{
using namespace mesh_abi;
using Ref = std::array<uint32_t, 3>;
using Token = std::array<uint8_t, 32>;
constexpr uint32_t None = 0xffff;

struct MaterializeFailure : std::runtime_error
{
    mesh_abi::MaterializerCheckSite site;
    agent_abi::DetailCode detail;
    MaterializeFailure(mesh_abi::MaterializerCheckSite check, const char *message,
        agent_abi::DetailCode code = agent_abi::E_MOE_PROFILE)
        : std::runtime_error(message), site(check), detail(code) {}
};

void append(std::vector<uint8_t> &bytes, uint64_t value, unsigned width)
{
    for (unsigned i = 0; i < width; ++i)
        bytes.push_back(value >> (8 * i));
}

std::vector<uint8_t> fillPattern(const char *key_domain, const char *block_domain,
    const MoeMaterializeConfig &config, const std::vector<uint8_t> &tail, uint32_t bytes)
{
    std::vector<uint8_t> key_bytes(key_domain, key_domain + std::strlen(key_domain) + 1);
    key_bytes.insert(key_bytes.end(), config.program_semantic_digest.begin(),
                     config.program_semantic_digest.end());
    key_bytes.insert(key_bytes.end(), config.workload_digest.begin(), config.workload_digest.end());
    key_bytes.insert(key_bytes.end(), tail.begin(), tail.end());
    const auto key = agentSha256(key_bytes);
    std::vector<uint8_t> result;
    result.reserve(bytes);
    for (uint64_t ordinal = 0; result.size() < bytes; ++ordinal) {
        std::vector<uint8_t> block(block_domain, block_domain + std::strlen(block_domain) + 1);
        block.insert(block.end(), key.begin(), key.end());
        append(block, ordinal, 8);
        const auto digest = agentSha256(block);
        result.insert(result.end(), digest.begin(),
                       digest.begin() + std::min<size_t>(digest.size(), bytes - result.size()));
    }
    return result;
}

struct RemoteChunk
{
    uint32_t phase, source, destination, expert, ordinal;
    uint32_t source_allocation, destination_allocation;
    uint32_t source_offset, destination_offset, bytes;
    std::vector<const MoeSelectedRoute *> rows;
};

class RemotePlan
{
  public:
    RemotePlan(const MoeLayerSpec &layer, const MoePopulation &population,
               const std::map<uint32_t, const MoeMemberSlice *> &members,
               const std::vector<const MoeExpertSpec *> &experts, uint32_t chunk_bytes)
    {
        if (chunk_bytes < std::max(layer.token_bytes, layer.output_token_bytes))
            throw MaterializeFailure(MaterializerCheckSite::BOUNDS, "E_MOE_MATERIALIZATION_V: transfer row exceeds chunk budget");
        for (const uint32_t phase : {kMoeTransferPhaseDISPATCH, kMoeTransferPhaseCOMBINE}) {
            std::vector<RemoteChunk> moves;
            std::vector<uint32_t> expert_rows(layer.expert_count);
            for (const auto &route : population.routes()) {
                if (!route.accepted)
                    continue;
                const auto row = expert_rows[route.expert]++;
                const auto &member = *members.at(route.token.member);
                const auto core = experts[route.expert]->core_id;
                if (member.core == core)
                    continue;
                const uint32_t token = route.token.uid.phase == 1 ? route.token.uid.token :
                    route.token.uid.sequence;
                if (token < member.first_token || token - member.first_token >= member.token_count)
                    throw MaterializeFailure(MaterializerCheckSite::BOUNDS, "E_MOE_MATERIALIZATION_V: remote token outside member");
                const auto local = token - member.first_token;
                if (phase == kMoeTransferPhaseDISPATCH)
                    moves.push_back({phase, member.core, core, route.expert, 0,
                        member.input_allocation, 0,
                        member.input_offset + local * layer.token_bytes,
                        row * layer.token_bytes, layer.token_bytes, {&route}});
                else
                    moves.push_back({phase, core, member.core, route.expert, 0,
                        0, member.output_allocation, row * layer.output_token_bytes,
                        member.output_offset + local * layer.output_token_bytes,
                        layer.output_token_bytes, {&route}});
            }
            std::sort(moves.begin(), moves.end(), [](const auto &left, const auto &right) {
                return std::tie(left.source, left.destination, left.expert, left.source_offset,
                                left.rows[0]->token.uid) <
                    std::tie(right.source, right.destination, right.expert, right.source_offset,
                             right.rows[0]->token.uid);
            });
            uint32_t ordinal = 0;
            for (auto &move : moves) {
                if (!chunks.empty()) {
                    auto &last = chunks.back();
                    if (last.phase == move.phase && last.source == move.source &&
                            last.destination == move.destination && last.expert == move.expert &&
                            last.source_allocation == move.source_allocation &&
                            last.destination_allocation == move.destination_allocation &&
                            uint64_t(last.source_offset) + last.bytes == move.source_offset &&
                            uint64_t(last.destination_offset) + last.bytes == move.destination_offset &&
                            last.bytes <= chunk_bytes - move.bytes) {
                        last.bytes += move.bytes;
                        last.rows.push_back(move.rows[0]);
                        continue;
                    }
                }
                move.ordinal = ordinal++;
                chunks.push_back(std::move(move));
            }
        }
    }
    std::vector<RemoteChunk> chunks;
};

class OverlayBuilder
{
  public:
    explicit OverlayBuilder(const MoeLayerSpec &layer, const std::vector<MoeDynamicRegion> &regions)
    {
        const uint64_t numerator = uint64_t(layer.max_routes) * layer.capacity_factor_q16;
        const uint64_t denominator = uint64_t(layer.expert_count) * 65536;
        const uint64_t padding = layer.overflow_policy == kMoeOverflowPolicyPAD_TO_CAPACITY ?
            (numerator / denominator + (numerator % denominator != 0)) * layer.expert_count : 0;
        limits = {layer.max_materialized_commands, layer.max_materialized_events,
            layer.max_materialized_descriptors, layer.max_materialized_transfers,
            layer.max_dynamic_allocations,
            4ULL * layer.max_routes + 2 * padding + 3ULL * layer.max_tokens_per_frozen_batch +
            3ULL * layer.max_materialized_commands + 2ULL * layer.max_materialized_descriptors +
            layer.max_dynamic_allocations + 2ULL * layer.max_materialized_transfers};
        uint64_t total = 0;
        for (const auto bound : limits)
            total += bound;
        if (total > objects.max_size())
            throw MaterializeFailure(MaterializerCheckSite::COUNT, "E_MOE_MATERIALIZE_CAPACITY: object storage exceeds host range", agent_abi::E_MOE_MATERIALIZE_CAPACITY);
        for (uint16_t kind = 0; kind < limits.size(); ++kind) {
            counts.emplace(std::make_pair(0, kind), 0);
            for (const auto &region : regions)
                counts.emplace(std::make_pair(region.region_id, kind), 0);
        }
        objects.reserve(total);
        payloads.reserve(layer.max_materialized_descriptors);
    }

    Ref add(MoeOverlayEntry entry)
    {
        if (entry.kind == kMeshObjectKindVIEW)
            for (const auto &existing : objects)
                if (existing.kind == entry.kind && existing.region_id == entry.region_id &&
                        existing.canonicalKey() == entry.canonicalKey())
                    return {existing.region_id, existing.kind, existing.ordinal};
        if (entry.kind >= limits.size() || used[entry.kind] == limits[entry.kind])
            throw MaterializeFailure(MaterializerCheckSite::COUNT, "E_MOE_MATERIALIZE_CAPACITY: frozen object storage exhausted", agent_abi::E_MOE_MATERIALIZE_CAPACITY);
        ++used[entry.kind];
        entry.ordinal = ++counts.at({entry.region_id, entry.kind});
        objects.push_back(entry);
        return {entry.region_id, entry.kind, entry.ordinal};
    }

    Ref allocation(uint32_t region, uint32_t core, uint32_t kind, uint32_t bytes,
                   uint32_t alignment, uint32_t expert = None,
                   uint32_t source = None, uint32_t phase = 0)
    {
        MoeOverlayEntry row;
        row.kind = kMeshObjectKindALLOCATION;
        row.region_id = region;
        row.owner_core = core;
        row.secondary_kind = kind;
        row.bytes = bytes;
        row.alignment = alignment;
        row.expert_id = expert;
        row.src_core = source;
        row.phase = phase;
        return add(row);
    }

    Ref view(uint32_t region, uint32_t core, uint32_t kind, uint32_t access,
             Ref backing, uint32_t offset, uint32_t bytes, uint32_t owner,
             uint32_t owner_ref, const Token &token = {},
             uint32_t backing_kind = kMoeViewBackingOVERLAY_ALLOCATION,
             uint32_t rows = 1)
    {
        MoeOverlayEntry row;
        row.kind = kMeshObjectKindVIEW;
        row.region_id = region;
        row.owner_core = core;
        row.secondary_kind = kind;
        row.access = access;
        row.ref_region = backing[0];
        row.ref_ordinal = backing[2];
        row.offset = offset;
        row.bytes = bytes;
        row.semantic_owner_kind = owner;
        row.semantic_owner_ref0 = owner_ref;
        row.token = token;
        row.backing_kind = backing_kind;
        row.validity_extent = rows;
        return add(row);
    }

    Ref operation(uint32_t region, uint32_t core, uint32_t phase, uint32_t opcode,
                  uint32_t role, uint32_t event_role, uint32_t event_phase,
                  const std::vector<Ref> &waits, const std::vector<Ref> &views,
                  uint32_t bytes, uint32_t expert = None, const Token &token = {},
                  Ref descriptor = {}, std::vector<Ref> *activity = nullptr,
                  const RemoteChunk *chunk = nullptr, bool emits = true)
    {
        if (waits.size() > MoeOverlayEntry::kMaxRefs || views.size() > MoeOverlayEntry::kMaxRefs)
            throw MaterializeFailure(MaterializerCheckSite::COUNT, "E_MOE_MATERIALIZE_CAPACITY: command references exhausted", agent_abi::E_MOE_MATERIALIZE_CAPACITY);
        MoeOverlayEntry event;
        event.kind = kMeshObjectKindEVENT;
        event.region_id = region;
        event.owner_core = core;
        event.secondary_kind = event_phase;
        event.role = event_role;
        event.expert_id = expert;
        event.token = token;
        event.wait_count = 1;
        const Ref command{region, kMeshObjectKindCOMMAND,
                          counts.at({region, kMeshObjectKindCOMMAND}) + 1};
        std::copy(command.begin(), command.end(), event.wait_refs[0]);
        if (chunk) {
            event.src_core = chunk->source;
            event.dst_core = chunk->destination;
            event.chunk_ordinal = chunk->ordinal;
        }
        const Ref done = emits ? add(event) : Ref{};
        MoeOverlayEntry row;
        row.kind = kMeshObjectKindCOMMAND;
        row.region_id = region;
        row.owner_core = core;
        row.phase = phase;
        row.secondary_kind = opcode;
        row.role = role;
        row.expert_id = expert;
        row.token = token;
        row.bytes = bytes;
        row.ref_region = descriptor[0];
        row.ref_ordinal = descriptor[2];
        row.wait_count = waits.size();
        row.view_count = views.size();
        row.signal_count = emits ? 1 : 0;
        for (size_t i = 0; i < waits.size(); ++i)
            std::copy(waits[i].begin(), waits[i].end(), row.wait_refs[i]);
        for (size_t i = 0; i < views.size(); ++i)
            std::copy(views[i].begin(), views[i].end(), row.view_refs[i]);
        std::copy(done.begin(), done.end(), row.signal_refs[0]);
        if (chunk) {
            row.src_core = chunk->source;
            row.dst_core = chunk->destination;
            row.chunk_ordinal = chunk->ordinal;
        }
        const auto installed = add(row);
        if (activity)
            activity->push_back(installed);
        return done;
    }

    Ref descriptor(uint32_t region, uint32_t core, uint32_t kind, Ref destination,
                   uint32_t bytes, uint32_t expert = None, const Token &token = {},
                   const std::vector<uint8_t> &payload = {})
    {
        MoeOverlayEntry row;
        row.kind = kMeshObjectKindDESCRIPTOR;
        row.region_id = region;
        row.owner_core = core;
        row.secondary_kind = kind;
        row.dst_core = core;
        row.role = kind == kMoeDescriptorKindSTREAMED_WEIGHT ? kMoeTrafficClassWEIGHT :
            kind == kMoeDescriptorKindDROPPED_TOKEN_FILL ? kMoeTrafficClassPARTIAL_RESULT :
            kMoeTrafficClassACTIVATION;
        row.expert_id = expert;
        row.token = token;
        row.bytes = bytes;
        row.dst_view_region = destination[0];
        row.dst_view_ordinal = destination[2];
        const auto result = add(row);
        if (!payload.empty()) {
            if (payload.size() != bytes)
                throw MaterializeFailure(MaterializerCheckSite::BOUNDS, "E_MOE_MATERIALIZATION_V: fill payload size differs");
            payloads.emplace_back(result, payload);
        }
        return result;
    }

    Ref transferEndpoint(uint32_t region, uint32_t core, const RemoteChunk &chunk,
                         Ref view, Ref wait, std::vector<Ref> &activity)
    {
        const bool sending = chunk.source == core;
        const bool dispatch = chunk.phase == kMoeTransferPhaseDISPATCH;
        MoeOverlayEntry row;
        row.kind = kMeshObjectKindDESCRIPTOR;
        row.region_id = region;
        row.owner_core = core;
        row.secondary_kind = dispatch ? kMoeDescriptorKindDISPATCH : kMoeDescriptorKindCOMBINE;
        row.phase = chunk.phase;
        row.src_core = chunk.source;
        row.dst_core = chunk.destination;
        row.expert_id = chunk.expert;
        row.chunk_ordinal = chunk.ordinal;
        row.bytes = chunk.bytes;
        row.role = dispatch ? kMoeTrafficClassMOE_DISPATCH : kMoeTrafficClassMOE_COMBINE;
        if (sending) {
            row.src_view_region = view[0];
            row.src_view_ordinal = view[2];
        } else {
            row.dst_view_region = view[0];
            row.dst_view_ordinal = view[2];
        }
        const auto descriptor = add(row);
        return operation(region, core,
            dispatch ? (sending ? kMoeCommandPhaseDISPATCH_PUSH : kMoeCommandPhaseDISPATCH_WAIT) :
                       (sending ? kMoeCommandPhaseCOMBINE_PUSH : kMoeCommandPhaseCOMBINE_WAIT),
            sending ? kOpcodeDMA_P2P_PUSH : kOpcodeRECV_WAIT,
            dispatch ? (sending ? kMoeCommandRoleDISPATCH_SENDER : kMoeCommandRoleDISPATCH_RECEIVER) :
                       (sending ? kMoeCommandRoleCOMBINE_SENDER : kMoeCommandRoleCOMBINE_RECEIVER),
            dispatch ? kMoeEventRoleDISPATCH_READY : kMoeEventRoleCOMBINE_READY,
            dispatch ? kMoeEventPhaseDISPATCH : kMoeEventPhaseCOMBINE,
            {wait}, {}, chunk.bytes, chunk.expert, {}, descriptor, &activity, &chunk, !sending);
    }

    bool finish(const MoeLayerSpec &layer, const std::vector<MoeDynamicRegion> &regions,
                const MoeMaterializeConfig &config, MoeOverlayGraph &out, std::string &error)
    {
        const std::pair<uint16_t, uint32_t> bounds[] = {
            {kMeshObjectKindCOMMAND, layer.max_materialized_commands},
            {kMeshObjectKindEVENT, layer.max_materialized_events},
            {kMeshObjectKindDESCRIPTOR, layer.max_materialized_descriptors},
            {kMeshObjectKindALLOCATION, layer.max_dynamic_allocations},
            {kMeshObjectKindTRANSFER, layer.max_materialized_transfers}};
        for (const auto &[kind, limit] : bounds)
            if (std::count_if(objects.begin(), objects.end(), [kind](const auto &row) {
                    return row.kind == kind;
                }) > limit) {
                throw MaterializeFailure(MaterializerCheckSite::COUNT,
                    "E_MOE_MATERIALIZE_CAPACITY: group object capacity exhausted",
                    agent_abi::E_MOE_MATERIALIZE_CAPACITY);
            }
        MoeOverlayGraph graph;
        graph.setProgramDigest(config.program_digest.data());
        graph.setFillMode(config.fill_mode);
        for (const auto &row : objects)
            graph.add(row);
        uint64_t offset = 0;
        std::sort(payloads.begin(), payloads.end(), [](const auto &left, const auto &right) {
            return left.first < right.first;
        });
        for (const auto &[ref, payload] : payloads) {
            graph.addPayload({ref[0], ref[2], offset, payload.size()});
            graph.addPayloadBytes(payload.data(), payload.size());
            offset += payload.size();
        }
        if (!graph.finalize(regions, error))
            return false;
        out = std::move(graph);
        return true;
    }

  private:
    std::vector<MoeOverlayEntry> objects;
    std::map<std::pair<uint32_t, uint32_t>, uint32_t> counts;
    std::vector<std::pair<Ref, std::vector<uint8_t>>> payloads;
    std::array<uint64_t, 6> limits{};
    std::array<uint64_t, 6> used{};
};
}

bool materializeMoeOverlay(const DecodedProgram &program, const mesh_abi::MoeLayerSpec &layer,
    const MoePopulation &population, const std::vector<MoeMemberSlice> &members,
    const MoeMaterializeConfig &config, MoeOverlayGraph &out, MoePreparationError &error)
{
    using namespace mesh_abi;
    out = {};
    try {
        if (!layer.expert_count || !layer.top_k ||
                layer.kernel_spec_index >= program.transport.moe_kernel_specs.size() ||
                members.empty() || members.size() > layer.max_requests_per_batch ||
                population.loads().size() != layer.expert_count)
            throw MaterializeFailure(MaterializerCheckSite::COUNT, "E_MOE_MATERIALIZATION_V: invalid frozen population");
        const auto &kernel = program.transport.moe_kernel_specs[layer.kernel_spec_index];
        std::vector<MoeDynamicRegion> regions;
        for (const auto &region : program.transport.moe_dynamic_regions)
            if (region.layer_id == layer.layer_id)
                regions.push_back(region);
        if (regions.empty())
            throw MaterializeFailure(MaterializerCheckSite::BOUNDS, "E_MOE_MATERIALIZATION_V: missing dynamic regions");
        std::map<uint32_t, const MoeMemberSlice *> owners;
        for (const auto &member : members)
            if (!owners.emplace(member.ordinal, &member).second)
                throw MaterializeFailure(MaterializerCheckSite::KEY_COLLISION, "E_MOE_MATERIALIZATION_V: duplicate member ordinal");
        std::vector<const MoeExpertSpec *> experts(layer.expert_count);
        for (const auto &expert : program.transport.moe_expert_specs)
            if (expert.layer_id == layer.layer_id && expert.expert_id < experts.size())
                experts[expert.expert_id] = &expert;
        for (const auto *expert : experts)
            if (!expert)
                throw MaterializeFailure(MaterializerCheckSite::BOUNDS, "E_MOE_MATERIALIZATION_V: missing expert");
        const RemotePlan remote(layer, population, owners, experts, config.chunk_bytes);
        OverlayBuilder builder(layer, regions);
        for (const auto &chunk : remote.chunks) {
            MoeOverlayEntry transfer;
            transfer.kind = kMeshObjectKindTRANSFER;
            transfer.secondary_kind = chunk.phase;
            transfer.src_core = chunk.source;
            transfer.dst_core = chunk.destination;
            transfer.expert_id = chunk.expert;
            transfer.chunk_ordinal = chunk.ordinal;
            transfer.bytes = chunk.bytes;
            builder.add(transfer);
        }
        std::vector<Ref> terminals;
        for (const auto &region : regions) {
            const uint32_t core = region.core_id, id = region.region_id;
            const Ref entry{id, kMeshObjectKindEVENT, 0};
            std::vector<Ref> activity;
            std::vector<uint8_t> routes;
            for (const auto &route : population.routes()) {
                const auto *member = owners.at(route.token.member);
                if (member->core != core)
                    continue;
                const auto uid = route.token.uid.encode();
                routes.insert(routes.end(), uid.begin(), uid.end());
                append(routes, member->request_id, 8);
                append(routes, route.token.source_rank, 4);
                append(routes, route.token.uid.phase == 1 ? route.token.uid.token :
                       route.token.uid.sequence, 4);
                append(routes, route.slot, 2);
                append(routes, route.expert, 2);
                append(routes, route.accepted ? route.expert : None, 2);
                append(routes, route.accepted ? experts[route.expert]->core_id : None, 2);
                append(routes, route.accepted ? kRouteDispositionACCEPT : kRouteDispositionDROP, 1);
                append(routes, 0, 7);
            }
            Ref source_ready = entry;
            if (!routes.empty()) {
                const auto allocation = builder.allocation(id, core, kMoeAllocationKindROUTE_BUFFER,
                    routes.size(), 64);
                const auto view = builder.view(id, core, kMoeViewKindROUTE_METADATA,
                    kMoeViewAccessREAD_WRITE, allocation, 0, routes.size(),
                    kMoeSemanticOwnerREGION, id);
                const auto descriptor = builder.descriptor(id, core, kMoeDescriptorKindROUTE_FILL,
                    view, routes.size(), None, {}, routes);
                source_ready = builder.operation(id, core, kMoeCommandPhaseROUTE_FILL, kOpcodeDMA_FILL,
                    kMoeCommandRoleROUTE_FILL, kMoeEventRoleROUTE_FILL_DONE, kMoeEventPhaseFILL,
                    {entry}, {}, routes.size(), None, {}, descriptor, &activity);
            }
            std::map<uint32_t, std::vector<Ref>> dispatch_ready;
            std::map<std::pair<uint32_t, Token>, std::tuple<Ref, uint32_t, Ref>> received;
            for (const auto &chunk : remote.chunks) {
                if (chunk.phase != kMoeTransferPhaseDISPATCH)
                    continue;
                if (chunk.source == core) {
                    const auto view = builder.view(id, core, kMoeViewKindMEMBER_INPUT,
                        kMoeViewAccessREAD, {0, 0, chunk.source_allocation}, chunk.source_offset,
                        chunk.bytes, kMoeSemanticOwnerMEMBER, chunk.rows[0]->token.member,
                        chunk.rows[0]->token.uid.encode(), kMoeViewBackingSTATIC_ALLOCATION,
                        chunk.rows.size());
                    builder.transferEndpoint(id, core, chunk, view, source_ready, activity);
                }
                if (chunk.destination == core) {
                    const auto allocation = builder.allocation(id, core,
                        kMoeAllocationKindDISPATCH_REMOTE, chunk.bytes, config.bus_bytes,
                        chunk.expert, chunk.source, chunk.ordinal);
                    const auto view = builder.view(id, core, kMoeViewKindDISPATCH_BUFFER,
                        kMoeViewAccessREAD_WRITE, allocation, 0, chunk.bytes,
                        kMoeSemanticOwnerEXPERT, chunk.expert);
                    const auto ready = builder.transferEndpoint(id, core, chunk, view, entry, activity);
                    dispatch_ready[chunk.expert].push_back(ready);
                    for (size_t row = 0; row < chunk.rows.size(); ++row)
                        received[{chunk.expert, chunk.rows[row]->token.uid.encode()}] =
                            {allocation, row * layer.token_bytes, ready};
                }
            }
            std::map<uint32_t, std::pair<Ref, Ref>> results;
            for (uint32_t expert = 0; expert < layer.expert_count; ++expert) {
                const uint32_t rows = population.loads()[expert] + population.padding()[expert];
                if (!rows || experts[expert]->core_id != core)
                    continue;
                const auto allocation = builder.allocation(id, core, kMoeAllocationKindEXPERT_OUTPUT,
                    rows * layer.output_token_bytes, config.bus_bytes, expert);
                const auto output = builder.view(id, core, kMoeViewKindEXPERT_OUTPUT,
                    kMoeViewAccessREAD_WRITE, allocation, 0, rows * layer.output_token_bytes,
                    kMoeSemanticOwnerEXPERT, expert, {}, kMoeViewBackingOVERLAY_ALLOCATION, rows);
                Ref weight;
                std::vector<Ref> waits{entry};
                const uint32_t weight_bytes = experts[expert]->weight_bytes;
                if (config.cached) {
                    weight = builder.view(id, core, kMoeViewKindWEIGHT, kMoeViewAccessREAD,
                        {0, 0, config.weight_tags.at(expert)}, 0, weight_bytes,
                        kMoeSemanticOwnerEXPERT, expert, {}, kMoeViewBackingWEIGHT_CACHE_SLOT);
                } else {
                    const auto storage = builder.allocation(id, core,
                        kMoeAllocationKindSTREAMED_WEIGHT, weight_bytes,
                        std::max(region.scratch_alignment, config.bus_bytes), expert);
                    weight = builder.view(id, core, kMoeViewKindWEIGHT, kMoeViewAccessREAD,
                        storage, 0, weight_bytes, kMoeSemanticOwnerEXPERT, expert);
                    const auto descriptor = builder.descriptor(id, core,
                        kMoeDescriptorKindSTREAMED_WEIGHT, weight, weight_bytes, expert);
                    waits.push_back(builder.operation(id, core, kMoeCommandPhaseSTREAMED_WEIGHT_LOAD,
                        kOpcodeDMA_LOAD, kMoeCommandRoleSTREAMED_WEIGHT_LOAD, kMoeEventRoleWEIGHT_READY,
                        kMoeEventPhaseWEIGHT, {entry}, {}, weight_bytes, expert, {}, descriptor, &activity));
                }
                std::vector<Ref> operands{output, weight};
                std::optional<Ref> pad_view;
                if (population.padding()[expert]) {
                    std::vector<uint8_t> payload;
                    for (uint32_t padding = 0; padding < population.padding()[expert]; ++padding) {
                        std::vector<uint8_t> tail;
                        append(tail, layer.layer_id, 4);
                        append(tail, expert, 4);
                        append(tail, padding, 4);
                        append(tail, layer.token_bytes, 4);
                        const auto row = fillPattern("AI_MESH_MOE_PAD_FILL_KEY_V1",
                            "AI_MESH_MOE_PAD_FILL_BLOCK_V1", config, tail, layer.token_bytes);
                        payload.insert(payload.end(), row.begin(), row.end());
                    }
                    const auto storage = builder.allocation(id, core, kMoeAllocationKindPAD_INPUT,
                        payload.size(), config.bus_bytes, expert);
                    pad_view = builder.view(id, core, kMoeViewKindPAD_BUFFER, kMoeViewAccessWRITE,
                        storage, 0, payload.size(), kMoeSemanticOwnerEXPERT, expert, {},
                        kMoeViewBackingOVERLAY_ALLOCATION, population.padding()[expert]);
                    const auto descriptor = builder.descriptor(id, core, kMoeDescriptorKindPAD_FILL,
                        *pad_view, payload.size(), expert, {}, payload);
                    waits.push_back(builder.operation(id, core, kMoeCommandPhasePAD_FILL,
                        kOpcodeDMA_FILL, kMoeCommandRolePAD_FILL, kMoeEventRolePAD_FILL_DONE,
                        kMoeEventPhaseFILL, {entry}, {}, payload.size(), expert, {}, descriptor, &activity));
                }
                for (const auto &route : population.routes()) {
                    if (!route.accepted || route.expert != expert)
                        continue;
                    const auto *member = owners.at(route.token.member);
                    if (member->core != core) {
                        const auto &[storage, offset, ready] = received.at(
                            {expert, route.token.uid.encode()});
                        operands.push_back(builder.view(id, core, kMoeViewKindDISPATCH_BUFFER,
                            kMoeViewAccessREAD, storage, offset, layer.token_bytes,
                            kMoeSemanticOwnerEXPERT, expert, route.token.uid.encode()));
                        continue;
                    }
                    const auto ordinal = route.token.uid.phase == 1 ? route.token.uid.token :
                        route.token.uid.sequence;
                    if (ordinal < member->first_token || ordinal - member->first_token >= member->token_count)
                        throw MaterializeFailure(MaterializerCheckSite::BOUNDS, "E_MOE_MATERIALIZATION_V: token outside member slice");
                    operands.push_back(builder.view(id, core, kMoeViewKindMEMBER_INPUT,
                        kMoeViewAccessREAD, {0, 0, member->input_allocation},
                        member->input_offset + (ordinal - member->first_token) * layer.token_bytes,
                        layer.token_bytes, kMoeSemanticOwnerMEMBER, member->ordinal,
                        route.token.uid.encode(), kMoeViewBackingSTATIC_ALLOCATION));
                }
                if (pad_view)
                    operands.push_back(*pad_view);
                waits.insert(waits.end(), dispatch_ready[expert].begin(), dispatch_ready[expert].end());
                const auto ready = builder.operation(id, core, kMoeCommandPhaseEXPERT_COMPUTE,
                    kernel.expert_opcode, kMoeCommandRoleEXPERT_COMPUTE, kMoeEventRoleEXPERT_RESULT,
                    kMoeEventPhaseEXPERT, waits, operands, rows * layer.output_token_bytes,
                    expert, {}, {}, &activity);
                results[expert] = {ready, allocation};
            }
            received.clear();
            for (const auto &chunk : remote.chunks) {
                if (chunk.phase != kMoeTransferPhaseCOMBINE)
                    continue;
                if (chunk.source == core) {
                    const auto &result = results.at(chunk.expert);
                    const auto view = builder.view(id, core, kMoeViewKindEXPERT_OUTPUT,
                        kMoeViewAccessREAD, result.second, chunk.source_offset, chunk.bytes,
                        kMoeSemanticOwnerEXPERT, chunk.expert, {}, kMoeViewBackingOVERLAY_ALLOCATION,
                        chunk.rows.size());
                    builder.transferEndpoint(id, core, chunk, view, result.first, activity);
                }
                if (chunk.destination == core) {
                    const auto allocation = builder.allocation(id, core,
                        kMoeAllocationKindCOMBINE_REMOTE, chunk.bytes, config.bus_bytes,
                        chunk.expert, chunk.source, chunk.ordinal);
                    const auto view = builder.view(id, core, kMoeViewKindCOMBINE_BUFFER,
                        kMoeViewAccessREAD_WRITE, allocation, 0, chunk.bytes,
                        kMoeSemanticOwnerREGION, id);
                    const auto ready = builder.transferEndpoint(id, core, chunk, view, entry, activity);
                    for (size_t row = 0; row < chunk.rows.size(); ++row)
                        received[{chunk.expert, chunk.rows[row]->token.uid.encode()}] =
                            {allocation, row * layer.output_token_bytes, ready};
                }
            }
            std::vector<const MoeSelectedRoute *> tokens;
            for (const auto &route : population.routes())
                if (!route.slot && owners.at(route.token.member)->core == core)
                    tokens.push_back(&route);
            if (!tokens.empty()) {
                const auto accumulator = builder.allocation(id, core, kMoeAllocationKindREDUCE_OUTPUT,
                    tokens.size() * layer.output_token_bytes, region.scratch_alignment);
                for (size_t i = 0; i < tokens.size(); ++i) {
                    const auto &token = tokens[i]->token;
                    const auto *member = owners.at(token.member);
                    const auto ordinal = token.uid.phase == 1 ? token.uid.token : token.uid.sequence;
                    const auto uid = token.uid.encode();
                    const auto output = builder.view(id, core, kMoeViewKindMEMBER_OUTPUT,
                        kMoeViewAccessWRITE, {0, 0, member->output_allocation}, member->output_offset +
                        (ordinal - member->first_token) * layer.output_token_bytes,
                        layer.output_token_bytes, kMoeSemanticOwnerMEMBER, member->ordinal, uid,
                        kMoeViewBackingSTATIC_ALLOCATION);
                    std::vector<uint32_t> accepted;
                    for (const auto &route : population.routes())
                        if (route.accepted && route.token.uid.encode() == uid)
                            accepted.push_back(route.expert);
                    std::sort(accepted.begin(), accepted.end());
                    if (accepted.empty()) {
                        const char domain[] = "AI_MESH_MOE_DROP_PROJECTION_V1";
                        std::vector<uint8_t> projection(domain, domain + sizeof(domain));
                        append(projection, layer.top_k, 4);
                        for (const auto &route : population.routes()) {
                            if (route.token.uid.encode() != uid)
                                continue;
                            append(projection, route.slot, 2);
                            append(projection, route.expert, 2);
                            append(projection, kRouteDispositionDROP, 1);
                            append(projection, 0, 3);
                        }
                        const auto digest = agentSha256(projection);
                        std::vector<uint8_t> tail(uid.begin(), uid.end());
                        append(tail, layer.layer_id, 4);
                        append(tail, layer.output_token_bytes, 4);
                        tail.insert(tail.end(), digest.begin(), digest.end());
                        const auto payload = fillPattern("AI_MESH_MOE_DROPPED_FILL_KEY_V1",
                            "AI_MESH_MOE_DROPPED_FILL_BLOCK_V1", config, tail, layer.output_token_bytes);
                        const auto descriptor = builder.descriptor(id, core,
                            kMoeDescriptorKindDROPPED_TOKEN_FILL, output, payload.size(), None, uid, payload);
                        builder.operation(id, core, kMoeCommandPhaseDROPPED_TOKEN_FILL, kOpcodeDMA_FILL,
                            kMoeCommandRoleDROPPED_TOKEN_FILL, kMoeEventRoleTOKEN_OUTPUT_READY,
                            kMoeEventPhaseREDUCE, {entry}, {}, payload.size(), None, uid, descriptor, &activity);
                        continue;
                    }
                    std::vector<Ref> waits{entry}, views{output};
                    for (const auto expert : accepted) {
                        if (experts[expert]->core_id != core) {
                            const auto &[storage, offset, ready] = received.at({expert, uid});
                            waits.push_back(ready);
                            views.push_back(builder.view(id, core, kMoeViewKindCOMBINE_BUFFER,
                                kMoeViewAccessREAD, storage, offset, layer.output_token_bytes,
                                kMoeSemanticOwnerEXPERT, expert, uid));
                            continue;
                        }
                        waits.push_back(results.at(expert).first);
                        uint32_t row = 0;
                        for (const auto &route : population.routes()) {
                            if (!route.accepted || route.expert != expert)
                                continue;
                            if (route.token.uid.encode() == uid)
                                break;
                            ++row;
                        }
                        views.push_back(builder.view(id, core, kMoeViewKindEXPERT_OUTPUT,
                            kMoeViewAccessREAD, results.at(expert).second,
                            row * layer.output_token_bytes, layer.output_token_bytes,
                            kMoeSemanticOwnerEXPERT, expert, uid));
                    }
                    views.push_back(builder.view(id, core, kMoeViewKindREDUCE_ACCUMULATOR,
                        kMoeViewAccessREAD_WRITE, accumulator, i * layer.output_token_bytes,
                        layer.output_token_bytes, kMoeSemanticOwnerTOKEN, token.uid.item, uid));
                    const bool copy = accepted.size() == 1;
                    builder.operation(id, core, copy ? kMoeCommandPhaseCOPY_THROUGH : kMoeCommandPhaseLOCAL_REDUCE,
                        kOpcodeLOCAL_REDUCE, copy ? kMoeCommandRoleCOPY_THROUGH : kMoeCommandRoleLOCAL_REDUCE,
                        kMoeEventRoleTOKEN_OUTPUT_READY, kMoeEventPhaseREDUCE, waits, views,
                        layer.output_token_bytes, None, uid, {}, &activity);
                }
            }
            const bool empty = activity.empty();
            terminals.push_back(builder.operation(id, core, kMoeCommandPhaseEXIT_SIGNAL, kOpcodeEVENT_SIGNAL,
                empty ? kMoeCommandRoleEMPTY_REGION_TERMINAL : kMoeCommandRoleREGION_TERMINAL,
                empty ? kMoeEventRoleEMPTY_REGION_TERMINAL : kMoeEventRoleREGION_TERMINAL,
                kMoeEventPhaseEXIT, empty ? std::vector<Ref>{entry} : activity, {}, 0));
        }
        const auto coordinator = std::min_element(regions.begin(), regions.end(),
            [](const auto &left, const auto &right) { return left.core_id < right.core_id; });
        builder.operation(0, coordinator->core_id, kMoeCommandPhaseEXIT_SIGNAL, kOpcodeEVENT_SIGNAL,
            kMoeCommandRoleGROUP_EXIT, kMoeEventRoleOVERLAY_EXIT, kMoeEventPhaseEXIT, terminals, {}, 0);
        std::string reason;
        if (!builder.finish(layer, regions, config, out, reason)) {
            error.set(layer.layer_id, 0, MaterializerCheckSite::DAG, reason);
            return false;
        }
        return true;
    } catch (const MaterializeFailure &failure) {
        error.set(layer.layer_id, 0, failure.site, failure.what(), failure.detail);
        return false;
    }
}

}
