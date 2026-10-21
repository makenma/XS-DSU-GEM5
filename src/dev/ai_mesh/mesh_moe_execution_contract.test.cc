#include "dev/ai_mesh/mesh_binary_test_support.hh"
using namespace gem5::ai_mesh::test_support;
namespace gem5
{
namespace ai_mesh
{
namespace
{
using namespace mesh_abi;
std::array<uint8_t, 32> tokenOfHex(const char *hex)
{
    std::array<uint8_t, 32> out{};
    if (hex == nullptr || std::strlen(hex) != 64)
        return out;
    for (size_t i = 0; i < 32; i++) {
        const auto nibble = [](char c) -> uint8_t {
            return c <= '9' ? uint8_t(c - '0') : uint8_t(c - 'a' + 10);
        };
        out[i] = uint8_t((nibble(hex[2 * i]) << 4) | nibble(hex[2 * i + 1]));
    }
    return out;
}

MoeRngKey rngKeyOf(const golden::RngKeyFields &fields)
{
    MoeRngKey key;
    key.master_seed = fields.master_seed;
    key.workload_plan_digest = hexToDigest(fields.workload_plan_digest_hex);
    key.user_id = fields.user_id;
    key.task_seq = fields.task_seq;
    key.repair_round = fields.repair_round;
    key.phase = fields.phase;
    key.sequence_ordinal = fields.sequence_ordinal;
    key.token_ordinal = fields.token_ordinal;
    key.layer_id = fields.layer_id;
    key.logical_source_rank = fields.logical_source_rank;
    key.topk_slot = fields.topk_slot;
    key.draw_id = fields.draw_id;
    return key;
}

std::string hexOfBytes(const std::array<uint8_t, MoeRngKey::kBytes> &bytes)
{
    static const char digits[] = "0123456789abcdef";
    std::string hex;
    for (uint8_t byte : bytes) {
        hex += digits[byte >> 4];
        hex += digits[byte & 0xF];
    }
    return hex;
}

TEST(MeshMoeRngTest, MatchesCrossLanguageKeyedGolden)
{
    EXPECT_EQ(golden::kRngKeyBytes, MoeRngKey::kBytes);
    ASSERT_GT(golden::kRngVectorCount, 0u);
    for (uint32_t index = 0; index < golden::kRngVectorCount; index++) {
        const auto &vector = golden::kRngVectors[index];
        const MoeRngKey key = rngKeyOf(vector.key);
        EXPECT_EQ(hexOfBytes(key.encode()), std::string(vector.key_hex))
            << vector.name;
        EXPECT_EQ(moeRngSeed64(key), vector.seed64) << vector.name;
        EXPECT_EQ(moeRngDraw(key), vector.splitmix64) << vector.name;
        EXPECT_EQ(moeRngThreshold(vector.total, vector.splitmix64),
                  vector.threshold) << vector.name;
        std::vector<uint64_t> weights(vector.weights,
                                      vector.weights + vector.weight_count);
        uint64_t total = 0;
        for (uint64_t weight : weights)
            total += weight;
        EXPECT_EQ(total, vector.total) << vector.name;
        EXPECT_EQ(moeRngCategorical(weights, key), vector.selected_index)
            << vector.name;
    }
}

TEST(MeshMoeRngTest, MatchesPublishedSplitmixSequence)
{
    EXPECT_EQ(splitmix64Once(0), 0xE220A8397B1DCDAFull);
    EXPECT_EQ(splitmix64Once(1), 0x910A2DEC89025CC1ull);
}

TEST(MeshMoeRngTest, MatchesCrossLanguageWithoutReplacementGolden)
{
    ASSERT_GT(golden::kUniformVectorCount, 0u);
    for (uint32_t index = 0; index < golden::kUniformVectorCount; index++) {
        const auto &vector = golden::kUniformVectors[index];
        const MoeRngKey base = rngKeyOf(vector.key);
        EXPECT_EQ(hexOfBytes(base.encode()), std::string(vector.key_hex))
            << vector.name;
        const auto selected = moeRngWithoutReplacement(
            vector.expert_count, vector.top_k, base);
        ASSERT_EQ(selected.size(), vector.top_k) << vector.name;
        for (uint32_t slot = 0; slot < vector.top_k; slot++)
            EXPECT_EQ(selected[slot], vector.selected[slot]) << vector.name;
    }
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(MeshMoeOverlayTest, MatchesCrossLanguageOverlayGolden)
{
    MoeOverlayGraph graph;
    ASSERT_GT(golden::kGate5OverlayObjectCount, 0u);
    for (uint32_t index = 0; index < golden::kGate5OverlayObjectCount;
         index++) {
        const auto &source = golden::kGate5OverlayObjects[index];
        MoeOverlayEntry entry;
        entry.kind = source.kind;
        entry.region_id = source.region_id;
        entry.ordinal = source.ordinal;
        entry.owner_core = source.owner_core;
        entry.secondary_kind = source.secondary_kind;
        entry.expert_id = source.expert_id;
        entry.src_core = source.src_core;
        entry.dst_core = source.dst_core;
        entry.chunk_ordinal = source.chunk_ordinal;
        entry.phase = source.phase;
        entry.role = source.role;
        entry.access = source.access;
        entry.bytes = source.bytes;
        entry.alignment = source.alignment;
        entry.offset = source.offset;
        entry.ref_region = source.ref_region;
        entry.ref_ordinal = source.ref_ordinal;
        entry.src_view_region = source.src_view_region;
        entry.src_view_ordinal = source.src_view_ordinal;
        entry.dst_view_region = source.dst_view_region;
        entry.dst_view_ordinal = source.dst_view_ordinal;
        entry.backing_kind = source.backing_kind;
        entry.semantic_owner_kind = source.semantic_owner_kind;
        entry.semantic_owner_ref0 = source.semantic_owner_ref0;
        entry.validity_extent = source.validity_extent;
        entry.token = tokenOfHex(source.token_hex);
        entry.wait_count = source.wait_count;
        entry.signal_count = source.signal_count;
        graph.add(entry);
    }
    std::string error;
    EXPECT_TRUE(graph.validate(error)) << error;
    std::vector<uint32_t> pythonOrdinals;
    for (const auto &entry : graph.objects())
        pythonOrdinals.push_back(entry.ordinal);
    graph.assignOrdinals();
    const auto &objects = graph.objects();
    ASSERT_EQ(objects.size(), size_t(golden::kGate5OverlayObjectCount));
    for (size_t index = 0; index < objects.size(); index++)
        EXPECT_EQ(objects[index].ordinal, pythonOrdinals[index])
            << golden::kGate5OverlayObjects[index].kind << " @ "
            << golden::kGate5OverlayObjects[index].region_id;
    std::string secondError;
    EXPECT_TRUE(graph.validate(secondError)) << secondError;
    if (digestToHex(graph.digest()) !=
        std::string(golden::kGate5OverlayDigest)) {
        const auto bytes = graph.wireBytes();
        static const char digits[] = "0123456789abcdef";
        std::string prefix;
        for (size_t i = 0; i < std::min<size_t>(bytes.size(), 122); i++) {
            prefix += digits[bytes[i] >> 4];
            prefix += digits[bytes[i] & 0xF];
        }
        ADD_FAILURE() << "first overlay entry bytes: " << prefix;
    }
    EXPECT_EQ(digestToHex(graph.digest()),
              std::string(golden::kGate5OverlayDigest));
    size_t allocations = 0;
    size_t views = 0;
    size_t commands = 0;
    size_t events = 0;
    size_t descriptors = 0;
    size_t transfers = 0;
    for (const auto &entry : objects) {
        switch (entry.kind) {
        case mesh_abi::kMeshObjectKindALLOCATION: allocations++; break;
        case mesh_abi::kMeshObjectKindVIEW: views++; break;
        case mesh_abi::kMeshObjectKindCOMMAND: commands++; break;
        case mesh_abi::kMeshObjectKindEVENT: events++; break;
        case mesh_abi::kMeshObjectKindDESCRIPTOR: descriptors++; break;
        case mesh_abi::kMeshObjectKindTRANSFER: transfers++; break;
        default: break;
        }
    }
    EXPECT_EQ(allocations, golden::kGate5OverlayAllocations);
    EXPECT_EQ(views, golden::kGate5OverlayViews);
    EXPECT_EQ(commands, golden::kGate5OverlayCommands);
    EXPECT_EQ(events, golden::kGate5OverlayEvents);
    EXPECT_EQ(descriptors, golden::kGate5OverlayDescriptors);
    EXPECT_EQ(transfers, golden::kGate5OverlayTransfers);
}

TEST(MeshMoeOverlayTest, RejectsDuplicateKeysAndDenseOrdinalBreaks)
{
    MoeOverlayEntry first;
    first.kind = mesh_abi::kMeshObjectKindEVENT;
    first.region_id = 1;
    first.ordinal = 1;
    first.role = mesh_abi::kMoeEventRoleREGION_TERMINAL;
    first.wait_count = 1;
    MoeOverlayEntry duplicate = first;
    duplicate.ordinal = 2;
    MoeOverlayGraph graph;
    graph.add(first);
    graph.add(duplicate);
    std::string error;
    EXPECT_FALSE(graph.validate(error));
    EXPECT_EQ(error, "duplicate canonical overlay key");
    MoeOverlayEntry gap = first;
    gap.ordinal = 3;
    MoeOverlayGraph spaced;
    spaced.add(first);
    spaced.add(gap);
    std::string spacedError;
    EXPECT_FALSE(spaced.validate(spacedError));
    MoeOverlayEntry noProducer = first;
    noProducer.wait_count = 0;
    MoeOverlayGraph orphan;
    orphan.add(noProducer);
    std::string orphanError;
    EXPECT_FALSE(orphan.validate(orphanError));
    EXPECT_EQ(orphanError, "overlay event needs exactly one producer");
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

MoeInsertionGate::RegionSpec gateSpec(uint32_t layer_id, uint32_t region_id,
                                      uint32_t insert_after,
                                      uint32_t resume_before,
                                      uint32_t resume_index)
{
    MoeInsertionGate::RegionSpec spec;
    spec.layer_id = layer_id;
    spec.region_id = region_id;
    spec.insert_after_command_id = insert_after;
    spec.resume_before_command_id = resume_before;
    spec.resume_before_index = resume_index;
    return spec;
}

TEST(MoeInsertionGateTest, HoldsTheCursorBetweenInsertAndResume)
{
    MoeInsertionGate gate;
    gate.arm(gateSpec(1, 1, 4, 5, 4));
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::ARMED);
    EXPECT_FALSE(gate.blockedIndex(0));
    EXPECT_FALSE(gate.blockedIndex(3));
    EXPECT_FALSE(gate.blockedIndex(4));
    gate.noteIssued(1);
    gate.noteIssued(4);
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::REACHED);
    EXPECT_FALSE(gate.blockedIndex(3));
    EXPECT_TRUE(gate.blockedIndex(4));
    EXPECT_TRUE(gate.blockedIndex(5));
    EXPECT_TRUE(gate.blockedIndex(9));
    EXPECT_FALSE(gate.allReleased());
    gate.release(1);
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::RELEASED);
    EXPECT_FALSE(gate.blockedIndex(4));
    EXPECT_FALSE(gate.blockedIndex(9));
    EXPECT_TRUE(gate.allReleased());
}

TEST(MoeInsertionGateTest, KeepsOtherLayersDecodable)
{
    MoeInsertionGate gate;
    gate.arm(gateSpec(1, 1, 4, 5, 4));
    gate.arm(gateSpec(2, 1, 6, 7, 6));
    EXPECT_FALSE(gate.blockedIndex(3));
    gate.noteIssued(4);
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::REACHED);
    EXPECT_EQ(gate.phase(2), MoeInsertionGate::RegionPhase::ARMED);
    EXPECT_TRUE(gate.blockedIndex(4));
    EXPECT_TRUE(gate.blockedIndex(6));
    gate.release(1);
    EXPECT_FALSE(gate.blockedIndex(4));
    EXPECT_FALSE(gate.blockedIndex(6));
    gate.noteIssued(6);
    EXPECT_TRUE(gate.blockedIndex(6));
    gate.release(2);
    EXPECT_FALSE(gate.blockedIndex(6));
    EXPECT_TRUE(gate.allReleased());
    EXPECT_NE(gate.describe().find("layer=1"), std::string::npos);
}

TEST(MoeInsertionGateTest, IgnoresUnrelatedCommandsUntilTheGateIsReached)
{
    MoeInsertionGate gate;
    gate.arm(gateSpec(1, 1, 4, 5, 4));
    gate.noteIssued(2);
    gate.noteIssued(3);
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::ARMED);
    EXPECT_FALSE(gate.blockedIndex(4));
    EXPECT_TRUE(gate.layerArmed(1));
    EXPECT_FALSE(gate.layerArmed(2));
    EXPECT_EQ(gate.phase(2), MoeInsertionGate::RegionPhase::IDLE);
    EXPECT_FALSE(gate.allReleased());
    gate.noteIssued(4);
    EXPECT_TRUE(gate.blockedIndex(4));
    EXPECT_FALSE(gate.blockedIndex(3));
    gate.release(1);
    EXPECT_TRUE(gate.allReleased());
    EXPECT_FALSE(gate.blockedIndex(4));
    MoeInsertionGate fresh;
    fresh.arm(gateSpec(3, 2, 8, 9, 8));
    EXPECT_EQ(fresh.phase(3), MoeInsertionGate::RegionPhase::ARMED);
    EXPECT_FALSE(fresh.blockedIndex(8));
    fresh.noteIssued(8);
    EXPECT_TRUE(fresh.blockedIndex(8));
    fresh.release(3);
    EXPECT_FALSE(fresh.blockedIndex(8));
    EXPECT_NE(fresh.describe().find("region=2"), std::string::npos);
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

MoeOverlayGraph overlayGoldenGraph()
{
    MoeOverlayGraph graph;
    for (uint32_t index = 0; index < golden::kGate5OverlayObjectCount; index++) {
        const auto &source = golden::kGate5OverlayObjects[index];
        MoeOverlayEntry entry;
        entry.kind = source.kind;
        entry.region_id = source.region_id;
        entry.ordinal = source.ordinal;
        entry.owner_core = source.owner_core;
        entry.secondary_kind = source.secondary_kind;
        entry.expert_id = source.expert_id;
        entry.src_core = source.src_core;
        entry.dst_core = source.dst_core;
        entry.chunk_ordinal = source.chunk_ordinal;
        entry.phase = source.phase;
        entry.role = source.role;
        entry.access = source.access;
        entry.bytes = source.bytes;
        entry.alignment = source.alignment;
        entry.offset = source.offset;
        entry.ref_region = source.ref_region;
        entry.ref_ordinal = source.ref_ordinal;
        entry.backing_kind = source.backing_kind;
        entry.semantic_owner_kind = source.semantic_owner_kind;
        entry.semantic_owner_ref0 = source.semantic_owner_ref0;
        entry.validity_extent = source.validity_extent;
        entry.token = tokenOfHex(source.token_hex);
        entry.wait_count = source.wait_count;
        entry.signal_count = source.signal_count;
        entry.view_count = source.view_count;
        for (uint32_t ref = 0; ref < MoeOverlayEntry::kMaxRefs; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++) {
                entry.wait_refs[ref][field] = source.wait_refs[ref][field];
                entry.signal_refs[ref][field] = source.signal_refs[ref][field];
                entry.view_refs[ref][field] = source.view_refs[ref][field];
            }
        graph.add(entry);
    }
    for (uint32_t index = 0; index < golden::kGate5OverlayPayloadCount;
         index++) {
        const auto &source = golden::kGate5OverlayPayload[index];
        MoeFillPayload payload;
        payload.region_id = source.region_id;
        payload.ordinal = source.ordinal;
        payload.offset = source.offset;
        payload.bytes = source.bytes;
        graph.addPayload(payload);
    }
    if (golden::kGate5OverlayPayloadBytes > 0)
        graph.addPayloadBytes(golden::kGate5OverlayPayloadBlob,
                              golden::kGate5OverlayPayloadBytes);
    graph.setFillMode(golden::kGate5OverlayFillMode);
    for (uint32_t index = 0; index < golden::kGate5OverlayScratchCount;
         index++) {
        const auto &source = golden::kGate5OverlayScratch[index];
        MoeScratchInterval interval;
        interval.region_id = source.region_id;
        interval.ordinal = source.ordinal;
        interval.offset = source.offset;
        interval.bytes = source.bytes;
        graph.addScratch(interval);
    }
    graph.assignOrdinals();
    return graph;
}

MoeOverlayAddressSpace overlayGoldenAddresses(const MoeOverlayGraph &graph)
{
    MoeOverlayAddressSpace addresses;
    uint64_t next = 0x800;
    for (const auto &entry : graph.objects()) {
        if (entry.kind != mesh_abi::kMeshObjectKindVIEW ||
            entry.backing_kind != mesh_abi::kMoeViewBackingSTATIC_ALLOCATION)
            continue;
        auto &interval = addresses.allocations[entry.ref_ordinal];
        interval.second = std::max<uint64_t>(interval.second, entry.bytes);
        if (interval.first == 0) {
            interval.first = next;
            next += ((entry.bytes + 63) / 64) * 64 + 64;
        }
    }
    addresses.hbm_region = 0;
    addresses.sram_region = 0;
    return addresses;
}

TEST(MoeOverlayRuntimeTest, ExecutesTheGoldenOverlayInDependencyOrder)
{
    const MoeOverlayGraph graph = overlayGoldenGraph();
    const MoeOverlayAddressSpace addresses = overlayGoldenAddresses(graph);
    ProgramScoreboard scoreboard;
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 16, /*alignment*/ 64,
                    /*line_bytes*/ 32, /*read_bytes_per_cycle*/ 32,
                    /*write_bytes_per_cycle*/ 32, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 1);
    sram.addPartition(mesh_abi::SramPartitionKind::RUNTIME_SCRATCH, 0x80000,
                      0x40000, 64, 64);
    MoeOverlayEventBus bus;
    std::vector<std::unique_ptr<MoeOverlayExecutor>> executors;
    std::map<uint16_t, MoeOverlayExecutor *> by_core;
    std::set<uint16_t> overlay_cores;
    for (const auto &entry : graph.objects())
        if (entry.kind == mesh_abi::kMeshObjectKindCOMMAND)
            overlay_cores.insert(uint16_t(entry.owner_core));
    ASSERT_FALSE(overlay_cores.empty());
    for (uint16_t core : overlay_cores) {
        MoeOverlayExecutor::Config config;
        config.core_id = core;
        auto executor = std::make_unique<MoeOverlayExecutor>(
            config, &scoreboard, &sram, &bus, &addresses);
        executor->load(golden::kGate5OverlayLayerId, InstanceGeneration(1),
                       graph);
        bus.subscribe(core, executor.get());
        by_core[core] = executor.get();
        executors.push_back(std::move(executor));
    }
    std::set<uint32_t> region_ids;
    for (const auto &entry : graph.objects())
        if (entry.kind == mesh_abi::kMeshObjectKindCOMMAND &&
            entry.region_id != 0)
            region_ids.insert(entry.region_id);
    ASSERT_FALSE(region_ids.empty());
    for (uint32_t region_id : region_ids) {
        RuntimeObjectKey entry_key;
        entry_key.instance = InstanceGeneration(1);
        entry_key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
        entry_key.regionGroupId = golden::kGate5OverlayLayerId;
        entry_key.regionId = uint16_t(region_id);
        entry_key.kind = mesh_abi::MeshObjectKind::EVENT;
        entry_key.ordinal = 0;
        for (auto &kv : by_core)
            kv.second->publishEntry(entry_key, 0);
    }
    for (uint32_t cycle = 0; cycle < 2000; cycle++) {
        bool progressed = false;
        uint64_t live_now = 0;
        for (auto &executor : executors) {
            progressed = executor->tick(cycle) || progressed;
            live_now += executor->liveCommands();
        }
        if (!progressed && live_now == 0)
            break;
    }
    for (auto &executor : executors) {
        if (executor->layerId() != golden::kGate5OverlayLayerId)
            continue;
        if (executor->completedCommands() == 0)
            continue;
        EXPECT_TRUE(executor->drained());
        EXPECT_EQ(executor->issuedCommands(), executor->completedCommands());
    }
    const auto &core0 = *by_core.begin()->second;
    uint64_t all_completed = 0;
    uint64_t all_bytes = 0;
    uint64_t all_service = 0;
    for (auto &kv : by_core) {
        all_completed += kv.second->completedCommands();
        all_bytes += kv.second->sramWriteBytes() + kv.second->sramReadBytes();
        all_service += kv.second->sramServiceCycles();
        for (uint32_t region_id : region_ids) {
            const uint64_t bytes =
                kv.second->sramReadBytesForRegion(region_id) +
                kv.second->sramWriteBytesForRegion(region_id);
            const bool region_here = std::any_of(
                graph.objects().begin(), graph.objects().end(),
                [region_id, core = kv.first](const MoeOverlayEntry &entry) {
                    return entry.kind == mesh_abi::kMeshObjectKindCOMMAND &&
                           entry.region_id == region_id &&
                           entry.owner_core == core;
                });
            if (region_here) {
                EXPECT_GT(bytes, 0u)
                    << "region " << region_id << " served no SRAM bytes";
            }
        }
    }
    EXPECT_GT(all_completed, 0u);
    EXPECT_GT(all_bytes, 0u);
    EXPECT_GT(all_service, 0u);
    ASSERT_FALSE(core0.signalled().empty());
    bool saw_group_exit = false;
    for (const auto &event : core0.signalled())
        if (event.regionId == 0)
            saw_group_exit = true;
    EXPECT_TRUE(saw_group_exit);
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(MoeOverlayImageTest, CachedWeightViewsCarryTheRuntimeTagIndices)
{
    const MeshBytes program_image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_dual.mshb");
    ASSERT_FALSE(program_image.empty());
    DecodedProgram program;
    MeshLoadError load_error;
    ASSERT_TRUE(decodeMeshBinary(program_image, program, load_error))
        << load_error.code << ": " << load_error.message;
    const MeshBytes image = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate5/moe_dual_overlay_cached.bin");
    ASSERT_FALSE(image.empty());
    MoeOverlayImage codec;
    MoeOverlayGraph graph;
    uint32_t layer_id = 0;
    std::string error;
    ASSERT_TRUE(codec.decode(image, graph, layer_id, error)) << error;
    uint32_t weight_views = 0;
    for (const auto &entry : graph.objects()) {
        if (entry.kind != mesh_abi::kMeshObjectKindVIEW ||
            entry.secondary_kind != mesh_abi::kMoeViewKindWEIGHT)
            continue;
        EXPECT_EQ(entry.backing_kind,
                  mesh_abi::kMoeViewBackingWEIGHT_CACHE_SLOT);
        EXPECT_EQ(entry.semantic_owner_kind, mesh_abi::kMoeSemanticOwnerEXPERT);
        bool found_expert = false;
        for (const auto &expert : program.transport.moe_expert_specs) {
            if (expert.layer_id != layer_id ||
                expert.expert_id != entry.semantic_owner_ref0)
                continue;
            found_expert = true;
            EXPECT_EQ(entry.owner_core, expert.core_id);
            EXPECT_EQ(entry.bytes, expert.weight_bytes);
            bool matched = false;
            for (const auto &site :
                 weightTagSitesOf(program, expert.core_id))
                if (site.weight_symbol_id == expert.weight_symbol_id &&
                    site.weight_region_offset == expert.weight_region_offset &&
                    site.weight_bytes == expert.weight_bytes) {
                    EXPECT_EQ(entry.ref_ordinal, site.weight_tag_index);
                    matched = true;
                }
            EXPECT_TRUE(matched) << "expert " << expert.expert_id
                                 << " of layer " << layer_id
                                 << " has no runtime tag site";
        }
        EXPECT_TRUE(found_expert);
        weight_views++;
    }
    EXPECT_EQ(weight_views, 2u);
    EXPECT_TRUE(std::none_of(
        graph.objects().begin(), graph.objects().end(),
        [](const MoeOverlayEntry &entry) {
            return entry.kind == mesh_abi::kMeshObjectKindDESCRIPTOR &&
                   entry.secondary_kind ==
                       mesh_abi::kMoeDescriptorKindSTREAMED_WEIGHT;
        }));
}

TEST(MoeOverlayImageTest, DecodesTheRuntimeImageIntoTheCanonicalGraph)
{
    const MeshBytes image = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate5/moe_overlay_objects.bin");
    ASSERT_FALSE(image.empty());
    MoeOverlayImage codec;
    MoeOverlayGraph graph;
    uint32_t layer_id = 0;
    std::string error;
    ASSERT_TRUE(codec.decode(image, graph, layer_id, error)) << error;
    EXPECT_EQ(layer_id, golden::kGate5OverlayLayerId);
    EXPECT_EQ(graph.objects().size(), size_t(golden::kGate5OverlayObjectCount));
    std::string validate_error;
    EXPECT_TRUE(graph.validate(validate_error)) << validate_error;
    EXPECT_EQ(digestToHex(graph.digest()),
              std::string(golden::kGate5OverlayDigest));
    const auto &scratch = graph.scratch();
    ASSERT_EQ(scratch.size(), size_t(golden::kGate5OverlayScratchCount));
    for (size_t index = 0; index < scratch.size(); index++) {
        const auto &interval = scratch[index];
        const auto &expected = golden::kGate5OverlayScratch[index];
        EXPECT_EQ(interval.region_id, expected.region_id);
        EXPECT_EQ(interval.ordinal, expected.ordinal);
        EXPECT_EQ(interval.offset, expected.offset);
        EXPECT_EQ(interval.bytes, expected.bytes);
    }
    for (const auto &entry : graph.objects()) {
        if (entry.kind != mesh_abi::kMeshObjectKindALLOCATION)
            continue;
        const auto *interval =
            graph.resolveAllocation(entry.region_id, entry.ordinal);
        ASSERT_NE(interval, nullptr);
        EXPECT_EQ(interval->bytes, entry.bytes);
    }
    std::map<std::tuple<uint32_t, uint32_t, uint32_t>,
             const golden::OverlayObjectEntry *>
        golden_by_key;
    uint32_t golden_views = 0;
    for (uint32_t index = 0; index < golden::kGate5OverlayObjectCount; index++) {
        const auto &source = golden::kGate5OverlayObjects[index];
        golden_by_key[{uint32_t(source.kind), source.region_id,
                       source.ordinal}] = &source;
        golden_views += source.view_count;
    }
    uint32_t decoded_views = 0;
    for (const auto &entry : graph.objects()) {
        const auto it = golden_by_key.find(
            {uint32_t(entry.kind), entry.region_id, entry.ordinal});
        ASSERT_NE(it, golden_by_key.end());
        const auto &source = *it->second;
        EXPECT_EQ(entry.src_view_region, source.src_view_region);
        EXPECT_EQ(entry.src_view_ordinal, source.src_view_ordinal);
        EXPECT_EQ(entry.dst_view_region, source.dst_view_region);
        EXPECT_EQ(entry.dst_view_ordinal, source.dst_view_ordinal);
        EXPECT_EQ(entry.view_count, source.view_count);
        for (uint32_t ref = 0; ref < MoeOverlayEntry::kMaxRefs; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++)
                EXPECT_EQ(entry.view_refs[ref][field],
                          source.view_refs[ref][field]);
        decoded_views += entry.view_count;
    }
    EXPECT_GT(decoded_views, 0u);
    EXPECT_EQ(decoded_views, golden_views);
    const auto round_trip = codec.encode(graph, layer_id);
    EXPECT_EQ(round_trip, image);
    const MeshBytes truncated(image.begin(), image.end() - 1);
    MoeOverlayGraph rejected;
    uint32_t rejected_layer = 0;
    std::string truncated_error;
    EXPECT_FALSE(codec.decode(truncated, rejected, rejected_layer,
                              truncated_error));
    EXPECT_TRUE(truncated_error ==
                    "overlay image size does not match its object count" ||
                truncated_error == "overlay payload escapes its blob")
        << truncated_error;
    MeshBytes wrong_magic = image;
    wrong_magic[0] ^= 0xFF;
    MoeOverlayGraph other;
    uint32_t other_layer = 0;
    std::string magic_error;
    EXPECT_FALSE(codec.decode(wrong_magic, other, other_layer, magic_error));
    EXPECT_EQ(magic_error, "overlay image magic mismatch");
}

class StubOverlayDmaPort : public MoeOverlayDmaPort
{
  public:
    bool submitOverlayDma(const mesh_abi::DmaDescriptor &descriptor,
                          const RuntimeObjectKey &descriptor_key,
                          const RuntimeObjectKey &command_key,
                          uint64_t issue_tick) override
    {
        submissions.push_back(command_key);
        issue_ticks.push_back(issue_tick);
        if (refusals > 0) {
            --refusals;
            return false;
        }
        ++accepted;
        return true;
    }

