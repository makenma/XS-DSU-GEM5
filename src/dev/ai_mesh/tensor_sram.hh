#ifndef DEV_AI_MESH_TENSOR_SRAM_HH
#define DEV_AI_MESH_TENSOR_SRAM_HH

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <map>
#include <optional>
#include <vector>

#include "base/logging.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

// Optional external functional store for a SRAM tile.  When set, the tile's
// functional bytes live in the backing (Gate 2: the peer SRAM aperture's
// AXI target memory) while bank/port timing stays inside TensorSram.
class SramBacking
{
  public:
    virtual ~SramBacking() = default;
    virtual bool read(uint64_t offset, uint64_t size, uint8_t *out) const = 0;
    virtual bool write(uint64_t offset, uint64_t size, const uint8_t *in) = 0;
};

// Functional SRAM tile with bank/port occupancy timing and allocation
// validity tracking.  Data bytes are real: DMA moves actual bytes between
// HBM/peer tiles and this array; compute opcodes never touch the payload,
// they only annotate validity/digest.  Read and write ports are independent
// pools per bank (spec arch manifest sram_read/write_ports_per_bank).
class TensorSram
{
  public:
    TensorSram(uint64_t bytes, uint32_t banks, uint32_t alignment,
               uint32_t line_bytes, uint32_t read_bytes_per_cycle,
               uint32_t write_bytes_per_cycle, uint32_t read_ports,
               uint32_t write_ports, uint64_t line_tick,
               uint32_t bank_queue_depth = 1)
        : storage(bytes), banks(banks), alignment(alignment),
          line_bytes(line_bytes),
          read_bytes_per_cycle(read_bytes_per_cycle),
          write_bytes_per_cycle(write_bytes_per_cycle),
          read_ports(read_ports), write_ports(write_ports),
          line_tick(line_tick), bank_queue_depth(bank_queue_depth),
          bank_reads(banks, 0), bank_writes(banks, 0)
    {
        fatal_if(bytes == 0, "TensorSram: bytes must be nonzero");
        fatal_if(banks == 0, "TensorSram: banks must be nonzero");
        fatal_if(line_bytes == 0, "TensorSram: line_bytes must be nonzero");
        fatal_if(line_tick == 0, "TensorSram: line_tick must be nonzero");
        fatal_if(read_bytes_per_cycle == 0 || write_bytes_per_cycle == 0,
                 "TensorSram: bytes-per-cycle must be nonzero");
        fatal_if(read_ports == 0 || write_ports == 0,
                 "TensorSram: read/write ports must be nonzero");
        fatal_if(bank_queue_depth == 0,
                 "TensorSram: bank queue depth must be nonzero");
        read_busy.resize(banks, std::vector<uint64_t>(read_ports, 0));
        write_busy.resize(banks, std::vector<uint64_t>(write_ports, 0));
        read_request_ledger.resize(banks);
        write_request_ledger.resize(banks);
    }

    void setBacking(SramBacking *backing) { external_backing = backing; }

    bool fits(uint64_t offset, uint64_t size) const
    {
        const uint64_t capacity = storage.size();
        return offset <= capacity && size <= capacity - offset;
    }

    bool read(uint64_t offset, uint64_t size, uint8_t *out) const
    {
        if (external_backing)
            return external_backing->read(offset, size, out);
        if (!fits(offset, size))
            return false;
        std::memcpy(out, storage.data() + offset, size);
        return true;
    }

    bool write(uint64_t offset, uint64_t size, const uint8_t *in)
    {
        if (external_backing)
            return external_backing->write(offset, size, in);
        if (!fits(offset, size))
            return false;
        std::memcpy(storage.data() + offset, in, size);
        return true;
    }

    uint32_t bankOf(uint64_t offset) const
    {
        return static_cast<uint32_t>((offset / line_bytes) % banks);
    }

    // Reserve bank service for [offset, offset+size) at absolute `tick`.
    // All quantities are ticks.  Each bank has independent read and write
    // port pools; a request splits per bank line segment, picks the
    // earliest-free port of its direction's pool on each bank, and the
    // caller observes the worst-case stall so SRAM service participates in
    // completion timing.
    struct ReserveResult
    {
        uint64_t stall_ticks = 0;
        uint64_t service_ticks = 0;
        uint64_t conflict_ticks = 0;
    };

