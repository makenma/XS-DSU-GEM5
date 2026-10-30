#include "mem/cache/CHI/SlcSfModel.hh"
#include "mem/cache/CHI/PocqVictimBuffer.hh"

#include <gtest/gtest.h>
#include <algorithm>
#include <sstream>
#include <vector>

namespace gem5::Chi
{
namespace
{
using Kind = SlcSfResultKind;
SlcSfModel::Config small(std::size_t ways = 2)
{
    SlcSfModel::Config c;
    c.slcSets = c.sfSets = 4; c.slcWays = c.sfWays = ways; c.rnfCount = 4;
    return c;
}
SlcSfModel::Config cmn()
{
    SlcSfModel::Config c;
    c.slcSets = 1024; c.slcWays = 16;
    c.sfSets = 4096; c.sfWays = 16; c.rnfCount = 4;
    return c;
}
SlcLineData pattern(uint8_t seed)
{
    SlcLineData d;
    for (std::size_t i = 0; i < d.size(); ++i) d[i] = seed + i * 7;
    return d;
}
SlcSfRequest request(uint64_t token, uint64_t addr, uint8_t opcode = 7)
{
    SlcSfRequest r; r.token = token; r.addr = addr; r.opcode = opcode; return r;
}
std::vector<SlcSfResult> drain(SlcSfModel &m, uint64_t end)
{
    m.advance(end);
    std::vector<SlcSfResult> out;
    while (auto r = m.popResult()) out.push_back(*r);
    return out;
}
std::vector<SlcSfResult> transact(SlcSfModel &m, SlcSfRequest r)
{
    uint64_t start = m.cycle();
    while (!m.tryAccept(r, start)) ++start;
    return drain(m, start + 32);
}
SlcLineData returnedData(const std::vector<SlcSfResult> &rs)
{
    SlcLineData out{};
    for (const auto &r : rs) if (r.kind == Kind::DataBeat)
        std::copy(r.data.begin(), r.data.end(), out.begin() + r.beat * 32);
    return out;
}
unsigned count(const std::vector<SlcSfResult> &rs, Kind kind)
{
    return std::count_if(rs.begin(), rs.end(), [=](const auto &r) { return r.kind == kind; });
}

TEST(SlcSfModelTest, RejectsUnsupportedTimingAndWidth)
{
    auto c = small(); c.ramLatency = 3;
    EXPECT_THROW(SlcSfModel m(c), std::invalid_argument);
    c = small(); c.lineBytes = 128;
    EXPECT_THROW(SlcSfModel m(c), std::invalid_argument);
    c = small(); c.seqEntries = 9;
    EXPECT_THROW(SlcSfModel m(c), std::invalid_argument);
}

TEST(SlcSfModelTest, ReadUniqueAllHitCombinations)
{
    for (unsigned hits = 0; hits < 4; ++hits) {
        SlcSfModel m(small());
        if (hits & 1) m.preloadSlc(0x1000, true, {SlcMesi::Shared, false}, 1, pattern(9));
        if (hits & 2) m.preloadSf(0x1000, true, {SlcMesi::Shared, false}, 1,
                                {false, true, true, false});
        auto rs = transact(m, request(1, 0x1000));
        ASSERT_EQ(rs.front().kind, Kind::LookupDone);
        EXPECT_EQ(rs.front().lookup.slcHit, bool(hits & 1));
        EXPECT_EQ(rs.front().lookup.sfHit, bool(hits & 2));
        EXPECT_EQ(rs.front().lookup.memoryRead, hits == 0);
        EXPECT_EQ(rs.front().lookup.snoopTargets[1], bool(hits & 2));
        EXPECT_EQ(rs.front().lookup.snoopTargets[2], bool(hits & 2));
        EXPECT_EQ(rs.front().lookup.snoopOpcode, 7);
        EXPECT_EQ(count(rs, Kind::DataBeat), (hits & 1) ? 2 : 0);
        if (hits & 1) { EXPECT_EQ(returnedData(rs), pattern(9)); }
        EXPECT_EQ(count(rs, Kind::CommitDone), 1);
        EXPECT_EQ(rs.back().kind, Kind::CommitDone);
        EXPECT_EQ(m.findSlc(0x1000), nullptr);
        ASSERT_NE(m.findSf(0x1000), nullptr);
        EXPECT_EQ(m.findSf(0x1000)->mesi, SlcMesi::Exclusive);
        EXPECT_TRUE(m.findSf(0x1000)->unique);
        EXPECT_EQ(m.findSf(0x1000)->rnfId, 0);
    }
}

TEST(SlcSfModelTest, NSIsPartOfBothTagsAndByteAddressIsLineAligned)
{
    SlcSfModel m(small());
    m.preloadSlc(0x1000, false, {SlcMesi::Exclusive, true}, 0, pattern(1));
    m.preloadSf(0x1000, false, {SlcMesi::Exclusive, true}, 1,
                {false, true, false, false});
    auto r = request(1, 0x1020); r.ns = true;
    auto rs = transact(m, r);
    EXPECT_FALSE(rs.front().lookup.slcHit);
    EXPECT_FALSE(rs.front().lookup.sfHit);
    ASSERT_NE(m.findSlc(0x103f, false), nullptr);
    ASSERT_NE(m.findSf(0x103f, false), nullptr);
    ASSERT_NE(m.findSf(0x1000, true), nullptr);
}

TEST(SlcSfModelTest, DirtyCleanInvalidPreservesDataUntilWriteback)
{
    SlcSfModel m(small());
    m.preloadSlc(0x8000, true, {SlcMesi::Modified, false}, 1, pattern(31));
    m.preloadSf(0x8000, true, {SlcMesi::Shared, false}, 1,
                {false, true, true, false});
    auto rs = transact(m, request(1, 0x8000, 9));
    ASSERT_EQ(count(rs, Kind::DataBeat), 2);
    EXPECT_EQ(returnedData(rs), pattern(31));
    EXPECT_EQ(rs.front().lookup.snoopOpcode, 9);
    EXPECT_FALSE(rs.front().lookup.memoryRead);
    EXPECT_EQ(m.findSlc(0x8000), nullptr);
    EXPECT_EQ(m.findSf(0x8000), nullptr);
}

TEST(SlcSfModelTest, CopyReadsApplyEUToENAndSUToSN)
{
    for (auto state : {SlcMesi::Exclusive, SlcMesi::Shared}) {
        SlcSfModel m(small());
        m.preloadSf(0, true, {state, true}, 1, {false, true, false, false});
        auto r = request(1, 0, 1); r.rnf = 2;
        auto rs = transact(m, r);
        ASSERT_NE(m.findSf(0), nullptr);
        EXPECT_EQ(m.findSf(0)->mesi, state);
        EXPECT_FALSE(m.findSf(0)->unique);
        EXPECT_TRUE(m.findSf(0)->sfRnfVec[1]);
        EXPECT_TRUE(m.findSf(0)->sfRnfVec[2]);
        EXPECT_TRUE(rs.front().lookup.snoopTargets[1]);
        EXPECT_FALSE(rs.front().lookup.snoopTargets[2]);
    }
}

TEST(SlcSfModelTest, ENAndMNLocalCopiesAllocateEN)
{
    for (auto state : {SlcMesi::Exclusive, SlcMesi::Modified}) {
        SlcSfModel m(small());
        m.preloadSlc(0, true, {state, false}, 1, pattern(4));
        auto rs = transact(m, request(1, 0, 1));
        ASSERT_NE(m.findSf(0), nullptr);
        EXPECT_EQ(m.findSf(0)->mesi, SlcMesi::Exclusive);
        EXPECT_FALSE(m.findSf(0)->unique);
        EXPECT_EQ(returnedData(rs), pattern(4));
    }
}

TEST(SlcSfModelTest, MatchingUniqueLocalOwnerMigratesIntoSF)
{
    SlcSfModel m(small());
    m.preloadSlc(0, true, {SlcMesi::Exclusive, true}, 2, pattern(2));
    auto r = request(1, 0, 1); r.rnf = 2;
    auto rs = transact(m, r);
    EXPECT_EQ(m.findSlc(0), nullptr);
    ASSERT_NE(m.findSf(0), nullptr);
    EXPECT_TRUE(m.findSf(0)->unique);
    EXPECT_EQ(m.findSf(0)->rnfId, 2);
    EXPECT_EQ(returnedData(rs), pattern(2));
}

TEST(SlcSfModelTest, DirtyVictimUsesOldAddressAndOwnedData)
{
    SlcSfModel m(small(1));
    m.preloadSlc(0, false, {SlcMesi::Modified, false}, 1, pattern(7));
    auto r = request(1, 0x100); r.op = SlcSfOp::Fill;
    r.state = {SlcMesi::Exclusive, true}; r.data = pattern(90);
    ASSERT_TRUE(m.tryAccept(r, 0));
    r.data.fill(0); // The request caller may immediately recycle its storage.
    auto rs = drain(m, 32);
    ASSERT_TRUE(rs.front().lookup.victim);
    EXPECT_EQ(rs.front().lookup.victimAddr, 0);
    EXPECT_FALSE(rs.front().lookup.victimNs);
    EXPECT_EQ(returnedData(rs), pattern(7));
    for (const auto &v : rs) {
        if (v.kind == Kind::DataBeat) { EXPECT_TRUE(v.victim); }
    }
    EXPECT_EQ(m.findSlc(0, false), nullptr);
    ASSERT_NE(m.findSlc(0x100), nullptr);
    const auto expected = pattern(90);
    EXPECT_TRUE(std::equal(expected.begin(), expected.end(),
                          m.l3DataRam().at(0, 0).bytes.begin()));
}

TEST(SlcSfModelTest, CleanVictimNeedsNoWriteback)
{
    SlcSfModel m(small(1));
    m.preloadSlc(0, true, {SlcMesi::Exclusive, false}, 1, pattern(7));
    auto r = request(1, 0x100); r.op = SlcSfOp::Fill;
    r.state = {SlcMesi::Modified, true}; r.data = pattern(42);
    auto rs = transact(m, r);
    EXPECT_TRUE(rs.front().lookup.victim);
    EXPECT_EQ(count(rs, Kind::DataBeat), 0);
    ASSERT_NE(m.findSlc(0x100), nullptr);
    EXPECT_EQ(m.findSlc(0x100)->mesi, SlcMesi::Modified);
}

TEST(SlcSfModelTest, ReplayCancelsBothDirectoryUpdatesAndAllData)
{
    SlcSfModel m(small());
    m.preloadSlc(0, true, {SlcMesi::Modified, false}, 1, pattern(7));
    m.preloadSf(0, true, {SlcMesi::Shared, false}, 1, {false, true, true, false});
    auto r = request(1, 0, 9); r.victimConflict = true;
    auto rs = transact(m, r);
    ASSERT_EQ(rs.size(), 1);
    EXPECT_EQ(rs[0].kind, Kind::Replay);
    EXPECT_EQ(rs[0].replay, SlcSfReplay::VictimConflict);
    EXPECT_NE(m.findSlc(0), nullptr);
    EXPECT_NE(m.findSf(0), nullptr);
    r.token = 2; r.victimConflict = false;
    rs = transact(m, r);
    EXPECT_EQ(count(rs, Kind::CommitDone), 1);
    EXPECT_EQ(returnedData(rs), pattern(7));
    EXPECT_EQ(m.findSlc(0), nullptr);
    EXPECT_EQ(m.findSf(0), nullptr);
}

TEST(SlcSfModelTest, HistoryProtectsWayNotWholeSet)
{
    auto cfg = small(); cfg.sfWays = 16;
    SlcSfModel m(cfg);
    m.preloadSlc(0, true, {SlcMesi::Exclusive, false}, 1, pattern(7), 0);
    m.preloadSlc(0x100, true, {SlcMesi::Exclusive, false}, 1, pattern(8), 1);
    auto a = request(1, 0, 3), b = request(2, 0x100, 3), c = request(3, 0, 3);
    ASSERT_TRUE(m.tryAccept(a, 0));
    EXPECT_FALSE(m.tryAccept(b, 1));
    ASSERT_TRUE(m.tryAccept(b, 2));
    ASSERT_TRUE(m.tryAccept(c, 4));
    auto rs = drain(m, 32);
    EXPECT_EQ(count(rs, Kind::Replay), 1);
    for (const auto &r : rs) if (r.kind == Kind::Replay) {
        EXPECT_EQ(r.token, 3); EXPECT_EQ(r.replay, SlcSfReplay::SlcSetWay);
    }
    EXPECT_EQ(count(rs, Kind::CommitDone), 2);
}

TEST(SlcSfModelTest, EightSEQEntriesAndNinthReplacementReplay)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Shared, false}, 1, {false, true, true, false});
    for (uint64_t i = 1; i <= 8; ++i) {
        auto r = request(i, i * 64); r.op = SlcSfOp::UpdateSf;
        r.state = {SlcMesi::Exclusive, true}; r.rnf = 2;
        auto rs = transact(m, r);
        EXPECT_EQ(count(rs, Kind::Replay), 0);
        EXPECT_EQ(m.seqOccupancy(), i);
    }
    auto r = request(9, 9 * 64); r.op = SlcSfOp::UpdateSf;
    r.state = {SlcMesi::Exclusive, true};
    auto rs = transact(m, r);
    ASSERT_EQ(rs.size(), 1);
    EXPECT_EQ(rs[0].replay, SlcSfReplay::SeqFull);
    EXPECT_NE(m.findSf(8 * 64), nullptr);
    EXPECT_EQ(m.findSf(9 * 64), nullptr);
}

