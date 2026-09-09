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


#include "mem/ruby/network/garnet/SwitchAllocator.hh"

#include "debug/RubyNetwork.hh"
#include "debug/GarnetDualLane.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/garnet/InputUnit.hh"
#include "mem/ruby/network/garnet/OutputUnit.hh"
#include "mem/ruby/network/garnet/Router.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

SwitchAllocator::SwitchAllocator(Router *router)
    : Consumer(router)
{
    m_router = router;
    m_num_vcs = m_router->get_num_vcs();
    m_vc_per_vnet = m_router->get_vc_per_vnet();

    m_input_arbiter_activity = 0;
    m_output_arbiter_activity = 0;
}

void
SwitchAllocator::init()
{
    m_num_inports = m_router->get_num_inports();
    m_num_outports = m_router->get_num_outports();
    const auto lane_count = m_router->dualLane() ? 2 : 1;
    m_lanes.clear();
    for (LaneId lane = 0; lane < lane_count; ++lane) {
        std::vector<int> ports;
        for (int port = 0; port < m_num_inports; ++port) {
            if (m_router->getInputUnit(port)->get_lane() == lane)
                ports.push_back(port);
        }
        m_lanes.emplace_back(ports, m_num_outports, m_num_vcs);
    }
    m_local_merges.assign(m_num_outports, LaneMergeArbiter{});
}

/*
 * The wakeup function of the SwitchAllocator performs a 2-stage
 * seperable switch allocation. At the end of the 2nd stage, a free
 * output VC is assigned to the winning flits of each output port.
 * There is no separate VCAllocator stage like the one in garnet1.0.
 * At the end of this function, the router is rescheduled to wakeup
 * next cycle for peforming SA for any flits ready next cycle.
 */

void
SwitchAllocator::wakeup()
{
    arbitrate_inports(); // First stage of allocation
    arbitrate_outports(); // Second stage of allocation

    for (auto &lane : m_lanes) {
        for (size_t slot = 0; slot < lane.size(); ++slot) {
            if (lane.pendingVc(slot) != -1) {
                m_router->getInputUnit(lane.inputPort(slot))->recordStall(
                    lane.pendingVc(slot), GarnetInputStall::SaLost);
            }
        }
        lane.clearRequests();
    }

    check_for_wakeup();
}

/*
 * SA-I (or SA-i) loops through all input VCs at every input port,
 * and selects one in a round robin manner.
 *    - For HEAD/HEAD_TAIL flits only selects an input VC whose output port
 *     has at least one free output VC.
 *    - For BODY/TAIL flits, only selects an input VC that has credits
 *      in its output VC.
 * Places a request for the output port from this input VC.
 */

void
SwitchAllocator::arbitrate_inports()
{
    for (auto &lane : m_lanes) {
        for (size_t slot = 0; slot < lane.size(); ++slot) {
            const int inport = lane.inputPort(slot);
            auto input = m_router->getInputUnit(inport);
            int invc = lane.firstVc(slot);
            for (int examined = 0; examined < m_num_vcs; ++examined) {
                if (input->need_stage(invc, SA_, curTick())) {
                    const int outport = input->get_outport(invc);
                    if (send_allowed(inport, invc, outport,
                                     input->get_outvc(invc))) {
                        ++m_input_arbiter_activity;
                        lane.request(slot, outport, invc);
                        break;
                    }
                }
                invc = (invc + 1) % m_num_vcs;
            }
        }
    }
}

/*
 * SA-II (or SA-o) loops through all output ports,
 * and selects one input VC (that placed a request during SA-I)
 * as the winner for this output port in a round robin manner.
 *      - For HEAD/HEAD_TAIL flits, performs simplified outvc allocation.
 *        (i.e., select a free VC from the output port).
 *      - For BODY/TAIL flits, decrement a credit in the output vc.
 * The winning flit is read out from the input VC and sent to the
 * CrossbarSwitch.
 * An increment_credit signal is sent from the InputUnit
 * to the upstream router. For HEAD_TAIL/TAIL flits, is_free_signal in the
 * credit is set to true.
 */

