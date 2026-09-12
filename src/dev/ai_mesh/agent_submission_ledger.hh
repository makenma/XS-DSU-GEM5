#ifndef DEV_AI_MESH_AGENT_SUBMISSION_LEDGER_HH
#define DEV_AI_MESH_AGENT_SUBMISSION_LEDGER_HH

#include <cstddef>
#include <cstdint>
#include <optional>
#include <set>
#include <vector>

#include "dev/ai_mesh/agent_ring_state.hh"
#include "mem/axi/axi_types.hh"

namespace gem5
{
namespace ai_mesh
{

struct PendingSqPublication
{
    SqSeq base;
    SqSeq tail;
    std::vector<RequestId> requestIds;
    bool headEvidence = false;
    bool completionEvidence = false;
};

enum class PublicationResolution : uint8_t
{
    Committed,
    RolledBack,
    Fatal,
};

class SubmissionLedger
{
  public:
    SubmissionLedger(uint32_t depth, size_t requestIdCapacity);

    std::optional<SqSeq> reserve(RequestId requestId);
    std::optional<SqSeq> reserveBatch(
        const std::vector<RequestId> &requestIds);
    bool observeHead(SqSeq head);
    bool observeCompletion(SqSeq sequence);
    PublicationResolution resolve(axi::AxiResp response,
                                   bool provenNoSideEffect = false);

    bool hasPending() const { return _pending.has_value(); }
    bool pendingCompletionEvidence() const
    { return _pending && _pending->completionEvidence; }
    const PendingSqPublication *pending() const
    { return _pending ? &*_pending : nullptr; }

    SqSeq tentativeProducer() const { return _tentativeProducer; }
    SqSeq committedProducer() const { return _committedProducer; }
    SqSeq observedHead() const { return _observedHead; }
    SqSeq reusableHead() const { return _reusableHead; }
    uint64_t occupancy() const
    { return _tentativeProducer.value() - _reusableHead.value(); }
    uint64_t available() const
    { return _geometry.depth() - occupancy(); }
    bool wasIssued(RequestId requestId) const
    { return _issuedRequestIds.count(requestId.value()) != 0; }
    uint32_t slot(SqSeq sequence) const
    { return _geometry.slot(sequence); }
    uint64_t generation(SqSeq sequence) const
    { return _geometry.generation(sequence); }

  private:
    void updateReusableHead();

    RingGeometry _geometry;
    size_t _requestIdCapacity;
    std::set<uint64_t> _issuedRequestIds;
    SqSeq _tentativeProducer;
    SqSeq _committedProducer;
    SqSeq _observedHead;
    SqSeq _reusableHead;
    std::optional<PendingSqPublication> _pending;
};

}
}

#endif