TEST(SlcSfModelTest, SeqHazardHoldsSlotButOnlyOnePOCQTransaction)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1, {false, true, false, false});
    auto r = request(1, 64); r.op = SlcSfOp::UpdateSf;
    r.state = {SlcMesi::Exclusive, true};
    transact(m, r);
    auto sq = m.pendingSeq(); ASSERT_TRUE(sq);
    m.acceptSeq(sq->id, true);
    EXPECT_EQ(m.seqOccupancy(), 1);
    EXPECT_FALSE(m.pendingSeq());
    m.releaseSeq(sq->id);
    EXPECT_EQ(m.seqOccupancy(), 1);
    m.advance(m.cycle() + 1);
    EXPECT_EQ(m.seqOccupancy(), 0);
    EXPECT_FALSE(m.pendingSeq());
    r.token = 2; r.addr = 128; transact(m, r);
    EXPECT_EQ(m.seqOccupancy(), 1);
    EXPECT_FALSE(m.pendingSeq());
    m.completeSeq(sq->id);
    ASSERT_TRUE(m.pendingSeq());
    sq = m.pendingSeq();
    m.acceptSeq(sq->id, false);
    EXPECT_EQ(m.seqOccupancy(), 1);
    m.advance(m.cycle() + SlcSfModel::SeqNoHazardReleaseDelay);
    EXPECT_EQ(m.seqOccupancy(), 0);
    m.completeSeq(sq->id);
    EXPECT_FALSE(m.hasWork());
}

TEST(SlcSfModelTest, SeqHitCopyReadSuppressesDuplicateAllocationWithoutReplay)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1, {false, true, false, false});
    auto r = request(1, 64); r.op = SlcSfOp::UpdateSf;
    r.state = {SlcMesi::Exclusive, true};
    transact(m, r);
    auto rs = transact(m, request(2, 0, 1));
    EXPECT_TRUE(rs.front().lookup.seqHit);
    EXPECT_TRUE(rs.front().lookup.snoopTargets[1]);
    EXPECT_EQ(count(rs, Kind::Replay), 0);
    EXPECT_EQ(m.findSf(0), nullptr);
    EXPECT_NE(m.findSf(64), nullptr);
}

TEST(SlcSfModelTest, ResultBackpressureIsBoundedAndPreservesBeatOrder)
{
    SlcSfModel m(small());
    m.preloadSlc(0, true, {SlcMesi::Exclusive, true}, 1, pattern(19));
    uint64_t token = 1;
    for (; token <= 16; ++token) {
        auto r = request(token, 0, 3);
        ASSERT_TRUE(m.tryAccept(r, (token - 1) * 32));
        m.advance((token - 1) * 32 + 31);
    }
    EXPECT_FALSE(m.tryAccept(request(token, 0, 3), 512));
    auto rs = drain(m, 512);
    ASSERT_EQ(rs.size(), 64);
    for (std::size_t i = 0; i < rs.size(); i += 4) {
        EXPECT_EQ(rs[i].kind, Kind::LookupDone);
        EXPECT_EQ(rs[i + 1].kind, Kind::DataBeat);
        EXPECT_EQ(rs[i + 1].beat, 0);
        EXPECT_EQ(rs[i + 2].kind, Kind::DataBeat);
        EXPECT_EQ(rs[i + 2].beat, 1);
        EXPECT_EQ(rs[i + 3].kind, Kind::CommitDone);
        EXPECT_LT(rs[i].cycle, rs[i + 1].cycle);
        EXPECT_EQ(rs[i + 2].cycle, rs[i + 1].cycle + 1);
    }
    EXPECT_TRUE(m.tryAccept(request(token, 0, 3), 512));
}


