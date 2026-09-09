/*
 * Copyright (c) 2020 Advanced Micro Devices, Inc.
 * Copyright (c) 2008 Princeton University
 * Copyright (c) 2016 Georgia Institute of Technology
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution;
 * neither the name of the copyright holders nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */


#include "mem/ruby/network/garnet/GarnetNetwork.hh"

#include <cassert>
#include <utility>

#include "base/cast.hh"
#include "base/compiler.hh"
#include "base/logging.hh"
#include "debug/RubyNetwork.hh"
#include "debug/GarnetDualLane.hh"
#include "mem/ruby/common/NetDest.hh"
#include "mem/ruby/network/MessageBuffer.hh"
#include "mem/ruby/network/garnet/CommonTypes.hh"
#include "mem/ruby/network/garnet/CreditLink.hh"
#include "mem/ruby/network/garnet/GarnetLink.hh"
#include "mem/ruby/network/garnet/Packetization.hh"
#include "mem/ruby/network/garnet/NetworkInterface.hh"
#include "mem/ruby/network/garnet/NetworkLink.hh"
#include "mem/ruby/network/garnet/Router.hh"
#include "mem/ruby/network/garnet/VnetConfig.hh"
#include "mem/ruby/system/RubySystem.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

/*
 * GarnetNetwork sets up the routers and links and collects stats.
 * Default parameters (GarnetNetwork.py) can be overwritten from command line
 * (see configs/network/Network.py)
 */

GarnetNetwork::GarnetNetwork(const Params &p)
    : Network(p)
{
    m_num_rows = p.num_rows;
    m_ni_flit_size = p.ni_flit_size;
    m_vcs_per_vnet = p.vcs_per_vnet;
    m_max_vcs_per_vnet = 0;
    m_buffers_per_data_vc = p.buffers_per_data_vc;
    m_buffers_per_ctrl_vc = p.buffers_per_ctrl_vc;
    m_routing_algorithm = p.routing_algorithm;
    fatal_if(!p.yx_vnets.empty() && m_routing_algorithm != XY_,
             "%s: yx_vnets requires dimension-order routing_algorithm=1",
             name());
    m_yx_vnets.assign(m_virtual_networks, false);
    for (int vnet : p.yx_vnets) {
        fatal_if(vnet < 0 || vnet >= m_virtual_networks,
                 "%s: yx_vnets contains invalid vnet %d", name(), vnet);
        fatal_if(m_yx_vnets[vnet],
                 "%s: yx_vnets contains duplicate vnet %d", name(), vnet);
        m_yx_vnets[vnet] = true;
    }
    m_dual_lane = p.dual_lane;
    m_dual_lane_wire_bytes = p.dual_lane_vnet_wire_bytes;
    m_next_packet_id = 0;

    m_enable_fault_model = p.enable_fault_model;
    if (m_enable_fault_model)
        fault_model = p.fault_model;

    VnetConfigInput config;
    config.virtualNetworks = m_virtual_networks;
    config.vcsPerVnet = m_vcs_per_vnet;
    config.niFlitSize = m_ni_flit_size;
    config.legacyCtrlDepth = m_buffers_per_ctrl_vc;
    config.legacyDataDepth = m_buffers_per_data_vc;
    config.legacyTypeNames = m_vnet_type_names;
    config.configuredClasses = p.vnet_classes;
    config.configuredDepths = p.buffers_per_vnet;
    config.faultModelEnabled = m_enable_fault_model;

    NormalizedVnetConfig normalized;
    const std::string config_error = normalizeVnetConfig(config, normalized);
    fatal_if(!config_error.empty(), "%s: %s", name(), config_error);
    m_vnet_type = std::move(normalized.types);
    m_buffers_per_vnet = std::move(normalized.depths);
    m_buffers_per_ctrl_vc = normalized.legacyCtrlDepth;
    m_buffers_per_data_vc = normalized.legacyDataDepth;
    const std::string input_capacity_error = m_input_capacity.configure(
        m_buffers_per_vnet, p.ni_buffers_per_vnet,
        p.router_input_vc_depths, p.routers.size(), m_enable_fault_model);
    fatal_if(!input_capacity_error.empty(), "%s: %s",
             name(), input_capacity_error);
    m_input_vc_full_events_raw.assign(m_virtual_networks, 0);
    m_input_vc_max_occupancy_raw.assign(m_virtual_networks, 0);
    m_credit_stall_vc_cycles_raw.assign(m_virtual_networks, 0);
    m_vc_alloc_stall_vc_cycles_raw.assign(m_virtual_networks, 0);
    m_ni_credit_stall_vc_cycles_raw.assign(m_virtual_networks, 0);
    m_packets_injected_raw.assign(m_virtual_networks, 0);
    m_packets_received_raw.assign(m_virtual_networks, 0);
    m_flits_injected_raw.assign(m_virtual_networks, 0);
    m_flits_received_raw.assign(m_virtual_networks, 0);
    m_wire_bytes_injected_raw.assign(m_virtual_networks, 0);
    m_wire_bytes_received_raw.assign(m_virtual_networks, 0);
    m_input_vc_occupancy_flit_cycles_raw.assign(m_virtual_networks, 0);
    m_input_vc_full_vc_cycles_raw.assign(m_virtual_networks, 0);
    m_ni_vc_busy_cycles_raw.assign(m_virtual_networks, 0);
    m_experiment_packets_injected.assign(m_virtual_networks, 0);
    m_experiment_packets_received.assign(m_virtual_networks, 0);
    m_experiment_flits_injected.assign(m_virtual_networks, 0);
    m_experiment_flits_received.assign(m_virtual_networks, 0);
    m_experiment_wire_bytes_injected.assign(m_virtual_networks, 0);
    m_experiment_wire_bytes_received.assign(m_virtual_networks, 0);

    // record the routers
    for (std::vector<BasicRouter*>::const_iterator i =  p.routers.begin();
         i != p.routers.end(); ++i) {
        Router* router = safe_cast<Router*>(*i);
        m_routers.push_back(router);

        // initialize the router's network pointers
        router->init_net_ptr(this);
    }

    // record the network interfaces
    for (std::vector<ClockedObject*>::const_iterator i = p.netifs.begin();
         i != p.netifs.end(); ++i) {
        NetworkInterface *ni = safe_cast<NetworkInterface *>(*i);
        m_nis.push_back(ni);
        ni->init_net_ptr(this);
    }

    // Print Garnet version
    inform("Garnet version %s\n", garnetVersion);
}

