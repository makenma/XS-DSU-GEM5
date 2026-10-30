#include <gtest/gtest.h>
#include <sys/stat.h>
#include <unistd.h>

#include <fstream>
#include <sstream>

#include "arch/riscv/tlb_checkpoint.hh"
#include "base/gtest/cur_tick_fake.hh"
#include "base/gtest/serialization_fixture.hh"

using namespace gem5;
using namespace gem5::RiscvISA;
GTestTickHandler tlbCheckpointTick;

using TlbCheckpoint = SerializationFixture;

TEST_F(TlbCheckpoint, SparseBanksPreserveSlotsKeysFieldsAndFreeOrder)
{
    for (const char *bank : {"L1", "L2L3", "L2L2", "L2L1", "L2L0",
                            "L2Sp", "Forward", "Back"}) {
        std::vector<TlbEntry> original(8), restored(8);
        TlbEntryTrie sourceTrie, restoredTrie;
        std::list<TlbEntry *> available, restoredFree;
        for (size_t slot : {7, 1, 5, 3}) available.push_back(&original[slot]);
        std::vector<Addr> keys;
        for (size_t slot : {0, 2, 4, 6}) {
            auto &entry = original[slot];
            entry.paddr = 0x84000000 + slot * 4096;
            entry.vaddr = 0xffffffc000000000ULL + slot * 4096;
            entry.gpaddr = 0x200000 + slot * 4096;
            entry.logBytes = 12 + slot / 2 * 9;
            entry.translateMode = slot / 2;
            entry.asid = slot + 5;
            entry.vmid = slot + 100;
            entry.pte = 0x12345678 | slot;
            entry.pteVS = 0x87654321 | (slot << 4);
            entry.lruSeq = 100 - slot;
            entry.level = slot / 2;
            entry.VSlevel = 3 - slot / 2;
            entry.index = 37 + slot;
            entry.isSquashed = true;
            entry.used = true;
            entry.isPre = true;
            entry.fromForwardPreReq = true;
            entry.fromBackPreReq = true;
            entry.preSign = true;
            const Addr key = (Addr(entry.vmid) << 48) |
                (Addr(entry.translateMode) << 46) | (entry.gpaddr >> 12);
            keys.push_back(key);
            entry.trieHandle = sourceTrie.insert(key, 64 - entry.logBytes + 12,
                                                 &entry);
        }
        {
            std::ofstream out(getCptPath());
            tlb_checkpoint::saveBank(out, bank, original, available);
        }
        // Loading replaces a nonempty destination without retaining its old
        // trie nodes or free-list entries.
        restored[1].trieHandle = restoredTrie.insert(0x112233, 64, &restored[1]);
        restoredFree.push_back(&restored[0]);
        CheckpointIn in(getDirName());
        tlb_checkpoint::loadBank(in, bank, restored, restoredTrie, restoredFree);
        EXPECT_EQ(restoredTrie.lookup(0x112233), nullptr);
        std::vector<size_t> slots;
        for (auto *entry : restoredFree) slots.push_back(entry - restored.data());
        EXPECT_EQ(slots, (std::vector<size_t>{7, 1, 5, 3}));
        for (size_t n = 0; n < keys.size(); ++n) {
            const size_t slot = n * 2;
            EXPECT_EQ(restoredTrie.lookup(keys[n]), &restored[slot]);
            EXPECT_EQ(restored[slot].trieHandle->key,
                      original[slot].trieHandle->key);
            EXPECT_EQ(restored[slot].trieHandle->mask,
                      original[slot].trieHandle->mask);
        }
        std::ostringstream before, after;
        tlb_checkpoint::saveBank(before, bank, original, available);
        tlb_checkpoint::saveBank(after, bank, restored, restoredFree);
        EXPECT_EQ(before.str(), after.str());
        // The next allocation must select the same physical way.
        EXPECT_EQ(available.front() - original.data(),
                  restoredFree.front() - restored.data());
    }
}

TEST_F(TlbCheckpoint, EmptyAndFullBank)
{
    for (bool full : {false, true}) {
        std::vector<TlbEntry> original(4), restored(4);
        TlbEntryTrie sourceTrie, restoredTrie;
        std::list<TlbEntry *> available, restoredFree;
        for (size_t i = 0; i < original.size(); ++i) {
            ASSERT_EQ(original[i].trieHandle, nullptr);
            if (full) {
                original[i].logBytes = 12;
                original[i].lruSeq = 7 - i;
                original[i].trieHandle = sourceTrie.insert(0x100 + i, 64,
                                                          &original[i]);
            } else {
                available.push_front(&original[i]);
            }
        }
        {
            std::ofstream out(getCptPath());
            tlb_checkpoint::saveBank(out, "Bank", original, available);
        }
        CheckpointIn in(getDirName());
        tlb_checkpoint::loadBank(in, "Bank", restored, restoredTrie, restoredFree);
        std::ostringstream before, after;
        tlb_checkpoint::saveBank(before, "Bank", original, available);
        tlb_checkpoint::saveBank(after, "Bank", restored, restoredFree);
        EXPECT_EQ(before.str(), after.str());
    }
}

TEST_F(TlbCheckpoint, RejectChangedCapacity)
{
    std::vector<TlbEntry> original(4), restored(2);
    TlbEntryTrie restoredTrie;
    std::list<TlbEntry *> available, restoredFree;
    for (auto &entry : original) available.push_back(&entry);
    {
        std::ofstream out(getCptPath());
        tlb_checkpoint::saveBank(out, "Bank", original, available);
    }
    CheckpointIn in(getDirName());
    EXPECT_ANY_THROW(tlb_checkpoint::loadBank(in, "Bank", restored,
                                             restoredTrie, restoredFree));
}
