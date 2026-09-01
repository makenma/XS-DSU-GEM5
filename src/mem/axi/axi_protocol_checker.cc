#include "mem/axi/axi_protocol_checker.hh"

#include <algorithm>
#include <sstream>

namespace gem5
{
namespace axi
{

namespace
{

uint64_t
independentDigest(const std::vector<uint8_t> &data)
{
    uint64_t digest = 1469598103934665603ULL;
    for (const uint8_t byte : data) {
        digest ^= byte;
        digest *= 1099511628211ULL;
    }
    return digest;
}

} // anonymous namespace

bool
AxiProtocolChecker::fail(const std::string &message)
{
    if (_error.empty())
        _error = message;
    return false;
}

bool
AxiProtocolChecker::addExpected(const AxiCheckerTransaction &transaction)
{
    if (!okay())
        return false;
    if (transaction.beatCount == 0 || transaction.beatCount > 256)
        return fail("checker expectation has invalid beatCount");
    if (transaction.response == AxiResp::ExOkay)
        return fail("checker expectation requests forbidden EXOKAY");
    if (transaction.expectedData.size() != transaction.beatCount)
        return fail("checker expectation data count differs from beatCount");
    State state;
    state.expected = transaction;
    state.beatsSeen.assign(transaction.beatCount, false);
    if (!_states.emplace(transaction.meta.txnUid, std::move(state)).second)
        return fail("checker has duplicate expected txnUid");
    return true;
}

bool
AxiProtocolChecker::checkMeta(const AxiCommonMeta &actual,
                              const AxiCommonMeta &expected)
{
    if (actual.txnUid != expected.txnUid ||
        actual.targetSeq != expected.targetSeq ||
        actual.responseSeq != expected.responseSeq ||
        actual.srcNode != expected.srcNode ||
        actual.srcPort != expected.srcPort ||
        actual.dstNode != expected.dstNode ||
        actual.axiId != expected.axiId) {
        return fail("checker observed conflicting immutable metadata");
    }
    return true;
}

bool
AxiProtocolChecker::checkData(
    const AxiCheckerEvent &event, const std::vector<uint8_t> &expected)
{
    if (event.functionalData != expected)
        return fail("checker observed incorrect functional data");
    if (event.payloadDigest != independentDigest(event.functionalData))
        return fail("checker observed incorrect payload digest");
    return true;
}

bool
AxiProtocolChecker::retire(const State &state)
{
    const auto &meta = state.expected.meta;
    const RetireKey key{
        meta.srcNode, meta.srcPort, meta.axiId, state.expected.read};
    uint64_t &next = _nextResponseSeq[key];
    if (meta.responseSeq != next) {
        std::ostringstream out;
        out << "checker responseSeq out of order: expected " << next
            << " observed " << meta.responseSeq;
        return fail(out.str());
    }
    ++next;
    return true;
}

bool
AxiProtocolChecker::observe(const AxiCheckerEvent &event)
{
    if (!okay())
        return false;
    auto found = _states.find(event.meta.txnUid);
    if (found == _states.end())
        return fail("checker observed unknown txnUid");
    State &state = found->second;
    if (!checkMeta(event.meta, state.expected.meta))
        return false;
    if (event.beatCount != state.expected.beatCount)
        return fail("checker observed incorrect beatCount");

    const bool address_channel =
        event.channel == AxiChannel::Aw || event.channel == AxiChannel::Ar;
    const bool data_channel =
        event.channel == AxiChannel::W || event.channel == AxiChannel::R;
    if (address_channel) {
        const AxiChannel expected = state.expected.read ?
            AxiChannel::Ar : AxiChannel::Aw;
        if (event.channel != expected)
            return fail("checker observed address on wrong direction");
        if (state.addressSeen)
            return fail("checker observed duplicate address packet");
        state.addressSeen = true;
        return true;
    }

    if (data_channel) {
        const AxiChannel expected = state.expected.read ?
            AxiChannel::R : AxiChannel::W;
        if (event.channel != expected)
            return fail("checker observed data on wrong direction");
        if (event.beatIndex >= state.expected.beatCount)
            return fail("checker observed out-of-range beat index");
        if (state.beatsSeen[event.beatIndex])
            return fail("checker observed duplicate beat index");
        const bool expected_last =
            event.beatIndex + 1 == state.expected.beatCount;
        if (event.last != expected_last)
            return fail("checker observed invalid LAST position");
        if (!checkData(event,
                       state.expected.expectedData[event.beatIndex]))
            return false;
        if (state.expected.read && event.response != state.expected.response)
            return fail("checker observed incorrect R response");
        state.beatsSeen[event.beatIndex] = true;
        if (state.expected.read && event.last) {
            if (state.responseSeen)
                return fail("checker observed duplicate R completion");
            state.responseSeen = true;
            return retire(state);
        }
        return true;
    }

    if (event.channel != AxiChannel::B || state.expected.read)
        return fail("checker observed response on wrong direction");
    if (state.responseSeen)
        return fail("checker observed duplicate B response");
    if (!state.addressSeen ||
        !std::all_of(state.beatsSeen.begin(), state.beatsSeen.end(),
                     [](bool seen) { return seen; })) {
        return fail("checker observed B before complete write");
    }
    if (event.response != state.expected.response)
        return fail("checker observed incorrect B response");
    state.responseSeen = true;
    return retire(state);
}

bool
AxiProtocolChecker::finish()
{
    if (!okay())
        return false;
    for (const auto &[uid, state] : _states) {
        (void)uid;
        if (!state.addressSeen)
            return fail("checker trace is missing address packet");
        if (!std::all_of(state.beatsSeen.begin(), state.beatsSeen.end(),
                         [](bool seen) { return seen; })) {
            return fail("checker trace is missing data beat");
        }
        if (!state.responseSeen)
            return fail("checker trace is missing response completion");
    }
    return true;
}

} // namespace axi
} // namespace gem5