    void bindFillPattern(const RuntimeObjectKey &command,
                         uint64_t pattern) override
    {
        patterns[command] = pattern;
    }

    void bindFillContent(const RuntimeObjectKey &command,
                         const std::vector<uint8_t> &content,
                         bool install_bytes) override
    {
        contents[command] = content.size();
        installed[command] = install_bytes;
    }

    uint32_t refusals = 0;
    uint32_t accepted = 0;
    std::vector<RuntimeObjectKey> submissions;
    std::vector<uint64_t> issue_ticks;
    std::map<RuntimeObjectKey, uint64_t> patterns;
    std::map<RuntimeObjectKey, size_t> contents;
    std::map<RuntimeObjectKey, bool> installed;
};

MoeOverlayGraph fillOverlayGraph()
{
    MoeOverlayGraph graph;
    MoeOverlayEntry allocation;
    allocation.kind = mesh_abi::kMeshObjectKindALLOCATION;
    allocation.region_id = 1;
    allocation.ordinal = 1;
    allocation.bytes = 64;
    allocation.alignment = 64;
    allocation.validity_extent = 64;
    graph.add(allocation);
    MoeOverlayEntry view;
    view.kind = mesh_abi::kMeshObjectKindVIEW;
    view.region_id = 1;
    view.ordinal = 1;
    view.owner_core = 0;
    view.secondary_kind = mesh_abi::kMoeViewKindPAD_BUFFER;
    view.access = mesh_abi::kMoeViewAccessWRITE;
    view.bytes = 64;
    view.backing_kind = mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION;
    view.ref_region = 1;
    view.ref_ordinal = 1;
    graph.add(view);
    MoeOverlayEntry descriptor;
    descriptor.kind = mesh_abi::kMeshObjectKindDESCRIPTOR;
    descriptor.region_id = 1;
    descriptor.ordinal = 1;
    descriptor.secondary_kind = mesh_abi::kMoeDescriptorKindPAD_FILL;
    descriptor.bytes = 64;
    descriptor.dst_view_region = 1;
    descriptor.dst_view_ordinal = 1;
    graph.add(descriptor);
    MoeOverlayEntry command;
    command.kind = mesh_abi::kMeshObjectKindCOMMAND;
    command.region_id = 1;
    command.ordinal = 1;
    command.owner_core = 0;
    command.secondary_kind = mesh_abi::kOpcodeDMA_FILL;
    command.role = mesh_abi::kMoeCommandRolePAD_FILL;
    command.bytes = 64;
    command.ref_region = 1;
    command.ref_ordinal = 1;
    command.wait_count = 1;
    command.wait_refs[0][0] = 1;
    command.wait_refs[0][1] = mesh_abi::kMeshObjectKindEVENT;
    command.wait_refs[0][2] = 0;
    command.signal_count = 1;
    command.signal_refs[0][0] = 1;
    command.signal_refs[0][1] = mesh_abi::kMeshObjectKindEVENT;
    command.signal_refs[0][2] = 1;
    graph.add(command);
    MoeOverlayEntry event;
    event.kind = mesh_abi::kMeshObjectKindEVENT;
    event.region_id = 1;
    event.ordinal = 1;
    graph.add(event);
    MoeFillPayload payload;
    payload.region_id = 1;
    payload.ordinal = 1;
    payload.bytes = 64;
    graph.addPayload(payload);
    const std::vector<uint8_t> content(64, 0x5A);
    graph.addPayloadBytes(content.data(), content.size());
    return graph;
}

TEST(MoeOverlayRuntimeTest, RetriesBackpressuredDmaUntilTheEngineAccepts)
{
    const MoeOverlayGraph graph = fillOverlayGraph();
    MoeScratchInterval interval;
    interval.region_id = 1;
    interval.ordinal = 1;
    interval.offset = 0x1000;
    interval.bytes = 64;
    MoeOverlayGraph with_scratch = graph;
    with_scratch.addScratch(interval);
    ProgramScoreboard scoreboard;
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 16, /*alignment*/ 64,
                    /*line_bytes*/ 32, /*read_bytes_per_cycle*/ 32,
                    /*write_bytes_per_cycle*/ 32, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 1);
    MoeOverlayAddressSpace addresses;
    addresses.sram_region = 0;
    addresses.hbm_region = 0;
    addresses.max_burst_beats = 16;
    MoeOverlayExecutor::Config config;
    config.core_id = 0;
    MoeOverlayExecutor executor(config, &scoreboard, &sram, nullptr,
                                &addresses);
    executor.load(1, InstanceGeneration(1), with_scratch);
    StubOverlayDmaPort port;
    port.refusals = 2;
    executor.bindDma(&port);
    RuntimeObjectKey entry;
    entry.instance = InstanceGeneration(1);
    entry.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    entry.regionGroupId = 1;
    entry.regionId = 1;
    entry.kind = mesh_abi::MeshObjectKind::EVENT;
    entry.ordinal = 0;
    executor.publishEntry(entry, 0);
    for (uint32_t cycle = 0; cycle < 8 && port.accepted == 0; cycle++)
        executor.tick(cycle);
    EXPECT_EQ(port.submissions.size(), 3u);
    EXPECT_EQ(port.accepted, 1u);
    EXPECT_EQ(executor.completedCommands(), 0u);
    EXPECT_GT(port.issue_ticks[2], port.issue_ticks[0]);
    EXPECT_EQ(port.contents.count(port.submissions[0]), 1u);
    EXPECT_TRUE(port.installed[port.submissions[0]]);
    EXPECT_FALSE(executor.drained());
    executor.completeDma(port.submissions[2], 0);
    for (uint32_t cycle = 0; cycle < 4 && !executor.drained(); cycle++)
        executor.tick(cycle);
    EXPECT_TRUE(executor.drained());
    EXPECT_EQ(executor.completedCommands(), 1u);
    EXPECT_GT(executor.sramWriteBytes(), 0u);
}

