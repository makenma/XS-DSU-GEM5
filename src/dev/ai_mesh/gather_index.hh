#ifndef DEV_AI_MESH_GATHER_INDEX_HH
#define DEV_AI_MESH_GATHER_INDEX_HH

#include <array>
#include <cstdint>
#include <optional>

#include "base/checkpoint_fields.hh"

namespace gem5::ai_mesh
{

struct GatherIndexLayout
{
    uint64_t offset = 0;
    uint64_t stride = 0;
    uint64_t rows = 0;
    uint64_t table_rows = 0;
};

struct GatherIndexRead
{
    GEM5_CHECKPOINT_AGGREGATE()
    uint64_t occurrence = 0;
    uint64_t index_offset = 0;
    uint32_t raw_index = 0;
    uint64_t issue_tick = 0;
    uint64_t ready_tick = 0;
    uint64_t read_tick = 0;
};

class GatherIndexPort
{
  public:
    virtual ~GatherIndexPort() = default;
    virtual std::optional<uint64_t> reserveIndexRead(uint64_t offset,
                                                     uint64_t tick) = 0;
    virtual bool readIndexWord(uint64_t offset, std::array<uint8_t, 4> &bytes) = 0;
};

class GatherIndexReader
{
  public:
    explicit GatherIndexReader(GatherIndexLayout layout) : layout_(layout),
        state_(layout.rows ? State::Ready : State::Complete)
    {}

    std::optional<GatherIndexRead> advance(uint64_t tick, GatherIndexPort &port);
    void cancel();
    bool pending() const { return state_ == State::Pending; }
    bool invalidIndex() const { return state_ == State::InvalidIndex; }
    bool readError() const { return state_ == State::ReadError; }
    bool cancelled() const { return state_ == State::Cancelled; }
    bool done() const { return state_ != State::Ready && state_ != State::Pending; }

  private:
    enum class State { Ready, Pending, Complete, InvalidIndex, ReadError, Cancelled };
    GatherIndexLayout layout_;
    State state_;
    uint64_t occurrence_ = 0;
    uint64_t issue_tick_ = 0;
    uint64_t ready_tick_ = 0;
    bool cancel_requested_ = false;
};

}

#endif