void
GarnetNetwork::validateDualLaneConfig() const
{
    if (!m_dual_lane)
        return;

    fatal_if(m_routing_algorithm != XY_,
             "%s: dual-lane mode requires dimension-order routing", name());
    fatal_if(m_dual_lane_wire_bytes.size() != m_vnet_type.size(),
             "%s: dual-lane mode requires wire bytes for all %zu vnets",
             name(), m_vnet_type.size());
    // Spec 1: dual-lane transport is defined for single-flit packets on
    // all five channels under the effective configuration; anything else
    // is an initialization-time configuration error.
    for (unsigned vnet = 0; vnet < m_dual_lane_wire_bytes.size(); ++vnet) {
        MessagePacketization packetization;
        const std::string error = resolveMessagePacketization(
            0, m_dual_lane_wire_bytes[vnet], m_ni_flit_size, packetization);
        fatal_if(!error.empty(),
                 "%s: dual-lane vnet %u wire bytes invalid: %s",
                 name(), vnet, error.c_str());
        fatal_if(packetization.numFlits != 1,
                 "%s: dual-lane vnet %u transports %d flits per packet "
                 "(wire %d B, flit %u B); single-flit packets required",
                 name(), vnet, packetization.numFlits,
                 packetization.wireBytes, m_ni_flit_size);
    }
}

uint32_t
GarnetNetwork::getBuffersPerVnet(unsigned vnet) const
{
    panic_if(vnet >= m_buffers_per_vnet.size(),
             "%s: vnet %u is outside configured range [0,%zu)",
             name(), vnet, m_buffers_per_vnet.size());
    return m_buffers_per_vnet[vnet];
}

std::vector<uint32_t>
GarnetNetwork::routerInputDepths(uint32_t router, uint32_t inport) const
{
    return m_input_capacity.routerDepths(router, inport);
}

VNET_type
GarnetNetwork::getVnetType(unsigned vnet) const
{
    panic_if(vnet >= m_vnet_type.size(),
             "%s: vnet %u is outside configured range [0,%zu)",
             name(), vnet, m_vnet_type.size());
    return m_vnet_type[vnet];
}

void
GarnetNetwork::init()
{
    Network::init();

    validateDualLaneConfig();

    for (int i=0; i < m_nodes; i++) {
        m_nis[i]->addNode(m_toNetQueues[i], m_fromNetQueues[i]);
    }

    // The topology pointer should have already been initialized in the
    // parent network constructor
    assert(m_topology_ptr != NULL);
    m_topology_ptr->createLinks(this);
    std::vector<uint32_t> input_ports;
    for (const auto *router : m_routers)
        input_ports.push_back(router->get_num_inports());
    const auto input_capacity_error = m_input_capacity.validatePorts(input_ports);
    fatal_if(!input_capacity_error.empty(), "%s: %s",
             name(), input_capacity_error);

    // Initialize topology specific parameters
    if (getNumRows() > 0) {
        // Only for Mesh topology
        // m_num_rows and m_num_cols are only used for
        // implementing XY or custom routing in RoutingUnit.cc
        m_num_rows = getNumRows();
        m_num_cols = m_routers.size() / m_num_rows;
        assert(m_num_rows * m_num_cols == m_routers.size());
    } else {
        m_num_rows = -1;
        m_num_cols = -1;
    }

    // FaultModel: declare each router to the fault model
    if (isFaultModelEnabled()) {
        for (std::vector<Router*>::const_iterator i= m_routers.begin();
             i != m_routers.end(); ++i) {
            Router* router = safe_cast<Router*>(*i);
            [[maybe_unused]] int router_id =
                fault_model->declare_router(router->get_num_inports(),
                                            router->get_num_outports(),
                                            router->get_vc_per_vnet(),
                                            getBuffersPerDataVC(),
                                            getBuffersPerCtrlVC());
            assert(router_id == router->get_id());
            router->printAggregateFaultProbability(std::cout);
            router->printFaultVector(std::cout);
        }
    }
}

/*
 * This function creates a link from the Network Interface (NI)
 * into the Network.
 * It creates a Network Link from the NI to a Router and a Credit Link from
 * the Router to the NI
*/

