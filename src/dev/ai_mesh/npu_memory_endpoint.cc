#include "dev/ai_mesh/npu_memory_endpoint.hh"

#include <fstream>
#include <iomanip>
#include <sstream>

#include "base/logging.hh"
#include "params/NpuMemoryEndpoint.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

std::string
dualFnvDigest(const uint8_t *data, uint64_t size)
{
    uint64_t h0 = 0xCBF29CE484222325ull;
    uint64_t h1 = 0x9E3779B97F4A7C15ull;
    for (uint64_t i = 0; i < size; i++) {
        h0 = (h0 ^ data[i]) * 0x100000001B3ull;
        h1 = (h1 + ((h0 >> 31) ^ data[i])) * 0xBF58476D1CE4E5B9ull;
    }
    std::ostringstream out;
    out << std::hex << std::setfill('0') << std::setw(16) << h0 << "-"
        << std::setw(16) << h1;
    return out.str();
}

} // anonymous namespace

NpuMemoryEndpoint::NpuMemoryEndpoint(const Params &p)
    : ClockedObject(p),
      adapter(p.adapter),
      seed_json_path(p.seed_json),
      verify_json_path(p.verify_json)
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
            fatal_if(verify.size == 0, "verify row with zero size");
            verifies.push_back(verify);
        }
    }
}

void
NpuMemoryEndpoint::seed(uint64_t address, uint64_t size, uint8_t pattern)
{
    seedBytes(address, size, pattern);
}

void
NpuMemoryEndpoint::seedBytes(uint64_t address, uint64_t size, uint8_t pattern)
{
    fatal_if(!adapter->containsMemoryAddress(address) ||
             !adapter->containsMemoryAddress(address + size - 1),
             "seed range [%#llx,%#llx) escapes the endpoint memory",
             (unsigned long long)address,
             (unsigned long long)(address + size));
    for (uint64_t i = 0; i < size; i++)
        adapter->writeMemoryByte(address + i, pattern);
}

std::string
NpuMemoryEndpoint::rangeDigest(uint64_t address, uint64_t size) const
{
    std::vector<uint8_t> bytes(size);
    for (uint64_t i = 0; i < size; i++)
        bytes[i] = adapter->readMemoryByte(address + i);
    return dualFnvDigest(bytes.data(), bytes.size());
}

void
NpuMemoryEndpoint::computeVerifyDigests()
{
    for (auto &verify : verifies) {
        std::vector<uint8_t> bytes(verify.size);
        for (uint64_t i = 0; i < verify.size; i++)
            bytes[i] = adapter->readMemoryByte(verify.address + i);
        verify.digest = dualFnvDigest(bytes.data(), bytes.size());
        const uint64_t after = verify.size > 16 ? verify.size - 16 : 0;
        verify.after_digest =
            dualFnvDigest(bytes.data() + (verify.size - after), after);
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
