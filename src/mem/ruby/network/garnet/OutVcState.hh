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


#ifndef __MEM_RUBY_NETWORK_GARNET_0_OUTVCSTATE_HH__
#define __MEM_RUBY_NETWORK_GARNET_0_OUTVCSTATE_HH__

#include "mem/ruby/network/garnet/CommonTypes.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

class OutVcState
{
  public:
    OutVcState(int id, unsigned vnet, uint32_t depth);

    uint32_t get_credit_count() const { return m_credit_count; }
    uint32_t get_max_credit_count() const { return m_max_credit_count; }
    uint64_t get_sent_count() const { return m_sent_count; }
    uint64_t get_returned_count() const { return m_returned_count; }
    unsigned get_vnet() const { return m_vnet; }
    inline bool has_credit() const { return m_credit_count > 0; }
    void increment_credit();
    void decrement_credit();

    inline bool
    isInState(VC_state_type state, Tick request_time) const
    {
        return ((m_vc_state == state) && (request_time >= m_time) );
    }
    VC_state_type getState() const { return m_vc_state; }
    inline void
    setState(VC_state_type state, Tick time)
    {
        m_vc_state = state;
        m_time = time;
    }

  private:
    int m_id ;
    unsigned m_vnet;
    Tick m_time;
    VC_state_type m_vc_state;
    uint32_t m_credit_count;
    uint32_t m_max_credit_count;
    uint64_t m_sent_count;
    uint64_t m_returned_count;
};

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif //__MEM_RUBY_NETWORK_GARNET_0_OUTVCSTATE_HH__
