/*
 * Copyright (c) 2020 Inria
 * Copyright (c) 2016 Georgia Institute of Technology
 * Copyright (c) 2008 Princeton University
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


#include "mem/ruby/network/garnet/InputUnit.hh"

#include <algorithm>

#include "base/logging.hh"
#include "debug/RubyNetwork.hh"
#include "mem/ruby/network/garnet/Credit.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/garnet/Router.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

InputUnit::InputUnit(int id, PortDirection direction, Router *router)
  : Consumer(router), m_router(router), m_id(id), m_direction(direction),
    m_vc_per_vnet(m_router->get_vc_per_vnet())
{
    const int m_num_vcs = m_router->get_num_vcs();
    fatal_if(m_vc_per_vnet == 0,
             "Router %d input port %d has zero VCs per vnet",
             m_router->get_id(), m_id);
    m_num_buffer_reads.resize(m_num_vcs/m_vc_per_vnet);
    m_num_buffer_writes.resize(m_num_vcs/m_vc_per_vnet);
    for (int i = 0; i < m_num_buffer_reads.size(); i++) {
        m_num_buffer_reads[i] = 0;
        m_num_buffer_writes[i] = 0;
    }

    // Instantiating the virtual channels
    virtualChannels.reserve(m_num_vcs);
    m_vc_last_accounted_cycle.assign(m_num_vcs, m_router->curCycle());
    m_vc_high_water.assign(m_num_vcs, 0);
    for (int i=0; i < m_num_vcs; i++) {
        const unsigned vnet = i / m_vc_per_vnet;
        const uint32_t capacity =
            m_router->get_net_ptr()->getBuffersPerVnet(vnet);
        virtualChannels.emplace_back(capacity);
    }
}

void
InputUnit::accountVc(int vc)
{
    panic_if(vc < 0 || vc >= virtualChannels.size(),
             "Router %d input port %d VC index %d is out of range",
             m_router->get_id(), m_id, vc);
    const Cycles now = m_router->curCycle();
    const uint64_t elapsed = now - m_vc_last_accounted_cycle[vc];
    const uint64_t occupancy = virtualChannels[vc].getOccupancy();
    m_router->get_net_ptr()->addInputVcIntegral(
        vc / m_vc_per_vnet, occupancy * elapsed,
        virtualChannels[vc].isFull() ? elapsed : 0);
    m_vc_last_accounted_cycle[vc] = now;
}

flit *
InputUnit::getTopFlit(int vc)
{
    accountVc(vc);
    return virtualChannels[vc].getTopFlit();
}

uint32_t
InputUnit::get_vc_capacity(int vc) const
{
    panic_if(vc < 0 || vc >= virtualChannels.size(),
             "Router %d input port %d VC index %d is out of range",
             m_router->get_id(), m_id, vc);
    return virtualChannels[vc].getCapacity();
}

uint32_t
InputUnit::get_vc_occupancy(int vc) const
{
    panic_if(vc < 0 || vc >= virtualChannels.size(),
             "Router %d input port %d VC index %d is out of range",
             m_router->get_id(), m_id, vc);
    return virtualChannels[vc].getOccupancy();
}

uint64_t
InputUnit::bufferedFlits() const
{
    uint64_t total = 0;
    for (const auto &vc : virtualChannels)
        total += vc.getOccupancy();
    return total;
}

uint64_t
InputUnit::nonIdleVcs() const
{
    uint64_t total = 0;
    for (const auto &vc : virtualChannels)
        total += vc.get_state() != IDLE_;
    return total;
}

/*
 * The InputUnit wakeup function reads the input flit from its input link.
 * Each flit arrives with an input VC.
 * For HEAD/HEAD_TAIL flits, performs route computation,
 * and updates route in the input VC.
 * The flit is buffered for (m_latency - 1) cycles in the input VC
 * and marked as valid for SwitchAllocation starting that cycle.
 *
 */

