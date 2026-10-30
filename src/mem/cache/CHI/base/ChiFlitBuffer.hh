#ifndef __MEM_CACHE_CHI_FLIT_BUFFER_HH__
#define __MEM_CACHE_CHI_FLIT_BUFFER_HH__

#include <cassert>
#include <vector>

#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi
{

/** Wire buffers cannot contain more flits than their configured credits.
 * Allocate that backing store once. Admission freezes borrowed DAT bytes;
 * recycling a slot never changes the ownership of an already dequeued flit.
 */
class ChiFlitBuffer
{
  public:
    struct Item { FlitVariant flit; uint64_t accepted = 0; };

    void configure(std::size_t capacity)
    {
        assert(empty());
        slots.resize(capacity);
        head = tail = count = 0;
    }
    bool empty() const { return count == 0; }
    bool full() const { return count == slots.size(); }
    std::size_t size() const { return count; }
    const Item &front() const { assert(!empty()); return slots[head]; }

    void push(const FlitVariant &flit, uint64_t accepted)
    {
        assert(!full());
        auto &slot = slots[tail];
        slot.flit = flit;
        if (auto *dat = std::get_if<RawDat>(&slot.flit)) dat->ownPayload();
        slot.accepted = accepted;
        if (++tail == slots.size()) tail = 0;
        ++count;
    }
    FlitVariant pop()
    {
        assert(!empty());
        auto flit = std::move(slots[head].flit);
        if (++head == slots.size()) head = 0;
        --count;
        return flit;
    }

  private:
    std::vector<Item> slots;
    std::size_t head = 0, tail = 0, count = 0;
};

} // namespace gem5::Chi
#endif
