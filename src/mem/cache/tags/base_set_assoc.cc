/*
 * Copyright (c) 2012-2014 ARM Limited
 * All rights reserved.
 *
 * The license below extends only to copyright in the software and shall
 * not be construed as granting a license to any other intellectual
 * property including but not limited to intellectual property relating
 * to a hardware implementation of the functionality of the software
 * licensed hereunder.  You may use the software subject to the license
 * terms below provided that you ensure that this notice is replicated
 * unmodified and in its entirety in all distributions of the software,
 * modified or unmodified, in source code or in binary form.
 *
 * Copyright (c) 2003-2005,2014 The Regents of The University of Michigan
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

/**
 * @file
 * Definitions of a conventional tag store.
 */

#include "mem/cache/tags/base_set_assoc.hh"

#include <algorithm>
#include <string>
#include <typeinfo>

#include "base/intmath.hh"
#include "mem/cache/replacement_policies/lru_rp.hh"
#include "mem/cache/tags/indexing_policies/set_associative.hh"
#include "sim/serialize.hh"

namespace gem5
{

BaseSetAssoc::BaseSetAssoc(const Params &p)
    :BaseTags(p), allocAssoc(p.assoc), blks(p.size / p.block_size),
     sequentialAccess(p.sequential_access),
     replacementPolicy(p.replacement_policy)
{
    // There must be a indexing policy
    fatal_if(!p.indexing_policy, "An indexing policy is required");

    // Check parameters
    if (blkSize < 4 || !isPowerOf2(blkSize)) {
        fatal("Block size must be at least 4 and a power of 2");
    }
}

void
BaseSetAssoc::tagsInit()
{
    // Initialize all blocks
    for (unsigned blk_index = 0; blk_index < numBlocks; blk_index++) {
        // Locate next cache block
        CacheBlk* blk = &blks[blk_index];

        // Link block to indexing policy
        indexingPolicy->setEntry(blk, blk_index);

        // Associate a data chunk to the block
        blk->data = &dataBlks[blkSize*blk_index];

        // Associate a replacement data entry to the block
        blk->replacementData = replacementPolicy->instantiateEntry();
    }
}

void
BaseSetAssoc::invalidate(CacheBlk *blk)
{
    BaseTags::invalidate(blk);

    // Decrease the number of tags in use
    stats.tagsInUse--;

    // Invalidate replacement data
    replacementPolicy->invalidate(blk->replacementData);
}

void
BaseSetAssoc::moveBlock(CacheBlk *src_blk, CacheBlk *dest_blk)
{
    BaseTags::moveBlock(src_blk, dest_blk);

    // Since the blocks were using different replacement data pointers,
    // we must touch the replacement data of the new entry, and invalidate
    // the one that is being moved.
    replacementPolicy->invalidate(src_blk->replacementData);
    replacementPolicy->reset(dest_blk->replacementData);
}

bool
BaseSetAssoc::supportsWarmCheckpoint() const
{
    return typeid(*this) == typeid(BaseSetAssoc) &&
        typeid(*indexingPolicy) == typeid(SetAssociative) &&
        typeid(*replacementPolicy) == typeid(replacement_policy::LRU);
}

void
BaseSetAssoc::serializeWarm(CheckpointOut &cp) const
{
    fatal_if(!supportsWarmCheckpoint(),
             "%s warm checkpoints require plain set-associative/LRU tags",
             name());
    const auto *lru = static_cast<replacement_policy::LRU *>(replacementPolicy);
    SERIALIZE_SCALAR(blkSize);
    SERIALIZE_SCALAR(numBlocks);
    SERIALIZE_SCALAR(allocAssoc);
    SERIALIZE_SCALAR(warmedUp);
    for (unsigned i = 0; i < blks.size(); ++i) {
        ScopedCheckpointSection section(cp, csprintf("block%u", i));
        const auto &blk = blks[i];
        fatal_if(blk._xsMeta.instXsMetadata,
                 "%s cannot checkpoint live O3 instruction metadata", name());
        paramOut(cp, "set", blk.getSet());
        paramOut(cp, "way", blk.getWay());
        paramOut(cp, "valid", blk.isValid());
        paramOut(cp, "secure", blk.isSecure());
        paramOut(cp, "tag", blk.getTag());
        paramOut(cp, "coherence", blk.coherence);
        paramOut(cp, "whenReady", blk.whenReady);
        paramOut(cp, "taskId", blk._taskId);
        paramOut(cp, "requestor", blk._srcRequestorId);
        paramOut(cp, "refCount", blk._refCount);
        paramOut(cp, "demandHits", blk._refDemandHits);
        paramOut(cp, "inserted", blk._tickInserted);
        paramOut(cp, "prefetched", blk._prefetched);
        paramOut(cp, "everPrefetched", blk._ever_prefetched);
        paramOut(cp, "pendingInvalidate", blk._needInvalidate);
        paramOut(cp, "hitWay", blk._way);
        paramOut(cp, "xsValid", blk._xsMeta.validXsMetadata);
        paramOut(cp, "prefetchSource", unsigned(blk._xsMeta.prefetchSource));
        paramOut(cp, "prefetchDepth", blk._xsMeta.prefetchDepth);
        paramOut(cp, "lruTick", lru->checkpointTick(blk.replacementData));
        if (blk.isValid())
            arrayParamOut(cp, "data", blk.data, blkSize);
        std::vector<ContextID> lockContexts;
        std::vector<Addr> lockLow, lockHigh;
        for (const auto &lock : blk.lockList) {
            lockContexts.push_back(lock.contextId);
            lockLow.push_back(lock.lowAddr);
            lockHigh.push_back(lock.highAddr);
        }
        SERIALIZE_CONTAINER(lockContexts);
        SERIALIZE_CONTAINER(lockLow);
        SERIALIZE_CONTAINER(lockHigh);
    }
}

void
BaseSetAssoc::unserializeWarm(CheckpointIn &cp)
{
    fatal_if(!supportsWarmCheckpoint(),
             "%s warm checkpoints require plain set-associative/LRU tags",
             name());
    unsigned savedBlockSize, savedCount;
    paramIn(cp, "blkSize", savedBlockSize);
    paramIn(cp, "numBlocks", savedCount);
    fatal_if(savedBlockSize != blkSize || savedCount != numBlocks,
             "%s warm checkpoint tag geometry changed", name());
    UNSERIALIZE_SCALAR(allocAssoc);
    fatal_if(allocAssoc == 0 ||
             allocAssoc > indexingPolicy->getPossibleEntries(0).size(),
             "%s invalid checkpoint allocation associativity", name());
    UNSERIALIZE_SCALAR(warmedUp);
    auto *lru = static_cast<replacement_policy::LRU *>(replacementPolicy);
    unsigned validCount = 0;
    for (unsigned i = 0; i < blks.size(); ++i) {
        ScopedCheckpointSection section(cp, csprintf("block%u", i));
        auto &blk = blks[i];
        unsigned set, way, source;
        bool valid, secure;
        Addr tag;
        Tick lruTick;
        paramIn(cp, "set", set);
        paramIn(cp, "way", way);
        fatal_if(set != blk.getSet() || way != blk.getWay(),
                 "%s checkpoint block location changed", name());
        paramIn(cp, "valid", valid);
        paramIn(cp, "secure", secure);
        paramIn(cp, "tag", tag);
        blk.invalidate();
        if (valid) {
            blk.TaggedEntry::insert(tag, secure);
            ++validCount;
        }
        paramIn(cp, "coherence", blk.coherence);
        paramIn(cp, "whenReady", blk.whenReady);
        paramIn(cp, "taskId", blk._taskId);
        paramIn(cp, "requestor", blk._srcRequestorId);
        paramIn(cp, "refCount", blk._refCount);
        paramIn(cp, "demandHits", blk._refDemandHits);
        paramIn(cp, "inserted", blk._tickInserted);
        paramIn(cp, "prefetched", blk._prefetched);
        paramIn(cp, "everPrefetched", blk._ever_prefetched);
        paramIn(cp, "pendingInvalidate", blk._needInvalidate);
        paramIn(cp, "hitWay", blk._way);
        paramIn(cp, "xsValid", blk._xsMeta.validXsMetadata);
        paramIn(cp, "prefetchSource", source);
        blk._xsMeta.prefetchSource = PrefetchSourceType(source);
        paramIn(cp, "prefetchDepth", blk._xsMeta.prefetchDepth);
        blk._xsMeta.instXsMetadata = nullptr;
        paramIn(cp, "lruTick", lruTick);
        lru->restoreCheckpointTick(blk.replacementData, lruTick);
        fatal_if((blk.coherence & ~CacheBlk::AllBits) ||
                 (valid && (blk._tickInserted > curTick() ||
                            blk.whenReady < blk._tickInserted)),
                 "%s invalid warm checkpoint coherence or timing", name());
        if (valid)
            arrayParamIn(cp, "data", blk.data, blkSize);
        else
            std::fill_n(blk.data, blkSize, 0);
        std::vector<ContextID> lockContexts;
        std::vector<Addr> lockLow, lockHigh;
        UNSERIALIZE_CONTAINER(lockContexts);
        UNSERIALIZE_CONTAINER(lockLow);
        UNSERIALIZE_CONTAINER(lockHigh);
        fatal_if(lockContexts.size() != lockLow.size() ||
                 lockContexts.size() != lockHigh.size(),
                 "%s invalid checkpoint lock vectors", name());
        for (unsigned j = 0; j < lockContexts.size(); ++j) {
            fatal_if(!valid || lockLow[j] > lockHigh[j] ||
                     lockHigh[j] - lockLow[j] >= blkSize ||
                     (lockLow[j] & ~Addr(blkSize - 1)) !=
                         regenerateBlkAddr(&blk) ||
                     (lockHigh[j] & ~Addr(blkSize - 1)) !=
                         regenerateBlkAddr(&blk),
                     "%s invalid checkpoint LR/SC range", name());
            auto request = std::make_shared<Request>(
                lockLow[j], lockHigh[j] - lockLow[j] + 1,
                Request::LLSC, Request::funcRequestorId);
            request->setContext(lockContexts[j]);
            blk.lockList.emplace_back(request);
        }
    }
    stats.tagsInUse = validCount;
}

} // namespace gem5
