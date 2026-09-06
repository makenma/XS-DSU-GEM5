#ifndef DEV_AI_MESH_GATE3_COMPLETION_LEDGER_HH
#define DEV_AI_MESH_GATE3_COMPLETION_LEDGER_HH

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <vector>

#include "dev/ai_mesh/agent_runtime_types.hh"
#include "mem/axi/axi_types.hh"

namespace gem5
{
namespace ai_mesh
{

enum class CompletionOwner : uint8_t
{
    Live,
    Retired,
    Fatal,
};

enum class MsiTerminal : uint8_t
{
    Pending,
    Okay,
    Error,
};

enum class AckDisposition : uint8_t
{
    Accepted,
    Duplicate,
    Stale,
    Future,
};

struct CompletionObligation
{
    CqObligationId id;
    SqSeq sqSequence;
    RequestId requestId;
    CompletionCookie cookie;
    std::optional<CqSeq> cqSequence;
    std::optional<Tick> terminalReadyTick;
    uint8_t effectiveQos = 0;
    uint64_t effectiveRequestId = 0;
    CompletionOwner owner = CompletionOwner::Live;
};

struct MsiNotifyRecord
{
    MsiIssueOrdinal issueOrdinal;
    CqSeq cqSequence;
    uint64_t tail = 0;
    uint32_t axiId = 0;
    MsiTerminal terminal = MsiTerminal::Pending;
};

class Gate3CompletionLedger
{
  public:
    explicit Gate3CompletionLedger(uint32_t cqDepth);

    std::optional<CqObligationId> reserve(
        SqSeq sqSequence, RequestId requestId, CompletionCookie cookie);
    bool markTerminalReady(CqObligationId id, Tick readyTick,
                           uint8_t effectiveQos,
                           uint64_t effectiveRequestId);
    std::optional<CqObligationId> nextTerminal(Tick now) const;
    std::optional<CqSeq> assign(CqObligationId id);
    bool issueMsi(CqObligationId id, uint32_t axiId);
    bool completeMsi(uint32_t axiId, axi::AxiResp response);
    AckDisposition classifyAck(uint64_t sequence) const;
    AckDisposition acceptAck(uint64_t sequence);
    std::vector<CqObligationId> retirable() const;
    bool retire(CqObligationId id);
    void transferLiveToFatal();

    const CompletionObligation *find(CqObligationId id) const;
    std::vector<CompletionObligation> fatalObligations() const;
    const std::map<uint64_t, MsiNotifyRecord> &msiRecords() const
    { return _msiRecords; }
    uint64_t producerSequence() const { return _nextCqSequence.value(); }
    uint64_t issuedSequence() const { return _issuedSequence; }
    uint64_t notifiedSequence() const { return _notifiedSequence; }
    uint64_t ackReceivedSequence() const { return _ackReceivedSequence; }
    size_t liveCount() const;
    size_t retiredCount() const;
    size_t fatalCount() const;
    size_t msiRobEntries() const;

  private:
    CompletionObligation *findMutable(CqObligationId id);
    void advanceNotifiedPrefix();

    bool _fatalCut = false;
    uint32_t _cqDepth;
    CqSeq _nextCqSequence;
    uint64_t _nextObligationId = 1;
    uint64_t _nextMsiOrdinal = 1;
    uint64_t _issuedSequence = 0;
    uint64_t _notifiedSequence = 0;
    uint64_t _ackReceivedSequence = 0;
    std::map<uint64_t, CompletionObligation> _obligations;
    std::map<uint64_t, MsiNotifyRecord> _msiRecords;
    std::map<uint32_t, uint64_t> _liveMsiIds;
};

}
}

#endif
