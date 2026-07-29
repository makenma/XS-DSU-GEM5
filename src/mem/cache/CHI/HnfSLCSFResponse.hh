#ifndef __HNF_SLCSF_RESPONSE_HH__
#define __HNF_SLCSF_RESPONSE_HH__

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <variant>

#include "base/types.hh"
#include "mem/cache/CHI/HnfSLCSFRequest.hh"

namespace gem5::Chi
{

enum class SlcSfOperationKind : uint8_t
{
    Lookup,
    CommitRead,
    FillCleanShared,
    CompleteMaintenance,
    RemoveSharer,
    WriteLine,
    FlushSf,
    FlushL3,
    WriteL3FlushSf,
    CompleteSfEvict,
    ReleaseDirtyVictim
};

enum class SlcSfTerminalStatus : uint8_t
{
    Done,
    Replay,
    Error
};

enum class SlcSfReplayReason : uint8_t
{
    StaleCommitToken,
    ResourceConflict,
    SeqConflict,
    VictimBufferFull,
    Cancelled
};

enum class SlcSfErrorCode : uint8_t
{
    InvalidRequest,
    InvariantViolation,
    UnknownCompletion
};

struct SlcSfLookupResponse
{
    HnfSlcLookupResult result;
    SlcSfCommitToken token;
};

struct SlcSfFillResponse
{
    SlcSfUpdateKind updateKind = SlcSfUpdateKind::CommitRead;
    std::optional<SlcSfSlcVictim> slcVictim;
    std::optional<SlcSfSfVictim> sfVictim;
};

struct SlcSfUpdateResponse
{
    SlcSfUpdateKind updateKind = SlcSfUpdateKind::CompleteMaintenance;
    std::optional<SlcSfSfVictim> sfVictim;
};

struct SlcSfEvictResponse
{
    SlcSfUpdateKind updateKind = SlcSfUpdateKind::FlushSf;
    std::optional<SlcSfSfVictim> sfVictim;
};

struct SlcSfReplay
{
    SlcSfReplayReason reason = SlcSfReplayReason::ResourceConflict;
    Tick retryNotBeforeTick = 0;
    bool redoLookup = false;
};

struct SlcSfError
{
    SlcSfErrorCode code = SlcSfErrorCode::InvalidRequest;
    std::string description;
};

using SlcSfResponsePayload =
    std::variant<SlcSfLookupResponse, SlcSfFillResponse,
                 SlcSfUpdateResponse, SlcSfEvictResponse, SlcSfReplay,
                 SlcSfError>;

inline SlcSfOperationKind
slcSfResponseOperation(const SlcSfLookupReq&)
{
    return SlcSfOperationKind::Lookup;
}

inline SlcSfOperationKind
slcSfResponseOperation(SlcSfUpdateKind kind)
{
    switch (kind) {
      case SlcSfUpdateKind::CommitRead:
        return SlcSfOperationKind::CommitRead;
      case SlcSfUpdateKind::FillCleanShared:
        return SlcSfOperationKind::FillCleanShared;
      case SlcSfUpdateKind::CompleteMaintenance:
        return SlcSfOperationKind::CompleteMaintenance;
      case SlcSfUpdateKind::RemoveSharer:
        return SlcSfOperationKind::RemoveSharer;
      case SlcSfUpdateKind::WriteLine:
        return SlcSfOperationKind::WriteLine;
      case SlcSfUpdateKind::FlushSf:
        return SlcSfOperationKind::FlushSf;
      case SlcSfUpdateKind::FlushL3:
        return SlcSfOperationKind::FlushL3;
      case SlcSfUpdateKind::WriteL3FlushSf:
        return SlcSfOperationKind::WriteL3FlushSf;
      case SlcSfUpdateKind::CompleteSfEvict:
        return SlcSfOperationKind::CompleteSfEvict;
      case SlcSfUpdateKind::ReleaseDirtyVictim:
        return SlcSfOperationKind::ReleaseDirtyVictim;
    }
    throw std::logic_error("unknown SLCSF update operation");
}

inline SlcSfOperationKind
slcSfResponseOperation(const SlcSfFillReq& request)
{
    return slcSfResponseOperation(request.kind());
}

inline SlcSfOperationKind
slcSfResponseOperation(const SlcSfUpdateReq& request)
{
    return slcSfResponseOperation(request.kind());
}

inline SlcSfOperationKind
slcSfResponseOperation(const SlcSfEvictReq& request)
{
    return slcSfResponseOperation(request.kind());
}

/**
 * One self-contained terminal completion.
 *
 * Construction is restricted to the factories below so status, operation,
 * and payload cannot describe different terminal outcomes.
 */
class SlcSfResponse
{
  public:
    SlcSfReqId reqId() const { return requestId; }
    uint32_t pocEntryId() const { return pocId; }
    SlcSfOperationKind operationKind() const { return operation; }
    SlcSfTerminalStatus status() const { return terminalStatus; }
    const SlcSfResponsePayload& payload() const { return responsePayload; }