TEST(SlcSfModelTest, RTLReadUniqueCycleBoundaries)
{
    // From actual CMN r3p0 LAT2/ECC0/DSLICES0 RTL signal traces, not
    // SlcSfModel timing constants: tag feedback7, OW valid9/10, SRAM12/13.
    SlcSfModel m(cmn());
    m.preloadSlc(0, true, {SlcMesi::Shared, false}, 1, pattern(6));
    m.preloadSf(0, true, {SlcMesi::Shared, false}, 1,
                {false, true, true, false});
    ASSERT_TRUE(m.tryAccept(request(1, 0), 0));
    m.advance(6); EXPECT_FALSE(m.popResult());
    m.advance(7);
    auto r = m.popResult(); ASSERT_TRUE(r);
    EXPECT_EQ(r->kind, Kind::LookupDone); EXPECT_EQ(r->cycle, 7);
    EXPECT_FALSE(m.popResult());
    for (unsigned cycle : {9, 10}) {
        m.advance(cycle); r = m.popResult(); ASSERT_TRUE(r);
        EXPECT_EQ(r->kind, Kind::DataBeat); EXPECT_EQ(r->beat, cycle - 9);
        EXPECT_EQ(r->cycle, cycle);
    }
    m.advance(11); EXPECT_NE(m.findSlc(0), nullptr);
    EXPECT_EQ(m.findSf(0)->mesi, SlcMesi::Shared);
    m.advance(12); EXPECT_EQ(m.findSlc(0), nullptr);
    EXPECT_EQ(m.findSf(0)->mesi, SlcMesi::Shared);
    EXPECT_FALSE(m.popResult());
    m.advance(13);
    EXPECT_EQ(m.findSf(0)->mesi, SlcMesi::Exclusive);
    EXPECT_EQ(m.findSf(0)->rnfId, 0);
    r = m.popResult(); ASSERT_TRUE(r);
    EXPECT_EQ(r->kind, Kind::CommitDone); EXPECT_EQ(r->cycle, 13);
}

TEST(SlcSfModelTest, RTLFillWritesTwoBanksAtSeparateEdges)
{
    SlcSfModel m(cmn());
    auto req = request(1, 0); req.op = SlcSfOp::Fill;
    req.state = {SlcMesi::Modified, true}; req.data = pattern(12);
    ASSERT_TRUE(m.tryAccept(req, 0));
    m.advance(11); EXPECT_EQ(m.findSlc(0), nullptr);
    m.advance(12); EXPECT_NE(m.findSlc(0), nullptr);
    m.advance(15);
    EXPECT_EQ(m.l3DataRam().at(0, 0).bytes, std::vector<uint8_t>(64, 0));
    m.advance(16);
    const auto &data = m.l3DataRam().at(0, 0).bytes;
    EXPECT_TRUE(std::equal(req.data.begin(), req.data.begin() + 32, data.begin()));
    EXPECT_TRUE(std::all_of(data.begin() + 32, data.end(), [](auto b) { return b == 0; }));
    auto rs = drain(m, 16); EXPECT_EQ(count(rs, Kind::CommitDone), 0);
    rs = drain(m, 17);
    ASSERT_EQ(rs.size(), 1); EXPECT_EQ(rs.front().kind, Kind::CommitDone);
    EXPECT_EQ(rs.front().cycle, 17);
    EXPECT_TRUE(std::equal(req.data.begin(), req.data.end(), data.begin()));
}

TEST(SlcSfModelTest, RTLBusyIncludesReadControlSlotAndUpdateRecirculation)
{
    for (bool update : {false, true}) {
        SlcSfModel m(cmn());
        auto first = request(1, 0, update ? 7 : 3);
        ASSERT_TRUE(m.tryAccept(first, 0));
        EXPECT_FALSE(m.tryAccept(request(2, 64, 3), 1));
        EXPECT_FALSE(m.tryAccept(request(2, 64, 3), 9));
        if (update) {
            EXPECT_FALSE(m.tryAccept(request(2, 64, 3), 10));
            EXPECT_FALSE(m.tryAccept(request(2, 64, 3), 11));
            EXPECT_TRUE(m.tryAccept(request(2, 64, 3), 12));
        } else EXPECT_TRUE(m.tryAccept(request(2, 64, 3), 10));
    }
}

TEST(SlcSfModelTest, RTLReplayControlStillReservesBusySlot)
{
    SlcSfModel m(cmn());
    auto r = request(1, 0); r.victimConflict = true;
    ASSERT_TRUE(m.tryAccept(r, 0));
    m.advance(6); EXPECT_FALSE(m.popResult());
    m.advance(7); auto out = m.popResult(); ASSERT_TRUE(out);
    EXPECT_EQ(out->kind, Kind::Replay); EXPECT_EQ(out->cycle, 7);
    EXPECT_FALSE(m.tryAccept(request(2, 64), 9));
    EXPECT_TRUE(m.tryAccept(request(2, 64), 10));
}

TEST(SlcSfModelTest, PendingAllocationsUseDistinctEmptyWays)
{
    SlcSfModel m(small());
    ASSERT_TRUE(m.tryAccept(request(1, 0), 0));
    ASSERT_TRUE(m.tryAccept(request(2, 256), 2));
    auto rs = drain(m, 32);
    EXPECT_EQ(count(rs, Kind::Replay), 0);
    EXPECT_EQ(count(rs, Kind::CommitDone), 2);
    EXPECT_NE(m.findSf(0), nullptr);
    EXPECT_NE(m.findSf(256), nullptr);
}

TEST(SlcSfModelTest, NonAllocatingCacheReadStillTracksRNFInDirectory)
{
    SlcSfModel m(small());
    auto r = request(1, 0, 1); r.allocate = false;
    transact(m, r);
    EXPECT_NE(m.findSf(0), nullptr);
    EXPECT_EQ(m.findSlc(0), nullptr);
}

TEST(SlcSfModelTest, CleanReadOfMatchingMUAllocatesENAndKeepsMN)
{
    SlcSfModel m(small());
    m.preloadSlc(0, true, {SlcMesi::Modified, true}, 0, pattern(7));
    transact(m, request(1, 0, 2));
    ASSERT_NE(m.findSlc(0), nullptr);
    EXPECT_EQ(m.findSlc(0)->mesi, SlcMesi::Modified);
    EXPECT_FALSE(m.findSlc(0)->unique);
    ASSERT_NE(m.findSf(0), nullptr);
    EXPECT_EQ(m.findSf(0)->mesi, SlcMesi::Exclusive);
    EXPECT_FALSE(m.findSf(0)->unique);
}

TEST(SlcSfModelTest, OrdinaryMaintenanceAndEvictionStateChanges)
{
    for (uint8_t opcode : {0x08, 0x0a, 0x0b, 0x0c, 0x0d}) {
        SlcSfModel m(small());
        m.preloadSlc(0, true, {SlcMesi::Modified, false}, 1, pattern(8));
        m.preloadSf(0, true, {SlcMesi::Shared, false}, 1,
                    {false, true, true, false});
        auto r = request(1, 0, opcode); r.rnf = 1;
        auto rs = transact(m, r);
        if (opcode == 0x08) {
            ASSERT_NE(m.findSlc(0), nullptr);
            EXPECT_EQ(m.findSlc(0)->mesi, SlcMesi::Exclusive);
            EXPECT_EQ(count(rs, Kind::DataBeat), 2);
        } else if (opcode == 0x0d) {
            EXPECT_NE(m.findSlc(0), nullptr);
            ASSERT_NE(m.findSf(0), nullptr);
            EXPECT_FALSE(m.findSf(0)->sfRnfVec[1]);
            EXPECT_TRUE(m.findSf(0)->sfRnfVec[2]);
        } else {
            EXPECT_EQ(m.findSlc(0), nullptr);
            EXPECT_EQ(count(rs, Kind::DataBeat), 2);
            if (opcode == 0x0a) EXPECT_EQ(m.findSf(0), nullptr);
            else {
                ASSERT_NE(m.findSf(0), nullptr);
                EXPECT_TRUE(m.findSf(0)->unique); EXPECT_EQ(m.findSf(0)->rnfId, 1);
            }
        }
    }
}

