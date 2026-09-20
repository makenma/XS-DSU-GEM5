#include <gtest/gtest.h>

#include <cstddef>
#include <memory>
#include <vector>

#include "dev/ai_mesh/generated/golden_mshb.inc"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_invocation_binding.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_splitter.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

RuntimeArch
testArch()
{
    RuntimeArch arch;
    arch.arch_digest_hex = kGoldenArchDigest;
    arch.core_ids = {0, 1};
    arch.sram_bytes = 2097152;
    arch.sram_banks = 16;
    arch.sram_alignment = 64;
    arch.axi_data_bytes = 32;
    arch.axi_max_burst_beats = 16;
    arch.axi_address_bits = 64;
    arch.regions = {
        {0, 0x800000000, 0x400000000, 0, 0,
         RuntimeArch::Region::kKindHbm, false},
        {1, 0x100000000, 0x10000000, 0, 0,
         RuntimeArch::Region::kKindHostShared, false},
        {2, 0x400000000, 0x800000, 0x400000, 0x200000,
         RuntimeArch::Region::kKindSramAperture, true},
    };
    return arch;
}

std::shared_ptr<DecodedProgram>
decodeGolden()
{
    auto program = std::make_shared<DecodedProgram>();
    MeshBytes image(kGoldenMshbSingle,
                    kGoldenMshbSingle + sizeof(kGoldenMshbSingle));
    MeshLoadError error;
    EXPECT_TRUE(decodeMeshBinary(image, *program, error))
        << error.code << ": " << error.message;
    return program;
}

std::vector<DispatchBinding>
referenceDispatch(const MeshProgramAdmission &admission, uint64_t variant_id)
{
    const auto *variant = admission.context().variants().at(variant_id);
    std::vector<DispatchBinding> result;
    for (const auto &[slot_id, slot] : admission.context().bindingSlots()) {
        if (admission.context().owner("binding_slots", slot_id) != variant)
            continue;
        const auto &reference = admission.program()
                                    .semantic_tables.binding_rows
                                        [slot->reference_binding.row_id - 1];
        DispatchBinding binding;
        binding.slot_id = uint32_t(reference.slot_id);
        binding.region_id = uint32_t(reference.region_id);
        binding.owner_core = uint16_t(reference.owner_core);
        binding.allocation_offset_bytes = reference.allocation_offset_bytes;
        binding.allocation_size_bytes = reference.allocation_size_bytes;
        binding.allocation_alignment_bytes =
            uint32_t(reference.allocation_alignment_bytes);
        binding.access = uint32_t(reference.access);
        result.push_back(binding);
    }
    return result;
}

struct Fixture
{
    std::shared_ptr<DecodedProgram> program = decodeGolden();
    RuntimeArch arch = testArch();
    MeshProgramAdmission admission;
    MeshLoadError error;

    bool admit() { return admitProgram(program, arch, admission, error); }
    std::vector<DispatchBinding> reference() const
    {
        return referenceDispatch(admission, 1);
    }
};

TEST(MeshInvocationBindingTest, ReferenceBindingsResolveEveryEndpoint)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    MeshInvocationBinding binding;
    std::vector<DispatchBinding> dispatch = fixture.reference();
    ASSERT_FALSE(dispatch.empty());
    ASSERT_TRUE(resolveInvocationBinding(fixture.admission, 1, dispatch, binding,
                                         fixture.error))
        << fixture.error.code << ": " << fixture.error.message;
    EXPECT_EQ(binding.variantId(), 1u);
    EXPECT_EQ(binding.bindings().size(), dispatch.size());
    for (const auto &descriptor :
         fixture.admission.program().transport.dma_descriptors) {
        ASSERT_TRUE(binding.hasEndpoint(descriptor.descriptor_id));
        EXPECT_EQ(binding.endpointAddress(descriptor.descriptor_id, true),
                  fixture.admission.dma().sourceAddress(
                      descriptor.descriptor_id));
        EXPECT_EQ(binding.endpointAddress(descriptor.descriptor_id, false),
                  fixture.admission.dma().destinationAddress(
                      descriptor.descriptor_id));
    }
}

TEST(MeshInvocationBindingTest, EmptyDispatchUsesReferenceBindings)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    MeshInvocationBinding binding;
    ASSERT_TRUE(resolveInvocationBinding(fixture.admission, 1, {}, binding,
                                         fixture.error))
        << fixture.error.code << ": " << fixture.error.message;
    for (const auto &descriptor :
         fixture.admission.program().transport.dma_descriptors)
        EXPECT_EQ(binding.endpointAddress(descriptor.descriptor_id, false),
                  fixture.admission.dma().destinationAddress(
                      descriptor.descriptor_id));
}

TEST(MeshInvocationBindingTest, RejectsUnknownVariant)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    MeshInvocationBinding binding;
    EXPECT_FALSE(resolveInvocationBinding(fixture.admission, 4242, {},
                                          binding, fixture.error));
    EXPECT_EQ(fixture.error.code, "E_RELOCATION");
}

TEST(MeshInvocationBindingTest, RejectsIncompleteSlotCoverage)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    std::vector<DispatchBinding> dispatch = fixture.reference();
    ASSERT_GT(dispatch.size(), 1u);
    dispatch.pop_back();
    MeshInvocationBinding binding;
    EXPECT_FALSE(resolveInvocationBinding(fixture.admission, 1, dispatch,
                                          binding, fixture.error));
    EXPECT_EQ(fixture.error.code, "E_RELOCATION");
}

