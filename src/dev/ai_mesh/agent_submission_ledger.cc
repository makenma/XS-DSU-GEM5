#include "dev/ai_mesh/agent_submission_ledger.hh"

#include <algorithm>
#include <stdexcept>

namespace gem5
{
namespace ai_mesh
{

SubmissionLedger::SubmissionLedger(uint32_t depth, size_t requestIdCapacity)
    : _geometry(depth), _requestIdCapacity(requestIdCapacity)
{
    if (_requestIdCapacity == 0)
        throw std::invalid_argument("request ID capacity must be positive");
}

std::optional<SqSeq>
SubmissionLedger::reserve(RequestId requestId)
{
    return reserveBatch({requestId});
}

std::optional<SqSeq>
SubmissionLedger::reserveBatch(const std::vector<RequestId> &requestIds)
{
    if (requestIds.empty() || hasPending() ||
        requestIds.size() > _requestIdCapacity - _issuedRequestIds.size())
        return std::nullopt;
    const uint64_t occupancy =
        _committedProducer.value() - _reusableHead.value();
    if (requestIds.size() > _geometry.depth() - occupancy)
        return std::nullopt;
    std::set<uint64_t> batch;
    for (const RequestId requestId : requestIds) {
        if (requestId.value() == 0 || wasIssued(requestId) ||
            !batch.insert(requestId.value()).second)
            return std::nullopt;
    }
    const auto next = checkedSequenceAdd(
        _committedProducer, requestIds.size());
    if (!next)
        return std::nullopt;
    const SqSeq slotSequence = _committedProducer;
    _issuedRequestIds.insert(batch.begin(), batch.end());
    _tentativeProducer = *next;
    _pending = PendingSqPublication{
        slotSequence, *next, requestIds, false, false};
    return slotSequence;
}

bool
SubmissionLedger::observeHead(SqSeq head)
{
    const SqSeq upper = _tentativeProducer;
    if (head < _observedHead || upper < head)
        return false;
    _observedHead = head;
    if (_pending && _pending->base < head)
        _pending->headEvidence = true;
    updateReusableHead();
    return true;
}

bool
SubmissionLedger::observeCompletion(SqSeq sequence)
{
    if (!_pending || sequence < _pending->base ||
        _pending->tail <= sequence)
        return false;
    _pending->completionEvidence = true;
    return true;
}

PublicationResolution
SubmissionLedger::resolve(axi::AxiResp response, bool provenNoSideEffect)
{
    if (!_pending)
        return PublicationResolution::Fatal;

    const bool evidence = _pending->headEvidence ||
        _pending->completionEvidence;
    if (response == axi::AxiResp::Okay) {
        _committedProducer = _pending->tail;
        _tentativeProducer = _committedProducer;
        _pending.reset();
        updateReusableHead();
        return PublicationResolution::Committed;
    }

    if (provenNoSideEffect && !evidence) {
        _tentativeProducer = _pending->base;
        _pending.reset();
        updateReusableHead();
        return PublicationResolution::RolledBack;
    }

    return PublicationResolution::Fatal;
}

void
SubmissionLedger::updateReusableHead()
{
    _reusableHead = _observedHead < _committedProducer
        ? _observedHead : _committedProducer;
}

}
}
