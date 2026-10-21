#include "dev/ai_mesh/gather_index.hh"

namespace gem5::ai_mesh
{

std::optional<GatherIndexRead>
GatherIndexReader::advance(uint64_t tick, GatherIndexPort &port)
{
    if (done())
        return std::nullopt;
    const uint64_t offset = layout_.offset + occurrence_ * layout_.stride;
    if (state_ == State::Ready) {
        const auto ready = port.reserveIndexRead(offset, tick);
        if (!ready)
            return std::nullopt;
        issue_tick_ = tick;
        ready_tick_ = *ready;
        state_ = State::Pending;
    }
    if (tick < ready_tick_)
        return std::nullopt;
    std::array<uint8_t, 4> bytes{};
    if (!port.readIndexWord(offset, bytes)) {
        state_ = State::ReadError;
        return std::nullopt;
    }
    uint32_t value = 0;
    for (size_t lane = 0; lane < bytes.size(); ++lane)
        value |= uint32_t(bytes[lane]) << (lane * 8);
    GatherIndexRead result{occurrence_, offset, value, issue_tick_, ready_tick_, tick};
    ++occurrence_;
    state_ = cancel_requested_ ? State::Cancelled :
        value > INT32_MAX || value >= layout_.table_rows ? State::InvalidIndex :
        occurrence_ == layout_.rows ? State::Complete : State::Ready;
    return result;
}

void GatherIndexReader::cancel()
{
    if (done())
        return;
    cancel_requested_ = true;
    if (state_ == State::Ready)
        state_ = State::Cancelled;
}

}
