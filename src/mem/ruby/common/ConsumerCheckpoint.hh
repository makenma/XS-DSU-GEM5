#ifndef MEM_RUBY_COMMON_CONSUMER_CHECKPOINT_HH
#define MEM_RUBY_COMMON_CONSUMER_CHECKPOINT_HH

#include "mem/ruby/common/Consumer.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ruby
{
template<class Archive>
void Consumer::checkpoint(Archive &archive)
{
    archive & m_wakeup_ticks;
    gem5::checkpoint::event(archive, m_wakeup_event, *em);
}
}
#endif
