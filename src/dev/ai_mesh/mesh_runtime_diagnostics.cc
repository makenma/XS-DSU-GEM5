#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"

#include <cstdlib>
#include <fstream>
#include <mutex>

#include "dev/ai_mesh/mesh_canonical.hh"

#include "base/logging.hh"

namespace gem5
{
namespace ai_mesh
{

const char *
diagnosticStageName(DiagnosticStage stage)
{
    switch (stage) {
      case DiagnosticStage::Load:
        return "load";
      case DiagnosticStage::Invocation:
        return "invocation";
      case DiagnosticStage::Runtime:
        return "runtime";
      case DiagnosticStage::Watchdog:
        return "watchdog";
    }
    return "runtime";
}

static const char *
severityName(mesh_diagnostics::Severity severity)
{
    switch (severity) {
      case mesh_diagnostics::Severity::Error:
        return "error";
      case mesh_diagnostics::Severity::Warning:
        return "warning";
    }
    return "error";
}

RuntimeDiagnostic
invalidResidencyDiagnostic(const ResidencyFacts &facts)
{
    RuntimeDiagnostic record;
    record.code = mesh_diagnostics::DiagnosticCode::E_TENSOR_NOT_RESIDENT;
    record.stage = DiagnosticStage::Runtime;
    record.context = {
        {"instance", std::to_string(facts.instance_id)},
        {"core_id", std::to_string(facts.core_id)},
        {"command_id", std::to_string(facts.command_id)},
        {"generation", std::to_string(facts.generation)},
        {"allocation_id", std::to_string(facts.allocation_id)},
        {"operand_index", std::to_string(facts.operand_index)},
        {"dma_kind", facts.dma ? "dma" : "compute"}};
    return record;
}

std::string
invalidResidencyReport(const ResidencyFacts &facts)
{
    return "E_TENSOR_NOT_RESIDENT: " +
           std::string(facts.dma ? "DMA" : "compute") + " command " +
           std::to_string(facts.command_id) + " generation " +
           std::to_string(facts.generation) + " operand " +
           std::to_string(facts.operand_index) + " reads allocation " +
           std::to_string(facts.allocation_id) +
           " before any producer committed and without an initial residency "
           "declaration";
}

std::string
runtimeDiagnosticsPath(const std::string &base_file)
{
    const char *artifact = std::getenv("AI_MESH_ARTIFACT_DIR");
    if (artifact != nullptr && artifact[0] != '\0')
        return std::string(artifact) + "/runtime_diagnostics.jsonl";
    const size_t slash = base_file.find_last_of('/');
    if (slash == std::string::npos)
        return "runtime_diagnostics.jsonl";
    return base_file.substr(0, slash) + "/runtime_diagnostics.jsonl";
}

MeshRuntimeDiagnostics::MeshRuntimeDiagnostics(const std::string &base_file)
    : diagnostics_path(runtimeDiagnosticsPath(base_file))
{}

bool
MeshRuntimeDiagnostics::write(const RuntimeDiagnostic &record)
{
    const mesh_diagnostics::Definition *definition =
        mesh_diagnostics::findDefinition(record.code);
    if (definition == nullptr) {
        io_failure = "unknown diagnostic code";
        return false;
    }
    static std::mutex sink_mutex;
    std::lock_guard<std::mutex> guard(sink_mutex);
    std::ofstream out(diagnostics_path, std::ios::app);
    if (!out) {
        io_failure = "cannot open " + diagnostics_path;
        return false;
    }
    auto fail = [this](const char *reason) {
        io_failure = std::string(reason) + " " + diagnostics_path;
        return false;
    };
    out << "{";
    out << "\"code\": ";
    if (!mesh_canonical::writeString(out, definition->code))
        return fail("cannot encode the code of");
    out << ", \"severity\": ";
    if (!mesh_canonical::writeString(out, severityName(definition->severity)))
        return fail("cannot encode the severity of");
    out << ", \"stage\": ";
    if (!mesh_canonical::writeString(out, diagnosticStageName(record.stage)))
        return fail("cannot encode the stage of");
    out << ", \"message\": ";
    if (!mesh_canonical::writeString(out, definition->message))
        return fail("cannot encode the message of");
    out << ", \"context\": {";
    bool first = true;
    for (const auto &entry : record.context) {
        if (!first)
            out << ", ";
        first = false;
        if (!mesh_canonical::writeString(out, entry.first))
            return fail("cannot encode a context key of");
        out << ": ";
        if (!mesh_canonical::writeString(out, entry.second))
            return fail("cannot encode a context value of");
    }
    out << "}}\n";
    out.flush();
    if (!out)
        return fail("cannot flush");
    return true;
}

void
reportDiagnostic(MeshRuntimeDiagnostics &sink,
                 const RuntimeDiagnostic &record, const std::string &business)
{
    if (!sink.write(record))
        fatal("%s (diagnostic sink failure: %s)", business.c_str(),
              sink.failure().c_str());
}

} // namespace ai_mesh
} // namespace gem5