void
InputUnit::wakeup()
{
    flit *t_flit;
    if (m_in_link->isReady(curTick())) {
        t_flit = m_in_link->peekLink();
        const int vc = t_flit->get_vc();
        panic_if(vc < 0 || vc >= virtualChannels.size(),
                 "Garnet invalid input VC: router=%d inport=%d vc=%d "
                 "num_vcs=%zu", m_router->get_id(), m_id, vc,
                 virtualChannels.size());
        const int vnet = vc / m_vc_per_vnet;
        panic_if(virtualChannels[vc].isFull(),
                 "Garnet input VC capacity exceeded: router=%d inport=%d "
                 "vc=%d vnet=%d occupancy=%u capacity=%u",
                 m_router->get_id(), m_id, vc, vnet,
                 virtualChannels[vc].getOccupancy(),
                 virtualChannels[vc].getCapacity());

        t_flit = m_in_link->consumeLink();
        DPRINTF(RubyNetwork, "Router[%d] Consuming:%s Width: %d Flit:%s\n",
        m_router->get_id(), m_in_link->name(),
        m_router->getBitWidth(), *t_flit);
        assert(t_flit->m_width == m_router->getBitWidth());
        t_flit->increment_hops(); // for stats

        if ((t_flit->get_type() == HEAD_) ||
            (t_flit->get_type() == HEAD_TAIL_)) {

            assert(virtualChannels[vc].get_state() == IDLE_);
            set_vc_active(vc, curTick());

            // Route computation for this vc
            int outport = m_router->route_compute(t_flit->get_route(),
                m_id, m_direction);

            // Update output port in VC
            // All flits in this packet will use this output port
            // The output port field in the flit is updated after it wins SA
            grant_outport(vc, outport);

        } else {
            assert(virtualChannels[vc].get_state() == ACTIVE_);
        }


        // Account the old occupancy over elapsed router cycles before the
        // insertion changes it.  Pops use the same event-integration rule.
        accountVc(vc);
        // Buffer the flit
        virtualChannels[vc].insertFlit(t_flit);
        m_vc_high_water[vc] = std::max<uint64_t>(
            m_vc_high_water[vc], virtualChannels[vc].getOccupancy());
        m_router->get_net_ptr()->observeInputVc(
            vnet, virtualChannels[vc].getOccupancy(),
            virtualChannels[vc].getCapacity());

        // number of writes same as reads
        // any flit that is written will be read only once
        m_num_buffer_writes[vnet]++;
        m_num_buffer_reads[vnet]++;

        Cycles pipe_stages = m_router->get_pipe_stages();
        if (pipe_stages == 1) {
            // 1-cycle router
            // Flit goes for SA directly
            t_flit->advance_stage(SA_, curTick());
        } else {
            assert(pipe_stages > 1);
            // Router delay is modeled by making flit wait in buffer for
            // (pipe_stages cycles - 1) cycles before going for SA

            Cycles wait_time = pipe_stages - Cycles(1);
            t_flit->advance_stage(SA_, m_router->clockEdge(wait_time));

            // Wakeup the router in that cycle to perform SA
            m_router->schedule_wakeup(Cycles(wait_time));
        }

        if (m_in_link->isReady(curTick())) {
            m_router->schedule_wakeup(Cycles(1));
        }
    }
}

void
InputUnit::collateStats()
{
    for (int vc = 0; vc < virtualChannels.size(); ++vc)
        accountVc(vc);
}

void
InputUnit::resetInputVcHighWater()
{
    for (unsigned vc = 0; vc < virtualChannels.size(); ++vc)
        m_vc_high_water[vc] = virtualChannels[vc].getOccupancy();
}

void
InputUnit::appendInputVcHighWater(GarnetInputVcHighWater &entries) const
{
    for (unsigned vc = 0; vc < virtualChannels.size(); ++vc) {
        entries.push_back({
            m_router->get_id(), m_id, vc,
            vc / static_cast<unsigned>(m_vc_per_vnet),
            m_vc_high_water[vc], virtualChannels[vc].getCapacity()});
    }
}

// Send a credit back to upstream router for this VC.
// Called by SwitchAllocator when the flit in this VC wins the Switch.
void
InputUnit::increment_credit(int in_vc, bool free_signal, Tick curTime)
{
    DPRINTF(RubyNetwork, "Router[%d]: Sending a credit vc:%d free:%d to %s\n",
    m_router->get_id(), in_vc, free_signal, m_credit_link->name());
    Credit *t_credit = new Credit(in_vc, free_signal, curTime);
    creditQueue.insert(t_credit);
    m_credit_link->scheduleEventAbsolute(m_router->clockEdge(Cycles(1)));
}

bool
InputUnit::functionalRead(Packet *pkt, WriteMask &mask)
{
    bool read = false;
    for (auto& virtual_channel : virtualChannels) {
        if (virtual_channel.functionalRead(pkt, mask))
            read = true;
    }

    return read;
}

uint32_t
InputUnit::functionalWrite(Packet *pkt)
{
    uint32_t num_functional_writes = 0;
    for (auto& virtual_channel : virtualChannels) {
        num_functional_writes += virtual_channel.functionalWrite(pkt);
    }

    return num_functional_writes;
}

void
InputUnit::resetStats()
{
    const Cycles now = m_router->curCycle();
    for (unsigned vc = 0; vc < virtualChannels.size(); ++vc) {
        m_vc_last_accounted_cycle[vc] = now;
        m_vc_high_water[vc] = virtualChannels[vc].getOccupancy();
    }
    for (int j = 0; j < m_num_buffer_reads.size(); j++) {
        m_num_buffer_reads[j] = 0;
        m_num_buffer_writes[j] = 0;
    }
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
