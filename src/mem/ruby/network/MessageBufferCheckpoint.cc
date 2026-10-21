#include "mem/ruby/network/MessageBuffer.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ruby
{
template<class Archive>
void MessageBuffer::serialize(Archive &archive, unsigned)
{
    fatal_if(getNumMessages() || getNumStalledMessages() || !m_deferred_msg_map.empty(),
             "E_CHECKPOINT_BOUNDARY: message buffer is active");
    checkpoint::fields(archive, m_dequeues_this_cy, m_time_last_time_size_checked,
        m_size_last_time_size_checked, m_time_last_time_enqueue, m_time_last_time_pop,
        m_last_arrival_time, m_size_at_cycle_start, m_stalled_at_cycle_start,
        m_msgs_this_cycle, m_msg_counter, m_priority_rank,
        m_last_message_strict_fifo_bypassed, m_not_avail_count, m_msg_count,
        m_stall_time, m_stall_count);
}

void MessageBuffer::serialize(CheckpointOut &cp) const
{
    checkpoint::save(cp, name(), *this);
}

void MessageBuffer::unserialize(CheckpointIn &cp)
{
    checkpointState = checkpoint::read(cp, name());
}

void MessageBuffer::drainResume()
{
    SimObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}
