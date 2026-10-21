#include "dev/ai_mesh/npu_memory_endpoint.hh"

#include <fstream>
#include <iomanip>
#include <sstream>

#include "base/logging.hh"
#include "dev/ai_mesh/mesh_payload_digest.hh"
#include "params/NpuMemoryEndpoint.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

NpuMemoryEndpoint::NpuMemoryEndpoint(const Params &p)
    : ClockedObject(p),
      adapter(p.adapter),
      seed_json_path(p.seed_json),
      verify_json_path(p.verify_json),
      verify_bytes_limit(p.verify_bytes_limit),
      sentinel_json_path(p.sentinel_json)
{}

void
NpuMemoryEndpoint::init()
{
    ClockedObject::init();
    fatal_if(adapter == nullptr, "%s: no target adapter bound", name());
    adapter->registerWriteCommitObserver(this);
}

void
NpuMemoryEndpoint::regStats()
{
    ClockedObject::regStats();
}

void
NpuMemoryEndpoint::startup()
{
    ClockedObject::startup();

    if (!seed_json_path.empty()) {
        // Line format: <address-hex> <size> <pattern-byte>
        std::ifstream in(seed_json_path);
        fatal_if(!in, "NpuMemoryEndpoint: cannot open seed file %s",
                 seed_json_path);
        std::string line;
        while (std::getline(in, line)) {
            std::istringstream fields(line);
            std::string address_hex;
            uint64_t size = 0, pattern = 0;
            if (!(fields >> address_hex >> size >> pattern))
                fatal("NpuMemoryEndpoint: bad seed line: %s", line);
            SeedRow seed;
            seed.address = std::stoull(address_hex, nullptr, 0);
            seed.size = size;
            seed.pattern = uint8_t(pattern);
            fatal_if(seed.size == 0, "seed row with zero size");
            seedBytes(seed.address, seed.size, seed.pattern);
            std::vector<uint8_t> bytes(seed.size, seed.pattern);
            seed.digest = dualFnvDigest(bytes.data(), bytes.size());
            seeds.push_back(seed);
        }
    }

    sentinel_spans.load(sentinel_json_path);
    if (!sentinel_spans.empty())
        sentinel_spans.sample(*adapter);

    if (!verify_json_path.empty()) {
        // Line format: <address-hex> <size>
        std::ifstream in(verify_json_path);
        fatal_if(!in, "NpuMemoryEndpoint: cannot open verify file %s",
                 verify_json_path);
        std::string line;
        while (std::getline(in, line)) {
            std::istringstream fields(line);
            std::string address_hex;
            uint64_t size = 0;
            if (!(fields >> address_hex >> size))
                fatal("NpuMemoryEndpoint: bad verify line: %s", line);
            VerifyRow verify;
            verify.address = std::stoull(address_hex, nullptr, 0);
            verify.size = size;
            fatal_if(!containsMemoryRange(verify.address, verify.size),
                     "verify row escapes endpoint memory");
            verifies.push_back(verify);
        }
    }
}

bool
NpuMemoryEndpoint::writeBytes(uint64_t address, uint64_t size, const uint8_t *data)
{
    if (!containsMemoryRange(address, size))
        return false;
    for (uint64_t i = 0; i < size; ++i)
        adapter->writeMemoryByte(address + i, data[i]);
    return true;
}

void
NpuMemoryEndpoint::seed(uint64_t address, uint64_t size, uint8_t pattern)
{
    seedBytes(address, size, pattern);
}

void
NpuMemoryEndpoint::seedBytes(uint64_t address, uint64_t size, uint8_t pattern)
{
    fatal_if(!containsMemoryRange(address, size),
             "seed range [%#llx,%#llx) escapes the endpoint memory",
             (unsigned long long)address,
             (unsigned long long)(address + size));
    for (uint64_t i = 0; i < size; i++)
        adapter->writeMemoryByte(address + i, pattern);
}

bool
NpuMemoryEndpoint::containsMemoryRange(uint64_t address, uint64_t size) const
{
    return adapter->containsMemoryRange(address, size);
}

std::string
NpuMemoryEndpoint::rangeDigest(uint64_t address, uint64_t size) const
{
    if (!containsMemoryRange(address, size))
        return std::string();
    std::vector<uint8_t> bytes(size);
    for (uint64_t i = 0; i < size; i++)
        bytes[i] = adapter->readMemoryByte(address + i);
    return dualFnvDigest(bytes.data(), bytes.size());
}

bool
NpuMemoryEndpoint::readBytes(uint64_t address, uint64_t size, uint8_t *out) const
{
    if (!containsMemoryRange(address, size))
        return false;
    for (uint64_t i = 0; i < size; i++)
        out[i] = adapter->readMemoryByte(address + i);
    return true;
}

void
NpuMemoryEndpoint::computeVerifyDigests()
{
    sentinels = sentinel_spans.compare(*adapter);
    for (auto &verify : verifies) {
        std::vector<uint8_t> bytes(verify.size);
        for (uint64_t i = 0; i < verify.size; i++)
            bytes[i] = adapter->readMemoryByte(verify.address + i);
        verify.digest = dualFnvDigest(bytes.data(), bytes.size());
        const uint64_t after = verify.size > 16 ? verify.size - 16 : 0;
        verify.after_digest =
            dualFnvDigest(bytes.data() + (verify.size - after), after);
        if (verify_bytes_limit != 0 && verify.size <= verify_bytes_limit) {
            static const char *hex = "0123456789abcdef";
            verify.bytes_hex.resize(verify.size * 2);
            for (uint64_t i = 0; i < verify.size; i++) {
                verify.bytes_hex[2 * i] = hex[bytes[i] >> 4];
                verify.bytes_hex[2 * i + 1] = hex[bytes[i] & 0xF];
            }
        }
    }
}

void
NpuMemoryEndpoint::onAxiWriteCommitted(
    const axi::AxiAddressRequest &request,
    const std::vector<axi::AxiDataPacket> &beats,
    axi::AxiResp resp)
{
    uint64_t strobed = 0;
    for (const auto &beat : beats)
        strobed += __builtin_popcountll(beat.byteStrobe);
    if (resp != axi::AxiResp::Okay) {
        error_drained_bytes += strobed;
        return;
    }
    committed_valid_bytes += strobed;
}

} // namespace ai_mesh
} // namespace gem5
