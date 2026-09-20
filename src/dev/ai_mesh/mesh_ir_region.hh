#ifndef GEM5_DEV_AI_MESH_MESH_IR_REGION_HH
#define GEM5_DEV_AI_MESH_MESH_IR_REGION_HH

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

namespace gem5
{
namespace ai_mesh
{

class VerifiedMatrixContractionFacts;
class VerifiedProgramBacking;

class ExactByteRegion
{
  public:
    ExactByteRegion() = default;

  private:
    struct Storage;

    std::shared_ptr<const Storage> storage_;

    friend class VerifiedProgramGeometry;
    friend struct GeometryBuilder;
    friend struct GeometryRegionOperation;
};

enum class GeometryAccessRole : uint8_t
{
    Read,
    Write,
};

class GeometryAccessFact
{
  public:
    const mesh_abi::semantic_abi::KernelOp &operation() const;
    GeometryAccessRole role() const;
    size_t ordinal() const;
    const mesh_abi::semantic_abi::OperandAccess *read() const;
    const mesh_abi::semantic_abi::StateTransition *write() const;
    const mesh_abi::semantic_abi::KernelTensor &tensor() const;
    const mesh_abi::semantic_abi::TensorShard &logicalShard() const;
    const mesh_abi::semantic_abi::BufferObject &object() const;
    const mesh_abi::semantic_abi::BufferView &view() const;
    const mesh_abi::semantic_abi::ElementRegion &region() const;
    uint64_t logicalElementCount() const;
    const std::vector<uint64_t> &localOrigin() const;
    const std::vector<uint64_t> &globalOrigin() const;
    const std::vector<uint64_t> &shape() const;
    const std::vector<uint64_t> &steps() const;
    uint64_t objectByteOffset() const;
    const std::vector<uint64_t> &objectByteStrides() const;
    const ExactByteRegion &bytes() const;

  private:
    GeometryAccessFact() = default;

    const mesh_abi::semantic_abi::KernelOp *operation_ = nullptr;
    GeometryAccessRole role_ = GeometryAccessRole::Read;
    size_t ordinal_ = 0;
    const mesh_abi::semantic_abi::OperandAccess *read_ = nullptr;
    const mesh_abi::semantic_abi::StateTransition *write_ = nullptr;
    const mesh_abi::semantic_abi::KernelTensor *tensor_ = nullptr;
    const mesh_abi::semantic_abi::TensorShard *logicalShard_ = nullptr;
    const mesh_abi::semantic_abi::BufferObject *object_ = nullptr;
    const mesh_abi::semantic_abi::BufferView *view_ = nullptr;
    const mesh_abi::semantic_abi::ElementRegion *region_ = nullptr;
    uint64_t logicalElementCount_ = 0;
    std::vector<uint64_t> localOrigin_;
    std::vector<uint64_t> globalOrigin_;
    std::vector<uint64_t> shape_;
    std::vector<uint64_t> steps_;
    uint64_t objectByteOffset_ = 0;
    std::vector<uint64_t> objectByteStrides_;
    ExactByteRegion bytes_;

    friend class VerifiedProgramGeometry;
    friend struct GeometryBuilder;
    friend struct GeometryRegionOperation;
};

class GeometryAccessPieceFact
{
  public:
    GeometryAccessPieceFact(const GeometryAccessPieceFact &) = delete;
    GeometryAccessPieceFact &operator=(const GeometryAccessPieceFact &) = delete;
    GeometryAccessPieceFact(GeometryAccessPieceFact &&) noexcept = default;
    GeometryAccessPieceFact &operator=(GeometryAccessPieceFact &&) noexcept =
        default;

    const GeometryAccessFact &access() const;
    const std::vector<uint64_t> &origin() const;
    const std::vector<uint64_t> &shape() const;
    const std::vector<uint64_t> &steps() const;
    uint64_t logicalElementCount() const;
    uint64_t elementWidthBytes() const;
    uint64_t objectByteOffset() const;
    const std::vector<uint64_t> &objectByteStrides() const;
    uint64_t objectByteEnd() const;

  private:
    GeometryAccessPieceFact() = default;

    std::shared_ptr<const void> owner_;
    const GeometryAccessFact *access_ = nullptr;
    std::vector<uint64_t> origin_;
    std::vector<uint64_t> shape_;
    std::vector<uint64_t> steps_;
    uint64_t logicalElementCount_ = 0;
    uint64_t elementWidthBytes_ = 0;
    uint64_t objectByteOffset_ = 0;
    std::vector<uint64_t> objectByteStrides_;
    uint64_t objectByteEnd_ = 0;

    friend class VerifiedProgramGeometry;
};

class VerifiedProgramGeometry
{
  public:
    VerifiedProgramGeometry() = default;
    ~VerifiedProgramGeometry();
    VerifiedProgramGeometry(const VerifiedProgramGeometry &) = delete;
    VerifiedProgramGeometry &operator=(const VerifiedProgramGeometry &) = delete;
    VerifiedProgramGeometry(VerifiedProgramGeometry &&) noexcept;
    VerifiedProgramGeometry &operator=(VerifiedProgramGeometry &&) noexcept;

    bool matches(
        const DecodedProgram &, const ProgramSemanticContext &) const;
    size_t accessCount(uint64_t operationId, GeometryAccessRole) const;
    const GeometryAccessFact *access(
        uint64_t operationId, GeometryAccessRole, size_t ordinal) const;
    bool accessMapsInjectively(
        uint64_t operationId, GeometryAccessRole, size_t ordinal,
        bool &, MeshLoadError &) const;
    bool accessByteCount(
        uint64_t operationId, GeometryAccessRole, size_t ordinal,
        uint64_t &, MeshLoadError &) const;
    std::optional<GeometryAccessPieceFact> projectAccessPiece(
        uint64_t operationId, GeometryAccessRole, size_t ordinal,
        const mesh_abi::semantic_abi::SemanticRef &, MeshLoadError &) const;
    const ExactByteRegion *objectBytes(uint64_t objectId) const;
    const ExactByteRegion &emptyBytes() const;
    bool unite(
        const ExactByteRegion &, const ExactByteRegion &, ExactByteRegion &,
        MeshLoadError &) const;
    bool subtract(
        const ExactByteRegion &, const ExactByteRegion &, ExactByteRegion &,
        MeshLoadError &) const;
    bool intersect(
        const ExactByteRegion &, const ExactByteRegion &, ExactByteRegion &,
        MeshLoadError &) const;
    bool contains(
        const ExactByteRegion &, const ExactByteRegion &, bool &,
        MeshLoadError &) const;
    bool disjoint(
        const ExactByteRegion &, const ExactByteRegion &, bool &,
        MeshLoadError &) const;

  private:
    struct Impl;

    const DecodedProgram *program_ = nullptr;
    const ProgramSemanticContext *context_ = nullptr;
    std::shared_ptr<const Impl> impl_;

    std::shared_ptr<const void> invocationIdentity() const;
    bool matchesInvocation(const std::shared_ptr<const void> &) const;

    friend class VerifiedMatrixContractionFacts;
    friend class VerifiedProgramBacking;
    friend bool verifyProgramGeometry(
        const DecodedProgram &, const ProgramSemanticContext &,
        VerifiedProgramGeometry &, MeshLoadError &);
    friend struct GeometryBuilder;
    friend struct GeometryRegionOperation;
};

bool verifyProgramGeometry(
    const DecodedProgram &, const ProgramSemanticContext &,
    VerifiedProgramGeometry &, MeshLoadError &);

}
}

#endif