class StubOverlayComputePort : public MoeOverlayComputePort
{
  public:
    uint64_t computeCycles(const MoeComputeShape &shape) const override
    {
        shapes.push_back(shape);
        return cycles;
    }
    bool weightSlotRange(uint32_t, uint64_t &, uint64_t &) const override
    {
        return false;
    }
    bool computeAdmissible(uint16_t) const override { return true; }
    void noteComputeAdmitted(uint16_t opcode) override
    {
        admitted.push_back(opcode);
    }
    void noteComputeFinished(uint16_t opcode, uint64_t charged_cycles) override
    {
        finished.push_back(opcode);
        charged.push_back(charged_cycles);
    }
    void noteWeightConsumed(uint32_t, uint32_t) override {}

    mutable std::vector<MoeComputeShape> shapes;
    std::vector<uint16_t> admitted;
    std::vector<uint16_t> finished;
    std::vector<uint64_t> charged;
    uint64_t cycles = 10;
};

MoeOverlayGraph combineOverlayGraph(uint32_t contributors)
{
    MoeOverlayGraph graph;
    MoeScratchInterval read_interval;
    read_interval.region_id = 1;
    read_interval.ordinal = 1;
    read_interval.offset = 0x100;
    read_interval.bytes = 16;
    graph.addScratch(read_interval);
    MoeScratchInterval write_interval;
    write_interval.region_id = 1;
    write_interval.ordinal = 2;
    write_interval.offset = 0x200;
    write_interval.bytes = 16;
    graph.addScratch(write_interval);
    MoeOverlayEntry read_view;
    read_view.kind = mesh_abi::kMeshObjectKindVIEW;
    read_view.region_id = 1;
    read_view.ordinal = 1;
    read_view.owner_core = 0;
    read_view.secondary_kind = mesh_abi::kMoeViewKindEXPERT_OUTPUT;
    read_view.access = mesh_abi::kMoeViewAccessREAD;
    read_view.bytes = 16;
    read_view.backing_kind = mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION;
    read_view.ref_region = 1;
    read_view.ref_ordinal = 1;
    graph.add(read_view);
    MoeOverlayEntry write_view;
    write_view.kind = mesh_abi::kMeshObjectKindVIEW;
    write_view.region_id = 1;
    write_view.ordinal = 2;
    write_view.owner_core = 0;
    write_view.secondary_kind = mesh_abi::kMoeViewKindMEMBER_OUTPUT;
    write_view.access = mesh_abi::kMoeViewAccessWRITE;
    write_view.bytes = 16;
    write_view.backing_kind = mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION;
    write_view.ref_region = 1;
    write_view.ref_ordinal = 2;
    graph.add(write_view);
    MoeOverlayEntry command;
    command.kind = mesh_abi::kMeshObjectKindCOMMAND;
    command.region_id = 1;
    command.ordinal = 1;
    command.owner_core = 0;
    command.secondary_kind = mesh_abi::kOpcodeLOCAL_REDUCE;
    command.role = contributors == 1 ? mesh_abi::kMoeCommandRoleCOPY_THROUGH
                                     : mesh_abi::kMoeCommandRoleLOCAL_REDUCE;
    command.bytes = 16;
    command.wait_count = 1 + contributors;
    command.wait_refs[0][0] = 1;
    command.wait_refs[0][1] = mesh_abi::kMeshObjectKindEVENT;
    command.wait_refs[0][2] = 0;
    for (uint32_t index = 0; index < contributors; index++) {
        command.wait_refs[index + 1][0] = 1;
        command.wait_refs[index + 1][1] = mesh_abi::kMeshObjectKindEVENT;
        command.wait_refs[index + 1][2] = 10 + index;
    }
    command.signal_count = 1;
    command.signal_refs[0][0] = 1;
    command.signal_refs[0][1] = mesh_abi::kMeshObjectKindEVENT;
    command.signal_refs[0][2] = 1;
    command.view_count = 2;
    command.view_refs[0][0] = 1;
    command.view_refs[0][1] = mesh_abi::kMeshObjectKindVIEW;
    command.view_refs[0][2] = 1;
    command.view_refs[1][0] = 1;
    command.view_refs[1][1] = mesh_abi::kMeshObjectKindVIEW;
    command.view_refs[1][2] = 2;
    graph.add(command);
    for (uint32_t index = 0; index < contributors; index++) {
        MoeOverlayEntry event;
        event.kind = mesh_abi::kMeshObjectKindEVENT;
        event.region_id = 1;
        event.ordinal = 10 + index;
        graph.add(event);
    }
    MoeOverlayEntry result;
    result.kind = mesh_abi::kMeshObjectKindEVENT;
    result.region_id = 1;
    result.ordinal = 1;
    graph.add(result);
    return graph;
}