TEST(MeshInvocationBindingTest, RejectsExtraBinding)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    std::vector<DispatchBinding> dispatch = fixture.reference();
    DispatchBinding extra = dispatch.front();
    extra.slot_id = 4096;
    dispatch.push_back(extra);
    MeshInvocationBinding binding;
    EXPECT_FALSE(resolveInvocationBinding(fixture.admission, 1, dispatch,
                                          binding, fixture.error));
    EXPECT_EQ(fixture.error.code, "E_RELOCATION");
}

TEST(MeshInvocationBindingTest, RejectsSlotConstraintOverride)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    std::vector<DispatchBinding> dispatch = fixture.reference();
    dispatch.front().region_id = 2;
    MeshInvocationBinding binding;
    EXPECT_FALSE(resolveInvocationBinding(fixture.admission, 1, dispatch,
                                          binding, fixture.error));
    EXPECT_EQ(fixture.error.code, "E_RELOCATION");
}

TEST(MeshInvocationBindingTest, RejectsMisalignedBinding)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    std::vector<DispatchBinding> dispatch = fixture.reference();
    dispatch.front().allocation_alignment_bytes = 48;
    MeshInvocationBinding binding;
    EXPECT_FALSE(resolveInvocationBinding(fixture.admission, 1, dispatch,
                                          binding, fixture.error));
    EXPECT_EQ(fixture.error.code, "E_RELOCATION");
}

TEST(MeshInvocationBindingTest, RejectsBindingBeyondItsRegion)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    std::vector<DispatchBinding> dispatch = fixture.reference();
    const RuntimeArch::Region *region =
        fixture.arch.region(uint16_t(dispatch.front().region_id));
    ASSERT_NE(region, nullptr);
    dispatch.front().allocation_offset_bytes = region->bytes;
    MeshInvocationBinding binding;
    EXPECT_FALSE(resolveInvocationBinding(fixture.admission, 1, dispatch,
                                          binding, fixture.error));
    EXPECT_EQ(fixture.error.code, "E_RELOCATION");
}

TEST(MeshInvocationBindingTest, ShiftedBindingMovesOnlyItsEndpoints)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    std::vector<DispatchBinding> dispatch = fixture.reference();
    ASSERT_FALSE(dispatch.empty());
    const DispatchBinding &first = dispatch.front();
    const uint64_t delta = first.allocation_alignment_bytes;
    dispatch.front().allocation_offset_bytes += delta;
    MeshInvocationBinding binding;
    ASSERT_TRUE(resolveInvocationBinding(fixture.admission, 1, dispatch, binding,
                                         fixture.error))
        << fixture.error.code << ": " << fixture.error.message;
    EXPECT_EQ(binding.binding(first.slot_id)->allocation_address,
              first.allocation_offset_bytes + fixture.arch.regions[first.region_id].base);
}

TEST(MeshInvocationBindingTest, MatchesPythonResolvedAddresses)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    std::vector<DispatchBinding> dispatch = fixture.reference();
    for (auto &entry : dispatch)
        if (entry.access == mesh_abi::kAccessKindREAD_WRITE)
            entry.allocation_offset_bytes += 0x1000;
    MeshInvocationBinding binding;
    ASSERT_TRUE(resolveInvocationBinding(fixture.admission, 1, dispatch, binding,
                                         fixture.error))
        << fixture.error.code << ": " << fixture.error.message;
    // Golden vectors from Python mesh_ir resolve_addresses for the same
    // program, arch and shifted dispatch bindings.
    EXPECT_EQ(binding.endpointAddress(1, true), 0x800100000ull);
    EXPECT_EQ(binding.endpointAddress(1, false), 0x400000000ull);
    EXPECT_EQ(binding.endpointAddress(2, true), 0x800200000ull);
    EXPECT_EQ(binding.endpointAddress(2, false), 0x400002000ull);
    EXPECT_EQ(binding.endpointAddress(3, true), 0x400004000ull);
    EXPECT_EQ(binding.endpointAddress(3, false), 0x800301000ull);
}

TEST(MeshInvocationBindingTest, DispatchBindingDrivesTheFourKiBBurstSplit)
{
    Fixture fixture;
    ASSERT_TRUE(fixture.admit()) << fixture.error.code;
    std::vector<DispatchBinding> shifted = fixture.reference();
    for (auto &entry : shifted)
        if (entry.access == mesh_abi::kAccessKindREAD_WRITE)
            entry.allocation_offset_bytes += 0x100;
    MeshInvocationBinding binding;
    ASSERT_TRUE(resolveInvocationBinding(fixture.admission, 1, shifted, binding,
                                         fixture.error))
        << fixture.error.code << ": " << fixture.error.message;
    const uint64_t reference_address =
        fixture.admission.dma().destinationAddress(3);
    const uint64_t shifted_address = binding.endpointAddress(3, false);
    EXPECT_NE(reference_address, shifted_address);
    const auto reference_bursts = splitBursts(reference_address, 8192, 32, 16);
    const auto shifted_bursts = splitBursts(shifted_address, 8192, 32, 16);
    EXPECT_EQ(reference_bursts.size(), 16u);
    EXPECT_EQ(shifted_bursts.size(), 17u);
}

}
}
}