TEST(SlcSfModelTest, FillWritebackRemovesOnlyRelinquishingRNF)
{
    SlcSfModel m(small());
    m.preloadSf(0, true, {SlcMesi::Shared, false}, 1,
                {false, true, true, false});
    auto r = request(1, 0, 0x1b); r.op = SlcSfOp::Fill; r.rnf = 1;
    r.state = {SlcMesi::Modified, false}; r.data = pattern(45);
    auto rs = transact(m, r);
    ASSERT_NE(m.findSf(0), nullptr);
    EXPECT_FALSE(m.findSf(0)->sfRnfVec[1]); EXPECT_TRUE(m.findSf(0)->sfRnfVec[2]);
    EXPECT_EQ(m.findSf(0)->rnfId, 2);
    EXPECT_EQ(count(rs, Kind::CommitDone), 1);
    EXPECT_NE(m.findSlc(0), nullptr);
}

TEST(SlcSfModelTest, SeqUniqueBroadcastAndReleasedSlotNoLongerHits)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1, {false, true, false, false});
    auto r = request(1, 64); r.op = SlcSfOp::UpdateSf;
    r.state = {SlcMesi::Exclusive, true}; transact(m, r);
    auto rs = transact(m, request(2, 0));
    EXPECT_TRUE(rs.front().lookup.seqHit); EXPECT_TRUE(rs.front().lookup.broadcast);
    EXPECT_EQ(rs.front().lookup.snoopTargets, (std::vector<bool>{false, true, true, true}));
    auto sq = m.pendingSeq(); ASSERT_TRUE(sq);
    EXPECT_TRUE(sq->broadcast);
    m.acceptSeq(sq->id, false);
    rs = transact(m, request(3, 0));
    EXPECT_FALSE(rs.front().lookup.seqHit);
    EXPECT_NE(m.findSf(0), nullptr);
}

TEST(SlcSfModelTest, CanonicalRNSDAndUnsupportedExtensions)
{
    SlcSfModel m(small());
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                {false, true, false, false});
    auto rs = transact(m, request(1, 0, 0x26));
    EXPECT_EQ(rs.front().lookup.snoopOpcode, 4);
    EXPECT_FALSE(m.findSf(0)->unique);
    EXPECT_THROW(m.tryAccept(request(2, 64, 0x4c)), std::invalid_argument);
    EXPECT_THROW(m.tryAccept(request(3, 64, 0x20)), std::invalid_argument);
}


TEST(SlcSfModelTest, WriteUniqueUsesSeparateLookupAndFillTagPhases)
{
    for (bool noFill : {false, true}) {
        SlcSfModel m(small());
        m.preloadSlc(0, true, {SlcMesi::Modified, false}, 1, pattern(11));
        m.preloadSf(0, true, {SlcMesi::Shared, false}, 1,
                    {false, true, true, false});
        auto r = request(1, 0, 0x19); r.noFill = noFill;
        auto rs = transact(m, r);
        EXPECT_EQ(count(rs, Kind::DataBeat), 2);
        EXPECT_EQ(returnedData(rs), pattern(11));
        if (noFill) {
            EXPECT_EQ(m.findSlc(0), nullptr); EXPECT_EQ(m.findSf(0), nullptr);
        } else {
            EXPECT_NE(m.findSlc(0), nullptr); EXPECT_NE(m.findSf(0), nullptr);
        }
        r.token = 2; r.op = SlcSfOp::Fill;
        r.state = {SlcMesi::Modified, true}; r.data = pattern(54);
        transact(m, r);
        EXPECT_EQ(m.findSf(0), nullptr);
        if (noFill) { EXPECT_EQ(m.findSlc(0), nullptr); }
        else {
            ASSERT_NE(m.findSlc(0), nullptr);
            const auto d = pattern(54);
            EXPECT_TRUE(std::equal(d.begin(), d.end(), m.l3DataRam().at(0, 0).bytes.begin()));
        }
    }
}

TEST(SlcSfModelTest, CleanHitMaintenanceStillReturnsTheReadDatapath)
{
    SlcSfModel m(small());
    m.preloadSlc(0, true, {SlcMesi::Exclusive, false}, 1, pattern(77));
    auto rs = transact(m, request(1, 0, 9));
    EXPECT_EQ(count(rs, Kind::DataBeat), 2);
    EXPECT_EQ(returnedData(rs), pattern(77));
    EXPECT_EQ(m.findSlc(0), nullptr);
}

TEST(SlcSfModelTest, RRPVHitPromotionAndRotatingVictims)
{
    auto cfg = small(4); SlcSfModel m(cfg);
    for (unsigned way = 0; way < 4; ++way) {
        m.preloadSlc(way * 256, true, {SlcMesi::Exclusive, true}, 1, pattern(way), way);
        m.l3TagRam().at(0, way).rrpv = 3;
    }
    transact(m, request(1, 0, 2)); // State update EU->EN must promote RRPV too.
    ASSERT_NE(m.findSlc(0), nullptr); EXPECT_EQ(m.findSlc(0)->rrpv, 0);
    for (unsigned i = 0; i < 2; ++i) {
        auto r = request(2 + i, (4 + i) * 256); r.op = SlcSfOp::Fill;
        r.state = {SlcMesi::Exclusive, true}; r.data = pattern(55);
        auto rs = transact(m, r);
        EXPECT_TRUE(rs.front().lookup.victim);
        EXPECT_EQ(rs.front().lookup.victimAddr, (i + 1) * 256);
    }
    EXPECT_NE(m.findSlc(0), nullptr);
}

TEST(SlcSfModelTest, SFLFSRMatchesRTLZeroSeedSequence)
{
    auto cfg = small(1); cfg.sfSets = 1; cfg.sfWays = 16;
    SlcSfModel m(cfg);
    for (unsigned way = 0; way < 16; ++way)
        m.preloadSf(way * 64, true, {SlcMesi::Exclusive, true}, 1,
                    {false, true, false, false}, way);
    unsigned i = 0;
    for (unsigned victim : {8, 4, 2, 9}) {
        ++i; auto r = request(i, (16 + i) * 64); r.op = SlcSfOp::UpdateSf;
        r.state = {SlcMesi::Exclusive, true}; transact(m, r);
        auto sq = m.pendingSeq(); ASSERT_TRUE(sq);
        EXPECT_EQ(sq->addr, victim * 64);
        m.acceptSeq(sq->id, false); m.completeSeq(sq->id);
    }
}


TEST(SlcSfModelTest, RTLContinuousSameWayAdmissionTrace)
{
    SlcSfModel m(cmn());
    m.preloadSlc(0, true, {SlcMesi::Modified, true}, 1, pattern(33));
    for (uint64_t cycle : {0, 2, 4, 6})
        ASSERT_TRUE(m.tryAccept(request(cycle + 1, 0), cycle));
    auto rs = drain(m, 32);
    std::vector<uint64_t> replayCycles;
    for (const auto &r : rs) if (r.kind == Kind::Replay) replayCycles.push_back(r.cycle);
    EXPECT_EQ(replayCycles, (std::vector<uint64_t>{9, 11, 13}));
    EXPECT_EQ(count(rs, Kind::LookupDone), 1);
    EXPECT_EQ(count(rs, Kind::DataBeat), 2);
    EXPECT_EQ(count(rs, Kind::CommitDone), 1);
    EXPECT_EQ(returnedData(rs), pattern(33));
}

TEST(SlcSfModelTest, DRRIPLeaderHitsChooseFollowerInsertionPolicy)
{
    SlcSfModel m(cmn());
    m.preloadSlc(64, true, {SlcMesi::Exclusive, true}, 0, pattern(1));
    m.preloadSlc(0, true, {SlcMesi::Exclusive, true}, 0, pattern(2));
    transact(m, request(1, 64, 3)); // BRRIP leader hit favors BRRIP followers.
    EXPECT_EQ(m.replacementPsel(), -1);
    auto fill = request(2, 128); fill.op = SlcSfOp::Fill;
    fill.state = {SlcMesi::Exclusive, true}; transact(m, fill);
    ASSERT_NE(m.findSlc(128), nullptr); EXPECT_EQ(m.findSlc(128)->rrpv, 3);
    transact(m, request(3, 0, 3));
    EXPECT_EQ(m.replacementPsel(), 0);
    fill.token = 4; fill.addr = 192; transact(m, fill);
    ASSERT_NE(m.findSlc(192), nullptr); EXPECT_EQ(m.findSlc(192)->rrpv, 2);
}

