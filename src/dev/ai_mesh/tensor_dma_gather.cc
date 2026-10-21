#include "dev/ai_mesh/tensor_dma_engine.hh"

#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_ir_dma_verifier.hh"
#include "dev/ai_mesh/mock_axi_transport.hh"

namespace gem5::ai_mesh
{

bool TensorDmaEngine::submitGather(const DecodedDmaDescriptor &descriptor,
                                   RuntimeObjectKey descriptor_key,
                                   RuntimeObjectKey command_key,
                                   RuntimeObjectKey completion_event, Tick issue_tick)
{
    fatal_if(!owner || descriptor_key.domain != mesh_abi::MeshObjectDomain::STATIC,
             "gather requires an admitted static descriptor");
    const auto key = owner->frozenDescriptorKey(descriptor.command_id, descriptor.descriptor_id);
    const auto *fact = owner->admittedGatherIndex(descriptor.descriptor_id);
    fatal_if(!key || !fact, "gather descriptor has no admitted index layout");
    owner->checkDmaSourceValidity(command_key);
    const Tick ready = std::max(issue_tick + setup_cycles * clockPeriod(), clockEdge(Cycles(1)));
    owner->recordDescriptorSubmission(*key, issue_tick, ready);
    transport->beginPayloadDigest(descriptor_key);
    auto *event = new GatherEvent(this, descriptor, descriptor_key, command_key,
        completion_event, *key, ready, fact->layout());
    schedule(event, ready);
    ++outstanding;
    return true;
}

void TensorDmaEngine::advanceGather(GatherEvent &state)
{
    if (!state.fault_checked) {
        state.fault_checked = true;
        const auto fault = transport->takeDescriptorFault(state.descriptor.descriptor_id);
        if (fault == MockAxiTransport::DescriptorFault::Lost)
            return;
        if (fault == MockAxiTransport::DescriptorFault::Error) {
            finishGather(state, DmaStatus::AXI_READ_ERROR);
            return;
        }
    }
    if (state.descriptor.useful_bytes == 0) {
        finishGather(state, DmaStatus::OK);
        return;
    }
    if (state.phase == GatherEvent::Phase::Index) {
        if (owner->instanceErrored())
            state.reader.cancel();
        const auto read = state.reader.advance(curTick(), *owner);
        if (read)
            owner->observations.recordDescriptorIndexRead(*state.key, *read);
        if (state.reader.invalidIndex() || state.reader.readError()) {
            finishGather(state, state.reader.invalidIndex()
                ? DmaStatus::INDEX_OUT_OF_RANGE : DmaStatus::AXI_READ_ERROR);
            return;
        }
        if (owner->instanceErrored() && !state.reader.pending()) {
            finishGather(state, DmaStatus::SUPPRESSED);
            return;
        }
        if (read) {
            const auto &descriptor = state.descriptor;
            const uint64_t address = owner->dmaEndpointAddress(descriptor, state.descriptor_key, true) +
                uint64_t(read->raw_index) * descriptor.src_stride_bytes;
            state.bursts = splitBursts(address, descriptor.row_bytes,
                transport->dataBusBytes(), descriptor.max_burst_beats);
            for (auto &burst : state.bursts)
                burst.row = read->occurrence;
            state.burst_index = 0;
            state.phase = GatherEvent::Phase::Response;
            schedule(&state, curTick() + transport->burstLatency(state.bursts.front().beats));
            return;
        }
        if (state.reader.done()) {
            finishGather(state, DmaStatus::OK);
            return;
        }
        schedule(&state, clockEdge(Cycles(1)));
        return;
    }
    const auto &burst = state.bursts.at(state.burst_index);
    const uint64_t local = owner->dmaLocalOffset(state.descriptor, state.descriptor_key, false) +
        uint64_t(burst.row) * state.descriptor.dst_stride_bytes + burst.row_offset;
    if (state.phase == GatherEvent::Phase::Response) {
        state.payload.resize(burst.useful_bytes);
        fatal_if(!transport->readHbm(burst.logical_start, burst.useful_bytes, state.payload.data()),
                 "admitted gather table row leaves its backing");
        state.phase = GatherEvent::Phase::Reserve;
    }
    if (state.phase == GatherEvent::Phase::Reserve) {
        const auto service = owner->tryReserveSramService(local, burst.useful_bytes, true);
        if (!service) {
            schedule(&state, clockEdge(Cycles(1)));
            return;
        }
        state.phase = GatherEvent::Phase::Commit;
        schedule(&state, std::max(curTick() + service->stall_ticks, clockEdge(Cycles(1))));
        return;
    }
    fatal_if(!owner->functionalSramWrite(local, burst.useful_bytes, state.payload.data()),
             "admitted gather occurrence leaves its staging window");
    transport->notePayload(state.descriptor_key, state.payload.data(), state.payload.size());
    state.contribution.read_bytes += burst.useful_bytes;
    ++state.contribution.read_bursts;
    state.contribution.committed_segments.push_back({burst.row, burst.row_offset, burst.useful_bytes});
    if (++state.burst_index < state.bursts.size() && !owner->instanceErrored()) {
        state.phase = GatherEvent::Phase::Response;
        schedule(&state, curTick() + transport->burstLatency(state.bursts[state.burst_index].beats));
    } else {
        state.phase = GatherEvent::Phase::Index;
        schedule(&state, clockEdge(Cycles(1)));
    }
}

void TensorDmaEngine::finishGather(GatherEvent &state, DmaStatus status)
{
    --outstanding;
    auto contribution = transport->accountRead(state.descriptor_key,
        state.contribution.read_bytes, state.contribution.read_bursts);
    contribution.committed_segments = std::move(state.contribution.committed_segments);
    transport->accountStatus(state.descriptor_key, status);
    owner->recordDescriptorCompletion(*state.key, curTick(), status == DmaStatus::OK,
        status == DmaStatus::OK ? curTick() : 0, status, contribution);
    owner->onDmaCompleted(state.descriptor_key, state.command_key,
                          state.completion_event, curTick(), status);
}

}
