#include "dev/ai_mesh/gate3_msi_id_pool.hh"

#include <limits>
#include <stdexcept>

namespace gem5
{
namespace ai_mesh
{

Gate3MsiIdPool::Gate3MsiIdPool(uint32_t baseValue, uint32_t countValue)
    : base(baseValue), count(countValue)
{
    if (count == 0 || count - 1 > std::numeric_limits<uint32_t>::max() - base)
        throw std::invalid_argument("MSI AXI ID range is invalid");
    for (size_t index = 0; index < count; ++index)
        freeIds.insert(base + index);
}

std::optional<uint32_t>
Gate3MsiIdPool::acquire()
{
    if (freeIds.empty())
        return std::nullopt;
    const uint32_t id = *freeIds.begin();
    freeIds.erase(freeIds.begin());
    return id;
}

bool
Gate3MsiIdPool::release(uint32_t id)
{
    if (id < base || uint64_t(id) >= uint64_t(base) + count)
        return false;
    return freeIds.insert(id).second;
}

}
}
