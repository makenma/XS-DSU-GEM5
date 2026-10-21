#include <sstream>

#include <boost/archive/text_iarchive.hpp>
#include <boost/archive/text_oarchive.hpp>
#include <gtest/gtest.h>

#include "dev/ai_mesh/dma_records.hh"

using namespace gem5::ai_mesh;

TEST(DmaRecordsTest, StatusNamesRetainBothExecutionContracts)
{
    EXPECT_STREQ(dmaStatusName(DmaStatus::OK), "OK");
    EXPECT_STREQ(dmaStatusName(DmaStatus::AXI_READ_ERROR), "AXI_READ_ERROR");
    EXPECT_STREQ(dmaStatusName(DmaStatus::AXI_WRITE_ERROR), "AXI_WRITE_ERROR");
    EXPECT_STREQ(dmaStatusName(DmaStatus::SUPPRESSED), "SUPPRESSED");
    EXPECT_EQ(static_cast<unsigned>(DmaStatus::SUPPRESSED), 3u);
}

TEST(DmaRecordsTest, CheckpointRetainsTrafficAndCommittedSegments)
{
    ActualTraffic source;
    source.read_bytes = 128;
    source.write_bytes = 64;
    source.p2p_bytes = 32;
    source.fill_bytes = 16;
    source.read_bursts = 2;
    source.write_bursts = 3;
    source.p2p_bursts = 4;
    source.read_discarded_bytes = 8;
    source.write_drained_uncommitted_bytes = 12;
    source.error_code = 2;
    source.dma_kind = 3;
    source.payload_digest = "physical-payload";
    source.committed_segments = {{1, 4, 8}, {3, 0, 16}};
    std::stringstream bytes;
    {
        boost::archive::text_oarchive archive(bytes);
        archive << source;
    }
    ActualTraffic restored;
    {
        boost::archive::text_iarchive archive(bytes);
        archive >> restored;
    }
    EXPECT_EQ(restored.read_bytes, source.read_bytes);
    EXPECT_EQ(restored.write_bytes, source.write_bytes);
    EXPECT_EQ(restored.p2p_bytes, source.p2p_bytes);
    EXPECT_EQ(restored.fill_bytes, source.fill_bytes);
    EXPECT_EQ(restored.read_bursts, source.read_bursts);
    EXPECT_EQ(restored.write_bursts, source.write_bursts);
    EXPECT_EQ(restored.p2p_bursts, source.p2p_bursts);
    EXPECT_EQ(restored.read_discarded_bytes, source.read_discarded_bytes);
    EXPECT_EQ(restored.write_drained_uncommitted_bytes,
              source.write_drained_uncommitted_bytes);
    EXPECT_EQ(restored.error_code, source.error_code);
    EXPECT_EQ(restored.dma_kind, source.dma_kind);
    EXPECT_EQ(restored.payload_digest, source.payload_digest);
    ASSERT_EQ(restored.committed_segments.size(), 2u);
    for (size_t index = 0; index < source.committed_segments.size(); ++index) {
        EXPECT_EQ(restored.committed_segments[index].row,
                  source.committed_segments[index].row);
        EXPECT_EQ(restored.committed_segments[index].offset_in_row,
                  source.committed_segments[index].offset_in_row);
        EXPECT_EQ(restored.committed_segments[index].bytes,
                  source.committed_segments[index].bytes);
    }
}
