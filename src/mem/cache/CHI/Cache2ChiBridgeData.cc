#include <algorithm>
#include <cstdint>
#include <limits>

#include "base/intmath.hh"
#include "base/logging.hh"
#include "mem/cache/CHI/Cache2ChiBridge.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"

namespace gem5::Chi
{

namespace
{

bool
isReadDataOpcode(uint8_t opcode)
{
    const auto decoded = decodeDat(opcode);
    return decoded.minor == DatMinor::CompData ||
           decoded.minor == DatMinor::DataSepResp;
}

bool
isDirtyResponse(uint8_t response)
{
    return response == 3 || response == 4 || response == 5;
}

} // anonymous namespace

uint32_t
Cache2ChiBridge::cmnHnfIndex(Addr address, size_t hnf_count,
                             uint8_t pa_bits)
{
    panic_if(hnf_count == 0 || hnf_count > 64 ||
                 !isPowerOf2(hnf_count),
             "CMN HNF count=%llu must be a power of two in [1, 64]\n",
             static_cast<unsigned long long>(hnf_count));
    panic_if(pa_bits < 6 || pa_bits > 64,
             "CMN HNF hash PA width=%u must be in [6, 64]\n", pa_bits);

    if (hnf_count == 1) {
        return 0;
    }

    const unsigned index_bits = floorLog2(hnf_count);
    uint32_t index = 0;
    for (unsigned output_bit = 0; output_bit < index_bits; ++output_bit) {
        unsigned parity = 0;
        for (unsigned bit = 6 + output_bit; bit < pa_bits;
             bit += index_bits) {
            parity ^= (address >> bit) & 1;
        }
        index |= parity << output_bit;
    }
    return index;
}

std::optional<uint32_t>
Cache2ChiBridge::allocateMonotonicTxnId(
    uint64_t& next_id, size_t outstanding, uint32_t max_outstanding,
    uint32_t namespace_count)
{
    if (outstanding >= max_outstanding || namespace_count == 0 ||
        next_id > std::numeric_limits<uint32_t>::max()) {
        return std::nullopt;
    }
    const uint32_t id = static_cast<uint32_t>(next_id);
    next_id += namespace_count;
    return id;
}

uint64_t
Cache2ChiBridge::firstTxnIdInNamespace(
    uint32_t base, uint32_t namespace_id, uint32_t namespace_count)
{
    panic_if(namespace_count == 0 || namespace_id >= namespace_count,
             "invalid Cache2ChiBridge TxnID namespace %u/%u\n",
             namespace_id, namespace_count);

    const uint64_t first = static_cast<uint64_t>(namespace_id) + 1;
    if (first > base) {
        return first;
    }
    const uint64_t epochs =
        (static_cast<uint64_t>(base) - first) / namespace_count + 1;
    return first + epochs * namespace_count;
}

Cache2ChiBridge::MemoryIntent
Cache2ChiBridge::failedScRefillIntent(bool needs_response)
{
    // A classic cache changes a raced SCUpgradeReq into
    // SCUpgradeFailReq.  The store-conditional has already failed, but the
    // cache still needs an exclusive data refill so it can satisfy later
    // snoops.  Preserve the original packet command: makeTimingResponse()
    // will then produce the required data-bearing UpgradeFailResp.
    MemoryIntent intent{};
    intent.kind = IntentKind::ReadUnique;
    intent.txnClass = TxnClass::Read;
    intent.needsResponse = needs_response;
    intent.expectsData = true;
    intent.expectsComp = true;
    intent.requiresCompAck = true;
    return intent;
}

Cache2ChiBridge::MemoryIntent
Cache2ChiBridge::cacheRespondingCoordinationIntent()
{
    MemoryIntent intent{};
    intent.kind = IntentKind::MakeUnique;
    intent.txnClass = TxnClass::Maintenance;
    intent.needsResponse = false;
    intent.expectsComp = true;
    return intent;
}

bool
Cache2ChiBridge::shouldPromoteUpgrade(MemCmd cmd, bool cache_responding)
{
    return !cache_responding &&
        (cmd == MemCmd::UpgradeReq || cmd == MemCmd::SCUpgradeReq);
}

bool
Cache2ChiBridge::advancePendingSnoopResponse(
    SnoopEntry& snoop,
    const std::function<bool(ChannelType, const FlitVariant&)>& enqueue)
{
    const bool has_rsp = snoop.pendingRsp.has_value();
    const bool has_data = !snoop.dataBeats.empty();
    panic_if(has_rsp == has_data,
             "Cache2ChiBridge snoop txnid=%u must own exactly one pending "
             "RSP or DAT response\n",
             snoop.snp.txnid);

    if (has_rsp) {
        if (!enqueue(RSP, FlitVariant{*snoop.pendingRsp})) {
            return false;
        }
        snoop.pendingRsp.reset();
        return true;
    }

    panic_if(snoop.nextDataBeat > snoop.dataBeats.size(),
             "Cache2ChiBridge snoop txnid=%u has invalid next DAT beat=%u/%u\n",
             snoop.snp.txnid,
             static_cast<unsigned>(snoop.nextDataBeat),
             static_cast<unsigned>(snoop.dataBeats.size()));
    while (snoop.nextDataBeat < snoop.dataBeats.size()) {
        const RawDat& dat = snoop.dataBeats[snoop.nextDataBeat];
        if (!enqueue(DAT, FlitVariant{dat})) {
            return false;
        }
        ++snoop.nextDataBeat;
    }
    return true;
}

bool
Cache2ChiBridge::advanceTxnData(
    TxnEntry& txn,
    const std::function<bool(ChannelType, const FlitVariant&)>& enqueue)
{
    if (!txn.dataBeats.empty() && !txn.hasDbid) {
        return false;
    }

    while (txn.nextDataBeat < txn.dataBeats.size()) {
        RawDat dat = txn.dataBeats[txn.nextDataBeat];
        dat.dbid = txn.dbid;
        if (!enqueue(DAT, FlitVariant{dat})) {
            return false;
        }
        ++txn.nextDataBeat;
    }

    return true;
}

bool
Cache2ChiBridge::txnDataPending(const TxnEntry& txn)
{
    return txn.hasDbid && txn.nextDataBeat < txn.dataBeats.size();
}

bool
Cache2ChiBridge::advanceUncacheableBypass(
    PacketPtr pkt, bool& blocked,
    const std::function<bool(PacketPtr)>& send)
{
    // A timing RequestPort must not send again after rejection until its
    // peer calls recvReqRetry().  The pump can still run for unrelated CHI
    // work, so enforce the gate at the actual send site as well.
    if (blocked) {
        return false;
    }
    if (!send(pkt)) {
        blocked = true;
        return false;
    }
    return true;
}

bool
Cache2ChiBridge::acceptReadDataBeat(
    TxnEntry& txn, const RawDat& dat, uint32_t data_beat_bytes,
    const char* owner_name)
{
    panic_if(data_beat_bytes == 0,
             "%s: DAT txnid=%u has zero beat geometry\n",
             owner_name, dat.txnid);
    panic_if(!txn.intent.expectsData || !isReadDataOpcode(dat.opcode),
             "%s: invalid incoming DAT opcode=0x%x txnid=%u for intent=%u\n",
             owner_name, dat.opcode, dat.txnid,
             static_cast<unsigned>(txn.intent.kind));
    panic_if(dat.srcid != txn.req.tgtid || dat.tgtid != txn.req.srcid ||
                 dat.HomeNID != txn.req.tgtid || dat.dbid != 0 ||
                 dat.qos != txn.req.qos,
             "%s: DAT identity mismatch txnid=%u src=%u/%u tgt=%u/%u "
             "home=%u dbid=%u qos=%u/%u\n",
             owner_name, dat.txnid, dat.srcid, txn.req.tgtid, dat.tgtid,
             txn.req.srcid, dat.HomeNID, dat.dbid, dat.qos, txn.req.qos);
    panic_if(txn.intent.forceCleanResponse && isDirtyResponse(dat.resp),
             "%s: ReadCleanReq mapped as ReadShared got dirty CHI data "
             "response state %u\n", owner_name, dat.resp);

    const uint32_t expected = txn.readExpectedBytes;
    panic_if(expected == 0,
             "%s: read txnid=%u has zero expected bytes\n",
             owner_name, txn.txnid);
    if (txn.readCoverage.empty()) {
        txn.readData.assign(expected, 0);
        txn.readCoverage.assign(expected, 0);
        txn.seenReadDataIds.assign(UINT8_MAX + 1, 0);
    }
    const uint32_t beat_count =
        1 + (expected - 1) / data_beat_bytes;
    panic_if(txn.gotData || dat.data.empty() || beat_count == 0 ||
                 beat_count > UINT8_MAX + 1 || dat.dataid >= beat_count ||
                 txn.seenReadDataIds[dat.dataid],
             "%s: DAT DataID mismatch txnid=%u dataid=%u beats=%u "
             "duplicate=%u complete=%u\n",
             owner_name, txn.txnid, dat.dataid, beat_count,
             txn.seenReadDataIds[dat.dataid], txn.gotData);
    const uint32_t expected_offset = dat.dataid * data_beat_bytes;
    const uint32_t expected_beat_bytes =
        std::min(data_beat_bytes, expected - expected_offset);
    panic_if(dat.beatOffset != expected_offset ||
                 dat.data.size() != expected_beat_bytes,
             "%s: DAT geometry mismatch txnid=%u dataid=%u offset=%u/%u "
             "bytes=%u/%u\n",
             owner_name, txn.txnid, dat.dataid, dat.beatOffset,
             expected_offset, static_cast<unsigned>(dat.data.size()),
             expected_beat_bytes);
    panic_if(dat.byteEnable.size() != dat.data.size() ||
                 dat.chunkValid.size() != (dat.data.size() + 7) / 8 ||
                 std::any_of(dat.byteEnable.begin(), dat.byteEnable.end(),
                             [](uint8_t byte) { return byte != 1; }) ||
                 std::any_of(dat.chunkValid.begin(), dat.chunkValid.end(),
                             [](uint8_t chunk) { return chunk != 1; }),
             "%s: DAT mask mismatch txnid=%u bytes=%u be=%u chunks=%u\n",
             owner_name, txn.txnid,
             static_cast<unsigned>(dat.data.size()),
             static_cast<unsigned>(dat.byteEnable.size()),
             static_cast<unsigned>(dat.chunkValid.size()));
    if (txn.readResp) {
        panic_if(*txn.readResp != dat.resp,
                 "%s: DAT response changed txnid=%u %u->%u\n",
                 owner_name, txn.txnid, *txn.readResp, dat.resp);
    }
    for (uint32_t i = 0; i < dat.data.size(); ++i) {
        panic_if(txn.readCoverage[dat.beatOffset + i],
                 "%s: DAT overlaps txnid=%u byte=%u\n", owner_name,
                 txn.txnid, dat.beatOffset + i);
    }

    const uint32_t covered_after =
        txn.readDataBytes + static_cast<uint32_t>(dat.data.size());
    const bool terminal_beat = dat.dataid + 1 == beat_count;
    panic_if(static_cast<bool>(dat.last) != terminal_beat,
             "%s: DAT last=%u disagrees with dataid=%u, terminal=%u "
             "txnid=%u\n",
             owner_name, dat.last, dat.dataid, beat_count - 1, txn.txnid);
    const bool saw_last_after = txn.sawReadDataLast || dat.last;
    const bool complete = saw_last_after && covered_after == expected;

    std::copy(dat.data.begin(), dat.data.end(),
              txn.readData.begin() + dat.beatOffset);
    std::fill(txn.readCoverage.begin() + dat.beatOffset,
              txn.readCoverage.begin() + dat.beatOffset + dat.data.size(), 1);
    txn.readDataBytes = covered_after;
    txn.seenReadDataIds[dat.dataid] = 1;
    txn.sawReadDataLast = saw_last_after;
    if (!txn.readResp) {
        txn.readResp = dat.resp;
    }
    txn.gotData = complete;
    txn.gotComp = true;
    return complete;
}

} // namespace gem5::Chi