    // Side-effect-free admission check for a future reserve: every bank the
    // range touches must have a free request-queue slot at `tick`.  Queue
    // ownership is the per-bank/per-direction reservation ledger (one
    // retireable entry per reserve()), not port availability: a 1-port bank
    // with depth 2 sequences two requests instead of overwriting one
    // timestamp.
    bool canReserve(uint64_t tick, uint64_t offset, uint64_t size,
                    bool is_write) const
    {
        return bankQueueLoad(tick, offset, size, is_write) <
               bank_queue_depth;
    }

    uint32_t bankQueueLoad(uint64_t tick, uint64_t offset, uint64_t size,
                           bool is_write) const
    {
        if (!fits(offset, size))
            return bank_queue_depth;
        if (size == 0)
            return 0;
        const auto &ledger = is_write ? write_request_ledger
                                       : read_request_ledger;
        const uint64_t first_line = offset / line_bytes;
        const uint64_t last_line = (offset + size - 1) / line_bytes;
        const uint32_t touched = std::min<uint64_t>(
            banks, last_line - first_line + 1);
        uint32_t maximum = 0;
        for (uint32_t index = 0; index < touched; ++index) {
            const uint32_t bank = (first_line + index) % banks;
            uint32_t queued = 0;
            for (uint64_t done : ledger[bank])
                queued += done > tick;
            maximum = std::max(maximum, queued);
        }
        return maximum;
    }

    std::optional<ReserveResult> tryReserve(uint64_t tick, uint64_t offset,
                                            uint64_t size, bool is_write)
    {
        fatal_if(!fits(offset, size), "TensorSram: reservation escapes SRAM");
        if (!canReserve(tick, offset, size, is_write)) {
            if (is_write)
                ++write_reservation_rejection_attempts;
            else
                ++read_reservation_rejection_attempts;
            return std::nullopt;
        }
        return reserve(tick, offset, size, is_write);
    }

    ReserveResult reserve(uint64_t tick, uint64_t offset, uint64_t size, bool is_write)
    {
        ReserveResult result;
        fatal_if(!fits(offset, size), "TensorSram: reservation escapes SRAM");
        if (size == 0)
            return result;
        const uint32_t per_cycle =
            is_write ? write_bytes_per_cycle : read_bytes_per_cycle;
        const uint32_t ports = is_write ? write_ports : read_ports;
        auto &busy_pool = is_write ? write_busy : read_busy;
        std::vector<uint64_t> request_done(banks, 0);
        uint64_t done_at = tick;
        uint64_t first_line = offset / line_bytes;
        uint64_t last_line = (offset + size - 1) / line_bytes;
        for (uint64_t line = first_line; line <= last_line; line++) {
            uint32_t bank = static_cast<uint32_t>(line % banks);
            uint64_t seg_start = line == first_line ? offset : line * line_bytes;
            uint64_t seg_end =
                line == last_line ? offset + size : (line + 1) * line_bytes;
            uint64_t seg_bytes = seg_end - seg_start;
            uint64_t cycles = (seg_bytes + per_cycle - 1) / per_cycle;
            uint64_t service = cycles * line_tick; // per-cycle tick quantum
            uint64_t best_free = ~uint64_t(0);
            for (uint32_t port = 0; port < ports; port++) {
                uint64_t busy = busy_pool[bank][port];
                if (busy < best_free)
                    best_free = busy;
            }
            uint32_t chosen = 0;
            for (uint32_t port = 0; port < ports; port++)
                if (busy_pool[bank][port] == best_free) {
                    chosen = port;
                    break;
                }
            uint64_t start = tick > best_free ? tick : best_free;
            if (start > tick)
                result.conflict_ticks += start - tick;
            busy_pool[bank][chosen] = start + service;
            request_done[bank] = std::max(request_done[bank],
                                           busy_pool[bank][chosen]);
            if (busy_pool[bank][chosen] > done_at)
                done_at = busy_pool[bank][chosen];
            result.service_ticks += service;
            if (is_write)
                bank_writes[bank]++;
            else
                bank_reads[bank]++;
        }
        auto &ledger = is_write ? write_request_ledger : read_request_ledger;
        for (uint32_t bank = 0; bank < banks; ++bank) {
            if (request_done[bank] == 0)
                continue;
            auto &entries = ledger[bank];
            entries.erase(std::remove_if(entries.begin(), entries.end(),
                                         [tick](uint64_t done) {
                                             return done <= tick;
                                         }),
                          entries.end());
            entries.push_back(request_done[bank]);
        }
        result.stall_ticks = done_at > tick ? done_at - tick : 0;
        if (const auto owner = partitionOf(offset, size)) {
            if (is_write)
                partitions.at(*owner).writeBytes += size;
            else
                partitions.at(*owner).readBytes += size;
        }
        return result;
    }