uint64_t runCombineStages(const MoeOverlayGraph &graph, uint32_t contributors,
                          StubOverlayComputePort &port,
                          MoeOverlayExecutor &executor)
{
    RuntimeObjectKey entry;
    entry.instance = InstanceGeneration(1);
    entry.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    entry.regionGroupId = 1;
    entry.regionId = 1;
    entry.kind = mesh_abi::MeshObjectKind::EVENT;
    entry.ordinal = 0;
    executor.publishEntry(entry, 1000);
    for (uint32_t index = 0; index < contributors; index++) {
        RuntimeObjectKey contributor = entry;
        contributor.ordinal = 10 + index;
        executor.deliverEvent(contributor);
    }
    uint64_t completed_at = 0;
    for (uint64_t tick = 1001; tick < 1200; tick++) {
        executor.tick(tick);
        if (executor.completedCommands() != 0) {
            completed_at = tick;
            break;
        }
    }
    return completed_at;
}

TEST(MoeOverlayRuntimeTest, ComputeStagesGateCompletionOnAbsoluteTicks)
{
    const MoeOverlayGraph graph = combineOverlayGraph(1);
    ProgramScoreboard scoreboard;
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 1, /*alignment*/ 8,
                    /*line_bytes*/ 8, /*read_bytes_per_cycle*/ 8,
                    /*write_bytes_per_cycle*/ 8, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 1);
    MoeOverlayExecutor::Config config;
    config.core_id = 0;
    config.ticks_per_cycle = 1;
    MoeOverlayExecutor executor(config, &scoreboard, &sram, nullptr, nullptr);
    executor.load(1, InstanceGeneration(1), graph);
    StubOverlayComputePort port;
    port.cycles = 10;
    std::vector<ComputeDigest> digests;
    executor.bindCompute(&port);
    executor.bindComputeCommit(&digests);
    const uint8_t source[16] = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                                0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E,
                                0x0F, 0x10};
    ASSERT_TRUE(sram.write(0x100, sizeof(source), source));
    const uint64_t completed_at = runCombineStages(graph, 1, port, executor);
    // Reads stall two ticks, the engine window is ten cycles and the
    // two-line output write stalls two more: the write tick retires it.
    EXPECT_EQ(completed_at, 1016u);
    ASSERT_EQ(port.shapes.size(), 1u);
    EXPECT_EQ(port.shapes[0].role, mesh_abi::kMoeCommandRoleCOPY_THROUGH);
    EXPECT_EQ(port.shapes[0].fan_in, 1u);
    EXPECT_EQ(port.shapes[0].payload_bytes, 16u);
    EXPECT_EQ(executor.sramReadBytes(), 16u);
    EXPECT_EQ(executor.sramWriteBytes(), 16u);
    EXPECT_EQ(executor.computeCommits(), 1u);
    EXPECT_EQ(executor.uncommittedViews(), 0u);
    ASSERT_EQ(digests.size(), 1u);
    EXPECT_EQ(digests[0].offset, 0x200u);
    uint8_t result[16] = {};
    ASSERT_TRUE(sram.read(0x200, sizeof(result), result));
    EXPECT_TRUE(std::equal(source, source + 16, result));
    uint8_t commit_snapshot[16];
    std::copy(result, result + 16, commit_snapshot);
    executor.tick(1100);
    uint8_t after[16] = {};
    ASSERT_TRUE(sram.read(0x200, sizeof(after), after));
    EXPECT_TRUE(std::equal(commit_snapshot, commit_snapshot + 16, after));
}

