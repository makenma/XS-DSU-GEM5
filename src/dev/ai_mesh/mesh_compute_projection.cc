#include "dev/ai_mesh/mesh_compute_projection.hh"

#include <algorithm>
#include <cstring>

#include "dev/ai_mesh/mesh_ir_dtype.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"

namespace gem5::ai_mesh
{

bool projectComputeCommit(const MeshProgramAdmission &admission,
                          const DecodedCommand &command,
                          RuntimeObjectKey key, ComputeCommitRequest &out,
                          MeshLoadError &error)
{
    const auto reject = [&](const char *message) {
        error = {"E_ABI_BOUNDS", message};
        return false;
    };
    const auto found = admission.context().commands().find(command.command_id);
    if (found == admission.context().commands().end() || found->second != &command ||
        key.domain != mesh_abi::MeshObjectDomain::STATIC ||
        key.kind != mesh_abi::MeshObjectKind::COMMAND || key.ordinal != command.command_id)
        return reject("compute projection needs its admitted static command");
    switch (command.opcode) {
      case mesh_abi::kOpcodeGEMM:
      case mesh_abi::kOpcodeBMM:
      case mesh_abi::kOpcodeELEMENTWISE:
      case mesh_abi::kOpcodeSOFTMAX:
      case mesh_abi::kOpcodeNORM:
      case mesh_abi::kOpcodeLOCAL_REDUCE:
        break;
      default:
        return reject("compute projection received a non-compute command");
    }
    ComputeCommitRequest request;
    request.command = key;
    request.opcode = command.opcode;
    const auto operation = admission.context().operations().find(command.source_op_id);
    if (operation == admission.context().operations().end())
        return reject("compute projection has no admitted operation");
    if (operation->second->opcode == mesh_abi::semantic_abi::KernelOpcode::VECTOR) {
        const auto row = operation->second->attrs.row_id;
        const auto &attrs = admission.program().semantic_tables.vector_kernel_attrs_rows.at(row - 1);
        if (attrs.algorithm == mesh_abi::semantic_abi::VectorAlgorithm::EMBEDDING_STAGED_COPY)
            request.copy_mode = ComputeCopyMode::Ordered;
    }
    if (!command.attr_index || command.attr_index > admission.program().transport.op_attrs.size())
        return reject("compute projection has no admitted attributes");
    const auto &attr = admission.program().transport.op_attrs[command.attr_index - 1];
    std::visit([&](const auto &payload) {
        using Payload = std::decay_t<decltype(payload)>;
        if constexpr (!std::is_same_v<Payload, std::monostate>) {
            request.attributes.resize(sizeof(Payload));
            std::memcpy(request.attributes.data(), &payload, sizeof(Payload));
        }
    }, attr.payload);
    for (const auto role : {GeometryAccessRole::Read, GeometryAccessRole::Write}) {
        auto &spans = role == GeometryAccessRole::Read ? request.inputs : request.results;
        const auto count = admission.geometry().accessCount(command.source_op_id, role);
        for (size_t index = 0; index < count; ++index) {
            const auto *fact = admission.geometry().access(command.source_op_id, role, index);
            const auto *allocation = fact ? admission.backing().local(fact->object().object_id) : nullptr;
            uint64_t width = 0;
            if (!fact || !allocation || allocation->owner_core != command.core_id ||
                fact->object().memory_space != mesh_abi::semantic_abi::MemorySpace::CORE_SRAM ||
                fact->shape().size() != fact->objectByteStrides().size() ||
                !dtypeByteWidth(fact->tensor().dtype, width))
                return reject("compute access has no admitted local geometry");
            if (role == GeometryAccessRole::Write) {
                if (!request.result_allocation_id)
                    request.result_allocation_id = allocation->allocation_id;
                request.valid_allocations.push_back(allocation->allocation_id);
            }
            const uint64_t elements = fact->logicalElementCount();
            const uint64_t row_elements = fact->shape().empty() ? elements : fact->shape().back();
            std::vector<uint64_t> coordinates(fact->shape().size(), 0);
            const size_t begin = spans.size();
            for (uint64_t element = 0; element < elements; ++element) {
                uint64_t offset = fact->objectByteOffset();
                bool overflow = false;
                for (size_t axis = 0; axis < coordinates.size(); ++axis)
                    offset = checkedMulAdd(coordinates[axis], fact->objectByteStrides()[axis], offset, overflow);
                if (overflow || !spanFits(offset, width, allocation->size_bytes))
                    return reject("compute access leaves its admitted allocation");
                const uint64_t address = allocation->offset_bytes + offset;
                const bool row_begin = role == GeometryAccessRole::Write && row_elements && element % row_elements == 0;
                if (spans.size() == begin || row_begin || spans.back().offset + spans.back().span != address)
                    spans.push_back({address, width});
                else
                    spans.back().span += width;
                for (size_t axis = coordinates.size(); axis-- > 0;) {
                    if (++coordinates[axis] < fact->shape()[axis])
                        break;
                    coordinates[axis] = 0;
                }
            }
        }
    }
    std::sort(request.valid_allocations.begin(), request.valid_allocations.end());
    request.valid_allocations.erase(std::unique(request.valid_allocations.begin(),
        request.valid_allocations.end()), request.valid_allocations.end());
    out = std::move(request);
    return true;
}

}
