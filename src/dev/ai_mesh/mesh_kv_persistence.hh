#ifndef DEV_AI_MESH_MESH_KV_PERSISTENCE_HH
#define DEV_AI_MESH_MESH_KV_PERSISTENCE_HH

#include "dev/ai_mesh/mesh_kv_manager.hh"

namespace gem5::ai_mesh
{
struct KvPersistentIdentity
{
    std::array<uint8_t, 32> architecture{};
    std::array<uint8_t, 32> program{};
    std::array<uint8_t, 32> weights{};
    std::array<uint8_t, 32> layout{};
    std::array<uint8_t, 32> plan{};
    bool operator==(const KvPersistentIdentity &other) const;
};

class KvPersistentManifest
{
  public:
    KvPersistentIdentity identity;
    KvGeometry geometry;
    KvCapacity capacity;
    KvPersistentState state;
    std::array<uint8_t, 32> digest{};

    static std::optional<KvPersistentManifest> capture(
        const MeshKvManager &manager, const KvPersistentIdentity &identity,
        std::string &reason);
    bool restore(MeshKvManager &manager, const KvPersistentIdentity &expected,
                 const std::array<uint8_t, 32> &expectedDigest, std::string &reason) const;
    std::optional<std::string> json(std::string &reason) const;

  private:
    std::optional<std::string> payload(std::string &reason) const;
};
}
#endif
