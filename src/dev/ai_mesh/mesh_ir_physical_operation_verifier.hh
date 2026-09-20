#ifndef GEM5_DEV_AI_MESH_MESH_IR_PHYSICAL_OPERATION_VERIFIER_HH
#define GEM5_DEV_AI_MESH_MESH_IR_PHYSICAL_OPERATION_VERIFIER_HH

#include <cstdint>
#include <map>
#include <memory>
#include <optional>

#include "dev/ai_mesh/mesh_ir_region.hh"

namespace gem5
{
namespace ai_mesh
{

class MatrixContractionBounds
{
  public:
    uint64_t domainBegin() const;
    uint64_t domainEnd() const;
    uint64_t intervalBegin() const;
    uint64_t intervalEnd() const;

  private:
    uint64_t domainBegin_ = 0;
    uint64_t domainEnd_ = 0;
    uint64_t intervalBegin_ = 0;
    uint64_t intervalEnd_ = 0;

    friend class MatrixContractionFact;
    friend class PhysicalOperationBuilder;
};

class MatrixContractionFact
{
  public:
    const MatrixContractionBounds *bounds() const;

  private:
    std::optional<MatrixContractionBounds> bounds_;

    friend class VerifiedMatrixContractionFacts;
    friend class PhysicalOperationBuilder;
};

class VerifiedMatrixContractionFacts
{
  public:
    VerifiedMatrixContractionFacts() = default;
    ~VerifiedMatrixContractionFacts();
    VerifiedMatrixContractionFacts(const VerifiedMatrixContractionFacts &) = delete;
    VerifiedMatrixContractionFacts &operator=(const VerifiedMatrixContractionFacts &) = delete;
    VerifiedMatrixContractionFacts(VerifiedMatrixContractionFacts &&) noexcept;
    VerifiedMatrixContractionFacts &operator=(VerifiedMatrixContractionFacts &&) noexcept;

    bool matches(
        const DecodedProgram &, const ProgramSemanticContext &,
        const VerifiedProgramGeometry &) const;
    const MatrixContractionFact *contraction(uint64_t operationId) const;

  private:
    const DecodedProgram *program_ = nullptr;
    const ProgramSemanticContext *context_ = nullptr;
    std::shared_ptr<const void> geometryIdentity_;
    std::map<uint64_t, MatrixContractionFact> contractions_;

    bool bind(
        const DecodedProgram &, const ProgramSemanticContext &,
        const VerifiedProgramGeometry &);

    friend class PhysicalOperationBuilder;
};

bool verifyProgramPhysicalOperationDomain(
    const DecodedProgram &, const ProgramSemanticContext &,
    const VerifiedProgramGeometry &, VerifiedMatrixContractionFacts &,
    MeshLoadError &);

}
}

#endif