    static SlcSfResponse done(const SlcSfLookupReq& request,
                              HnfSlcLookupResult result,
                              SlcSfCommitToken token)
    {
        return SlcSfResponse(
            request.header, slcSfResponseOperation(request),
            SlcSfTerminalStatus::Done,
            SlcSfLookupResponse{std::move(result), std::move(token)});
    }

    static SlcSfResponse done(
        const SlcSfFillReq& request,
        std::optional<SlcSfSlcVictim> slc_victim = std::nullopt,
        std::optional<SlcSfSfVictim> sf_victim = std::nullopt)
    {
        return SlcSfResponse(
            request.header, slcSfResponseOperation(request),
            SlcSfTerminalStatus::Done,
            SlcSfFillResponse{
                request.kind(), std::move(slc_victim),
                std::move(sf_victim)});
    }

    static SlcSfResponse done(
        const SlcSfUpdateReq& request,
        std::optional<SlcSfSfVictim> sf_victim = std::nullopt)
    {
        return SlcSfResponse(
            request.header, slcSfResponseOperation(request),
            SlcSfTerminalStatus::Done,
            SlcSfUpdateResponse{request.kind(), std::move(sf_victim)});
    }

    static SlcSfResponse done(
        const SlcSfEvictReq& request,
        std::optional<SlcSfSfVictim> sf_victim = std::nullopt)
    {
        return SlcSfResponse(
            request.header, slcSfResponseOperation(request),
            SlcSfTerminalStatus::Done,
            SlcSfEvictResponse{request.kind(), std::move(sf_victim)});
    }

    template <class Request>
    static SlcSfResponse replay(const Request& request, SlcSfReplay replay)
    {
        return SlcSfResponse(
            request.header, slcSfResponseOperation(request),
            SlcSfTerminalStatus::Replay, std::move(replay));
    }

    template <class Request>
    static SlcSfResponse error(const Request& request, SlcSfError error)
    {
        return SlcSfResponse(
            request.header, slcSfResponseOperation(request),
            SlcSfTerminalStatus::Error, std::move(error));
    }

  private:
    SlcSfResponse(const SlcSfReqHeader& header,
                  SlcSfOperationKind operation_kind,
                  SlcSfTerminalStatus status, SlcSfResponsePayload payload)
        : requestId(header.reqId), pocId(header.pocEntryId),
          operation(operation_kind),
          terminalStatus(status), responsePayload(std::move(payload))
    {}

    SlcSfReqId requestId{};
    uint32_t pocId = 0;
    SlcSfOperationKind operation = SlcSfOperationKind::Lookup;
    SlcSfTerminalStatus terminalStatus = SlcSfTerminalStatus::Error;
    SlcSfResponsePayload responsePayload;
};

inline SlcSfResponse
makeSlcSfDoneResponse(const SlcSfLookupReq& request,
                      HnfSlcLookupResult result, SlcSfCommitToken token)
{
    return SlcSfResponse::done(
        request, std::move(result), std::move(token));
}

inline SlcSfResponse
makeSlcSfDoneResponse(
    const SlcSfFillReq& request,
    std::optional<SlcSfSlcVictim> slc_victim = std::nullopt,
    std::optional<SlcSfSfVictim> sf_victim = std::nullopt)
{
    return SlcSfResponse::done(
        request, std::move(slc_victim), std::move(sf_victim));
}

inline SlcSfResponse
makeSlcSfDoneResponse(
    const SlcSfUpdateReq& request,
    std::optional<SlcSfSfVictim> sf_victim = std::nullopt)
{
    return SlcSfResponse::done(request, std::move(sf_victim));
}

inline SlcSfResponse
makeSlcSfDoneResponse(
    const SlcSfEvictReq& request,
    std::optional<SlcSfSfVictim> sf_victim = std::nullopt)
{
    return SlcSfResponse::done(request, std::move(sf_victim));
}

template <class Request>
inline SlcSfResponse
makeSlcSfReplayResponse(const Request& request, SlcSfReplay replay)
{
    return SlcSfResponse::replay(request, std::move(replay));
}

template <class Request>
inline SlcSfResponse
makeSlcSfErrorResponse(const Request& request, SlcSfError error)
{
    return SlcSfResponse::error(request, std::move(error));
}

} // namespace gem5::Chi

#endif // __HNF_SLCSF_RESPONSE_HH__
