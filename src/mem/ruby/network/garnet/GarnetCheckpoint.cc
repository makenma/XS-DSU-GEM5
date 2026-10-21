#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/garnet/NetworkInterface.hh"
#include "mem/ruby/network/garnet/Router.hh"
#include "mem/ruby/network/garnet/InputUnit.hh"
#include "mem/ruby/network/garnet/OutputUnit.hh"
#include "mem/ruby/network/simple/AggregateNetwork.hh"
#include "mem/ruby/common/ConsumerCheckpoint.hh"

namespace gem5::ruby::garnet
{
template<class Archive>
void GarnetNetwork::serialize(Archive &archive, unsigned)
{
    fatal_if(!isQuiescent() || m_dual_lane || m_enable_fault_model,
             "E_CHECKPOINT_BOUNDARY: unsupported or active Garnet state");
    bool aggregate = aggregate_model && aggregate_model->active();
    archive & aggregate;
    if constexpr (Archive::is_loading::value)
        if (aggregate != bool(aggregate_model && aggregate_model->active()))
            setAggregateMode(aggregate);
    checkpoint::fields(archive, m_next_packet_id,
        m_experiment_packets_injected, m_experiment_packets_received,
        m_experiment_flits_injected, m_experiment_flits_received,
        m_experiment_wire_bytes_injected, m_experiment_wire_bytes_received,
        m_input_vc_full_events_raw, m_input_vc_max_occupancy_raw,
        m_credit_stall_vc_cycles_raw, m_vc_alloc_stall_vc_cycles_raw,
        m_ni_credit_stall_vc_cycles_raw, m_packets_injected_raw,
        m_packets_received_raw, m_flits_injected_raw, m_flits_received_raw,
        m_wire_bytes_injected_raw, m_wire_bytes_received_raw,
        m_input_vc_occupancy_flit_cycles_raw, m_input_vc_full_vc_cycles_raw,
        m_ni_vc_busy_cycles_raw,
        m_packets_received, m_packets_injected, m_packet_network_latency,
        m_packet_queueing_latency, m_flits_received, m_flits_injected,
        m_flit_network_latency, m_flit_queueing_latency, m_input_vc_full_events,
        m_input_vc_max_occupancy, m_credit_stall_vc_cycles, m_vc_alloc_stall_vc_cycles,
        m_ni_credit_stall_vc_cycles, m_wire_bytes_injected, m_wire_bytes_received,
        m_input_vc_occupancy_flit_cycles, m_input_vc_full_vc_cycles,
        m_ni_vc_busy_cycles, m_total_ext_in_link_utilization,
        m_total_ext_out_link_utilization, m_total_int_link_utilization,
        m_average_link_utilization, m_average_vc_load, m_total_hops);
    for (auto *router : m_routers) archive & *router;
    for (auto *ni : m_nis) archive & *ni;
    for (auto *link : m_networklinks) archive & *link;
    for (auto *link : m_creditlinks) archive & static_cast<NetworkLink &>(*link);
    for (auto &row : m_data_traffic_distribution)
        for (auto *value : row) archive & *value;
    for (auto &row : m_ctrl_traffic_distribution)
        for (auto *value : row) archive & *value;
}

void GarnetNetwork::serialize(CheckpointOut &cp) const
{
    Network::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void GarnetNetwork::unserialize(CheckpointIn &cp)
{
    Network::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void GarnetNetwork::drainResume()
{
    Network::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}