void
GarnetNetwork::makeExtInLink(NodeID global_src, SwitchID dest, BasicLink* link,
                             std::vector<NetDest>& routing_table_entry)
{
    NodeID local_src = getLocalNodeID(global_src);
    assert(local_src < m_nodes);

    GarnetExtLink* garnet_link = safe_cast<GarnetExtLink*>(link);

    fatal_if(m_input_capacity.extended() &&
             (garnet_link->extBridgeEn || garnet_link->intBridgeEn),
             "%s: receiver capacity overrides do not support CDC/SerDes",
             name());

    // GarnetExtLink is bi-directional
    NetworkLink* net_link = garnet_link->m_network_links[LinkDirection_In];
    net_link->setType(EXT_IN_);
    CreditLink* credit_link = garnet_link->m_credit_links[LinkDirection_In];

    m_networklinks.push_back(net_link);
    m_creditlinks.push_back(credit_link);

    PortDirection dst_inport_dirn = "Local";
    const int receiver_port = m_routers[dest]->get_num_inports();
    const int sender_port = m_nis[local_src]->outputPortCount();
    const auto receiver_depths = routerInputDepths(dest, receiver_port);
    recordReceiverCapacity(0, local_src, sender_port, "Local",
        1, dest, receiver_port, dst_inport_dirn,
        m_networklinks.size() - 1, receiver_depths,
        m_routers[dest]->get_vc_per_vnet(), net_link->mVnets);

    m_max_vcs_per_vnet = std::max(m_max_vcs_per_vnet,
                             m_routers[dest]->get_vc_per_vnet());

    /*
     * We check if a bridge was enabled at any end of the link.
     * The bridge is enabled if either of clock domain
     * crossing (CDC) or Serializer-Deserializer(SerDes) unit is
     * enabled for the link at each end. The bridge encapsulates
     * the functionality for both CDC and SerDes and is a Consumer
     * object similiar to a NetworkLink.
     *
     * If a bridge was enabled we connect the NI and Routers to
     * bridge before connecting the link. Example, if an external
     * bridge is enabled, we would connect:
     * NI--->NetworkBridge--->GarnetExtLink---->Router
     */
    if (garnet_link->extBridgeEn) {
        DPRINTF(RubyNetwork, "Enable external bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->extNetBridge[LinkDirection_In];
        m_nis[local_src]->
        addOutPort(n_bridge,
                   garnet_link->extCredBridge[LinkDirection_In],
                   dest, m_routers[dest]->get_vc_per_vnet(),
                   receiver_depths, m_input_capacity.extended());
        m_networkbridges.push_back(n_bridge);
    } else {
        m_nis[local_src]->addOutPort(net_link, credit_link, dest,
            m_routers[dest]->get_vc_per_vnet(), receiver_depths,
            m_input_capacity.extended());
    }

    if (garnet_link->intBridgeEn) {
        DPRINTF(RubyNetwork, "Enable internal bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->intNetBridge[LinkDirection_In];
        m_routers[dest]->
            addInPort(dst_inport_dirn,
                      n_bridge,
                      garnet_link->intCredBridge[LinkDirection_In]);
        m_networkbridges.push_back(n_bridge);
    } else {
        m_routers[dest]->addInPort(dst_inport_dirn, net_link, credit_link);
    }

}

/*
 * This function creates a link from the Network to a NI.
 * It creates a Network Link from a Router to the NI and
 * a Credit Link from NI to the Router
*/

void
GarnetNetwork::makeExtOutLink(SwitchID src, NodeID global_dest,
                              BasicLink* link,
                              std::vector<NetDest>& routing_table_entry)
{
    NodeID local_dest = getLocalNodeID(global_dest);
    assert(local_dest < m_nodes);
    assert(src < m_routers.size());
    assert(m_routers[src] != NULL);

    GarnetExtLink* garnet_link = safe_cast<GarnetExtLink*>(link);

    fatal_if(m_input_capacity.extended() &&
             (garnet_link->extBridgeEn || garnet_link->intBridgeEn),
             "%s: receiver capacity overrides do not support CDC/SerDes",
             name());

    // GarnetExtLink is bi-directional
    NetworkLink* net_link = garnet_link->m_network_links[LinkDirection_Out];
    net_link->setType(EXT_OUT_);
    CreditLink* credit_link = garnet_link->m_credit_links[LinkDirection_Out];

    m_networklinks.push_back(net_link);
    m_creditlinks.push_back(credit_link);

    PortDirection src_outport_dirn = "Local";
    const int receiver_port = m_nis[local_dest]->inputPortCount();
    const int sender_port = m_routers[src]->get_num_outports();
    const auto &receiver_depths = m_input_capacity.niDepths();
    recordReceiverCapacity(1, src, sender_port, src_outport_dirn,
        0, local_dest, receiver_port, "Local", m_networklinks.size() - 1,
        receiver_depths, m_routers[src]->get_vc_per_vnet(), net_link->mVnets);

    m_max_vcs_per_vnet = std::max(m_max_vcs_per_vnet,
                             m_routers[src]->get_vc_per_vnet());

    /*
     * We check if a bridge was enabled at any end of the link.
     * The bridge is enabled if either of clock domain
     * crossing (CDC) or Serializer-Deserializer(SerDes) unit is
     * enabled for the link at each end. The bridge encapsulates
     * the functionality for both CDC and SerDes and is a Consumer
     * object similiar to a NetworkLink.
     *
     * If a bridge was enabled we connect the NI and Routers to
     * bridge before connecting the link. Example, if an external
     * bridge is enabled, we would connect:
     * NI<---NetworkBridge<---GarnetExtLink<----Router
     */
    if (garnet_link->extBridgeEn) {
        DPRINTF(RubyNetwork, "Enable external bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->extNetBridge[LinkDirection_Out];
        m_nis[local_dest]->
            addInPort(n_bridge, garnet_link->extCredBridge[LinkDirection_Out]);
        m_networkbridges.push_back(n_bridge);
    } else {
        m_nis[local_dest]->addInPort(net_link, credit_link);
    }

    if (garnet_link->intBridgeEn) {
        DPRINTF(RubyNetwork, "Enable internal bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->intNetBridge[LinkDirection_Out];
        m_routers[src]->
            addOutPort(src_outport_dirn,
                       n_bridge,
                       routing_table_entry, link->m_weight,
                       garnet_link->intCredBridge[LinkDirection_Out],
                       m_routers[src]->get_vc_per_vnet(), receiver_depths);
        m_networkbridges.push_back(n_bridge);
    } else {
        m_routers[src]->
            addOutPort(src_outport_dirn, net_link,
                       routing_table_entry,
                       link->m_weight, credit_link,
                       m_routers[src]->get_vc_per_vnet(), receiver_depths);
    }
}

/*
 * This function creates an internal network link between two routers.
 * It adds both the network link and an opposite credit link.
*/

void
GarnetNetwork::makeInternalLink(SwitchID src, SwitchID dest, BasicLink* link,
                                std::vector<NetDest>& routing_table_entry,
                                PortDirection src_outport_dirn,
                                PortDirection dst_inport_dirn)
{
    GarnetIntLink* garnet_link = safe_cast<GarnetIntLink*>(link);

    fatal_if(m_input_capacity.extended() &&
             (garnet_link->srcBridgeEn || garnet_link->dstBridgeEn),
             "%s: receiver capacity overrides do not support CDC/SerDes",
             name());

    // GarnetIntLink is unidirectional
    NetworkLink* net_link = garnet_link->m_network_link;
    net_link->setType(INT_);
    CreditLink* credit_link = garnet_link->m_credit_link;

    m_networklinks.push_back(net_link);
    m_creditlinks.push_back(credit_link);

    const int receiver_port = m_routers[dest]->get_num_inports();
    const int sender_port = m_routers[src]->get_num_outports();
    // A lane-1 (_ext) input port derives its capacity from its lane-0
    // twin; the capacity ledger records that actual receive resource.
    const uint32_t depth_source = m_routers[dest]->inputDepthSource(
        receiver_port, parsePortName(dst_inport_dirn));
    const auto receiver_depths = routerInputDepths(dest, depth_source);
    recordReceiverCapacity(1, src, sender_port, src_outport_dirn,
        1, dest, receiver_port, dst_inport_dirn,
        m_networklinks.size() - 1, receiver_depths,
        m_routers[dest]->get_vc_per_vnet(), net_link->mVnets);

    m_max_vcs_per_vnet = std::max(m_max_vcs_per_vnet,
                             std::max(m_routers[dest]->get_vc_per_vnet(),
                             m_routers[src]->get_vc_per_vnet()));

    /*
     * We check if a bridge was enabled at any end of the link.
     * The bridge is enabled if either of clock domain
     * crossing (CDC) or Serializer-Deserializer(SerDes) unit is
     * enabled for the link at each end. The bridge encapsulates
     * the functionality for both CDC and SerDes and is a Consumer
     * object similiar to a NetworkLink.
     *
     * If a bridge was enabled we connect the NI and Routers to
     * bridge before connecting the link. Example, if a source
     * bridge is enabled, we would connect:
     * Router--->NetworkBridge--->GarnetIntLink---->Router
     */
    if (garnet_link->dstBridgeEn) {
        DPRINTF(RubyNetwork, "Enable destination bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->dstNetBridge;
        m_routers[dest]->addInPort(dst_inport_dirn, n_bridge,
                                   garnet_link->dstCredBridge);
        m_networkbridges.push_back(n_bridge);
    } else {
        m_routers[dest]->addInPort(dst_inport_dirn, net_link, credit_link);
    }

    if (garnet_link->srcBridgeEn) {
        DPRINTF(RubyNetwork, "Enable source bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->srcNetBridge;
        m_routers[src]->
            addOutPort(src_outport_dirn, n_bridge,
                       routing_table_entry,
                       link->m_weight, garnet_link->srcCredBridge,
                       m_routers[dest]->get_vc_per_vnet(), receiver_depths);
        m_networkbridges.push_back(n_bridge);
    } else {
        m_routers[src]->addOutPort(src_outport_dirn, net_link,
                        routing_table_entry,
                        link->m_weight, credit_link,
                        m_routers[dest]->get_vc_per_vnet(), receiver_depths);
    }
}

// Total routers in the network
int
GarnetNetwork::getNumRouters()
{
    return m_routers.size();
}

// Get ID of router connected to a NI.
int
GarnetNetwork::get_router_id(int global_ni, int vnet)
{
    NodeID local_ni = getLocalNodeID(global_ni);

    return m_nis[local_ni]->get_router_id(vnet);
}

GarnetQuiescenceSnapshot
GarnetNetwork::quiescenceSnapshot() const
{
    GarnetQuiescenceSnapshot snapshot;
    for (const auto *ni : m_nis)
        snapshot += ni->quiescenceSnapshot();
    for (const auto *router : m_routers)
        snapshot += router->quiescenceSnapshot();
    for (const auto *link : m_networklinks)
        snapshot.dataLinkPendingFlits += link->pendingItems();
    for (const auto *link : m_creditlinks)
        snapshot.creditLinkPendingCredits += link->pendingItems();
    for (const auto *bridge : m_networkbridges)
        snapshot.bridgePendingItems += bridge->pendingItems();
    return snapshot;
}

GarnetCreditLedger
GarnetNetwork::creditLedger() const
{
    GarnetCreditLedger ledger;
    for (const auto *ni : m_nis)
        ni->appendCreditLedger(ledger);
    for (const auto *router : m_routers)
        router->appendCreditLedger(ledger);
    std::map<const void *, int32_t> stable_link_ids;
    for (size_t index = 0; index < m_networklinks.size(); ++index)
        stable_link_ids.emplace(m_networklinks[index], index);
    for (size_t index = 0; index < m_networkbridges.size(); ++index) {
        stable_link_ids.emplace(
            m_networkbridges[index], m_networklinks.size() + index);
    }
    for (auto &entry : ledger) {
        const auto found = stable_link_ids.find(entry.linkToken);
        panic_if(found == stable_link_ids.end(),
                 "%s: credit ledger output is not a registered data link",
                 name());
        entry.linkId = found->second;
        entry.linkToken = nullptr;
    }
    return ledger;
}

GarnetInputVcHighWater
GarnetNetwork::inputVcHighWater() const
{
    GarnetInputVcHighWater entries;
    for (const auto *router : m_routers)
        router->appendInputVcHighWater(entries);
    return entries;
}

void
GarnetNetwork::recordReceiverCapacity(uint32_t senderKind, int32_t senderId,
    int32_t senderPort, const std::string &senderDirection,
    uint32_t receiverKind, int32_t receiverId, int32_t receiverPort,
    const std::string &receiverDirection, int32_t linkId,
    const std::vector<uint32_t> &depths, uint32_t vcsPerVnet,
    const std::vector<int> &supportedVnets)
{
    for (uint32_t vnet = 0; vnet < depths.size(); ++vnet) {
        if (!supportedVnets.empty() &&
            std::find(supportedVnets.begin(), supportedVnets.end(), vnet) ==
                supportedVnets.end())
            continue;
        for (uint32_t offset = 0; offset < vcsPerVnet; ++offset) {
            m_receiver_capacity_map.push_back({
                senderKind, senderId, senderPort, senderDirection,
                receiverKind, receiverId, receiverPort, receiverDirection,
                linkId, vnet, vnet * vcsPerVnet + offset,
                depths[vnet], depths[vnet]});
        }
    }
}

GarnetReceiverCapacityMap
GarnetNetwork::receiverCapacityMap() const
{
    return m_receiver_capacity_map;
}

GarnetExperimentSnapshot
GarnetNetwork::experimentSnapshot() const
{
    GarnetExperimentSnapshot snapshot;
    snapshot.tick = curTick();
    snapshot.networkCycle = curCycle();
    snapshot.packetsInjected = m_experiment_packets_injected;
    snapshot.packetsReceived = m_experiment_packets_received;
    snapshot.flitsInjected = m_experiment_flits_injected;
    snapshot.flitsReceived = m_experiment_flits_received;
    snapshot.wireBytesInjected = m_experiment_wire_bytes_injected;
    snapshot.wireBytesReceived = m_experiment_wire_bytes_received;
    for (const auto *router : m_routers)
        router->appendExperimentSnapshot(snapshot);
    for (size_t link = 0; link < m_networklinks.size(); ++link) {
        const auto *network_link = m_networklinks[link];
        snapshot.links.push_back({static_cast<int32_t>(link),
            network_link->bitWidth, network_link->experimentFlits(),
            network_link->experimentVcFlits()});
    }
    return snapshot;
}

void
GarnetNetwork::increment_injected_wire_bytes(unsigned vnet, uint64_t bytes)
{
    panic_if(vnet >= m_virtual_networks, "invalid injected-byte vnet");
    m_wire_bytes_injected_raw[vnet] += bytes;
    m_experiment_wire_bytes_injected[vnet] += bytes;
    m_wire_bytes_injected[vnet] += bytes;
}

void
GarnetNetwork::increment_received_wire_bytes(unsigned vnet, uint64_t bytes)
{
    panic_if(vnet >= m_virtual_networks, "invalid received-byte vnet");
    m_wire_bytes_received_raw[vnet] += bytes;
    m_experiment_wire_bytes_received[vnet] += bytes;
    m_wire_bytes_received[vnet] += bytes;
}

void
GarnetNetwork::addInputVcIntegral(unsigned vnet,
                                  uint64_t occupancy_flit_cycles,
                                  uint64_t full_vc_cycles)
{
    panic_if(vnet >= m_virtual_networks, "invalid input-VC integral vnet");
    m_input_vc_occupancy_flit_cycles_raw[vnet] += occupancy_flit_cycles;
    m_input_vc_full_vc_cycles_raw[vnet] += full_vc_cycles;
    m_input_vc_occupancy_flit_cycles[vnet] += occupancy_flit_cycles;
    m_input_vc_full_vc_cycles[vnet] += full_vc_cycles;
}

void
GarnetNetwork::addNiVcBusyCycles(unsigned vnet, uint64_t cycles)
{
    panic_if(vnet >= m_virtual_networks, "invalid NI-VC busy vnet");
    m_ni_vc_busy_cycles_raw[vnet] += cycles;
    m_ni_vc_busy_cycles[vnet] += cycles;
}

#define GARNET_RAW_VNET_GETTER(method, member, label)                       \
uint64_t                                                                  \
GarnetNetwork::method(unsigned vnet) const                                \
{                                                                         \
    panic_if(vnet >= m_virtual_networks, "invalid " label " vnet");      \
    return member[vnet];                                                   \
}

GARNET_RAW_VNET_GETTER(packetsInjected, m_packets_injected_raw,
                       "packets-injected")
GARNET_RAW_VNET_GETTER(packetsReceived, m_packets_received_raw,
                       "packets-received")
GARNET_RAW_VNET_GETTER(flitsInjected, m_flits_injected_raw,
                       "flits-injected")
GARNET_RAW_VNET_GETTER(flitsReceived, m_flits_received_raw,
                       "flits-received")
GARNET_RAW_VNET_GETTER(wireBytesInjected, m_wire_bytes_injected_raw,
                       "wire-bytes-injected")
GARNET_RAW_VNET_GETTER(wireBytesReceived, m_wire_bytes_received_raw,
                       "wire-bytes-received")
GARNET_RAW_VNET_GETTER(inputVcOccupancyFlitCycles,
                       m_input_vc_occupancy_flit_cycles_raw,
                       "input-VC-occupancy")
GARNET_RAW_VNET_GETTER(inputVcFullVcCycles,
                       m_input_vc_full_vc_cycles_raw, "input-VC-full")
GARNET_RAW_VNET_GETTER(inputVcFullEvents, m_input_vc_full_events_raw,
                       "input-VC-full-events")
GARNET_RAW_VNET_GETTER(niVcBusyCycles, m_ni_vc_busy_cycles_raw,
                       "NI-VC-busy")

#undef GARNET_RAW_VNET_GETTER

void
GarnetNetwork::observeInputVc(unsigned vnet, uint32_t occupancy,
                              uint32_t capacity)
{
    panic_if(vnet >= m_virtual_networks || occupancy > capacity,
             "%s: invalid input VC observation vnet=%u occupancy=%u "
             "capacity=%u", name(), vnet, occupancy, capacity);
    if (occupancy > m_input_vc_max_occupancy_raw[vnet]) {
        m_input_vc_max_occupancy_raw[vnet] = occupancy;
        m_input_vc_max_occupancy[vnet] = occupancy;
    }
    if (occupancy == capacity) {
        ++m_input_vc_full_events_raw[vnet];
        ++m_input_vc_full_events[vnet];
    }
}

void
GarnetNetwork::incrementRouterCreditStall(unsigned vnet)
{
    panic_if(vnet >= m_virtual_networks, "invalid credit-stall vnet");
    ++m_credit_stall_vc_cycles_raw[vnet];
    ++m_credit_stall_vc_cycles[vnet];
}

void
GarnetNetwork::incrementVcAllocStall(unsigned vnet)
{
    panic_if(vnet >= m_virtual_networks, "invalid VC-alloc-stall vnet");
    ++m_vc_alloc_stall_vc_cycles_raw[vnet];
    ++m_vc_alloc_stall_vc_cycles[vnet];
}

void
GarnetNetwork::incrementNiCreditStall(unsigned vnet)
{
    panic_if(vnet >= m_virtual_networks, "invalid NI-credit-stall vnet");
    ++m_ni_credit_stall_vc_cycles_raw[vnet];
    ++m_ni_credit_stall_vc_cycles[vnet];
}

uint64_t
GarnetNetwork::inputVcMaxOccupancy(unsigned vnet) const
{
    panic_if(vnet >= m_virtual_networks, "invalid max-occupancy vnet");
    return m_input_vc_max_occupancy_raw[vnet];
}

void
GarnetNetwork::flushEventIntegratedStats()
{
    for (auto *ni : m_nis)
        ni->collateStats();
    for (auto *router : m_routers)
        router->flushEventIntegratedStats();
}

void
GarnetNetwork::resetInputVcHighWater()
{
    for (auto *router : m_routers)
        router->resetInputVcHighWater();
    std::fill(m_input_vc_max_occupancy_raw.begin(),
              m_input_vc_max_occupancy_raw.end(), 0);
    for (const auto &entry : inputVcHighWater()) {
        m_input_vc_max_occupancy_raw[entry.vnet] = std::max(
            m_input_vc_max_occupancy_raw[entry.vnet], entry.highWater);
    }
    for (unsigned vnet = 0; vnet < m_virtual_networks; ++vnet)
        m_input_vc_max_occupancy[vnet] =
            m_input_vc_max_occupancy_raw[vnet];
}

uint64_t
GarnetNetwork::routerCreditStalls(unsigned vnet) const
{
    panic_if(vnet >= m_virtual_networks, "invalid credit-stall vnet");
    return m_credit_stall_vc_cycles_raw[vnet];
}

uint64_t
GarnetNetwork::vcAllocStalls(unsigned vnet) const
{
    panic_if(vnet >= m_virtual_networks, "invalid VC-alloc-stall vnet");
    return m_vc_alloc_stall_vc_cycles_raw[vnet];
}

uint64_t
GarnetNetwork::niCreditStalls(unsigned vnet) const
{
    panic_if(vnet >= m_virtual_networks, "invalid NI-credit-stall vnet");
    return m_ni_credit_stall_vc_cycles_raw[vnet];
}

void
GarnetNetwork::regStats()
{
    Network::regStats();

    // Packets
    m_packets_received
        .init(m_virtual_networks)
        .name(name() + ".packets_received")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    m_packets_injected
        .init(m_virtual_networks)
        .name(name() + ".packets_injected")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    m_packet_network_latency
        .init(m_virtual_networks)
        .name(name() + ".packet_network_latency")
        .flags(statistics::oneline)
        ;

    m_packet_queueing_latency
        .init(m_virtual_networks)
        .name(name() + ".packet_queueing_latency")
        .flags(statistics::oneline)
        ;

    for (int i = 0; i < m_virtual_networks; i++) {
        m_packets_received.subname(i, csprintf("vnet-%i", i));
        m_packets_injected.subname(i, csprintf("vnet-%i", i));
        m_packet_network_latency.subname(i, csprintf("vnet-%i", i));
        m_packet_queueing_latency.subname(i, csprintf("vnet-%i", i));
    }

    m_input_vc_full_events
        .init(m_virtual_networks)
        .name(name() + ".input_vc_full_events");
    m_input_vc_max_occupancy
        .init(m_virtual_networks)
        .name(name() + ".input_vc_max_occupancy");
    m_credit_stall_vc_cycles
        .init(m_virtual_networks)
        .name(name() + ".credit_stall_vc_cycles");
    m_vc_alloc_stall_vc_cycles
        .init(m_virtual_networks)
        .name(name() + ".vc_alloc_stall_vc_cycles");
    m_ni_credit_stall_vc_cycles
        .init(m_virtual_networks)
        .name(name() + ".ni_credit_stall_vc_cycles");
    m_wire_bytes_injected
        .init(m_virtual_networks)
        .name(name() + ".wire_bytes_injected");
    m_wire_bytes_received
        .init(m_virtual_networks)
        .name(name() + ".wire_bytes_received");
    // Event-integrated over every router input VC.  Occupancy uses
    // flit*router-cycle units; full time uses VC-cycle units and therefore
    // may exceed elapsed simulation cycles.
    m_input_vc_occupancy_flit_cycles
        .init(m_virtual_networks)
        .name(name() + ".input_vc_occupancy_flit_cycles");
    m_input_vc_full_vc_cycles
        .init(m_virtual_networks)
        .name(name() + ".input_vc_full_vc_cycles");
    m_ni_vc_busy_cycles
        .init(m_virtual_networks)
        .name(name() + ".ni_vc_busy_cycles");
    for (int i = 0; i < m_virtual_networks; ++i) {
        const std::string label = csprintf("vnet-%i", i);
        m_input_vc_full_events.subname(i, label);
        m_input_vc_max_occupancy.subname(i, label);
        m_credit_stall_vc_cycles.subname(i, label);
        m_vc_alloc_stall_vc_cycles.subname(i, label);
        m_ni_credit_stall_vc_cycles.subname(i, label);
        m_wire_bytes_injected.subname(i, label);
        m_wire_bytes_received.subname(i, label);
        m_input_vc_occupancy_flit_cycles.subname(i, label);
        m_input_vc_full_vc_cycles.subname(i, label);
        m_ni_vc_busy_cycles.subname(i, label);
    }

    m_avg_packet_vnet_latency
        .name(name() + ".average_packet_vnet_latency")
        .flags(statistics::oneline);
    m_avg_packet_vnet_latency =
        m_packet_network_latency / m_packets_received;

    m_avg_packet_vqueue_latency
        .name(name() + ".average_packet_vqueue_latency")
        .flags(statistics::oneline);
    m_avg_packet_vqueue_latency =
        m_packet_queueing_latency / m_packets_received;

    m_avg_packet_network_latency
        .name(name() + ".average_packet_network_latency");
    m_avg_packet_network_latency =
        sum(m_packet_network_latency) / sum(m_packets_received);

    m_avg_packet_queueing_latency
        .name(name() + ".average_packet_queueing_latency");
    m_avg_packet_queueing_latency
        = sum(m_packet_queueing_latency) / sum(m_packets_received);

    m_avg_packet_latency
        .name(name() + ".average_packet_latency");
    m_avg_packet_latency
        = m_avg_packet_network_latency + m_avg_packet_queueing_latency;

    // Flits
    m_flits_received
        .init(m_virtual_networks)
        .name(name() + ".flits_received")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    m_flits_injected
        .init(m_virtual_networks)
        .name(name() + ".flits_injected")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    m_flit_network_latency
        .init(m_virtual_networks)
        .name(name() + ".flit_network_latency")
        .flags(statistics::oneline)
        ;

    m_flit_queueing_latency
        .init(m_virtual_networks)
        .name(name() + ".flit_queueing_latency")
        .flags(statistics::oneline)
        ;

    for (int i = 0; i < m_virtual_networks; i++) {
        m_flits_received.subname(i, csprintf("vnet-%i", i));
        m_flits_injected.subname(i, csprintf("vnet-%i", i));
        m_flit_network_latency.subname(i, csprintf("vnet-%i", i));
        m_flit_queueing_latency.subname(i, csprintf("vnet-%i", i));
    }

    m_avg_flit_vnet_latency
        .name(name() + ".average_flit_vnet_latency")
        .flags(statistics::oneline);
    m_avg_flit_vnet_latency = m_flit_network_latency / m_flits_received;

    m_avg_flit_vqueue_latency
        .name(name() + ".average_flit_vqueue_latency")
        .flags(statistics::oneline);
    m_avg_flit_vqueue_latency =
        m_flit_queueing_latency / m_flits_received;

    m_avg_flit_network_latency
        .name(name() + ".average_flit_network_latency");
    m_avg_flit_network_latency =
        sum(m_flit_network_latency) / sum(m_flits_received);

    m_avg_flit_queueing_latency
        .name(name() + ".average_flit_queueing_latency");
    m_avg_flit_queueing_latency =
        sum(m_flit_queueing_latency) / sum(m_flits_received);

    m_avg_flit_latency
        .name(name() + ".average_flit_latency");
    m_avg_flit_latency =
        m_avg_flit_network_latency + m_avg_flit_queueing_latency;


    // Hops
    m_avg_hops.name(name() + ".average_hops");
    m_avg_hops = m_total_hops / sum(m_flits_received);

    // Links
    m_total_ext_in_link_utilization
        .name(name() + ".ext_in_link_utilization");
    m_total_ext_out_link_utilization
        .name(name() + ".ext_out_link_utilization");
    m_total_int_link_utilization
        .name(name() + ".int_link_utilization");
    m_average_link_utilization
        .name(name() + ".avg_link_utilization");
    m_average_vc_load
        .init(m_virtual_networks * m_max_vcs_per_vnet)
        .name(name() + ".avg_vc_load")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    // Traffic distribution
    for (int source = 0; source < m_routers.size(); ++source) {
        m_data_traffic_distribution.push_back(
            std::vector<statistics::Scalar *>());
        m_ctrl_traffic_distribution.push_back(
            std::vector<statistics::Scalar *>());

        for (int dest = 0; dest < m_routers.size(); ++dest) {
            statistics::Scalar *data_packets = new statistics::Scalar();
            statistics::Scalar *ctrl_packets = new statistics::Scalar();

            data_packets->name(name() + ".data_traffic_distribution." + "n" +
                    std::to_string(source) + "." + "n" + std::to_string(dest));
            m_data_traffic_distribution[source].push_back(data_packets);

            ctrl_packets->name(name() + ".ctrl_traffic_distribution." + "n" +
                    std::to_string(source) + "." + "n" + std::to_string(dest));
            m_ctrl_traffic_distribution[source].push_back(ctrl_packets);
        }
    }
}

void
GarnetNetwork::collateStats()
{
    RubySystem *rs = params().ruby_system;
    double time_delta = double(curCycle() - rs->getStartCycle());

    for (int i = 0; i < m_networklinks.size(); i++) {
        link_type type = m_networklinks[i]->getType();
        int activity = m_networklinks[i]->getLinkUtilization();

        if (type == EXT_IN_)
            m_total_ext_in_link_utilization += activity;
        else if (type == EXT_OUT_)
            m_total_ext_out_link_utilization += activity;
        else if (type == INT_)
            m_total_int_link_utilization += activity;

        m_average_link_utilization +=
            (double(activity) / time_delta);

        std::vector<unsigned int> vc_load = m_networklinks[i]->getVcLoad();
        for (int j = 0; j < vc_load.size(); j++) {
            m_average_vc_load[j] += ((double)vc_load[j] / time_delta);
        }
    }

    // Flush event-integrated NI and router VC state to the current cycle;
    // neither integral relies on a component receiving a wakeup.
    flushEventIntegratedStats();

    // Router::collateStats flushes the same input integrals again at this
    // cycle (an idempotent operation) and preserves legacy activity stats.
    for (int i = 0; i < m_routers.size(); i++) {
        m_routers[i]->collateStats();
    }
    if (DTRACE(GarnetDualLane)) {
        DPRINTF(GarnetDualLane, "DL_SNAPSHOT tick=%llu\n", curTick());
        for (const auto &entry : receiverCapacityMap()) {
            DPRINTF(GarnetDualLane,
                    "DL_CAPACITY link=%d vc=%u vnet=%u kind=%u "
                    "receiver=%d port=%d direction=%s depth=%u\n",
                    entry.linkId, entry.vc, entry.vnet, entry.receiverKind,
                    entry.receiverId, entry.receiverPort,
                    entry.receiverDirection, entry.depth);
        }
        for (const auto &entry : experimentSnapshot().inputVcs) {
            DPRINTF(GarnetDualLane,
                    "DL_BUFFER router=%d input=%d ingress=%d lane=%u "
                    "vc=%u vnet=%u depth=%u occupancy=%u high_water=%llu "
                    "enqueued=%llu dequeued=%llu\n",
                    entry.routerId, entry.inportId, entry.ingressPort,
                    entry.lane, entry.vc, entry.vnet, entry.depth,
                    entry.occupancy, entry.highWater,
                    entry.enqueued, entry.dequeued);
        }
        for (const auto &entry : creditLedger()) {
            DPRINTF(GarnetDualLane,
                    "DL_CREDIT link=%d vc=%u initial=%llu sent=%llu "
                    "returned=%llu current=%llu\n",
                    entry.linkId, entry.vc, entry.initial, entry.sent,
                    entry.returned, entry.current);
        }
        DPRINTF(GarnetDualLane, "DL_SNAPSHOT_END tick=%llu\n", curTick());
    }
}

void
GarnetNetwork::resetStats()
{
    std::fill(m_input_vc_full_events_raw.begin(),
              m_input_vc_full_events_raw.end(), 0);
    std::fill(m_input_vc_max_occupancy_raw.begin(),
              m_input_vc_max_occupancy_raw.end(), 0);
    std::fill(m_credit_stall_vc_cycles_raw.begin(),
              m_credit_stall_vc_cycles_raw.end(), 0);
    std::fill(m_vc_alloc_stall_vc_cycles_raw.begin(),
              m_vc_alloc_stall_vc_cycles_raw.end(), 0);
    std::fill(m_ni_credit_stall_vc_cycles_raw.begin(),
              m_ni_credit_stall_vc_cycles_raw.end(), 0);
    std::fill(m_packets_injected_raw.begin(), m_packets_injected_raw.end(), 0);
    std::fill(m_packets_received_raw.begin(), m_packets_received_raw.end(), 0);
    std::fill(m_flits_injected_raw.begin(), m_flits_injected_raw.end(), 0);
    std::fill(m_flits_received_raw.begin(), m_flits_received_raw.end(), 0);
    std::fill(m_wire_bytes_injected_raw.begin(),
              m_wire_bytes_injected_raw.end(), 0);
    std::fill(m_wire_bytes_received_raw.begin(),
              m_wire_bytes_received_raw.end(), 0);
    std::fill(m_input_vc_occupancy_flit_cycles_raw.begin(),
              m_input_vc_occupancy_flit_cycles_raw.end(), 0);
    std::fill(m_input_vc_full_vc_cycles_raw.begin(),
              m_input_vc_full_vc_cycles_raw.end(), 0);
    std::fill(m_ni_vc_busy_cycles_raw.begin(),
              m_ni_vc_busy_cycles_raw.end(), 0);
    for (auto *ni : m_nis)
        ni->resetStats();
    for (int i = 0; i < m_routers.size(); i++) {
        m_routers[i]->resetStats();
    }
    for (int i = 0; i < m_networklinks.size(); i++) {
        m_networklinks[i]->resetStats();
    }
    for (int i = 0; i < m_creditlinks.size(); i++) {
        m_creditlinks[i]->resetStats();
    }
}

void
GarnetNetwork::print(std::ostream& out) const
{
    out << "[GarnetNetwork]";
}

void
GarnetNetwork::update_traffic_distribution(RouteInfo route)
{
    int src_node = route.src_router;
    int dest_node = route.dest_router;
    int vnet = route.vnet;

    if (m_vnet_type[vnet] == DATA_VNET_)
        (*m_data_traffic_distribution[src_node][dest_node])++;
    else
        (*m_ctrl_traffic_distribution[src_node][dest_node])++;
}

bool
GarnetNetwork::functionalRead(Packet *pkt, WriteMask &mask)
{
    bool read = false;
    for (unsigned int i = 0; i < m_routers.size(); i++) {
        if (m_routers[i]->functionalRead(pkt, mask))
            read = true;
    }

    for (unsigned int i = 0; i < m_nis.size(); ++i) {
        if (m_nis[i]->functionalRead(pkt, mask))
            read = true;
    }

    for (unsigned int i = 0; i < m_networklinks.size(); ++i) {
        if (m_networklinks[i]->functionalRead(pkt, mask))
            read = true;
    }

    for (unsigned int i = 0; i < m_networkbridges.size(); ++i) {
        if (m_networkbridges[i]->functionalRead(pkt, mask))
            read = true;
    }

    return read;
}

uint32_t
GarnetNetwork::functionalWrite(Packet *pkt)
{
    uint32_t num_functional_writes = 0;

    for (unsigned int i = 0; i < m_routers.size(); i++) {
        num_functional_writes += m_routers[i]->functionalWrite(pkt);
    }

    for (unsigned int i = 0; i < m_nis.size(); ++i) {
        num_functional_writes += m_nis[i]->functionalWrite(pkt);
    }

    for (unsigned int i = 0; i < m_networklinks.size(); ++i) {
        num_functional_writes += m_networklinks[i]->functionalWrite(pkt);
    }

    return num_functional_writes;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