void
SwitchAllocator::arbitrate_outports()
{
    for (int outport = 0; outport < m_num_outports; ++outport) {
        if (m_router->sharedLocalOutport(outport)) {
            arbitrate_merged_local_outport(outport);
        } else {
            const auto identity = parsePortName(
                m_router->getOutportDirection(outport));
            auto &lane = m_lanes.at(identity.lane);
            if (const auto winner = lane.candidate(outport))
                grantOutport(outport, lane, *winner);
        }
    }
}

void
SwitchAllocator::arbitrate_merged_local_outport(int outport)
{
    const std::array<std::optional<SwitchGrant>, 2> candidates{
        m_lanes[0].candidate(outport), m_lanes[1].candidate(outport)};
    auto &merge = m_local_merges[outport];
    const auto selected = merge.select(
        {candidates[0].has_value(), candidates[1].has_value()});
    if (!selected)
        return;

    const int req0 = candidates[0] ?
        m_lanes[0].inputPort(candidates[0]->slot) : -1;
    const int req1 = candidates[1] ?
        m_lanes[1].inputPort(candidates[1]->slot) : -1;
    DPRINTF(GarnetDualLane,
            "DL_MERGE cycle=%llu router=%d outport=%d req0=%d req1=%d "
            "selected=%u winner=%d\n",
            m_router->curCycle(), m_router->get_id(), outport, req0, req1,
            *selected, *selected == 0 ? req0 : req1);
    grantOutport(outport, m_lanes[*selected], *candidates[*selected]);
    merge.commit(*selected);
}

void
SwitchAllocator::grantOutport(int outport, LaneArbiter &lane,
                               const SwitchGrant &grant)
{
    const int inport = lane.inputPort(grant.slot);
    auto output_unit = m_router->getOutputUnit(outport);
    auto input_unit = m_router->getInputUnit(inport);

    // grant this outport to this inport
    int invc = grant.vc;

    int outvc = input_unit->get_outvc(invc);
    if (outvc == -1) {
        // VC Allocation - select any free VC from outport
        outvc = vc_allocate(outport, inport, invc);
    }

    // remove flit from Input VC
    flit *t_flit = input_unit->getTopFlit(invc);

    DPRINTF(RubyNetwork, "SwitchAllocator at Router %d "
                         "granted outvc %d at outport %d "
                         "to invc %d at inport %d to flit %s at "
                         "cycle: %lld\n",
            m_router->get_id(), outvc,
            m_router->getPortDirectionName(
                output_unit->get_direction()),
            invc,
            m_router->getPortDirectionName(
                input_unit->get_direction()),
                *t_flit,
            m_router->curCycle());


    // Update outport field in the flit since this is
    // used by CrossbarSwitch code to send it out of
    // correct outport.
    // Note: post route compute in InputUnit,
    // outport is updated in VC, but not in flit
    t_flit->set_outport(outport);

    // set outvc (i.e., invc for next hop) in flit
    // (This was updated in VC by vc_allocate, but not in flit)
    t_flit->set_vc(outvc);

    // decrement credit in outvc
    output_unit->decrement_credit(outvc);

    // flit ready for Switch Traversal
    t_flit->advance_stage(ST_, curTick());
    m_router->grant_switch(inport, t_flit);
    m_output_arbiter_activity++;

    if ((t_flit->get_type() == TAIL_) ||
        t_flit->get_type() == HEAD_TAIL_) {

        // This Input VC should now be empty
        assert(!(input_unit->isReady(invc, curTick())));

        // Free this VC
        input_unit->set_vc_idle(invc, curTick());

        // Send a credit back
        // along with the information that this VC is now idle
        input_unit->increment_credit(invc, true, curTick());
    } else {
        // Send a credit back
        // but do not indicate that the VC is idle
        input_unit->increment_credit(invc, false, curTick());
    }

    lane.commit(outport, grant);
}

