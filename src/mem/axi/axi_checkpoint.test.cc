#include <gtest/gtest.h>

#include <sstream>
#include <boost/archive/binary_iarchive.hpp>
#include <boost/archive/binary_oarchive.hpp>

#include "mem/axi/axi_initiator_adapter.hh"
#include "mem/axi/axi_target_adapter.hh"

using namespace gem5::axi;

TEST(AxiCheckpoint, RestoresMemoryOrderingAndSyntheticServiceHistory)
{
    AxiInitiatorConfig source_config;
    source_config.source = {3, 2};
    source_config.dataBusBytes = 8;
    source_config.ranges = {{0, 0x10000, 1}};
    source_config.defaultErrorTarget = 1;
    source_config.targetQuotas[1] = {8, 64, 8, 64};
    AxiTargetConfig target_config;
    target_config.dstNode = 1;
    target_config.dataBusBytes = 8;
    target_config.memoryRanges = source_config.ranges;
    target_config.sourceQuotas[source_config.source] = {8, 64, 8, 64};
    target_config.syntheticHbmEnabled = true;
    AxiInitiatorState source(source_config), restored_source(source_config);
    AxiTargetState target(target_config), restored_target(target_config);
    const auto transfer = [&](AxiInitiatorState &initiator, AxiTargetState &receiver,
                              uint64_t start, uint8_t byte) {
        AxiAddressRequest request;
        request.axiId = 2;
        request.address = 0xff0;
        request.size = 3;
        request.beatCount = 2;
        EXPECT_TRUE(initiator.tryAcceptAw(request, start));
        AxiWBeat beat;
        beat.byteStrobe = 0xff;
        beat.functionalData.assign(8, byte);
        EXPECT_TRUE(initiator.tryAcceptW(beat, start));
        beat.last = true;
        EXPECT_TRUE(initiator.tryAcceptW(beat, start));
        const auto packet = initiator.frontAwPacket();
        receiver.acceptAw(packet, start);
        initiator.popAwPacket();
        for (unsigned i = 0; i < 2; ++i) {
            receiver.acceptW(initiator.frontWPacket(), start);
            initiator.popWPacket();
        }
        uint64_t tick = start;
        while (!receiver.hasBPacket() && tick < start + 100)
            receiver.advance(++tick);
        EXPECT_TRUE(receiver.hasBPacket());
        if (!receiver.hasBPacket())
            return tick;
        initiator.acceptBPacket(receiver.frontBPacket(), tick);
        receiver.popBPacket();
        AxiBBeat response;
        EXPECT_TRUE(initiator.tryConsumeB(response));
        EXPECT_EQ(response.resp, AxiResp::Okay);
        EXPECT_TRUE(initiator.tryAcceptAr(request, tick));
        receiver.acceptAr(initiator.frontArPacket(), tick);
        initiator.popArPacket();
        for (unsigned i = 0; i < 2; ++i) {
            while (!receiver.hasRPacket() && tick < start + 200)
                receiver.advance(++tick);
            EXPECT_TRUE(receiver.hasRPacket());
            if (!receiver.hasRPacket())
                return tick;
            initiator.acceptRPacket(receiver.frontRPacket(), tick);
            receiver.popRPacket();
            AxiRBeat data;
            EXPECT_TRUE(initiator.tryConsumeR(data, tick));
            EXPECT_EQ(data.functionalData, std::vector<uint8_t>(8, byte));
        }
        return tick;
    };
    transfer(source, target, 10, 0x65);
    target.memory().writeByte(0x1007, 0x65);
    std::stringstream checkpoint;
    {
        boost::archive::binary_oarchive archive(checkpoint);
        archive << source << target;
    }
    {
        boost::archive::binary_iarchive archive(checkpoint);
        archive >> restored_source >> restored_target;
    }
    EXPECT_EQ(restored_target.memory().readByte(0xff8), 0x65);
    EXPECT_EQ(restored_target.memory().readByte(0x1007), 0x65);
    EXPECT_EQ(restored_target.memory().readByte(0x1008), 0);
    EXPECT_EQ(transfer(source, target, 1000, 0xa3),
              transfer(restored_source, restored_target, 1000, 0xa3));
    std::stringstream original, restored;
    { boost::archive::binary_oarchive archive(original); archive << source << target; }
    { boost::archive::binary_oarchive archive(restored); archive << restored_source << restored_target; }
    EXPECT_EQ(original.str(), restored.str());
}

TEST(AxiCheckpoint, RejectsOutstandingTransactions)
{
    AxiInitiatorConfig config;
    config.ranges = {{0, 0x1000, 1}};
    config.defaultErrorTarget = 1;
    config.targetQuotas[1] = {8, 64, 8, 64};
    AxiInitiatorState source(config);
    AxiAddressRequest request;
    request.address = 0x100;
    request.beatCount = 1;
    request.size = 3;
    ASSERT_TRUE(source.tryAcceptAr(request));
    std::stringstream checkpoint;
    boost::archive::binary_oarchive archive(checkpoint);
    EXPECT_THROW(archive << source, std::runtime_error);
}
