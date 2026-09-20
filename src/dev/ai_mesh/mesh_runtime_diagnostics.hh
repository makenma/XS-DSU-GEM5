#ifndef DEV_AI_MESH_MESH_RUNTIME_DIAGNOSTICS_HH
#define DEV_AI_MESH_MESH_RUNTIME_DIAGNOSTICS_HH

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "dev/ai_mesh/dma_records.hh"
#include "dev/ai_mesh/generated/mesh_diagnostics.hh"

namespace gem5
{
namespace ai_mesh
{

enum class DiagnosticStage : uint8_t
{
    Load = 0,
    Invocation = 1,
    Runtime = 2,
    Watchdog = 3,
};

const char *diagnosticStageName(DiagnosticStage stage);

struct InstanceFailureFacts
{
    uint16_t core_id = 0;
    uint32_t command_id = 0;
    uint32_t generation = 0;
    uint32_t descriptor_id = 0;
    uint32_t transfer_id = 0;
    uint16_t dma_kind = 0;
    DmaStatus status = DmaStatus::OK;
};

struct RuntimeDiagnostic
{
    mesh_diagnostics::DiagnosticCode code =
        mesh_diagnostics::DiagnosticCode::E_ABI_CORRUPT;
    DiagnosticStage stage = DiagnosticStage::Runtime;
    std::vector<std::pair<std::string, std::string>> context;
};

// One runtime invalid-residency detection: a command read a local allocation
// that no producer committed and that the compiler did not declare initially
// resident.
struct ResidencyFacts
{
    uint32_t instance_id = 0;
    uint16_t core_id = 0;
    uint32_t command_id = 0;
    uint32_t generation = 0;
    uint32_t allocation_id = 0;
    uint32_t operand_index = 0;
    bool dma = false;
};

RuntimeDiagnostic invalidResidencyDiagnostic(const ResidencyFacts &facts);
std::string invalidResidencyReport(const ResidencyFacts &facts);

std::string runtimeDiagnosticsPath(const std::string &base_file);

class MeshRuntimeDiagnostics
{
  public:
    explicit MeshRuntimeDiagnostics(const std::string &base_file);

    bool write(const RuntimeDiagnostic &record);
    const std::string &path() const { return diagnostics_path; }
    const std::string &failure() const { return io_failure; }

  private:
    std::string diagnostics_path;
    std::string io_failure;
};

// Single fail-closed entry for every diagnostic caller: the record is written
// through the shared sink; an I/O failure fails closed with the caller's
// business message (which carries the generated code and the original facts)
// plus the I/O reason, so the business code is never lost.  It returns
// normally when the record was written, letting callers that must fail anyway
// emit their own fatal.
void reportDiagnostic(MeshRuntimeDiagnostics &sink,
                      const RuntimeDiagnostic &record,
                      const std::string &business);

} // namespace ai_mesh
} // namespace gem5

#endif