    uint64_t capacity() const { return storage.size(); }
    uint32_t bankCount() const { return banks; }
    uint32_t baseAlignment() const { return alignment; }
    uint64_t reservationRejectionAttempts(bool is_write) const
    {
        return is_write ? write_reservation_rejection_attempts
                        : read_reservation_rejection_attempts;
    }

    // Allocation validity / poison tracking (spec 17.2.16): reads of an
    // allocation that was never written are poison and must fault.
    struct AllocationState
    {
        uint64_t offset = 0;
        uint64_t size = 0;
        bool valid = false;
        int pins = 0;
        uint32_t refcount = 0;
        std::optional<mesh_abi::SramPartitionKind> partition;
    };

    struct PartitionState
    {
        uint64_t base = 0;
        uint64_t bytes = 0;
        uint32_t alignment = 0;
        uint32_t metadataEntries = 0;
        uint64_t readBytes = 0;
        uint64_t writeBytes = 0;
    };

    static constexpr uint8_t MAX_VIEW_RANK = 8;

    struct ViewSlice
    {
        uint64_t offset = 0;
        uint64_t extent = 0;
    };

    struct ViewState
    {
        uint32_t allocationId = 0;
        uint8_t rank = 0;
        std::array<ViewSlice, MAX_VIEW_RANK> slices{};
    };

    void addPartition(mesh_abi::SramPartitionKind kind, uint64_t base,
                      uint64_t bytes, uint32_t alignment,
                      uint32_t metadataEntries)
    {
        fatal_if(bytes == 0 || alignment == 0,
                 "TensorSram: partition geometry must be nonzero");
        fatal_if(base > capacity() || bytes > capacity() - base,
                 "TensorSram: partition escapes SRAM");
        fatal_if(base % alignment != 0 || bytes % alignment != 0,
                 "TensorSram: partition is not aligned");
        for (const auto &entry : partitions)
            fatal_if(base < entry.second.base + entry.second.bytes &&
                         entry.second.base < base + bytes,
                     "TensorSram: partitions overlap");
        partitions.emplace(kind, PartitionState{base, bytes, alignment,
                                                metadataEntries, 0, 0});
    }

    bool hasPartition(mesh_abi::SramPartitionKind kind) const
    {
        return partitions.count(kind) != 0;
    }

    const PartitionState &partition(mesh_abi::SramPartitionKind kind) const
    {
        auto it = partitions.find(kind);
        fatal_if(it == partitions.end(), "TensorSram: partition is absent");
        return it->second;
    }

    std::optional<mesh_abi::SramPartitionKind> partitionOf(uint64_t offset,
                                                           uint64_t size) const
    {
        for (const auto &entry : partitions) {
            if (offset < entry.second.base)
                continue;
            const uint64_t delta = offset - entry.second.base;
            const uint64_t room = entry.second.bytes - delta;
            if (delta <= entry.second.bytes && size <= room)
                return entry.first;
        }
        return std::nullopt;
    }

    uint32_t partitionAllocationCount(mesh_abi::SramPartitionKind kind) const
    {
        uint32_t count = 0;
        for (const auto &entry : allocations)
            if (entry.second.partition && *entry.second.partition == kind)
                ++count;
        return count;
    }

