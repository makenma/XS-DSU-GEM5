/*
 * Copyright (c) 2026 The gem5 AI Mesh project
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#include <memory>
#include <vector>

#include <gtest/gtest.h>

#include "mem/ruby/network/garnet/DualLane.hh"
#include "mem/ruby/network/garnet/VirtualChannel.hh"
#include "mem/ruby/network/garnet/flit.hh"

// Link-time stubs for the protocol-generated MachineType helpers that
// NetDest storage sizing calls; declared earlier than NetDest.cc on the
// link line so archive member ordering resolves them.
namespace gem5
{

namespace ruby
{

int MachineType_base_level(const MachineType &obj)
{
    return static_cast<int>(obj);
}
MachineType MachineType_from_base_level(int level)
{
    return static_cast<MachineType>(level);
}
int MachineType_base_number(const MachineType &) { return MachineType_NUM; }
int MachineType_base_count(const MachineType &) { return MachineType_NUM; }
MachineType &operator++(MachineType &obj)
{
    obj = MachineType_from_base_level(MachineType_base_level(obj) + 1);
    return obj;
}

} // namespace ruby

namespace ruby
{

namespace garnet
{

namespace
{

constexpr uint32_t kVcsPerVnet = 2;
constexpr uint32_t kNumVnets = 5;
constexpr uint32_t kDepth = 4;

flit *
makeFlit(uint32_t vnet, uint32_t offset, bool singleFlit)
{
    RouteInfo route;
    const int vc = vnet * kVcsPerVnet + offset;
    // size == 1 yields a HEAD_TAIL flit; a later index of a multi-flit
    // packet yields BODY (the dual-lane transport config keeps every
    // packet single-flit, multi-flit shapes exercise VC state here).
    return new flit(0, singleFlit ? 0 : 1, vc, vnet, route,
                    singleFlit ? 1 : 3, nullptr, 32, 32, 0);
}

/** One lane's input FIFO group: real VirtualChannel objects, one group
 *  per vnet, exactly what InputUnit owns below the selector. */
struct TestLane
{
    std::vector<VirtualChannel> channels;

    TestLane() : channels(kNumVnets * kVcsPerVnet, VirtualChannel(kDepth)) {}

    uint32_t
    vnetOccupancy(uint32_t vnet) const
    {
        uint32_t total = 0;
        for (uint32_t offset = 0; offset < kVcsPerVnet; ++offset)
            total += channels[vnet * kVcsPerVnet + offset].getOccupancy();
        return total;
    }

    VirtualChannel &
    channel(uint32_t vnet, uint32_t offset)
    {
        return channels[vnet * kVcsPerVnet + offset];
    }

    void
    insertHead(uint32_t vnet, uint32_t offset)
    {
        channel(vnet, offset).insertFlit(makeFlit(vnet, offset, true));
    }

    void
    insertBody(uint32_t vnet, uint32_t offset)
    {
        channel(vnet, offset).insertFlit(makeFlit(vnet, offset, false));
    }
};

struct DualLaneFixture
{
    TestLane lane[2];

    DualLaneDecision
    decide(uint32_t vnet) const
    {
        return dualLaneSelect(lane[0].vnetOccupancy(vnet),
                              lane[1].vnetOccupancy(vnet));
    }
};

} // anonymous namespace

TEST(DualLaneSelectorTest, SmallerCountWins)
{
    DualLaneFixture f;
    f.lane[0].insertHead(1, 0);
    f.lane[0].insertHead(1, 1);
    for (int i = 0; i < 5; i++)
        f.lane[1].insertBody(1, i % kVcsPerVnet);

    EXPECT_EQ(f.decide(1).selected, 0);
    EXPECT_EQ(f.decide(1).count0, 2);
    EXPECT_EQ(f.decide(1).count1, 5);
}

TEST(DualLaneSelectorTest, GreaterCountLoses)
{
    DualLaneFixture f;
    for (int i = 0; i < 5; i++)
        f.lane[0].insertBody(1, i % kVcsPerVnet);
    f.lane[1].insertHead(1, 0);
    f.lane[1].insertHead(1, 1);

    EXPECT_EQ(f.decide(1).selected, 1);
}

