#ifndef DEV_AI_MESH_MESH_PROGRAM_LOADER_HH
#define DEV_AI_MESH_MESH_PROGRAM_LOADER_HH

#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"
#include "dev/ai_mesh/mesh_moe_overlay.hh"
#include "dev/ai_mesh/mesh_moe_runtime.hh"
#include "dev/ai_mesh/mesh_weight_cache.hh"
#include "sim/sim_object.hh"

namespace gem5
{

struct MeshProgramLoaderParams;

namespace ai_mesh
{

class MeshDummyCore;
class MockAxiTransport;
class PeerSramAperture;

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
    const DecodedProgram &program() const { return *decoded; }
    const std::string &overlayImageFile() const { return overlay_image_file; }
    const std::string &weightPolicy() const { return weight_policy; }
    const std::string &programName() const { return program_name; }
    const RuntimeArch &arch() const { return runtime_arch; }
    const std::string &effectiveArchDigest() const
    {
        return effective_arch_digest;
    }
    const std::vector<MeshDummyCore *> &coreObjects() const { return core_objects; }

    struct TransferPlan
    {
        uint16_t receiver_core;
        std::vector<std::pair<uint64_t, uint64_t>> ranges;
    };
    const TransferPlan *transferPlan(uint32_t transfer_id) const;
    const std::vector<std::pair<uint32_t, MoeOverlayGraph>> &
    overlayGraphs() const
    {
        return overlay_graphs;
    }
    void installOverlayImage();

  private:
    std::string program_file;
    std::string arch_digest_hex;
    std::string effective_arch_digest;
    std::string overlay_image_file;
    std::array<uint8_t, 32> program_digest{};
    std::string weight_policy;
    std::vector<std::pair<uint32_t, MoeOverlayGraph>> overlay_graphs;


    void loadOverlayImage();
    void verifyOverlayWeightPolicy(const MoeOverlayGraph &graph,
                                   uint32_t layer_id,
                                   const std::string &path);
    void buildOverlayAddressSpace();
    void installWeightCaches();
    std::vector<uint32_t> cacheableTagsOf(uint16_t core_id) const;
    MoeOverlayAddressSpace overlay_addresses;
    std::vector<MeshDummyCore *> core_objects;
    MockAxiTransport *transport = nullptr;
    std::vector<PeerSramAperture *> apertures;
    std::shared_ptr<DecodedProgram> decoded;
    RuntimeArch runtime_arch;
    std::string program_name;
    std::map<uint32_t, TransferPlan> transfer_plans;
    bool load_ok = false;
};

} // namespace ai_mesh
} // namespace gem5

#endif
