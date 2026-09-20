#include "dev/ai_mesh/target_sentinels.hh"

#include <fstream>
#include <sstream>

#include "base/logging.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "mem/axi/axi_garnet_endpoint.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

std::string
rangeDigest(axi::AxiTargetAdapter &adapter, uint64_t address, uint64_t size)
{
    std::vector<uint8_t> bytes(size);
    for (uint64_t index = 0; index < size; index++)
        bytes[index] = adapter.readMemoryByte(address + index);
    mesh_hash::Sha256 hash;
    hash.update(bytes.data(), bytes.size());
    return mesh_hash::digestHex(hash.digest());
}

} // anonymous namespace

void
TargetSentinels::load(const std::string &path)
{
    if (path.empty())
        return;
    std::ifstream in(path);
    fatal_if(!in, "TargetSentinels: cannot open sentinel file %s", path);
    std::string line;
    while (std::getline(in, line)) {
        std::istringstream fields(line);
        std::string address_hex;
        uint64_t size = 0;
        if (!(fields >> address_hex >> size))
            fatal("TargetSentinels: bad sentinel line: %s", line);
        fatal_if(size == 0, "TargetSentinels: zero-size sentinel span");
        Row row;
        row.address = std::stoull(address_hex, nullptr, 0);
        row.size = size;
        rows.push_back(std::move(row));
    }
}

void
TargetSentinels::sample(axi::AxiTargetAdapter &adapter)
{
    fatal_if(sampled, "TargetSentinels: sampled twice");
    for (Row &row : rows) {
        fatal_if(!adapter.containsMemoryAddress(row.address) ||
                     !adapter.containsMemoryAddress(row.address + row.size - 1),
                 "TargetSentinels: span [%#llx,%#llx) escapes the target memory",
                 (unsigned long long)row.address,
                 (unsigned long long)(row.address + row.size));
        row.before = rangeDigest(adapter, row.address, row.size);
    }
    sampled = true;
}

std::vector<SentinelRangeObservation>
TargetSentinels::compare(axi::AxiTargetAdapter &adapter) const
{
    std::vector<SentinelRangeObservation> observations;
    for (const Row &row : rows) {
        SentinelRangeObservation observation;
        observation.kind = "wstrb-sentinel";
        observation.address = row.address;
        observation.size = row.size;
        observation.available = sampled;
        if (sampled) {
            observation.before_digest = row.before;
            observation.after_digest =
                rangeDigest(adapter, row.address, row.size);
        }
        observations.push_back(std::move(observation));
    }
    return observations;
}

} // namespace ai_mesh
} // namespace gem5