/*
 * A flit can be sent only if
 * (1) there is at least one free output VC at the
 *     output port (for HEAD/HEAD_TAIL),
 *  or
 * (2) if there is at least one credit (i.e., buffer slot)
 *     within the VC for BODY/TAIL flits of multi-flit packets.
 * and
 * (3) pt-to-pt ordering is not violated in ordered vnets, i.e.,
 *     there should be no other flit in this input port
 *     within an ordered vnet
 *     that arrived before this flit and is requesting the same output port.
 */

bool
SwitchAllocator::send_allowed(int inport, int invc, int outport, int outvc)
{
    // Check if outvc needed
    // Check if credit needed (for multi-flit packet)
    // Check if ordering violated (in ordered vnet)

    int vnet = get_vnet(invc);
    bool has_outvc = (outvc != -1);
    bool has_credit = false;

    auto output_unit = m_router->getOutputUnit(outport);
    if (!has_outvc) {

        // needs outvc
        // this is only true for HEAD and HEAD_TAIL flits.

        if (output_unit->has_free_vc(vnet)) {

            has_outvc = true;

            // each VC has at least one buffer,
            // so no need for additional credit check
            has_credit = true;
        }
    } else {
        has_credit = output_unit->has_credit(outvc);
    }

    // Account for the two distinct backpressure causes before retrying on a
    // later router edge.
    if (!has_outvc) {
        m_router->get_net_ptr()->incrementVcAllocStall(vnet);
        m_router->getInputUnit(inport)->recordStall(
            invc, GarnetInputStall::NoVc);
        return false;
    }
    if (!has_credit) {
        m_router->get_net_ptr()->incrementRouterCreditStall(vnet);
        m_router->getInputUnit(inport)->recordStall(
            invc, GarnetInputStall::Credit);
        return false;
    }


    // protocol ordering check
    if ((m_router->get_net_ptr())->isVNetOrdered(vnet)) {
        auto input_unit = m_router->getInputUnit(inport);

        // enqueue time of this flit
        Tick t_enqueue_time = input_unit->get_enqueue_time(invc);

        // check if any other flit is ready for SA and for same output port
        // and was enqueued before this flit
        int vc_base = vnet*m_vc_per_vnet;
        for (int vc_offset = 0; vc_offset < m_vc_per_vnet; vc_offset++) {
            int temp_vc = vc_base + vc_offset;
            if (input_unit->need_stage(temp_vc, SA_, curTick()) &&
               (input_unit->get_outport(temp_vc) == outport) &&
               (input_unit->get_enqueue_time(temp_vc) < t_enqueue_time)) {
                return false;
            }
        }
    }

    return true;
}

// Assign a free VC to the winner of the output port.
int
SwitchAllocator::vc_allocate(int outport, int inport, int invc)
{
    // Select a free VC from the output port
    int outvc =
        m_router->getOutputUnit(outport)->select_free_vc(get_vnet(invc));

    // has to get a valid VC since it checked before performing SA
    assert(outvc != -1);
    m_router->getInputUnit(inport)->grant_outvc(invc, outvc);
    return outvc;
}

// Wakeup the router next cycle to perform SA again
// if there are flits ready.
void
SwitchAllocator::check_for_wakeup()
{
    Tick nextCycle = m_router->clockEdge(Cycles(1));

    if (m_router->alreadyScheduled(nextCycle)) {
        return;
    }

    for (int i = 0; i < m_num_inports; i++) {
        for (int j = 0; j < m_num_vcs; j++) {
            if (m_router->getInputUnit(i)->need_stage(j, SA_, nextCycle)) {
                m_router->schedule_wakeup(Cycles(1));
                return;
            }
        }
    }
}

int
SwitchAllocator::get_vnet(int invc)
{
    int vnet = invc/m_vc_per_vnet;
    assert(vnet < m_router->get_num_vnets());
    return vnet;
}


void
SwitchAllocator::resetStats()
{
    m_input_arbiter_activity = 0;
    m_output_arbiter_activity = 0;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
