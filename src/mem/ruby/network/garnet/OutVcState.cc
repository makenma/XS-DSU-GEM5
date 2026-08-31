/*
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


#include "mem/ruby/network/garnet/OutVcState.hh"

#include "base/logging.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

OutVcState::OutVcState(int id, unsigned vnet, uint32_t depth)
    : m_id(id), m_vnet(vnet), m_time(0), m_vc_state(IDLE_),
      m_credit_count(depth), m_max_credit_count(depth)
{
    fatal_if(depth == 0, "OutVcState vc=%d vnet=%u depth must be >= 1",
             m_id, m_vnet);
}

void
OutVcState::increment_credit()
{
    panic_if(m_credit_count >= m_max_credit_count,
             "Garnet credit overflow: vc=%d vnet=%u credit=%u depth=%u",
             m_id, m_vnet, m_credit_count, m_max_credit_count);
    m_credit_count++;
}

void
OutVcState::decrement_credit()
{
    panic_if(m_credit_count == 0,
             "Garnet credit underflow: vc=%d vnet=%u credit=0 depth=%u",
             m_id, m_vnet, m_max_credit_count);
    m_credit_count--;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