    bool registerAllocation(uint32_t allocation_id, uint64_t offset,
                            uint64_t bytes)
    {
        if (!fits(offset, bytes) || allocations.count(allocation_id) != 0)
            return false;
        AllocationState state;
        state.offset = offset;
        state.size = bytes;
        state.partition = partitionOf(offset, bytes);
        if (!partitions.empty() && !state.partition)
            return false;
        if (state.partition) {
            const PartitionState &owner = partition(*state.partition);
            if (owner.metadataEntries != 0 &&
                partitionAllocationCount(*state.partition) >=
                    owner.metadataEntries)
                return false;
        }
        allocations.emplace(allocation_id, state);
        return true;
    }

    bool acquireRef(uint32_t allocation_id)
    {
        auto it = allocations.find(allocation_id);
        if (it == allocations.end())
            return false;
        ++it->second.refcount;
        return true;
    }

    bool releaseRef(uint32_t allocation_id)
    {
        auto it = allocations.find(allocation_id);
        if (it == allocations.end() || it->second.refcount == 0)
            return false;
        --it->second.refcount;
        return true;
    }

    bool reclaimable(uint32_t allocation_id) const
    {
        auto it = allocations.find(allocation_id);
        return it != allocations.end() && it->second.pins == 0 &&
               it->second.refcount == 0;
    }

    bool freeAllocation(uint32_t allocation_id)
    {
        if (!reclaimable(allocation_id))
            return false;
        allocations.erase(allocation_id);
        return true;
    }

    bool createView(uint32_t view_id, uint32_t allocation_id, uint8_t rank,
                    const ViewSlice *slices)
    {
        if (viewEntries != 0 && views.size() >= viewEntries)
            return false;
        if (views.count(view_id) != 0 || rank > MAX_VIEW_RANK)
            return false;
        auto it = allocations.find(allocation_id);
        if (it == allocations.end())
            return false;
        ViewState state;
        state.allocationId = allocation_id;
        state.rank = rank;
        for (uint8_t index = 0; index < rank; ++index) {
            if (slices[index].extent == 0 ||
                slices[index].offset > it->second.size ||
                slices[index].extent >
                    it->second.size - slices[index].offset)
                return false;
            state.slices[index] = slices[index];
        }
        if (!acquireRef(allocation_id))
            return false;
        views.emplace(view_id, state);
        return true;
    }

    bool releaseView(uint32_t view_id)
    {
        auto it = views.find(view_id);
        if (it == views.end())
            return false;
        const uint32_t allocation_id = it->second.allocationId;
        views.erase(it);
        return releaseRef(allocation_id);
    }

    const ViewState &view(uint32_t view_id) const
    {
        auto it = views.find(view_id);
        fatal_if(it == views.end(), "TensorSram: view is absent");
        return it->second;
    }

    std::map<uint32_t, AllocationState> allocations;
    std::map<mesh_abi::SramPartitionKind, PartitionState> partitions;
    std::map<uint32_t, ViewState> views;
    uint32_t viewEntries = 0;

    std::vector<uint8_t> storage;

  private:
    SramBacking *external_backing = nullptr;
    uint32_t banks;
    uint32_t alignment;
    uint32_t line_bytes;
    uint32_t read_bytes_per_cycle;
    uint32_t write_bytes_per_cycle;
    uint32_t read_ports;
    uint32_t write_ports;
    uint64_t line_tick; // ticks per SRAM service cycle
    uint32_t bank_queue_depth;
    uint64_t read_reservation_rejection_attempts = 0;
    uint64_t write_reservation_rejection_attempts = 0;
    std::vector<std::vector<uint64_t>> read_busy;
    std::vector<std::vector<uint64_t>> write_busy;
    std::vector<std::vector<uint64_t>> read_request_ledger;
    std::vector<std::vector<uint64_t>> write_request_ledger;
    std::vector<uint64_t> bank_reads;
    std::vector<uint64_t> bank_writes;
};

} // namespace ai_mesh
} // namespace gem5

#endif