TEST(DualLaneSelectorTest, TieAlwaysTakesLane0)
{
    DualLaneFixture f;
    f.lane[0].insertHead(2, 0);
    f.lane[0].insertHead(2, 1);
    f.lane[1].insertHead(2, 0);
    f.lane[1].insertHead(2, 1);

    EXPECT_EQ(f.decide(2).selected, 0);
}

TEST(DualLaneSelectorTest, ContinuouslyIdleInjectsLane0)
{
    DualLaneFixture f;
    for (int attempt = 0; attempt < 100; ++attempt)
        EXPECT_EQ(f.decide(3).selected, 0);
}

TEST(DualLaneSelectorTest, CountsSumOverVcGroup)
{
    DualLaneFixture f;
    f.lane[0].insertHead(4, 0);
    f.lane[0].insertHead(4, 1);
    f.lane[0].insertHead(4, 1);

    EXPECT_EQ(f.lane[0].vnetOccupancy(4), 3);
}

TEST(DualLaneSelectorTest, OtherVnetsAndInputsAreIsolated)
{
    DualLaneFixture f;
    // Unrelated traffic on other vnets of both lanes must not influence
    // this input/vnet's decision.
    for (int i = 0; i < kDepth; i++) {
        f.lane[0].insertBody(0, 0);
        f.lane[1].insertBody(2, 0);
    }
    DualLaneFixture otherInput;
    for (int i = 0; i < 3; i++)
        otherInput.lane[0].insertBody(1, 0);

    EXPECT_EQ(f.decide(1).count0, 0);
    EXPECT_EQ(f.decide(1).count1, 0);
    EXPECT_EQ(f.decide(1).selected, 0);
    EXPECT_EQ(otherInput.decide(1).count0, 3);
    EXPECT_EQ(otherInput.decide(1).selected, 1);
}

TEST(DualLaneSelectorTest, SelectedLaneBackpressuresWithoutDiversion)
{
    DualLaneFixture f;
    // Lane 0 wins by count (4 < 5) while its VC is exactly full: the flit
    // stays in the upstream finite queue and is retried; even though lane
    // 1's second VC still has room, the selector never diverts.
    for (int i = 0; i < kDepth; i++) {
        f.lane[0].insertBody(1, 0);
        f.lane[1].insertBody(1, 0);
    }
    f.lane[1].insertBody(1, 1);

    const DualLaneDecision decision = f.decide(1);
    EXPECT_EQ(decision.selected, 0);
    EXPECT_EQ(decision.count0, 4);
    EXPECT_EQ(decision.count1, 5);
    EXPECT_TRUE(f.lane[0].channel(1, 0).isFull());
    EXPECT_FALSE(f.lane[1].channel(1, 1).isFull());
}

TEST(DualLaneSelectorTest, VcStateAndCapacityGateAcceptance)
{
    VirtualChannel channel(kDepth);
    EXPECT_EQ(channel.get_state(), IDLE_);
    EXPECT_FALSE(channel.isFull());

    channel.set_active(0);
    EXPECT_EQ(channel.get_state(), ACTIVE_);

    for (int i = 0; i < static_cast<int>(kDepth); i++)
        channel.insertFlit(makeFlit(1, 0, false));
    EXPECT_TRUE(channel.isFull());
    EXPECT_EQ(channel.getOccupancy(), kDepth);
}

TEST(DualLaneSelectorTest, PortNameIdentityRoundTrip)
{
    const PortIdentity east = parsePortName("East");
    EXPECT_EQ(east.logical, "East");
    EXPECT_EQ(east.lane, 0);

    const PortIdentity ext = parsePortName("West_ext");
    EXPECT_EQ(ext.logical, "West");
    EXPECT_EQ(ext.lane, 1);

    EXPECT_EQ(lanePortName("North", 0), "North");
    EXPECT_EQ(lanePortName("North", 1), "North_ext");
    EXPECT_EQ(lanePortName("Local", 0), "Local");
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
