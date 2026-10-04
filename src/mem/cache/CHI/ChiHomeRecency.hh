#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <set>
#include <utility>

namespace gem5::Chi {

// The previous address-ordered line scan chose the lowest address on a
// lastUse tie. Keep that ordering explicitly; this is a host-side index and
// does not schedule events or change when a line's lastUse is updated.
class HomeRecencyIndex
{
    using Key = std::pair<uint64_t, uint64_t>;
    std::set<Key> order;

  public:
    // New lines have previous == 0 and no existing key. Existing lines must
    // supply their unchanged lastUse before the caller stores current.
    void update(uint64_t address, uint64_t previous, uint64_t current)
    {
        order.erase({previous, address});
        order.emplace(current, address);
    }

    void erase(uint64_t address, uint64_t lastUse)
    {
        order.erase({lastUse, address});
    }

    std::optional<uint64_t> victim(const std::set<uint64_t>& busy) const
    {
        // At most busy.size() + 1 entries are visited before finding an
        // available line; an all-busy cache returns no candidate.
        for (const auto& entry : order) {
            if (!busy.count(entry.second))
                return entry.second;
        }
        return std::nullopt;
    }

    std::size_t size() const { return order.size(); }
};

} // namespace gem5::Chi
