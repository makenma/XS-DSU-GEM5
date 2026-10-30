#ifndef __ARCH_RISCV_TLB_CHECKPOINT_HH__
#define __ARCH_RISCV_TLB_CHECKPOINT_HH__

#include <list>
#include <vector>

#include "arch/riscv/pagetable.hh"
#include "base/cprintf.hh"

namespace gem5::RiscvISA::tlb_checkpoint
{
using ScopedCheckpointSection = Serializable::ScopedCheckpointSection;

/** Preserve physical slot placement and free-list order, as both participate
 * in replacement. Store the actual trie key/prefix rather than reconstructing
 * an ASID-only key: G-stage and prefetch banks use different key domains. */
inline void
saveBank(CheckpointOut &cp, const char *name,
         const std::vector<TlbEntry> &entries,
         const std::list<TlbEntry *> &free)
{
    ScopedCheckpointSection section(cp, name);
    const size_t capacity = entries.size();
    paramOut(cp, "capacity", capacity);
    std::vector<size_t> available;
    std::vector<bool> covered(capacity, false);
    for (const auto *entry : free) {
        const size_t index = entry - entries.data();
        panic_if(index >= capacity || covered[index] || entry->trieHandle,
                 "Invalid TLB free list in %s", name);
        covered[index] = true;
        available.push_back(index);
    }
    arrayParamOut(cp, "free", available);
    for (size_t i = 0; i < capacity; ++i) {
        const auto &entry = entries[i];
        if (covered[i]) continue;
        panic_if(!entry.trieHandle, "TLB slot missing from free list in %s", name);
        ScopedCheckpointSection slot(cp, csprintf("Entry%u", i));
        paramOut(cp, "key", entry.trieHandle->key);
        const unsigned prefix = __builtin_popcountll(entry.trieHandle->mask);
        paramOut(cp, "prefix", prefix);
        entry.serialize(cp);
    }
}

inline void
loadBank(CheckpointIn &cp, const char *name, std::vector<TlbEntry> &entries,
         TlbEntryTrie &trie, std::list<TlbEntry *> &free)
{
    ScopedCheckpointSection section(cp, name);
    size_t capacity;
    paramIn(cp, "capacity", capacity);
    fatal_if(capacity != entries.size(), "TLB bank %s capacity changed", name);
    trie.clear();
    free.clear();
    for (auto &entry : entries) entry = TlbEntry{};
    std::vector<size_t> available;
    arrayParamIn(cp, "free", available);
    std::vector<bool> isFree(capacity, false);
    for (const size_t index : available) {
        fatal_if(index >= capacity || isFree[index],
                 "Invalid checkpoint free list in TLB bank %s", name);
        isFree[index] = true;
        free.push_back(&entries[index]);
    }
    for (size_t i = 0; i < capacity; ++i) {
        if (isFree[i]) continue;
        auto &entry = entries[i];
        ScopedCheckpointSection slot(cp, csprintf("Entry%u", i));
        Addr key;
        unsigned prefix;
        paramIn(cp, "key", key);
        paramIn(cp, "prefix", prefix);
        fatal_if(prefix == 0 || prefix > TlbEntryTrie::MaxBits,
                 "Invalid trie prefix in TLB bank %s", name);
        entry.unserialize(cp);
        entry.trieHandle = trie.insert(key, prefix, &entry);
    }
}

} // namespace gem5::RiscvISA::tlb_checkpoint
#endif
