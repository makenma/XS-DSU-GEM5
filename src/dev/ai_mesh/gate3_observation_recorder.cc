#include "dev/ai_mesh/gate3_observation_recorder.hh"

#include <algorithm>
#include <fstream>
#include <stdexcept>
#include <utility>

#include "base/logging.hh"
#include "params/Gate3ObservationRecorder.hh"

namespace gem5
{
namespace ai_mesh
{

Gate3ObservationRecorder::Gate3ObservationRecorder(const Params &p)
    : SimObject(p), outputPath(p.output_path), phaseBarrierEvent(this)
{
    fatal_if(outputPath.empty(), "%s: observation output path is empty", name());
}

void
Gate3ObservationRecorder::registerPhaseParticipant(
    Gate3PhaseParticipant *participant)
{
    fatal_if(!participant, "%s: null phase participant", name());
    fatal_if(std::find(phaseParticipants.begin(), phaseParticipants.end(),
                      participant) != phaseParticipants.end(),
             "%s: duplicate phase participant", name());
    phaseParticipants.push_back(participant);
}

void
Gate3ObservationRecorder::requestNormalCommit()
{
    schedulePhase(true);
}

std::string
Gate3ObservationRecorder::fieldValue(const std::string &value)
{
    return value.empty() ? "-" : value;
}

std::string
Gate3ObservationRecorder::optionalValue(
    const std::optional<uint64_t> &value)
{
    return value ? std::to_string(*value) : "-";
}

std::string
Gate3ObservationRecorder::optionalValue(
    const std::optional<uint32_t> &value)
{
    return value ? std::to_string(*value) : "-";
}

void
Gate3ObservationRecorder::record(Gate3Event event)
{
    events.push_back(std::move(event));
}

bool
Gate3ObservationRecorder::hasEvent(const std::string &kind,
                                   const std::string &object,
                                   uint64_t absoluteSeq) const
{
    for (const auto &event : events) {
        if (event.kind == kind && event.object == object &&
            event.absoluteSeq && *event.absoluteSeq == absoluteSeq) {
            return true;
        }
    }
    return false;
}

bool
Gate3ObservationRecorder::hasAxiResponse(const std::string &control,
                                          const std::string &channel,
                                          uint64_t absoluteSeq,
                                          const std::string &response) const
{
    for (const auto &event : events) {
        if (event.kind == "AXI_ACCEPT" && event.control == control &&
            event.channel == channel && event.absoluteSeq &&
            *event.absoluteSeq == absoluteSeq &&
            event.response == response) {
            return true;
        }
    }
    return false;
}

void
Gate3ObservationRecorder::requestFault(
    agent_abi::FaultSiteV1 site, uint32_t componentLocalId,
    uint32_t endpointId, std::vector<uint8_t> objectKey,
    uint64_t issueOrdinal, Gate3PhysicalSourceTokenV1 sourceToken)
{
    const bool accepted = fatalReducer.stageFault(
        curTick(), site, componentLocalId, endpointId,
        std::move(objectKey), issueOrdinal, sourceToken);
    schedulePhase(accepted);
}

void
Gate3ObservationRecorder::requestInvariant(
    agent_abi::InvariantSiteV1 site, uint32_t componentLocalId,
    uint32_t endpointId, std::vector<uint8_t> objectKey,
    Gate3PhysicalSourceTokenV1 sourceToken)
{
    const bool accepted = fatalReducer.stageInvariant(
        curTick(), site, componentLocalId, endpointId,
        std::move(objectKey), sourceToken);
    schedulePhase(accepted);
}

std::string
Gate3ObservationRecorder::fatalSymbol() const
{
    const char *symbol = agent_abi::detailCodeNameV1(
        fatalReducer.first().errorCode);
    fatal_if(!symbol, "%s: fatal reducer selected an unknown detail code",
             name());
    return symbol;
}

uint32_t
Gate3ObservationRecorder::fatalValue() const
{
    return static_cast<uint32_t>(fatalReducer.first().errorCode);
}

void
Gate3ObservationRecorder::recordFatalSqIntake(FatalSqIntake intake)
{
    fatal_if(intake.intakeId == 0,
             "%s: fatal SQ intake has an invalid ID", name());
    const auto inserted = _fatalSqIntakes.emplace(
        intake.intakeId, std::move(intake));
    fatal_if(!inserted.second, "%s: duplicate fatal SQ intake ID", name());
}

void
Gate3ObservationRecorder::updateFatalSqIntakeTerminal(
    SqIntakeId intakeId, const std::string &terminalEvidence)
{
    auto found = _fatalSqIntakes.find(intakeId.value());
    fatal_if(found == _fatalSqIntakes.end(),
             "%s: missing fatal SQ intake ID", name());
    fatal_if(found->second.terminalEvidence != "NONE",
             "%s: fatal SQ intake terminal is already set", name());
    found->second.terminalEvidence = terminalEvidence;
}

void
Gate3ObservationRecorder::recordFatalCqObligation(
    FatalCqObligation obligation)
{
    _fatalCqObligations.insert_or_assign(
        obligation.id, std::move(obligation));
}

void
Gate3ObservationRecorder::recordHostAckIssue(
    uint64_t issueOrdinal, uint64_t sequence, uint32_t axiId,
    Gate3PhysicalSourceTokenV1 transactionToken)
{
    const auto wire = transactionToken.wire();
    _hostAckRecords.emplace(
        issueOrdinal, FatalControlRecord{
            issueOrdinal, sequence, axiId, "WAIT_B", "NONE", "UNKNOWN",
            gate3Hex(wire.data(), wire.size()), std::nullopt});
}

void
Gate3ObservationRecorder::recordMsiIssue(
    uint64_t issueOrdinal, uint64_t sequence, uint32_t axiId,
    Gate3PhysicalSourceTokenV1 transactionToken)
{
    const auto wire = transactionToken.wire();
    const uint64_t transactionOrdinal = transactionToken.primaryOrdinal;
    _msiRecords.emplace(
        issueOrdinal, FatalControlRecord{
            issueOrdinal, sequence, axiId, "ISSUED", "NONE", "UNKNOWN",
            gate3Hex(wire.data(), wire.size()), std::nullopt});
    _msiTransactionOrdinals.emplace(transactionOrdinal, issueOrdinal);
}

void
Gate3ObservationRecorder::recordHostAckTerminal(
    uint64_t issueOrdinal, const std::string &terminalEvidence,
    bool targetCommitted, Gate3PhysicalSourceTokenV1 responseToken)
{
    auto found = _hostAckRecords.find(issueOrdinal);
    fatal_if(found == _hostAckRecords.end(),
             "%s: ACK terminal has no issue record", name());
    const auto wire = responseToken.wire();
    found->second.terminalEvidence = terminalEvidence;
    found->second.targetCommitEvidence = targetCommitted ? "YES" : "NO";
    found->second.responseTokenWire = gate3Hex(wire.data(), wire.size());
}

void
Gate3ObservationRecorder::recordMsiTerminal(
    uint64_t transactionOrdinal, const std::string &terminalEvidence,
    bool targetCommitted, Gate3PhysicalSourceTokenV1 responseToken)
{
    const auto association = _msiTransactionOrdinals.find(transactionOrdinal);
    fatal_if(association == _msiTransactionOrdinals.end(),
             "%s: MSI terminal has no transaction record", name());
    auto found = _msiRecords.find(association->second);
    fatal_if(found == _msiRecords.end(),
             "%s: MSI terminal has no issue record", name());
    const auto wire = responseToken.wire();
    found->second.terminalEvidence = terminalEvidence;
    found->second.targetCommitEvidence = targetCommitted ? "YES" : "NO";
    found->second.responseTokenWire = gate3Hex(wire.data(), wire.size());
}

void
Gate3ObservationRecorder::recordHostAckTargetCommit(uint64_t issueOrdinal)
{
    auto found = _hostAckRecords.find(issueOrdinal);
    fatal_if(found == _hostAckRecords.end(),
             "%s: ACK target commit has no issue record", name());
    found->second.targetCommitEvidence = "YES";
}

void
Gate3ObservationRecorder::recordMsiTargetCommit(uint64_t transactionOrdinal)
{
    const auto association = _msiTransactionOrdinals.find(transactionOrdinal);
    fatal_if(association == _msiTransactionOrdinals.end(),
             "%s: MSI target commit has no transaction record", name());
    auto found = _msiRecords.find(association->second);
    fatal_if(found == _msiRecords.end(),
             "%s: MSI target commit has no issue record", name());
    found->second.targetCommitEvidence = "YES";
}

void
Gate3ObservationRecorder::recordFatalPublication(
    uint64_t publicationId, uint64_t doorbellIssueOrdinal,
    uint64_t baseSequence, uint64_t pendingTail,
    std::vector<uint64_t> requestIds, bool targetCommitted,
    std::string terminalEvidence, bool ambiguous,
    Gate3PhysicalSourceTokenV1 transactionToken,
    Gate3PhysicalSourceTokenV1 responseToken)
{
    const auto transactionWire = transactionToken.wire();
    const auto responseWire = responseToken.wire();
    _fatalPublications.emplace(
        publicationId, FatalPublicationRecord{
            publicationId, doorbellIssueOrdinal, baseSequence, pendingTail,
            std::move(requestIds), "AW_W_ACCEPTED",
            std::move(terminalEvidence),
            targetCommitted ? "YES" : "NO", ambiguous,
            gate3Hex(transactionWire.data(), transactionWire.size()),
            gate3Hex(responseWire.data(), responseWire.size())});
}

void
Gate3ObservationRecorder::schedulePhase(bool requested)
{
    if (requested && !_fatalRecorded && !phaseBarrierEvent.scheduled())
        schedule(&phaseBarrierEvent, curTick());
}

void
Gate3ObservationRecorder::finalizePhase()
{
    if (_fatalRecorded)
        return;
    if (fatalReducer.pending()) {
        Gate3Event event;
        event.tick = curTick();
        event.phase = 2;
        event.kind = "FATAL";
        record(std::move(event));
        metrics["fatal_records"] = 1;
        _fatalRecorded = true;
        for (Gate3PhaseParticipant *participant : phaseParticipants)
            participant->onGate3FatalCut(curTick());
        return;
    }
    for (Gate3PhaseParticipant *participant : phaseParticipants)
        participant->onGate3NormalCommit(curTick());
}

void
Gate3ObservationRecorder::setMetric(const std::string &name,
                                     uint64_t value)
{
    metrics[name] = value;
}

uint64_t
Gate3ObservationRecorder::metric(const std::string &name) const
{
    const auto found = metrics.find(name);
    return found == metrics.end() ? 0 : found->second;
}

void
Gate3ObservationRecorder::write(const Gate3FinalState &final) const
{
    std::ofstream output(outputPath, std::ios::trunc);
    fatal_if(!output, "%s: cannot open observation facts %s", name(),
             outputPath);
    for (const auto &event : events) {
        output << "EVENT|" << event.tick << '|' << unsigned(event.phase)
               << '|' << event.kind << '|' << fieldValue(event.object) << '|'
               << optionalValue(event.absoluteSeq) << '|'
               << optionalValue(event.requestId) << '|'
               << optionalValue(event.cookie) << '|'
               << optionalValue(event.slot) << '|'
               << optionalValue(event.generation) << '|'
               << optionalValue(event.txn) << '|'
               << fieldValue(event.channel) << '|'
               << fieldValue(event.direction) << '|'
               << fieldValue(event.control) << '|'
               << optionalValue(event.axiId) << '|'
               << optionalValue(event.address) << '|'
               << optionalValue(event.size) << '|'
               << fieldValue(event.response) << '|' << event.bytes << '|'
               << fieldValue(event.wstrb) << '|' << fieldValue(event.status)
               << '\n';
    }
    output << "FINAL|" << final.sqTentativeProducerSeq << '|'
           << final.sqCommittedProducerSeq << '|'
           << final.sqObservedHeadSeq << '|' << final.sqReusableHeadSeq << '|'
           << final.npuSqConsumerSeq << '|' << final.npuCqProducerSeq << '|'
           << final.cqMsiIssuedSeq << '|' << final.cqNotifiedSeq << '|'
           << final.driverCqConsumerSeq << '|' << final.npuCqAckSeq << '|'
           << final.liveSubmissions << '|' << final.liveContexts << '|'
           << final.liveCqObligations << '|' << final.msiRobEntries << '|'
           << final.ackWaitB << '|' << (final.fatal ? 1 : 0) << '|'
           << final.coreStarts << '|' << final.cqAssignments << '|'
           << final.irqDeliveries << '\n';
    for (const auto &[name, value] : metrics)
        output << "METRIC|" << name << '|' << value << '\n';
    for (const auto &[id, intake] : _fatalSqIntakes)
        output << "SQ_INTAKE|" << intake.intakeId << '|'
               << intake.expectedSqSeq << '|' << intake.readTag << '|'
               << intake.firstError << '|' << intake.stateAtCut << '|'
               << intake.terminalEvidence << '\n';
    for (const auto &[id, obligation] : _fatalCqObligations)
        output << "FATAL_CQ|" << obligation.id << '|'
               << obligation.sqSequence << '|' << obligation.requestId << '|'
               << optionalValue(obligation.cqSequence) << '|'
               << optionalValue(obligation.slot) << '|'
               << obligation.state << '\n';
    if (_fatalRecorded) {
        for (const auto &[id, record] : _fatalPublications) {
            output << "FATAL_PUBLICATION|" << record.publicationId << '|'
                   << record.doorbellIssueOrdinal << '|'
                   << record.baseSequence << '|' << record.pendingTail << '|';
            for (size_t index = 0; index < record.requestIds.size(); ++index) {
                if (index != 0)
                    output << ',';
                output << record.requestIds[index];
            }
            output << '|' << record.stateAtCut << '|'
                   << record.terminalEvidence << '|'
                   << record.targetCommitEvidence << '|'
                   << (record.ambiguous ? 1 : 0) << '|'
                   << record.transactionTokenWire << '|'
                   << record.responseTokenWire << '\n';
        }
        for (const auto &[id, record] : _hostAckRecords)
            output << "FATAL_ACK|" << record.issueOrdinal << '|'
                   << record.sequence << '|' << record.axiId << '|'
                   << record.stateAtCut << '|' << record.terminalEvidence
                   << '|' << record.targetCommitEvidence << '|'
                   << record.transactionTokenWire << '|'
                   << (record.responseTokenWire ?
                       *record.responseTokenWire : "-") << '\n';
        for (const auto &[id, record] : _msiRecords)
            output << "FATAL_MSI|" << record.issueOrdinal << '|'
                   << record.sequence << '|' << record.axiId << '|'
                   << record.stateAtCut << '|' << record.terminalEvidence
                   << '|' << record.targetCommitEvidence << '|'
                   << record.transactionTokenWire << '|'
                   << (record.responseTokenWire ?
                       *record.responseTokenWire : "-") << '\n';
        const Gate3FatalCandidateV1 &first = fatalReducer.first();
        const std::vector<uint8_t> candidateWire = first.wire();
        const auto sourceWire = first.sourceToken.wire();
        output << "FATAL_STATE|" << fatalReducer.candidateCount() << '|'
               << first.observedTick << '|'
               << static_cast<uint16_t>(first.sourceClass) << '|'
               << static_cast<uint16_t>(first.siteDomain) << '|'
               << first.siteId << '|'
               << static_cast<uint16_t>(first.componentKind) << '|'
               << first.componentLocalId << '|' << first.endpointId << '|'
               << static_cast<uint16_t>(first.objectKind) << '|'
               << first.issueOrdinal << '|'
               << static_cast<uint32_t>(first.errorCode) << '|'
               << gate3Hex(candidateWire.data(), candidateWire.size()) << '|'
               << gate3Hex(sourceWire.data(), sourceWire.size()) << '\n';
        for (const Gate3FatalCandidateV1 &candidate :
             fatalReducer.candidates()) {
            const std::vector<uint8_t> wire = candidate.wire();
            output << "FATAL_CANDIDATE|"
                   << gate3Hex(wire.data(), wire.size()) << '\n';
        }
        output << "FATAL|" << fatalSymbol() << '|' << fatalValue() << '\n';
    }
    output.flush();
    fatal_if(!output, "%s: failed to write observation facts", name());
}

}
}