TEST(SlcSfModelTest, BRRIPInsertsOneInThirtyTwoAtSRRIPValue)
{
    auto cfg = cmn(); cfg.slcWays = 1;
    SlcSfModel m(cfg);
    for (uint64_t i = 0; i < 32; ++i) {
        auto r = request(i + 1, 64 + i * 65536); r.op = SlcSfOp::Fill;
        r.state = {SlcMesi::Exclusive, true}; transact(m, r);
        ASSERT_NE(m.findSlc(r.addr), nullptr);
        EXPECT_EQ(m.findSlc(r.addr)->rrpv, i == 31 ? 2 : 3);
    }
    EXPECT_EQ(m.replacementBrripCounter(), 0);
}

TEST(SlcSfModelTest, FreeWayAndFillHitDoNotAgeUnrelatedWays)
{
    SlcSfModel m(small());
    m.preloadSlc(0, true, {SlcMesi::Exclusive, true}, 0, pattern(1), 0);
    m.l3TagRam().at(0, 0).rrpv = 1;
    auto r = request(1, 256); r.op = SlcSfOp::Fill;
    r.state = {SlcMesi::Exclusive, true}; transact(m, r);
    EXPECT_EQ(m.findSlc(0)->rrpv, 1);
    r.token = 2; r.addr = 256; transact(m, r);
    EXPECT_EQ(m.findSlc(0)->rrpv, 1);
    EXPECT_EQ(m.findSlc(256)->rrpv, 0);
}

TEST(SlcSfModelTest, OverlappingDifferentSetVictimsRotateGlobalMaskEarly)
{
    SlcSfModel m(small());
    for (unsigned set = 0; set < 2; ++set) {
        for (unsigned way = 0; way < 2; ++way) {
            m.preloadSlc((way * 4 + set) * 64, true,
                        {SlcMesi::Exclusive, true}, 0, pattern(1), way);
            m.l3TagRam().at(set, way).rrpv = 3;
        }
    }
    auto first = request(1, 512); first.op = SlcSfOp::Fill;
    first.state = {SlcMesi::Exclusive, true};
    auto second = first; second.token = 2; second.addr = 576;
    ASSERT_TRUE(m.tryAccept(first, 0)); ASSERT_TRUE(m.tryAccept(second, 2));
    const auto rs = drain(m, 32);
    std::vector<uint64_t> victims;
    for (const auto &r : rs) if (r.kind == Kind::LookupDone) victims.push_back(r.lookup.victimAddr);
    EXPECT_EQ(victims, (std::vector<uint64_t>{0, 320}));
}

TEST(SlcSfModelTest, NoTagWriteDoesNotPersistRRPVHitPromotion)
{
    SlcSfModel m(small());
    m.preloadSlc(0, true, {SlcMesi::Exclusive, true}, 1, pattern(1));
    m.l3TagRam().at(0, 0).rrpv = 3;
    transact(m, request(1, 0, 3));
    EXPECT_EQ(m.findSlc(0)->rrpv, 3);
    transact(m, request(2, 0, 2)); // EU->EN tag update persists promotion.
    EXPECT_EQ(m.findSlc(0)->rrpv, 0);
}


TEST(SlcSfModelTest, CMOSnoopMeIncludesRequesterEvenOnDirectoryMiss)
{
    for (uint8_t opcode : {0x08, 0x09, 0x0a}) {
        for (bool hit : {false, true}) {
            SlcSfModel m(small());
            if (hit) m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 0,
                                {true, false, false, false});
            auto r = request(1, 0, opcode); r.snoopMe = true;
            auto rs = transact(m, r);
            EXPECT_TRUE(rs.front().lookup.snoopTargets[0]);
            EXPECT_FALSE(rs.front().lookup.selfCancel);
        }
    }
}

TEST(SlcSfModelTest, CleanSharedOnlyDirectsToDifferentEUOwner)
{
    for (auto mesi : {SlcMesi::Shared, SlcMesi::Exclusive}) {
        for (bool unique : {false, true}) {
            SlcSfModel m(small());
            m.preloadSf(0, true, {mesi, unique}, 1, {false, true, true, false});
            auto rs = transact(m, request(1, 0, 8));
            EXPECT_EQ(rs.front().lookup.snoopTargets[1],
                      mesi == SlcMesi::Exclusive && unique);
            EXPECT_FALSE(rs.front().lookup.snoopTargets[2]);
        }
    }
}


TEST(SlcSfModelTest, FullHistoryDoesNotReplayUnrelatedFifthFill)
{
    SlcSfModel m(cmn());
    for (uint64_t i = 0; i < 5; ++i) {
        auto r = request(i + 1, i * 64); r.op = SlcSfOp::Fill;
        r.state = {SlcMesi::Exclusive, true}; r.data = pattern(i);
        ASSERT_TRUE(m.tryAccept(r, i * 2));
    }
    auto rs = drain(m, 32);
    EXPECT_EQ(count(rs, Kind::Replay), 0);
    EXPECT_EQ(count(rs, Kind::CommitDone), 5);
    for (uint64_t i = 0; i < 5; ++i) EXPECT_NE(m.findSlc(i * 64), nullptr);
}


TEST(SlcSfModelTest, SleepingSeqSnapshotTracksForegroundOwnershipChange)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                {false, true, false, false});
    auto replacement = request(1, 64); replacement.op = SlcSfOp::UpdateSf;
    replacement.state = {SlcMesi::Exclusive, true};
    transact(m, replacement);
    auto victim = m.pendingSeq(); ASSERT_TRUE(victim);
    EXPECT_FALSE(victim->broadcast);
    m.acceptSeq(victim->id, true);
    // An older foreground transaction takes unique ownership while the
    // internal eviction is asleep behind its line hazard.
    auto foreground = request(2, 0); foreground.rnf = 2;
    auto results = transact(m, foreground);
    ASSERT_TRUE(results.front().lookup.seqHit);
    auto refreshed = m.seqSnapshot(victim->id); ASSERT_TRUE(refreshed);
    EXPECT_TRUE(refreshed->broadcast);
    EXPECT_EQ(refreshed->targets, std::vector<bool>(4, true));
    m.releaseSeq(victim->id);
    EXPECT_EQ(m.seqOccupancy(), 1);
    m.advance(m.cycle() + 1);
    EXPECT_EQ(m.seqOccupancy(), 0);
    refreshed = m.seqSnapshot(victim->id); ASSERT_TRUE(refreshed);
    EXPECT_TRUE(refreshed->broadcast);
    EXPECT_TRUE(refreshed->targets[2]);
    m.completeSeq(victim->id);
    EXPECT_FALSE(m.seqSnapshot(victim->id));
}

TEST(SlcSfModelTest, SeqRegistersMatchRtlAllocationAckAndDeallocationEdges)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                {false, true, false, false});
    auto r = request(1, 64); r.op = SlcSfOp::UpdateSf;
    r.state = {SlcMesi::Exclusive, true};
    ASSERT_TRUE(m.tryAccept(r, 0));
    m.advance(6);
    EXPECT_EQ(m.seqOccupancy(), 0);
    EXPECT_FALSE(m.pendingSeq());
    m.advance(7);
    EXPECT_EQ(m.seqOccupancy(), 1);
    EXPECT_FALSE(m.pendingSeq());
    m.advance(8);
    auto sq = m.pendingSeq(); ASSERT_TRUE(sq);
    EXPECT_EQ(sq->createdCycle, 7);
    EXPECT_EQ(sq->readyCycle, 8);
    m.advance(20);
    m.acceptSeq(sq->id, false);
    EXPECT_EQ(m.seqOccupancy(), 1);
    m.advance(22);
    EXPECT_EQ(m.seqOccupancy(), 1);
    m.advance(23);
    EXPECT_EQ(m.seqOccupancy(), 0);
    EXPECT_FALSE(m.pendingSeq());
    m.completeSeq(sq->id);
}

