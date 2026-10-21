#include "mem/ruby/network/simple/Switch.hh"
#include "mem/ruby/common/ConsumerCheckpoint.hh"

namespace gem5::ruby
{
template<class Archive>
void Throttle::serialize(Archive &archive, unsigned)
{
    fatal_if(!quiescenceSnapshot().empty(), "E_CHECKPOINT_BOUNDARY: throttle is active");
    Consumer::checkpoint(archive);
    checkpoint::fields(archive, m_wakeups_wo_switch, m_units_remaining,
        throttleStats.acc_link_utilization, throttleStats.total_msg_count,
        throttleStats.total_msg_bytes, throttleStats.total_data_msg_bytes,
        throttleStats.total_msg_wait_time, throttleStats.total_stall_cy,
        throttleStats.total_bw_sat_cy);
    for (size_t type = 0; type < MessageSizeType_NUM; ++type)
        checkpoint::fields(archive, *throttleStats.msg_counts[type],
                           *throttleStats.msg_bytes[type]);
}

template<class Archive>
void Switch::serialize(Archive &archive, unsigned)
{
    fatal_if(!quiescenceSnapshot().empty(), "E_CHECKPOINT_BOUNDARY: switch is active");
    archive & perfectSwitch;
    checkpoint::existing(archive, throttles);
}

void Switch::serialize(CheckpointOut &cp) const
{
    BasicRouter::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void Switch::unserialize(CheckpointIn &cp)
{
    BasicRouter::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void Switch::drainResume()
{
    BasicRouter::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}
