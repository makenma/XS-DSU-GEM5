#include "dev/ai_mesh/gate3_completion_ledger.hh"

#include <algorithm>
#include <stdexcept>
#include <tuple>

namespace gem5
{
namespace ai_mesh
{

Gate3CompletionLedger::Gate3CompletionLedger(uint32_t cqDepth)
    : _cqDepth(cqDepth)
{
    if (_cqDepth == 0 || (_cqDepth & (_cqDepth - 1)) != 0)
        throw std::invalid_argument("CQ depth must be a power of two");
}

std::optional<CqObligationId>
Gate3CompletionLedger::reserve(
    SqSeq sqSequence, RequestId requestId, CompletionCookie cookie)
{
    if (_fatalCut || liveCount() >= _cqDepth || _nextObligationId == 0)
        return std::nullopt;
    const CqObligationId id(_nextObligationId++);
    _obligations.emplace(
        id.value(), CompletionObligation{
            id, sqSequence, requestId, cookie, std::nullopt, std::nullopt,
            0, 0,
            CompletionOwner::Live});
    return id;
}

bool
Gate3CompletionLedger::markTerminalReady(
    CqObligationId id, Tick readyTick, uint8_t effectiveQos,
    uint64_t effectiveRequestId)
{
    CompletionObligation *obligation = findMutable(id);
    if (!obligation || obligation->owner != CompletionOwner::Live ||
        obligation->terminalReadyTick || obligation->cqSequence)
        return false;
    obligation->terminalReadyTick = readyTick;
    obligation->effectiveQos = effectiveQos;
    obligation->effectiveRequestId = effectiveRequestId;
    return true;
}

std::optional<CqObligationId>
Gate3CompletionLedger::nextTerminal(Tick now) const
{
    const CompletionObligation *selected = nullptr;
    for (const auto &[id, obligation] : _obligations) {
        if (obligation.owner != CompletionOwner::Live ||
            !obligation.terminalReadyTick || obligation.cqSequence ||
            *obligation.terminalReadyTick > now)
            continue;
        const auto terminalKey = std::make_tuple(
            *obligation.terminalReadyTick,
            static_cast<uint8_t>(255 - obligation.effectiveQos),
            obligation.effectiveRequestId, obligation.sqSequence);
        const auto selectedKey = selected ? std::make_tuple(
            *selected->terminalReadyTick,
            static_cast<uint8_t>(255 - selected->effectiveQos),
            selected->effectiveRequestId, selected->sqSequence) : terminalKey;
        if (!selected || terminalKey < selectedKey)
            selected = &obligation;
    }
    return selected ? std::optional<CqObligationId>(selected->id) :
        std::nullopt;
}

std::optional<CqSeq>
Gate3CompletionLedger::assign(CqObligationId id)
{
    CompletionObligation *obligation = findMutable(id);
    if (!obligation || obligation->owner != CompletionOwner::Live ||
        !obligation->terminalReadyTick || obligation->cqSequence)
        return std::nullopt;
    const auto next = checkedSequenceAdd(_nextCqSequence, 1);
    if (!next)
        return std::nullopt;
    obligation->cqSequence = _nextCqSequence;
    _nextCqSequence = *next;
    return obligation->cqSequence;
}

bool
Gate3CompletionLedger::issueMsi(CqObligationId id, uint32_t axiId)
{
    CompletionObligation *obligation = findMutable(id);
    if (!obligation || obligation->owner != CompletionOwner::Live ||
        !obligation->cqSequence || _liveMsiIds.count(axiId) != 0)
        return false;
    const auto tailValue = checkedSequenceAdd(*obligation->cqSequence, 1);
    if (!tailValue)
        return false;
    const uint64_t tail = tailValue->value();
    if (tail != _issuedSequence + 1 || _nextMsiOrdinal == 0)
        return false;
    const uint64_t ordinal = _nextMsiOrdinal++;
    _msiRecords.emplace(
        ordinal, MsiNotifyRecord{MsiIssueOrdinal(ordinal),
                                 *obligation->cqSequence, tail, axiId,
                                 MsiTerminal::Pending});
    _liveMsiIds.emplace(axiId, ordinal);
    _issuedSequence = tail;
    return true;
}

bool
Gate3CompletionLedger::completeMsi(uint32_t axiId, axi::AxiResp response)
{
    if (_fatalCut)
        return false;
    const auto id = _liveMsiIds.find(axiId);
    if (id == _liveMsiIds.end())
        return false;
    MsiNotifyRecord &record = _msiRecords.at(id->second);
    if (record.terminal != MsiTerminal::Pending)
        return false;
    record.terminal = response == axi::AxiResp::Okay ?
        MsiTerminal::Okay : MsiTerminal::Error;
    _liveMsiIds.erase(id);
    advanceNotifiedPrefix();
    return true;
}

AckDisposition
Gate3CompletionLedger::classifyAck(uint64_t sequence) const
{
    if (sequence < _ackReceivedSequence)
        return AckDisposition::Stale;
    if (sequence == _ackReceivedSequence)
        return AckDisposition::Duplicate;
    if (sequence > _issuedSequence)
        return AckDisposition::Future;
    return AckDisposition::Accepted;
}

AckDisposition
Gate3CompletionLedger::acceptAck(uint64_t sequence)
{
    if (_fatalCut)
        throw std::logic_error("ACK commit after fatal cut");
    const AckDisposition disposition = classifyAck(sequence);
    if (disposition != AckDisposition::Accepted)
        return disposition;
    _ackReceivedSequence = sequence;
    return AckDisposition::Accepted;
}

std::vector<CqObligationId>
Gate3CompletionLedger::retirable() const
{
    const uint64_t covered = std::min(
        _ackReceivedSequence, _notifiedSequence);
    std::vector<CqObligationId> result;
    for (const auto &[key, obligation] : _obligations) {
        if (obligation.owner == CompletionOwner::Live &&
            obligation.cqSequence &&
            obligation.cqSequence->value() + 1 <= covered)
            result.push_back(CqObligationId(key));
    }
    return result;
}

bool
Gate3CompletionLedger::retire(CqObligationId id)
{
    CompletionObligation *obligation = findMutable(id);
    if (!obligation || obligation->owner != CompletionOwner::Live)
        return false;
    const auto ready = retirable();
    if (std::find(ready.begin(), ready.end(), id) == ready.end())
        return false;
    obligation->owner = CompletionOwner::Retired;
    return true;
}

void
Gate3CompletionLedger::transferLiveToFatal()
{
    _fatalCut = true;
    for (auto &[key, obligation] : _obligations) {
        if (obligation.owner == CompletionOwner::Live)
            obligation.owner = CompletionOwner::Fatal;
    }
}

const CompletionObligation *
Gate3CompletionLedger::find(CqObligationId id) const
{
    const auto found = _obligations.find(id.value());
    return found == _obligations.end() ? nullptr : &found->second;
}

std::vector<CompletionObligation>
Gate3CompletionLedger::fatalObligations() const
{
    std::vector<CompletionObligation> result;
    for (const auto &[key, obligation] : _obligations) {
        if (obligation.owner == CompletionOwner::Fatal)
            result.push_back(obligation);
    }
    return result;
}

CompletionObligation *
Gate3CompletionLedger::findMutable(CqObligationId id)
{
    const auto found = _obligations.find(id.value());
    return found == _obligations.end() ? nullptr : &found->second;
}

void
Gate3CompletionLedger::advanceNotifiedPrefix()
{
    while (true) {
        const uint64_t ordinal = _notifiedSequence + 1;
        const auto found = _msiRecords.find(ordinal);
        if (found == _msiRecords.end() ||
            found->second.tail != ordinal ||
            found->second.terminal != MsiTerminal::Okay)
            return;
        _notifiedSequence = found->second.tail;
    }
}

size_t
Gate3CompletionLedger::liveCount() const
{
    return std::count_if(
        _obligations.begin(), _obligations.end(),
        [](const auto &item) {
            return item.second.owner == CompletionOwner::Live;
        });
}

size_t
Gate3CompletionLedger::retiredCount() const
{
    return std::count_if(
        _obligations.begin(), _obligations.end(),
        [](const auto &item) {
            return item.second.owner == CompletionOwner::Retired;
        });
}

size_t
Gate3CompletionLedger::fatalCount() const
{
    return std::count_if(
        _obligations.begin(), _obligations.end(),
        [](const auto &item) {
            return item.second.owner == CompletionOwner::Fatal;
        });
}

size_t
Gate3CompletionLedger::msiRobEntries() const
{
    return std::count_if(
        _msiRecords.begin(), _msiRecords.end(),
        [this](const auto &item) {
            return item.second.issueOrdinal.value() > _notifiedSequence;
        });
}

}
}
