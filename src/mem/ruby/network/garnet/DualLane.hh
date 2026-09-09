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

#ifndef __MEM_RUBY_NETWORK_GARNET_DUAL_LANE_HH__
#define __MEM_RUBY_NETWORK_GARNET_DUAL_LANE_HH__

#include <cstdint>
#include <string>

#include "mem/ruby/common/TypeDefines.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

/** Lane dimension of a physical port: 0 = plain name, 1 = "_ext" suffix.
 *  Lane and logical direction are independent dimensions. */
using LaneId = uint8_t;

constexpr LaneId MaxLaneId = 1;

struct PortIdentity
{
    PortDirection logical;
    LaneId lane;
};

/** External port name -> (logical direction, lane).  The single mapping
 *  entry point between external names and port identity; "_ext" never
 *  falls into an unknown-direction fallback. */
inline PortIdentity
parsePortName(const PortDirection &physical)
{
    const std::string suffix = "_ext";
    if (physical.size() > suffix.size() &&
        physical.compare(physical.size() - suffix.size(), suffix.size(),
                         suffix) == 0) {
        return {physical.substr(0, physical.size() - suffix.size()), 1};
    }
    return {physical, 0};
}

/** (logical direction, lane) -> external port name. */
inline PortDirection
lanePortName(const PortDirection &logical, LaneId lane)
{
    return lane == 0 ? logical : logical + "_ext";
}

struct DualLaneDecision
{
    uint32_t count0 = 0;
    uint32_t count1 = 0;
    LaneId selected = 0;
};

/** Spec 3.2: strict per-flit comparison of the same P input and same
 *  vnet's two lane FIFO occupancies; smaller wins, tie takes lane 0. */
inline DualLaneDecision
dualLaneSelect(uint32_t count0, uint32_t count1)
{
    return {count0, count1, count0 <= count1 ? LaneId{0} : LaneId{1}};
}

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_NETWORK_GARNET_DUAL_LANE_HH__
