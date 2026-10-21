#include <gtest/gtest.h>
#include <fstream>

#include "dev/ai_mesh/mesh_cache_replay.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_invocation_binding.hh"
#include "dev/ai_mesh/mesh_json.hh"
#include "dev/ai_mesh/mesh_program_test_support.hh"
#include "dev/ai_mesh/mesh_weight_tags.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::test_support;

class CacheStartupBinding : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        std::ifstream file("tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb", std::ios::binary);
        ASSERT_TRUE(file.good());
        MeshBytes bytes((std::istreambuf_iterator<char>(file)), {});
        auto program = std::make_shared<DecodedProgram>();
        MeshLoadError error;
        ASSERT_TRUE(decodeMeshBinary(bytes, *program, error)) << error.message;
        arch.arch_digest_hex = hexDigest(program->header.arch_digest);
        ASSERT_TRUE(admitProgram(program, arch, admission, error)) << error.code << ": " << error.message;
        for (const auto core : arch.core_ids) {
            MoeWeightCache::Config config;
            config.core_id = core;
            config.slot_count = 1;
            config.slot_bytes = 4096;
            config.mshr_slots = config.eviction_slots = config.obligation_slots = config.subscriber_slots = 1;
            configs.emplace(core, config);
        }
        const auto &instance = program->transport.agent_instance_profiles.front();
        uint32_t weight_symbol = 0;
        for (const auto &row : program->transport.agent_request_binding_requirements)
            if (row.binding_kind == gem5::ai_mesh::agent_abi::kBindingKindWEIGHT_EXTERNAL)
                weight_symbol = row.symbol_id;
        const auto source = admission.context().symbolBinding(instance.mesh_entrypoint_id,
            instance.mesh_profile_id, weight_symbol);
        ASSERT_TRUE(source);
        MeshInvocationBinding binding;
        const auto variant = admission.context().owner("binding_slots", source->slot->slot_id);
        ASSERT_NE(variant, nullptr);
        ASSERT_TRUE(resolveInvocationBinding(admission, variant->variant_id, {}, binding, error)) << error.message;
        const auto resolved = binding.binding(source->slot->slot_id);
        ASSERT_NE(resolved, nullptr);
        address = resolved->allocation_address;
        size = resolved->allocation_size_bytes;
        boost::json::object symbol{{"symbol_id", weight_symbol}, {"address", address},
            {"data_hex", std::string(size * 2, '0')}};
        document = {{"program_semantic_digest", hexDigest(program->metadata.semantic_sha256)},
            {"effective_architecture_digest", arch.arch_digest_hex}, {"model_weight_image_digest", std::string(64, 'a')},
            {"program_weight_registry_digest", std::string(64, 'b')},
            {"weight_tag_manifest_digest", hexDigest(weightTagManifestDigest({}))},
            {"symbols", boost::json::array{symbol}}, {"reservation_token_bound", 1}};
    }

    std::optional<CacheStartupImage> prepare()
    {
        return CacheStartupImage::prepare(canonicalMeshJson(document), "", admission,
            arch.arch_digest_hex, std::string(64, 'a'), configs, reason);
    }

    RuntimeArch arch = testArch();
    MeshProgramAdmission admission;
    std::map<uint16_t, MoeWeightCache::Config> configs;
    boost::json::object document;
    uint64_t address = 0;
    uint64_t size = 0;
    std::string reason;
};

TEST_F(CacheStartupBinding, AdmittedCapacityAndAddressDefinePhysicalImage)
{
    auto image = prepare();
    ASSERT_TRUE(image) << reason;
    ASSERT_EQ(image->weights.size(), 1u);
    EXPECT_EQ(image->weights[0].address, address);
    EXPECT_EQ(image->weights[0].data.size(), size);
}

TEST_F(CacheStartupBinding, RejectsShiftedAddress)
{
    document.at("symbols").as_array()[0].as_object()["address"] = address + 64;
    EXPECT_FALSE(prepare());
    EXPECT_EQ(reason, "weight bytes or address differ from installed bindings");
}

TEST_F(CacheStartupBinding, RejectsTruncatedCapacity)
{
    ASSERT_GT(size, 1u);
    document.at("symbols").as_array()[0].as_object()["data_hex"] = std::string((size - 1) * 2, '0');
    EXPECT_FALSE(prepare());
    EXPECT_EQ(reason, "weight bytes or address differ from installed bindings");
}