TEST(MoeOverlayRuntimeTest, ComputeEngineWindowScalesWithKernelCycles)
{
    const MoeOverlayGraph graph = combineOverlayGraph(2);
    ProgramScoreboard scoreboard;
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 1, /*alignment*/ 8,
                    /*line_bytes*/ 8, /*read_bytes_per_cycle*/ 8,
                    /*write_bytes_per_cycle*/ 8, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 1);
    MoeOverlayExecutor::Config config;
    config.core_id = 0;
    config.ticks_per_cycle = 1;
    MoeOverlayExecutor executor(config, &scoreboard, &sram, nullptr, nullptr);
    executor.load(1, InstanceGeneration(1), graph);
    StubOverlayComputePort port;
    port.cycles = 30;
    std::vector<ComputeDigest> digests;
    executor.bindCompute(&port);
    executor.bindComputeCommit(&digests);
    const uint64_t completed_at = runCombineStages(graph, 2, port, executor);
    EXPECT_EQ(completed_at, 1036u);
    ASSERT_EQ(port.shapes.size(), 1u);
    EXPECT_EQ(port.shapes[0].role, mesh_abi::kMoeCommandRoleLOCAL_REDUCE);
    EXPECT_EQ(port.shapes[0].fan_in, 2u);
    ASSERT_EQ(port.charged.size(), 1u);
    EXPECT_EQ(port.charged[0], 30u);
    EXPECT_EQ(executor.computeCyclesTotal(), 30u);
    EXPECT_EQ(executor.localReduceCommands(), 1u);
}


}
}
}
