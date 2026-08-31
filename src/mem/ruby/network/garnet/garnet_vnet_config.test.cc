#include <gtest/gtest.h>

#include <climits>
#include <string>
#include <vector>

#include "mem/ruby/network/garnet/VnetConfig.hh"

namespace gem5
{
namespace ruby
{
namespace garnet
{

namespace
{

VnetConfigInput
legacyInput()
{
    VnetConfigInput input;
    input.virtualNetworks = 3;
    input.vcsPerVnet = 4;
    input.niFlitSize = 16;
    input.legacyCtrlDepth = 1;
    input.legacyDataDepth = 4;
    input.legacyTypeNames = {"request", "response", "response"};
    return input;
}

std::string
normalize(const VnetConfigInput &input, NormalizedVnetConfig *output=nullptr)
{
    NormalizedVnetConfig local;
    return normalizeVnetConfig(input, output ? *output : local);
}

} // anonymous namespace

TEST(GarnetVnetConfigTest, PreservesLegacyFallback)
{
    NormalizedVnetConfig output;
    EXPECT_TRUE(normalize(legacyInput(), &output).empty());
    EXPECT_EQ(output.types,
              (std::vector<VNET_type>{CTRL_VNET_, DATA_VNET_, DATA_VNET_}));
    EXPECT_EQ(output.depths, (std::vector<uint32_t>{1, 4, 4}));
    EXPECT_EQ(output.legacyCtrlDepth, 1);
    EXPECT_EQ(output.legacyDataDepth, 4);
}

TEST(GarnetVnetConfigTest, MapsPerVnetDepth)
{
    auto input = legacyInput();
    input.virtualNetworks = 5;
    input.legacyTypeNames = {"request", "response", "request",
                             "request", "response"};
    input.configuredDepths = {2, 3, 4, 5, 6};
    NormalizedVnetConfig output;
    EXPECT_TRUE(normalize(input, &output).empty());
    EXPECT_EQ(output.depths,
              (std::vector<uint32_t>{2, 3, 4, 5, 6}));
}

TEST(GarnetVnetConfigTest, MapsVnetClass)
{
    auto input = legacyInput();
    input.legacyTypeNames = {"response", "response", "response"};
    input.configuredClasses = {"ctrl", "data", "ctrl"};
    NormalizedVnetConfig output;
    EXPECT_TRUE(normalize(input, &output).empty());
    EXPECT_EQ(output.types,
              (std::vector<VNET_type>{CTRL_VNET_, DATA_VNET_, CTRL_VNET_}));
    EXPECT_EQ(output.depths, (std::vector<uint32_t>{1, 4, 1}));
}

TEST(GarnetVnetConfigTest, RejectsInvalidVectors)
{
    auto input = legacyInput();
    input.configuredDepths = {1, 2};
    EXPECT_NE(normalize(input).find(
                  "buffers_per_vnet length 2 must equal "
                  "number_of_virtual_networks 3"), std::string::npos);

    input = legacyInput();
    input.configuredDepths = {1, 0, 2};
    EXPECT_NE(normalize(input).find(
                  "buffers_per_vnet[1] must be >= 1"), std::string::npos);

    input = legacyInput();
    input.configuredClasses = {"ctrl", "data"};
    EXPECT_NE(normalize(input).find(
                  "vnet_classes length 2 must equal "
                  "number_of_virtual_networks 3"), std::string::npos);

    input = legacyInput();
    input.configuredClasses = {"ctrl", "payload", "data"};
    EXPECT_NE(normalize(input).find(
                  "vnet_classes[1]='payload' is invalid; expected "
                  "'ctrl' or 'data'"), std::string::npos);

    input = legacyInput();
    input.virtualNetworks = 0;
    EXPECT_NE(normalize(input).find(
                  "number_of_virtual_networks must be >= 1"),
              std::string::npos);

    input = legacyInput();
    input.vcsPerVnet = 0;
    EXPECT_NE(normalize(input).find("vcs_per_vnet must be >= 1"),
              std::string::npos);

    input = legacyInput();
    input.niFlitSize = 0;
    EXPECT_NE(normalize(input).find("ni_flit_size must be >= 1"),
              std::string::npos);

    input = legacyInput();
    input.virtualNetworks = INT_MAX;
    input.vcsPerVnet = 2;
    EXPECT_NE(normalize(input).find(
                  "number_of_virtual_networks * vcs_per_vnet overflows int"),
              std::string::npos);
}

TEST(GarnetVnetConfigTest, AcceptsFaultModelCompatibleDepths)
{
    auto input = legacyInput();
    input.virtualNetworks = 5;
    input.configuredClasses = {"ctrl", "data", "ctrl", "ctrl", "data"};
    input.configuredDepths = {2, 3, 2, 2, 3};
    input.faultModelEnabled = true;
    NormalizedVnetConfig output;
    EXPECT_TRUE(normalize(input, &output).empty());
    EXPECT_EQ(output.legacyCtrlDepth, 2);
    EXPECT_EQ(output.legacyDataDepth, 3);
}

TEST(GarnetVnetConfigTest, RejectsFaultModelIncompatibleDepths)
{
    auto input = legacyInput();
    input.virtualNetworks = 5;
    input.configuredClasses = {"ctrl", "data", "ctrl", "ctrl", "data"};
    input.configuredDepths = {2, 3, 4, 2, 3};
    input.faultModelEnabled = true;
    EXPECT_NE(normalize(input).find(
                  "FaultModel requires one uniform ctrl vnet depth"),
              std::string::npos);
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