TEST(SlcSfModelTest, SeqHitBroadcastRegisterUpdatesEvenForReplayedLookup)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                {false, true, false, false});
    auto replacement = request(1, 64); replacement.op = SlcSfOp::UpdateSf;
    replacement.state = {SlcMesi::Exclusive, true};
    transact(m, replacement);
    auto sq = m.pendingSeq(); ASSERT_TRUE(sq);
    m.acceptSeq(sq->id, true);
    auto lookup = request(2, 0);
    lookup.victimConflict = true;
    const auto start = m.cycle();
    ASSERT_TRUE(m.tryAccept(lookup, start));
    m.advance(start + 5);
    EXPECT_FALSE(m.seqSnapshot(sq->id)->broadcast);
    m.advance(start + 6);
    EXPECT_TRUE(m.seqSnapshot(sq->id)->broadcast);
    EXPECT_EQ(m.seqSnapshot(sq->id)->targets, std::vector<bool>(4, true));
    auto out = drain(m, start + 20);
    ASSERT_EQ(out.size(), 1);
    EXPECT_EQ(out[0].kind, Kind::Replay);
    EXPECT_EQ(out[0].replay, SlcSfReplay::VictimConflict);
    EXPECT_EQ(m.findSf(0), nullptr);
    EXPECT_NE(m.findSf(64), nullptr);
}

TEST(SlcSfModelTest, SeqRegisteredSnapshotsKeepTheirEdgeAndOwnedTargets)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                {false, true, false, false});
    auto replacement = request(1, 64); replacement.op = SlcSfOp::UpdateSf;
    replacement.state = {SlcMesi::Exclusive, true};
    ASSERT_TRUE(m.tryAccept(replacement, 0));
    drain(m, 20);
    const auto saved = m.pendingSeq(); ASSERT_TRUE(saved);
    EXPECT_EQ(saved->readyCycle, 8);
    EXPECT_FALSE(saved->broadcast);

    // A replay still broadens the live SEQ at H6. The request register
    // samples it one edge later and HNF's previous-edge view one after that.
    auto lookup = request(2, 0); lookup.rnf = 2;
    lookup.victimConflict = true;
    ASSERT_TRUE(m.tryAccept(lookup, 20));
    m.advance(26);
    ASSERT_TRUE(m.seqSnapshot(saved->id));
    EXPECT_TRUE(m.seqSnapshot(saved->id)->broadcast);
    ASSERT_TRUE(m.pendingSeq());
    ASSERT_TRUE(m.pendingSeq(true));
    EXPECT_FALSE(m.pendingSeq()->broadcast);
    EXPECT_FALSE(m.pendingSeq(true)->broadcast);
    m.advance(27);
    EXPECT_TRUE(m.pendingSeq()->broadcast);
    EXPECT_FALSE(m.pendingSeq(true)->broadcast);
    m.advance(28);
    EXPECT_TRUE(m.pendingSeq(true)->broadcast);
    EXPECT_EQ(m.pendingSeq()->readyCycle, 8);
    EXPECT_EQ(m.pendingSeq(true)->readyCycle, 8);
    EXPECT_EQ(m.pendingSeq(true)->targets, std::vector<bool>(4, true));
    // A caller's older owned snapshot must never alias the updated register.
    EXPECT_FALSE(saved->broadcast);
    EXPECT_EQ(saved->targets, (std::vector<bool>{false, true, false, false}));
}

TEST(SlcSfModelTest, FillAndExplicitMaintenanceDoNotProbeOrBroadenSeq)
{
    for (auto op : {SlcSfOp::Fill, SlcSfOp::FlushSlc,
                    SlcSfOp::FlushSf, SlcSfOp::FlushBoth}) {
        auto cfg = small(1); cfg.sfSets = 1;
        SlcSfModel m(cfg);
        m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                    {false, true, false, false});
        auto replacement = request(1, 64); replacement.op = SlcSfOp::UpdateSf;
        replacement.state = {SlcMesi::Exclusive, true};
        transact(m, replacement);
        auto sq = m.pendingSeq(); ASSERT_TRUE(sq);
        auto req = request(2, 0); req.op = op;
        req.state = {SlcMesi::Exclusive, true}; req.data = pattern(7);
        auto out = transact(m, req);
        EXPECT_FALSE(out.front().lookup.seqHit);
        EXPECT_FALSE(m.seqSnapshot(sq->id)->broadcast);
    }
}

TEST(SlcSfModelTest, NonUniqueVictimCarriesBroadcastAttribute)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Shared, false}, 1,
                {false, true, true, false});
    auto r = request(1, 64); r.op = SlcSfOp::UpdateSf;
    r.state = {SlcMesi::Exclusive, true};
    transact(m, r);
    ASSERT_TRUE(m.pendingSeq());
    EXPECT_TRUE(m.pendingSeq()->broadcast);
    EXPECT_EQ(m.pendingSeq()->targets,
              (std::vector<bool>{false, true, true, false}));
}

TEST(SlcSfModelTest, ReusedSeqSlotWaitsForPhysicalReadPointerWrap)
{
    auto cfg = small(1); cfg.sfSets = 1;
    SlcSfModel m(cfg);
    m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                {false, true, false, false});
    auto r = request(1, 64); r.op = SlcSfOp::UpdateSf;
    r.state = {SlcMesi::Exclusive, true};
    ASSERT_TRUE(m.tryAccept(r, 0));
    m.advance(20);
    auto first = m.pendingSeq(); ASSERT_TRUE(first);
    m.acceptSeq(first->id, false);
    m.advance(30);
    m.completeSeq(first->id);
    while (m.popResult()) {}
    r.token = 2; r.addr = 128;
    ASSERT_TRUE(m.tryAccept(r, 40));
    m.advance(47);
    EXPECT_EQ(m.seqOccupancy(), 1);
    EXPECT_FALSE(m.pendingSeq());
    m.advance(58);
    EXPECT_FALSE(m.pendingSeq());
    m.advance(59);
    auto second = m.pendingSeq(); ASSERT_TRUE(second);
    EXPECT_NE(first->id, second->id);
    EXPECT_EQ(second->createdCycle, 47);
    EXPECT_EQ(second->readyCycle, 59);
    EXPECT_EQ(second->addr, 64);
}

TEST(SlcSfModelTest, NoSnpSeqHitDoesNotSnoopButReturnsNoFill)
{
    for (uint8_t opcode : {0x04, 0x1c, 0x1d}) {
        auto cfg = small(1); cfg.sfSets = 1;
        SlcSfModel m(cfg);
        m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                    {false, true, false, false});
        auto r = request(1, 64); r.op = SlcSfOp::UpdateSf;
        r.state = {SlcMesi::Exclusive, true};
        transact(m, r);
        const auto seq = m.pendingSeq(); ASSERT_TRUE(seq);
        auto out = transact(m, request(2, 0, opcode));
        ASSERT_EQ(out.front().kind, Kind::LookupDone);
        const auto &lookup = out.front().lookup;
        EXPECT_TRUE(lookup.seqHit);
        EXPECT_TRUE(lookup.noFill);
        EXPECT_FALSE(lookup.broadcast);
        EXPECT_FALSE(lookup.selfCancel);
        EXPECT_EQ(lookup.snoopTargets, std::vector<bool>(4, false));
        EXPECT_TRUE(m.seqSnapshot(seq->id)->broadcast);
    }
}

TEST(SlcSfModelTest, NoSnpWritesPreserveAnExistingSfDirectory)
{
    for (uint8_t opcode : {0x1c, 0x1d}) {
        SlcSfModel m(small());
        const std::vector<bool> owners{false, true, true, false};
        m.preloadSf(0, true, {SlcMesi::Shared, false}, 1, owners);
        auto r = request(1, 0, opcode); r.noFill = true;
        auto out = transact(m, r);
        EXPECT_TRUE(out.front().lookup.noFill);
        EXPECT_EQ(out.front().lookup.snoopTargets, std::vector<bool>(4, false));
        ASSERT_NE(m.findSf(0), nullptr);
        EXPECT_EQ(m.findSf(0)->sfRnfVec, owners);
        r.token = 2; r.op = SlcSfOp::Fill;
        r.state = {SlcMesi::Modified, true}; r.data = pattern(3);
        transact(m, r);
        ASSERT_NE(m.findSf(0), nullptr);
        EXPECT_EQ(m.findSf(0)->sfRnfVec, owners);
    }
}

