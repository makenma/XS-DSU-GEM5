#include <gtest/gtest.h>

#include <map>
#include <set>

#include "dev/ai_mesh/runtime_key.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(RuntimeKeyTest, InstanceGenerationCarriesValueAndOrder)
{
    InstanceGeneration low(3);
    InstanceGeneration high(9);
    EXPECT_EQ(low.value(), 3u);
    EXPECT_LT(low, high);
    EXPECT_GT(high, low);
    EXPECT_NE(low, high);
    EXPECT_EQ(InstanceGeneration().value(), 0u);
}

TEST(RuntimeKeyTest, StaticProgramKeysAreProgramGlobal)
{
    RuntimeObjectKey event = staticProgramObject(
        InstanceGeneration(4), mesh_abi::MeshObjectKind::EVENT, 7);
    EXPECT_EQ(event.instance.value(), 4u);
    EXPECT_EQ(event.domain, mesh_abi::MeshObjectDomain::STATIC);
    EXPECT_EQ(event.regionGroupId, 0);
    EXPECT_EQ(event.regionId, 0);
    EXPECT_EQ(event.kind, mesh_abi::MeshObjectKind::EVENT);
    EXPECT_EQ(event.ordinal, 7u);
}

TEST(RuntimeKeyTest, OverlayKeysCarryDomainGroupAndRegion)
{
    RuntimeObjectKey view = overlayObject(
        InstanceGeneration(2), 3, 1, mesh_abi::MeshObjectKind::VIEW, 1);
    EXPECT_EQ(view.domain, mesh_abi::MeshObjectDomain::MOE_OVERLAY);
    EXPECT_EQ(view.regionGroupId, 3);
    EXPECT_EQ(view.regionId, 1);
    EXPECT_EQ(view.kind, mesh_abi::MeshObjectKind::VIEW);
    EXPECT_EQ(view.ordinal, 1u);
}

TEST(RuntimeKeyTest, SameOrdinalInDifferentDomainsOrRegionsDiffers)
{
    const InstanceGeneration instance(5);
    const RuntimeObjectKey overlay_a = overlayObject(
        instance, 1, 0, mesh_abi::MeshObjectKind::VIEW, 1);
    const RuntimeObjectKey overlay_b = overlayObject(
        instance, 1, 1, mesh_abi::MeshObjectKind::VIEW, 1);
    const RuntimeObjectKey static_one = staticProgramObject(
        instance, mesh_abi::MeshObjectKind::VIEW, 1);
    const RuntimeObjectKey other_kind = overlayObject(
        instance, 1, 0, mesh_abi::MeshObjectKind::COMMAND, 1);
    EXPECT_NE(overlay_a, overlay_b);
    EXPECT_NE(overlay_a, static_one);
    EXPECT_NE(overlay_a, other_kind);
    EXPECT_NE(static_one, other_kind);
}

TEST(RuntimeKeyTest, KeyOrderIsTotalAndStable)
{
    const InstanceGeneration instance(1);
    const RuntimeObjectKey first = staticProgramObject(
        instance, mesh_abi::MeshObjectKind::COMMAND, 1);
    const RuntimeObjectKey second = staticProgramObject(
        instance, mesh_abi::MeshObjectKind::COMMAND, 2);
    const RuntimeObjectKey later_instance = staticProgramObject(
        InstanceGeneration(2), mesh_abi::MeshObjectKind::COMMAND, 1);
    std::map<RuntimeObjectKey, int> table;
    table[second] = 2;
    table[first] = 1;
    table[later_instance] = 3;
    EXPECT_EQ(table.begin()->second, 1);
    EXPECT_LT(first, second);
    EXPECT_LT(second, later_instance);
}

TEST(RuntimeKeyTest, KeysIndexBoundedTables)
{
    std::set<RuntimeObjectKey> seen;
    const RuntimeObjectKey key = staticProgramObject(
        InstanceGeneration(1), mesh_abi::MeshObjectKind::EVENT, 4);
    seen.insert(key);
    seen.insert(key);
    EXPECT_EQ(seen.size(), 1u);
}

TEST(RuntimeKeyTest, CheckedSequenceAddDetectsOverflow)
{
    EXPECT_EQ(checkedSequenceAdd(InstanceGeneration(4), 6)->value(), 10u);
    EXPECT_FALSE(checkedSequenceAdd(
        InstanceGeneration(UINT64_MAX - 1), 2).has_value());
}

TEST(RuntimeKeyTest, CoreIdIsInvalidUntilAssigned)
{
    MeshCoreId unset;
    EXPECT_FALSE(unset.valid());
    EXPECT_EQ(unset.value(), MeshCoreId::INVALID_VALUE);
    MeshCoreId core(15);
    EXPECT_TRUE(core.valid());
    EXPECT_EQ(core.value(), 15u);
    EXPECT_TRUE(MeshCoreId::admissible(0));
    EXPECT_TRUE(MeshCoreId::admissible(MeshCoreId::MAX_VALUE));
    EXPECT_FALSE(MeshCoreId::admissible(MeshCoreId::INVALID_VALUE));
    EXPECT_FALSE(MeshCoreId::admissible(0x10000));
}

TEST(RuntimeKeyTest, CoreIdOrdersByNumericValue)
{
    EXPECT_LT(MeshCoreId(2), MeshCoreId(10));
    EXPECT_EQ(MeshCoreId(7), MeshCoreId(7));
    EXPECT_NE(MeshCoreId(7), MeshCoreId(8));
}

}
}
}
