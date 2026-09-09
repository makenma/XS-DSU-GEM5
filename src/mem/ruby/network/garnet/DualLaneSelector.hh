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

#ifndef __MEM_RUBY_NETWORK_GARNET_DUAL_LANE_SELECTOR_HH__
#define __MEM_RUBY_NETWORK_GARNET_DUAL_LANE_SELECTOR_HH__

#include "mem/ruby/network/garnet/DualLane.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

class InputUnit;
class Router;

/** One shared P (Local) ingress feeding two lane input FIFO groups.
 *
 *  On the shared link's wakeup, each flit is assigned to exactly one lane
 *  by the strict occupancy formula over the two lanes' real per-vnet FIFO
 *  groups.  When the selected lane cannot accept (capacity or VC state),
 *  the flit stays in the finite upstream link queue and the other lane is
 *  never substituted (spec 3.3).  The selector owns no occupancy ledger:
 *  counts always come from the InputUnit FIFOs themselves. */
class DualLaneSelector
{
  public:
    DualLaneSelector(Router *router, InputUnit *lane0, InputUnit *lane1);

    void wakeup();

  private:
    Router *m_router;
    InputUnit *m_lane[2];
};

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_NETWORK_GARNET_DUAL_LANE_SELECTOR_HH__