TEST(SlcSfModelTest, RtlReadOnceHistoryLivesThroughGapEight)
{
    for (uint64_t gap : {8, 10}) {
        SlcSfModel m(cmn());
        m.preloadSlc(0, true, {SlcMesi::Modified, false}, 1, pattern(5));
        ASSERT_TRUE(m.tryAccept(request(1, 0, 3), 0));
        ASSERT_TRUE(m.tryAccept(request(2, 0, 3), gap));
        auto rs = drain(m, 40);
        EXPECT_EQ(count(rs, Kind::Replay), gap == 8 ? 1 : 0);
        for (const auto &r : rs) if (r.kind == Kind::Replay) {
            EXPECT_EQ(r.token, 2); EXPECT_EQ(r.cycle, 15);
            EXPECT_EQ(r.replay, SlcSfReplay::SlcSetWay);
        }
        EXPECT_EQ(count(rs, Kind::DataBeat), gap == 8 ? 2 : 4);
    }
}

TEST(SlcSfModelTest, RtlNonAllocatingSfMissCanReplayWithoutAdvancingLfsr)
{
    SlcSfModel m(cmn());
    constexpr uint64_t stride = 4096 * 64;
    for (unsigned way = 0; way < 16; ++way)
        m.preloadSf((way + 1) * stride, true, {SlcMesi::Exclusive, true}, 1,
                    {false, true, false, false}, way);
    ASSERT_TRUE(m.tryAccept(request(1, 0, 3), 0));
    ASSERT_TRUE(m.tryAccept(request(2, 0, 3), 2));
    auto rs = drain(m, 32);
    ASSERT_EQ(count(rs, Kind::Replay), 1);
    for (const auto &r : rs) if (r.kind == Kind::Replay) {
        EXPECT_EQ(r.token, 2); EXPECT_EQ(r.cycle, 9);
        EXPECT_EQ(r.replay, SlcSfReplay::SfSetWay);
    }
    EXPECT_FALSE(m.pendingSeq());
    transact(m, request(3, 0));
    auto victim = m.pendingSeq(); ASSERT_TRUE(victim);
    EXPECT_EQ(victim->addr, 9 * stride); // Initial next-LFSR selects way 8.
}

TEST(SlcSfModelTest, RtlFillHistoryExpiresBeforeDataWriteCompletes)
{
    SlcSfModel m(cmn());
    auto a = request(1, 0, 0x1b); a.op = SlcSfOp::Fill;
    a.state = {SlcMesi::Exclusive, true}; a.data = pattern(11);
    ASSERT_TRUE(m.tryAccept(a, 0));
    auto b = a; b.token = 2; b.data = pattern(23);
    ASSERT_TRUE(m.tryAccept(b, 12));
    auto rs = drain(m, 40);
    EXPECT_EQ(count(rs, Kind::Replay), 0);
    EXPECT_EQ(count(rs, Kind::CommitDone), 2);
    EXPECT_TRUE(std::equal(b.data.begin(), b.data.end(),
                          m.l3DataRam().at(0, 0).bytes.begin()));
}

TEST(SlcSfModelTest, TagReadBeforeConcurrentInvalidationRetainsReplayHazard)
{
    for (bool slc : {false, true}) {
        SlcSfModel m(cmn());
        if (slc)
            m.preloadSlc(0, true, {SlcMesi::Modified, false}, 1, pattern(9));
        else
            m.preloadSf(0, true, {SlcMesi::Exclusive, true}, 1,
                        {false, true, false, false});
        ASSERT_TRUE(m.tryAccept(request(1, 0, 9), 0));
        ASSERT_TRUE(m.tryAccept(request(2, 0, 3), 8));
        auto rs = drain(m, 40);
        ASSERT_EQ(count(rs, Kind::Replay), 1);
        for (const auto &r : rs) if (r.kind == Kind::Replay) {
            EXPECT_EQ(r.token, 2); EXPECT_EQ(r.cycle, 15);
            EXPECT_EQ(r.lookup.slcHit, slc);
            EXPECT_EQ(r.lookup.sfHit, !slc);
        }
        EXPECT_EQ(count(rs, Kind::CommitDone), 1);
        EXPECT_FALSE(m.findSlc(0)); EXPECT_FALSE(m.findSf(0));
    }
}

TEST(SlcSfModelTest, ReadAfterFillSamplesBothDataBanksAfterTheirWrites)
{
    SlcSfModel m(cmn());
    auto fill = request(1, 0, 0x1b); fill.op = SlcSfOp::Fill;
    fill.state = {SlcMesi::Exclusive, true}; fill.data = pattern(37);
    ASSERT_TRUE(m.tryAccept(fill, 0));
    ASSERT_TRUE(m.tryAccept(request(2, 0, 3), 12));
    auto rs = drain(m, 40);
    EXPECT_EQ(count(rs, Kind::Replay), 0);
    EXPECT_EQ(count(rs, Kind::DataBeat), 2);
    EXPECT_EQ(returnedData(rs), fill.data);
}

TEST(SlcSfModelTest, ReplayedFillStillReservesItsRawH5WayForNextAccess)
{
    SlcSfModel m(cmn());
    auto a = request(1, 0, 0x1b); a.op = SlcSfOp::Fill;
    a.state = {SlcMesi::Exclusive, true}; a.data = pattern(4);
    a.victimConflict = true;
    auto b = a; b.token = 2; b.addr = 1024 * 64; b.victimConflict = false;
    b.data = pattern(41);
    ASSERT_TRUE(m.tryAccept(a, 0));
    ASSERT_TRUE(m.tryAccept(b, 2));
    auto rs = drain(m, 40);
    EXPECT_EQ(count(rs, Kind::Replay), 1);
    EXPECT_EQ(count(rs, Kind::CommitDone), 1);
    EXPECT_FALSE(m.findSlc(0));
    EXPECT_EQ(m.findSlc(b.addr), &m.l3TagRam().at(0, 1));
    EXPECT_TRUE(std::equal(b.data.begin(), b.data.end(),
                          m.l3DataRam().at(0, 1).bytes.begin()));
}

TEST(SlcSfModelTest, NonAllocatingMissReservesEmptySfWayForNextAllocation)
{
    SlcSfModel m(cmn());
    ASSERT_TRUE(m.tryAccept(request(1, 0, 3), 0));
    ASSERT_TRUE(m.tryAccept(request(2, 4096 * 64), 2));
    auto rs = drain(m, 40);
    EXPECT_EQ(count(rs, Kind::Replay), 0);
    EXPECT_EQ(m.findSf(4096 * 64), &m.sfTagRam().at(0, 1));
    EXPECT_FALSE(m.pendingSeq());
}

TEST(SlcSfModelTest, PocVictimCollisionSamplesH3RatherThanAdmissionOrH6)
{
    // Buffer appears after H1; disappears before H6; or appears after H3.
    // Only occupancy at H3 (+2) determines the registered replay result.
    for (unsigned scenario = 0; scenario < 4; ++scenario) {
        SlcSfModel m(small());
        PocqVictimBuffer buffers;
        unsigned queries = 0;
        m.setVictimConflictQuery([&](const SlcSfRequest &r, uint64_t cycle) {
            ++queries;
            EXPECT_EQ(cycle, 2);
            return buffers.conflicts(PocqVictimBuffer::ownerKey(r.srcid, r.txnid),
                                     r.addr, r.ns, cycle);
        });
        ASSERT_TRUE(m.tryAccept(request(1, 0, 3), 0));
        m.advance(1);
        if (scenario != 2) buffers.allocate(10, 1, 0, true, 1);
        if (scenario == 1) buffers.release(10, 1); // Gone by H3.
        m.advance(3);
        if (scenario == 2) buffers.allocate(10, 1, 0, true, 3); // Too late.
        if (scenario == 3) buffers.release(10, 3); // H3 evidence must survive.
        auto results = drain(m, 20);
        EXPECT_EQ(queries, 1);
        EXPECT_EQ(count(results, Kind::Replay), scenario == 0 || scenario == 3);
        if (scenario == 0 || scenario == 3) {
            ASSERT_EQ(results.size(), 1);
            EXPECT_EQ(results[0].replay, SlcSfReplay::VictimConflict);
        }
    }
}

TEST(SlcSfModelTest, PocVictimBufferSnapshotsIsolateNsAndOwner)
{
    PocqVictimBuffer buffers;
    buffers.allocate(1, 99, 0x1020, true, 0);
    EXPECT_FALSE(buffers.conflicts(0, 0x1000, true, 0));
    EXPECT_TRUE(buffers.conflicts(0, 0x1000, true, 1));
    EXPECT_FALSE(buffers.conflicts(0, 0x1000, false, 1));
    EXPECT_FALSE(buffers.conflicts(99, 0x1000, true, 1));
    EXPECT_FALSE(buffers.conflicts(0, 0x1040, true, 1));
    buffers.release(1, 3);
    EXPECT_TRUE(buffers.conflicts(0, 0x1000, true, 3));
    EXPECT_FALSE(buffers.conflicts(0, 0x1000, true, 4));
    // Historical H3 occupancy remains available after the live buffer clears.
    EXPECT_TRUE(buffers.conflicts(0, 0x1000, true, 2));
}

