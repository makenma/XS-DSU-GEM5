#include <gtest/gtest.h>

#include "dev/ai_mesh/mesh_kv_persistence.hh"
#include "dev/ai_mesh/mesh_kv_manager_test_support.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_kv_test;

TEST(KvPersistentManifest, ValidatesIdentityGeometryAndTombstonesAtomically)
{
    KvPersistentIdentity identity;
    identity.architecture.fill(1);
    identity.program.fill(2);
    identity.weights.fill(3);
    identity.layout.fill(4);
    auto record = makeRecord(7, 1, KvState::Resident, 0, 8, 4, 5);
    record.contract_digest = MeshKvManager::contractDigest(identity.program,
        identity.weights, identity.layout, kBytesPerToken);
    auto state = persistentState({record}, {std::make_pair(7, 7)}, 6);
    state.tombstones.push_back({{9, 9}, 2});
    MeshKvManager source(testGeometry(1), testCapacity());
    std::string reason;
    ASSERT_TRUE(source.loadPersistent(state, reason)) << reason;
    auto manifest = KvPersistentManifest::capture(source, identity, reason);
    ASSERT_TRUE(manifest) << reason;
    ASSERT_TRUE(manifest->json(reason)) << reason;
    for (unsigned mutation = 0; mutation < 9; ++mutation) {
        auto invalid = *manifest;
        switch (mutation) {
          case 0: invalid.identity.architecture[0]++; break;
          case 1: invalid.identity.program[0]++; break;
          case 2: invalid.identity.weights[0]++; break;
          case 3: invalid.identity.layout[0]++; break;
          case 4: invalid.geometry.region_base += 4096; break;
          case 5: invalid.state.tombstones.clear(); break;
          case 6: invalid.state.next_kv_use_epoch++; break;
          case 7: invalid.state.records[0].content_digest->at(0)++; break;
          case 8: invalid.identity.plan[0]++; break;
        }
        MeshKvManager target(testGeometry(1), testCapacity());
        EXPECT_FALSE(invalid.restore(target, identity, manifest->digest, reason)) << mutation;
        EXPECT_EQ(target.recordCount(), 0u);
    }
    auto missing = state;
    missing.tombstones.clear();
    MeshKvManager altered(testGeometry(1), testCapacity());
    ASSERT_TRUE(altered.loadPersistent(missing, reason));
    const auto resigned = KvPersistentManifest::capture(altered, identity, reason);
    ASSERT_TRUE(resigned);
    MeshKvManager target(testGeometry(1), testCapacity());
    EXPECT_FALSE(resigned->restore(target, identity, manifest->digest, reason));
    EXPECT_EQ(target.recordCount(), 0u);
    ASSERT_TRUE(manifest->restore(target, identity, manifest->digest, reason)) << reason;
    EXPECT_EQ(target.stateScalars(), source.stateScalars());
    KvEdgeInputs edge;
    edge.releases.push_back(release(10, 9, 1));
    EXPECT_EQ(target.commitEdge(edge).releases.at(10), KvReleaseOutcome::StaleGeneration);
}

TEST(KvPersistentManifest, RejectsLiveCaptureAndInstallation)
{
    MeshKvManager live(testGeometry(1), testCapacity());
    MeshKvManager empty(testGeometry(1), testCapacity());
    KvPersistentIdentity identity;
    std::string reason;
    auto manifest = KvPersistentManifest::capture(empty, identity, reason);
    ASSERT_TRUE(manifest) << reason;
    KvEdgeInputs edge;
    edge.admissions.push_back(admit(1, 7));
    ASSERT_FALSE(live.commitEdge(edge).fatal);
    EXPECT_FALSE(KvPersistentManifest::capture(live, identity, reason));
    const auto before = live.stateScalars();
    EXPECT_FALSE(manifest->restore(live, identity, manifest->digest, reason));
    EXPECT_EQ(live.stateScalars(), before);
}
