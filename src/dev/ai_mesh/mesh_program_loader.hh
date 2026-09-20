#ifndef DEV_AI_MESH_MESH_PROGRAM_LOADER_HH
#define DEV_AI_MESH_MESH_PROGRAM_LOADER_HH

#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_invocation_binding.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"
#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"
#include "mem/ruby/network/Network.hh"
#include "sim/sim_object.hh"

namespace gem5
{

struct MeshProgramLoaderParams;

namespace ai_mesh
{

class MeshDummyCore;
class CommandRom;
class MockAxiTransport;
class PeerSramAperture;
class AxiGarnetBridge;

// Control-plane traffic window: the aggregate network and AXI facts captured
// at the start and at the end of the loader install, so the install phase's
// zero-traffic property is observed on the real owners instead of assumed.
struct LoaderTrafficSnapshot
{
    uint64_t ar_accepted = 0;
    uint64_t aw_accepted = 0;
    uint64_t w_accepted = 0;
    uint64_t r_beats = 0;
    uint64_t b_consumed = 0;
    uint64_t b_errors = 0;
    uint64_t r_errors = 0;
    uint64_t ni_queued_flits = 0;
    uint64_t ni_queued_messages = 0;
    uint64_t router_buffered_flits = 0;
    uint64_t non_idle_input_vcs = 0;
    uint64_t non_idle_output_vcs = 0;
    uint64_t data_link_pending_flits = 0;
    uint64_t credit_link_pending_credits = 0;
    uint64_t bridge_pending_items = 0;
    uint64_t credit_deficit = 0;
    // Cumulative Garnet traffic that had really crossed the network interface
    // at this boundary: the install must not move a single packet or flit.
    uint64_t packets_injected = 0;
    uint64_t packets_received = 0;
    uint64_t flits_injected = 0;
    uint64_t flits_received = 0;
};

struct ControlPlaneWindow
{
    bool captured = false;
    Tick begin_tick = 0;
    Tick end_tick = 0;
    LoaderTrafficSnapshot begin;
    LoaderTrafficSnapshot end;
};

// Reads, integrity-checks, structurally verifies and installs a .mshb
// program into the participating cores before tick 0 work begins.  Any
// violation fails closed with a stable error code.  The optional transport
// selects the mock runtime; the optional per-core apertures select the
// real AXI-over-Garnet runtime (Gate 2).
class MeshProgramLoader : public SimObject
{
  public:
    using Params = MeshProgramLoaderParams;

    MeshProgramLoader(const Params &p);

    void startup() override;

    bool loaded() const { return load_ok; }
    const DecodedProgram &program() const { return admission->program(); }
    const MeshProgramAdmission &programAdmission() const
    {
        return *admission;
    }
    uint64_t selectedVariantId() const { return variant_id; }
    const MeshInvocationBinding &invocationBinding() const
    {
        return *invocation;
    }
    const std::string &programName() const { return program_name; }
    const RuntimeArch &arch() const { return runtime_arch; }
    const std::string &effectiveArchDigest() const
    {
        return effective_arch_digest;
    }
    const std::vector<MeshDummyCore *> &coreObjects() const { return core_objects; }
    MeshRuntimeDiagnostics &diagnostics() { return sink; }

    struct TransferPlan
    {
        uint16_t receiver_core = 0;
        uint64_t useful_bytes = 0;
        std::vector<std::pair<uint64_t, uint64_t>> ranges;
    };
    const TransferPlan *transferPlan(uint32_t transfer_id) const;

    const ControlPlaneWindow &controlPlaneWindow() const
    {
        return control_plane;
    }

  private:
    LoaderTrafficSnapshot trafficSnapshot() const;

    std::string program_file;
    std::string arch_digest_hex;
    std::string effective_arch_digest;
    uint32_t entrypoint_id = 0;
    uint32_t profile_id = 0;
    uint64_t variant_id = 0;
    std::vector<MeshDummyCore *> core_objects;
    MockAxiTransport *transport = nullptr;
    std::vector<PeerSramAperture *> apertures;
    std::shared_ptr<const MeshProgramAdmission> admission;
    std::shared_ptr<const MeshInvocationBinding> invocation;
    std::vector<DispatchBinding> dispatch_bindings;
    RuntimeArch runtime_arch;
    std::string program_name;
    std::map<uint32_t, TransferPlan> transfer_plans;
    MeshRuntimeDiagnostics sink;
    bool load_ok = false;
    ruby::Network *network = nullptr;
    std::vector<AxiGarnetBridge *> bridges;
    ControlPlaneWindow control_plane;
    // The stage names the lifecycle phase and the phase names the step inside
    // it, so a structured rejection says exactly where admission refused the
    // program instead of only that loading failed.
    void reportRejection(
        const MeshLoadError &error, DiagnosticStage stage, const char *phase,
        const std::vector<std::pair<std::string, std::string>> &extra = {});
    size_t installed_cores = 0;
};

} // namespace ai_mesh
} // namespace gem5

#endif
