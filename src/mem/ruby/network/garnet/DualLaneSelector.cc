/*
 * Copyright (c) 2026 The gem5 AI Mesh project
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution.
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

#include "mem/ruby/network/garnet/DualLaneSelector.hh"

#include "debug/GarnetDualLane.hh"
#include "mem/ruby/network/garnet/InputUnit.hh"
#include "mem/ruby/network/garnet/NetworkLink.hh"
#include "mem/ruby/network/garnet/Router.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

DualLaneSelector::DualLaneSelector(Router *router, InputUnit *lane0,
                                   InputUnit *lane1)
  : m_router(router), m_lane{lane0, lane1}
{
    panic_if(!m_lane[0] || !m_lane[1],
             "Router %d dual-lane selector requires both lane inputs",
             m_router->get_id());
    panic_if(m_lane[0]->get_in_link() == nullptr,
             "Router %d dual-lane selector lane 0 owns the shared link",
             m_router->get_id());
}

void
DualLaneSelector::wakeup()
{
    NetworkLink *link = m_lane[0]->get_in_link();
    if (!link->isReady(curTick()))
        return;

    flit *t_flit = link->peekLink();
    const int vc = t_flit->get_vc();
    const uint32_t vnet = vc / m_lane[0]->getVcsPerVnet();
    const bool head = (t_flit->get_type() == HEAD_) ||
                      (t_flit->get_type() == HEAD_TAIL_);

    // Occupancy snapshot taken before this cycle's enqueue/dequeue; both
    // counts come from the lanes' real FIFO groups (spec 3.1/3.3).
    const DualLaneDecision decision = dualLaneSelect(
        m_lane[0]->vnetOccupancy(vnet), m_lane[1]->vnetOccupancy(vnet));
    InputUnit *unit = m_lane[decision.selected];
    const bool accepted = unit->canAccept(vc, head);
    DPRINTF(GarnetDualLane,
            "DL_SELECT cycle=%llu router=%d ingress=%d lane0=%d "
            "lane1=%d vnet=%d packet=%d flit=%d "
            "count0=%u count1=%u selected=%u accepted=%d\n",
            m_router->curCycle(), m_router->get_id(),
            m_lane[0]->get_id(),
            m_lane[0]->get_id(), m_lane[1]->get_id(), vnet,
            t_flit->getPacketID(), t_flit->get_id(),
            decision.count0, decision.count1, decision.selected, accepted);

    if (!accepted) {
        // Backpressure: the flit stays in the finite link queue and is
        // re-compared next cycle; never diverted to the larger-count lane.
        m_router->schedule_wakeup(Cycles(1));
        return;
    }

    link->consumeLink();
    unit->enqueueFlit(t_flit);

    if (link->isReady(curTick()))
        m_router->schedule_wakeup(Cycles(1));
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
