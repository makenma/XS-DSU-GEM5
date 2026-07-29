#include <algorithm>
#include <cstdint>

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