TEST(SlcSfModelTest, StripedHomesReachEverySetAndRejectOtherBanks)
{
    for (unsigned bank = 0; bank < 2; ++bank) {
        auto cfg = small(1); cfg.homeCount = 2; cfg.homeIndex = bank;
        SlcSfModel model(cfg);
        auto physical = [bank](unsigned line) { return (line * 2 + bank) * 64; };
        for (unsigned set = 0; set < 4; ++set) {
            const auto addr = physical(8 + set);
            auto fill = request(set + 1, addr + 23, 0x1b);
            fill.op = SlcSfOp::Fill; fill.state = {SlcMesi::Modified, true};
            fill.data = pattern(set);
            ASSERT_EQ(count(transact(model, fill), Kind::CommitDone), 1);
            model.preloadSf(addr + 7, false, {SlcMesi::Exclusive, true},
                            1, {false, true, false, false});
        }
        for (unsigned set = 0; set < 4; ++set) {
            const auto addr = physical(8 + set);
            ASSERT_NE(model.findSlc(addr), nullptr);
            ASSERT_NE(model.findSf(addr, false), nullptr);
            EXPECT_EQ(model.l3TagRam().at(set, 0).paTag, 2);
            EXPECT_EQ(model.sfTagRam().at(set, 0).paTag, 2);
            EXPECT_EQ(model.l3DataRam().at(set, 0).bytes[0], pattern(set)[0]);
            auto flush = request(20 + set, addr + 31);
            flush.op = SlcSfOp::FlushBoth;
            ASSERT_EQ(count(transact(model, flush), Kind::CommitDone), 1);
            EXPECT_EQ(model.findSlc(addr), nullptr);
            // NS remains independent of the removed home-interleave bits.
            ASSERT_NE(model.findSf(addr, false), nullptr);
            flush.token += 10; flush.ns = false;
            ASSERT_EQ(count(transact(model, flush), Kind::CommitDone), 1);
            EXPECT_EQ(model.findSf(addr, false), nullptr);
        }
        const auto wrong = physical(8) ^ 64;
        EXPECT_THROW(model.tryAccept(request(99, wrong)), std::invalid_argument);
        EXPECT_THROW(model.findSlc(wrong), std::invalid_argument);
        EXPECT_THROW(model.findSf(wrong), std::invalid_argument);
        EXPECT_THROW(model.preloadSlc(wrong, true, {}, 0, pattern(0)),
                     std::invalid_argument);
        EXPECT_THROW(model.preloadSf(wrong, true, {}, 0,
                                     {false, false, false, false}),
                     std::invalid_argument);
        std::stringstream state;
        model.save(state);
        auto otherCfg = cfg; otherCfg.homeIndex ^= 1;
        SlcSfModel other(otherCfg);
        EXPECT_THROW(other.load(state), std::runtime_error);
        std::stringstream sameState(state.str()), roundtrip;
        SlcSfModel restored(cfg); restored.load(sameState); restored.save(roundtrip);
        EXPECT_EQ(state.str(), roundtrip.str());
    }
    auto invalid = small(); invalid.homeCount = 0;
    EXPECT_THROW(SlcSfModel model(invalid), std::invalid_argument);
    invalid.homeCount = 2; invalid.homeIndex = 2;
    EXPECT_THROW(SlcSfModel model(invalid), std::invalid_argument);
}

TEST(SlcSfModelTest, StripedDirtyAndSfVictimsKeepTheirPhysicalAddresses)
{
    for (unsigned bank = 0; bank < 2; ++bank) {
        auto cfg = small(1); cfg.homeCount = 2; cfg.homeIndex = bank;
        SlcSfModel model(cfg);
        const uint64_t oldAddr = (11 * 2 + bank) * 64;
        const uint64_t newAddr = (15 * 2 + bank) * 64;
        model.preloadSlc(oldAddr, false, {SlcMesi::Modified, true}, 1, pattern(73));
        model.preloadSf(oldAddr, false, {SlcMesi::Exclusive, true}, 1,
                        {false, true, false, false});
        auto fill = request(1, newAddr, 0x1b);
        fill.op = SlcSfOp::Fill; fill.state = {SlcMesi::Modified, true};
        fill.data = pattern(129);
        auto results = transact(model, fill);
        ASSERT_EQ(count(results, Kind::LookupDone), 1);
        EXPECT_TRUE(results[0].lookup.victim);
        EXPECT_EQ(results[0].lookup.victimAddr, oldAddr);
        EXPECT_FALSE(results[0].lookup.victimNs);
        EXPECT_EQ(returnedData(results), pattern(73));
        ASSERT_NE(model.findSlc(newAddr), nullptr);
        EXPECT_EQ(model.findSlc(oldAddr, false), nullptr);
        EXPECT_EQ(model.l3DataRam().at(3, 0).bytes[0], pattern(129)[0]);
        results = transact(model, request(2, newAddr));
        EXPECT_EQ(returnedData(results), pattern(129));
        auto victim = model.pendingSeq();
        ASSERT_TRUE(victim);
        EXPECT_EQ(victim->addr, oldAddr);
        EXPECT_FALSE(victim->ns);
        EXPECT_TRUE(victim->targets[1]);
    }
}

TEST(SlcSfModelTest, CheckpointPreservesDirtyLinesDirectoryAndReplacement)
{
    SlcSfModel original(small());
    for (unsigned s = 0; s < 4; ++s) {
        original.preloadSlc(s * 64, true, {SlcMesi::Modified, false},
                            s, pattern(s), 0);
        original.preloadSlc((s + 4) * 64, false,
                            {SlcMesi::Exclusive, true}, s, pattern(s + 4), 1);
        original.preloadSf(s * 64, true, {SlcMesi::Exclusive, false},
                           s, {true, false, true, false}, 0);
    }
    auto fill = request(1, 8 * 64, 0x1b);
    fill.op = SlcSfOp::Fill;
    fill.state = {SlcMesi::Modified, true}; fill.data = pattern(99);
    ASSERT_EQ(count(transact(original, fill), Kind::CommitDone), 1);
    ASSERT_TRUE(original.drained());
    std::stringstream state;
    original.save(state);
    SlcSfModel restored(small());
    restored.load(state);
    std::stringstream roundtrip;
    restored.save(roundtrip);
    EXPECT_EQ(roundtrip.str(), state.str());
    // Continue replacements on both models. Their returned dirty victim
    // bytes, exact result edges and complete future snapshots must agree.
    for (unsigned i = 9; i < 20; ++i) {
        fill.token = i; fill.addr = i * 64; fill.data = pattern(i);
        const auto left = transact(original, fill);
        const auto right = transact(restored, fill);
        ASSERT_EQ(left.size(), right.size());
        for (unsigned n = 0; n < left.size(); ++n) {
            EXPECT_EQ(left[n].cycle, right[n].cycle);
            EXPECT_EQ(left[n].kind, right[n].kind);
            EXPECT_EQ(left[n].data, right[n].data);
            EXPECT_EQ(left[n].lookup.victimAddr, right[n].lookup.victimAddr);
        }
        std::stringstream a, b;
        original.save(a); restored.save(b);
        EXPECT_EQ(a.str(), b.str());
    }
}

TEST(SlcSfModelTest, CheckpointRequiresExpiredHistoryAndCompatibleGeometry)
{
    SlcSfModel model(small());
    ASSERT_TRUE(model.tryAccept(request(1, 0, 3), 0));
    std::stringstream state;
    EXPECT_THROW(model.save(state), std::logic_error);
    drain(model, 14);
    EXPECT_FALSE(model.hasWork());
    EXPECT_FALSE(model.drained());
    model.advance(15);
    ASSERT_TRUE(model.drained());
    model.save(state);
    SlcSfModel incompatible(small(4));
    EXPECT_THROW(incompatible.load(state), std::runtime_error);
    std::stringstream truncated(state.str().substr(0, 12));
    SlcSfModel fresh(small());
    EXPECT_THROW(fresh.load(truncated), std::runtime_error);
}

} // anonymous namespace
} // namespace gem5::Chi
