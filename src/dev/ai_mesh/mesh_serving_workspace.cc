#include "dev/ai_mesh/mesh_serving_workspace.hh"

#include <algorithm>
#include <map>
#include <set>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5::ai_mesh
{
namespace
{

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;

bool reject(MeshLoadError &error, const char *message)
{
    error = {"E_BINDING_ROLE", message};
    return false;
}

struct ByteOrigin
{
    ExactByteRegion bytes;
    uint64_t object = 0;
};

class ServingByteOrigins
{
  public:
    ServingByteOrigins(const DecodedProgram &program_, const ProgramSemanticContext &context_,
        const VerifiedProgramGeometry &geometry_)
        : program(program_), context(context_), geometry(geometry_)
    {
        for (const auto &[id, op] : context.operations())
            for (size_t index = 0; index < geometry.accessCount(id, GeometryAccessRole::Write); ++index) {
                const auto *write = geometry.access(id, GeometryAccessRole::Write, index);
                producers[write->write()->new_state_id].push_back(write);
            }
        for (const auto &[id, command] : context.commandSemantics())
            if (command->source.section_type == SemanticRecordTraits<KernelCommandSource>::section_type &&
                    command->execution.section_type == SemanticRecordTraits<ComputeExecution>::section_type)
                computed.insert(program.semantic_tables.kernel_command_source_rows.at(command->source.row_id - 1).kernel_op_id);
        for (const auto &use : program.semantic_tables.descriptor_endpoint_use_rows) {
            uint64_t op = 0, ordinal = 0;
            const bool read = use.use.section_type == SemanticRecordTraits<ReadAccessUse>::section_type;
            if (read) {
                const auto &access = program.semantic_tables.read_access_use_rows.at(use.use.row_id - 1);
                op = access.kernel_op_id;
                ordinal = access.access_index;
            } else {
                const auto &access = program.semantic_tables.write_access_use_rows.at(use.use.row_id - 1);
                op = access.kernel_op_id;
                ordinal = access.access_index;
            }
            endpoints[{use.descriptor_id, use.side == EndpointSide::SRC}] = geometry.access(op,
                read ? GeometryAccessRole::Read : GeometryAccessRole::Write, ordinal);
            if (use.side == EndpointSide::DST)
                descriptors[op].push_back(context.descriptors().at(use.descriptor_id));
        }
    }

    const GeometryAccessFact &access(const DmaDescriptor &descriptor, bool source) const
    {
        return *endpoints.at({descriptor.descriptor_id, source});
    }

    bool trace(uint64_t stateId, const ExactByteRegion &bytes, std::vector<ByteOrigin> &out, MeshLoadError &error) const
    {
        std::vector<std::pair<uint64_t, ExactByteRegion>> pending{{stateId, bytes}};
        out.clear();
        while (!pending.empty()) {
            const auto [identity, required] = pending.back();
            pending.pop_back();
            bool empty = false;
            if (!geometry.contains(geometry.emptyBytes(), required, empty, error))
                return false;
            if (empty)
                continue;
            const auto found = producers.find(identity);
            if (found == producers.end()) {
                const auto &state = *context.states().at(identity);
                if (state.origin != StateOrigin::EXTERNAL)
                    return reject(error, "published bytes have no initialized origin");
                out.push_back({required, state.object_id});
                continue;
            }
            const auto &writes = found->second;
            const auto &op = writes.front()->operation();
            auto written = geometry.emptyBytes();
            for (const auto *write : writes)
                if (!geometry.unite(written, write->bytes(), written, error))
                    return false;
            ExactByteRegion preserved, pieces;
            if (!geometry.subtract(required, written, preserved, error) ||
                    !geometry.intersect(required, written, pieces, error))
                return false;
            pending.emplace_back(writes.front()->write()->old_state_id, preserved);
            if (!geometry.contains(geometry.emptyBytes(), pieces, empty, error))
                return false;
            if (empty)
                continue;
            if (computed.count(op.op_id) && op.computation_id) {
                out.push_back({pieces, 0});
                continue;
            }
            if (op.attrs.section_type != SemanticRecordTraits<DmaAttrs>::section_type)
                return reject(error, "published bytes are not computed or byte-preserving DMA");
            const auto kind = program.semantic_tables.dma_attrs_rows.at(op.attrs.row_id - 1).kind;
            if (kind != semantic_abi::DmaKind::LOAD && kind != semantic_abi::DmaKind::STORE && kind != semantic_abi::DmaKind::PREFETCH && kind != semantic_abi::DmaKind::P2P_PUSH)
                return reject(error, "published bytes are not computed or byte-preserving DMA");
            auto covered = geometry.emptyBytes();
            const auto group = descriptors.find(op.op_id);
            if (group != descriptors.end())
                for (const auto *descriptor : group->second) {
                    if (access(*descriptor, false).write()->new_state_id != identity)
                        continue;
                    for (uint32_t row = 0; row < descriptor->rows; ++row) {
                        const uint64_t begin = descriptor->dst.offset_bytes + row * descriptor->dst_stride_bytes;
                        const uint64_t source = descriptor->src.offset_bytes + row * descriptor->src_stride_bytes;
                        ExactByteRegion range, piece, translated;
                        if (!geometry.byteInterval(begin, begin + descriptor->row_bytes, range, error) ||
                                !geometry.intersect(pieces, range, piece, error) ||
                                !geometry.relocateBytes(piece, begin, source, translated, error) ||
                                !geometry.unite(covered, piece, covered, error))
                            return false;
                        pending.emplace_back(access(*descriptor, true).read()->state_id, translated);
                    }
                }
            bool complete = false;
            if (!geometry.contains(covered, pieces, complete, error))
                return false;
            if (!complete)
                return reject(error, "published bytes escape their DMA source projection");
        }
        return true;
    }

  private:
    const DecodedProgram &program;
    const ProgramSemanticContext &context;
    const VerifiedProgramGeometry &geometry;
    std::map<uint64_t, std::vector<const GeometryAccessFact *>> producers;
    std::map<std::pair<uint32_t, bool>, const GeometryAccessFact *> endpoints;
    std::map<uint64_t, std::vector<const DmaDescriptor *>> descriptors;
    std::set<uint64_t> computed;
};

struct WorkspaceTensor
{
    const AgentInstanceTensorBinding *mapping;
    ProgramSymbolBinding binding;
    uint64_t state = 0;
};

}

bool verifyWorkspacePublication(const DecodedProgram &program,
    const ProgramSemanticContext &context, const VerifiedProgramGeometry &geometry,
    const ExecutionViewSet &views, const AgentRequestProfile &request, MeshLoadError &error)
{
    ServingByteOrigins origins(program, context, geometry);
    std::map<uint32_t, std::vector<uint32_t>> ranks;
    std::map<uint32_t, std::map<uint64_t, WorkspaceTensor>> tensors;
    for (const auto &instance : program.transport.agent_instance_profiles) {
        if (instance.request_program_id != request.program_id || instance.request_profile_id != request.profile_id)
            continue;
        for (size_t index = instance.member_binding_first; index < instance.member_binding_first + instance.member_binding_count; ++index)
            ranks[instance.instance_profile_id].push_back(program.transport.agent_instance_member_bindings.at(index).expected_logical_source_rank);
        for (const auto &row : program.transport.agent_instance_tensor_bindings) {
            if (row.instance_profile_id != instance.instance_profile_id)
                continue;
            const auto binding = context.symbolBinding(instance.mesh_entrypoint_id, instance.mesh_profile_id, row.static_symbol_id);
            tensors[instance.instance_profile_id].emplace(binding->object->object_id, WorkspaceTensor{&row, *binding, 0});
        }
    }
    for (const auto &prefill : program.transport.agent_instance_profiles) {
        if (!ranks.count(prefill.instance_profile_id) || prefill.phase != kPhasePREFILL)
            continue;
        const AgentInstanceProfile *publish = nullptr;
        for (const auto &instance : program.transport.agent_instance_profiles)
            if (ranks.count(instance.instance_profile_id) && instance.phase == kPhasePUBLISH &&
                    ranks.at(instance.instance_profile_id) == ranks.at(prefill.instance_profile_id))
                publish = &instance;
        if (!publish)
            return reject(error, "workspace has no matching PUBLISH instance");
        auto writers = tensors.at(prefill.instance_profile_id);
        const auto &readers = tensors.at(publish->instance_profile_id);
        const auto *producerView = views.forInstance(prefill.mesh_entrypoint_id, prefill.mesh_profile_id);
        const auto *consumerView = views.forInstance(publish->mesh_entrypoint_id, publish->mesh_profile_id);
        std::set<uint32_t> producerCommands, consumerCommands;
        for (const auto &range : producerView->ranges)
            producerCommands.insert(range.command_ids.begin(), range.command_ids.end());
        for (const auto &range : consumerView->ranges)
            consumerCommands.insert(range.command_ids.begin(), range.command_ids.end());
        for (const auto &descriptor : program.transport.dma_descriptors) {
            if (!producerCommands.count(descriptor.command_id))
                continue;
            const auto &source = origins.access(descriptor, true);
            const auto sourceMap = writers.find(source.object().object_id);
            if (sourceMap != writers.end() && sourceMap->second.mapping->binding_kind == agent_abi::kBindingKindREQUEST_WORKSPACE)
                return reject(error, "PREFILL must produce request workspace");
            const auto &destination = origins.access(descriptor, false);
            const auto found = writers.find(destination.object().object_id);
            if (found == writers.end() || found->second.mapping->binding_kind != agent_abi::kBindingKindREQUEST_WORKSPACE)
                continue;
            if (descriptor.kind != kDmaKindSTORE)
                return reject(error, "workspace producer must store computed bytes");
            const auto state = destination.write()->new_state_id;
            if (!found->second.state || context.states().at(state)->version > context.states().at(found->second.state)->version)
                found->second.state = state;
        }
        for (const auto &descriptor : program.transport.dma_descriptors) {
            if (!consumerCommands.count(descriptor.command_id))
                continue;
            const auto &source = origins.access(descriptor, true);
            const auto &destination = origins.access(descriptor, false);
            const auto mappedSource = readers.find(source.object().object_id);
            const auto mappedDestination = readers.find(destination.object().object_id);
            if (mappedDestination != readers.end() && mappedDestination->second.mapping->binding_kind == agent_abi::kBindingKindREQUEST_WORKSPACE)
                return reject(error, "PUBLISH must preserve request workspace");
            const WorkspaceTensor *mapped = nullptr;
            if (mappedSource != readers.end() && mappedSource->second.mapping->binding_kind == agent_abi::kBindingKindREQUEST_WORKSPACE)
                mapped = &mappedSource->second;
            if (mappedDestination != readers.end() && mappedDestination->second.mapping->binding_kind == agent_abi::kBindingKindHOST_OUTPUT)
                mapped = &mappedDestination->second;
            if (!mapped)
                continue;
            for (uint32_t row = 0; row < descriptor.rows; ++row) {
                const auto begin = descriptor.src.offset_bytes + row * descriptor.src_stride_bytes;
                ExactByteRegion bytes;
                std::vector<ByteOrigin> values;
                if (!geometry.byteInterval(begin, begin + descriptor.row_bytes, bytes, error) ||
                        !origins.trace(source.read()->state_id, bytes, values, error))
                    return false;
                for (const auto &value : values) {
                    const auto found = readers.find(value.object);
                    if (found == readers.end() || found->second.mapping->binding_kind != agent_abi::kBindingKindREQUEST_WORKSPACE ||
                            found->second.mapping->member_ordinal != mapped->mapping->member_ordinal)
                        return reject(error, "PUBLISH bytes must preserve their workspace origin");
                    const auto &reader = found->second;
                    ExactByteRegion wanted;
                    auto covered = geometry.emptyBytes();
                    if (!geometry.relocateBytes(value.bytes, 0, reader.mapping->offset_bytes, wanted, error))
                        return false;
                    for (const auto &[object, writer] : writers) {
                        if (!writer.state || writer.mapping->binding_kind != agent_abi::kBindingKindREQUEST_WORKSPACE ||
                                writer.mapping->member_ordinal != reader.mapping->member_ordinal ||
                                writer.mapping->request_symbol_id != reader.mapping->request_symbol_id)
                            continue;
                        ExactByteRegion range, piece, sourceBytes;
                        bool empty = false;
                        if (!geometry.byteInterval(writer.mapping->offset_bytes,
                                writer.mapping->offset_bytes + writer.binding.object->footprint_bytes, range, error) ||
                                !geometry.intersect(wanted, range, piece, error) ||
                                !geometry.contains(geometry.emptyBytes(), piece, empty, error))
                            return false;
                        if (empty)
                            continue;
                        const auto &left = *writer.binding.logical_tensor;
                        const auto &right = *reader.binding.logical_tensor;
                        const auto equal = [&](const ListSpan &a, const ListSpan &b) {
                            return a.count == b.count && std::equal(program.semantic_u64_values.begin() + a.begin,
                                program.semantic_u64_values.begin() + a.begin + a.count, program.semantic_u64_values.begin() + b.begin);
                        };
                        if (left.dtype != right.dtype || !equal(left.shape, right.shape) || !equal(left.strides, right.strides))
                            return reject(error, "workspace producer and consumer layouts differ");
                        std::vector<ByteOrigin> computed;
                        if (!geometry.relocateBytes(piece, writer.mapping->offset_bytes, 0, sourceBytes, error) ||
                                !origins.trace(writer.state, sourceBytes, computed, error) ||
                                !geometry.unite(covered, piece, covered, error))
                            return false;
                        if (std::any_of(computed.begin(), computed.end(), [](const auto &item) { return item.object != 0; }))
                            return reject(error, "workspace bytes lack a preceding computation");
                    }
                    bool complete = false;
                    if (!geometry.contains(covered, wanted, complete, error))
                        return false;
                    if (!complete)
                        return reject(error, "PUBLISH reads an unwritten workspace range");
                }
            }
        }
    }
    return true;
}

}
