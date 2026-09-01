#ifndef __MEM_AXI_AXI_TYPES_HH__
#define __MEM_AXI_AXI_TYPES_HH__

#include <cstddef>
#include <cstdint>
#include <deque>
#include <limits>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "base/types.hh"

namespace gem5
{
namespace axi
{

enum class AxiChannel : uint8_t { Aw, W, B, Ar, R };
enum class AxiBurst : uint8_t { Fixed = 0, Incr = 1, Wrap = 2 };
enum class AxiResp : uint8_t {
    Okay = 0, ExOkay = 1, SlvErr = 2, DecErr = 3
};

struct AxiInternalId
{
    uint64_t txnUid = 0;
    uint64_t writeOrdinal = 0;
    uint64_t targetSeq = 0;
    uint64_t responseSeq = 0;
};

struct AxiCommonMeta
{
    uint64_t txnUid = 0;
    uint64_t targetSeq = 0;
    uint64_t responseSeq = 0;
    uint32_t srcNode = 0;
    uint16_t srcPort = 0;
    uint32_t dstNode = 0;
    uint32_t axiId = 0;
    uint32_t semanticBytes = 0;
    uint32_t wireBytes = 0;
    uint8_t qos = 0;
    Tick acceptedTick = 0;
};

struct AxiAddressRequest
{
    uint32_t axiId = 0;
    uint64_t address = 0;
    uint16_t beatCount = 0;
    uint8_t size = 0;
    AxiBurst burst = AxiBurst::Incr;
    uint8_t lock = 0;
    uint8_t cache = 0;
    uint8_t prot = 0;
    uint8_t region = 0;
    uint8_t qos = 0;
};

struct AxiWBeat
{
    bool last = false;
    uint64_t byteStrobe = 0;
    uint64_t payloadDigest = 0;
    std::vector<uint8_t> functionalData;
};

struct AxiBBeat
{
    uint32_t axiId = 0;
    AxiResp resp = AxiResp::Okay;
};

struct AxiRBeat
{
    uint32_t axiId = 0;
    bool last = false;
    AxiResp resp = AxiResp::Okay;
    uint64_t payloadDigest = 0;
    std::vector<uint8_t> functionalData;
};

struct AxiAddressPacket
{
    AxiCommonMeta meta;
    AxiAddressRequest request;
    uint64_t writeOrdinal = 0;
    AxiResp decodeResp = AxiResp::Okay;
};

struct AxiDataPacket
{
    AxiCommonMeta meta;
    uint64_t writeOrdinal = 0;
    uint16_t beatIndex = 0;
    uint16_t beatCount = 0;
    bool last = false;
    uint64_t byteStrobe = 0;
    AxiResp resp = AxiResp::Okay;
    uint64_t payloadDigest = 0;
    uint64_t address = 0;
    std::vector<uint8_t> functionalData;
};

struct AxiBPacket
{
    AxiCommonMeta meta;
    AxiResp resp = AxiResp::Okay;
};

struct AxiEndpointKey
{
    uint32_t srcNode = 0;
    uint16_t srcPort = 0;

    auto tie() const { return std::tie(srcNode, srcPort); }
    bool operator<(const AxiEndpointKey &other) const
    { return tie() < other.tie(); }
    bool operator==(const AxiEndpointKey &other) const
    { return tie() == other.tie(); }
};

struct AxiRange
{
    uint64_t start = 0;
    uint64_t end = 0;
    uint32_t dstNode = 0;
};

struct AxiQuota
{
    uint32_t writeContexts = 0;
    uint32_t writeBeats = 0;
    uint32_t readContexts = 0;
    uint32_t readBeats = 0;
};

struct AxiTargetCapacity
{
    uint32_t writeContexts = 0;
    uint32_t writeAssemblyBeats = 0;
    uint32_t readContexts = 0;
    uint32_t readResponseBeats = 0;
};

template <class T>
class BoundedFifo
{
  public:
    explicit BoundedFifo(size_t capacity = 1) : _capacity(capacity)
    {
        if (_capacity == 0)
            throw std::invalid_argument("bounded FIFO capacity must be >= 1");
    }

    bool full() const { return _items.size() == _capacity; }
    bool empty() const { return _items.empty(); }
    size_t size() const { return _items.size(); }
    size_t capacity() const { return _capacity; }
    size_t available() const { return _capacity - _items.size(); }

    bool push(const T &item)
    {
        if (full())
            return false;
        _items.push_back(item);
        return true;
    }

    bool push(T &&item)
    {
        if (full())
            return false;
        _items.push_back(std::move(item));
        return true;
    }

    T &front()
    {
        if (empty())
            throw std::logic_error("front of empty bounded FIFO");
        return _items.front();
    }

    const T &front() const
    {
        if (empty())
            throw std::logic_error("front of empty bounded FIFO");
        return _items.front();
    }

    void pop()
    {
        if (empty())
            throw std::logic_error("pop of empty bounded FIFO");
        _items.pop_front();
    }

  private:
    size_t _capacity;
    std::deque<T> _items;
};

inline AxiResp
mergeResp(AxiResp lhs, AxiResp rhs)
{
    if (lhs == AxiResp::DecErr || rhs == AxiResp::DecErr)
        return AxiResp::DecErr;
    if (lhs == AxiResp::SlvErr || rhs == AxiResp::SlvErr)
        return AxiResp::SlvErr;
    return AxiResp::Okay;
}

inline uint64_t
payloadDigest(const std::vector<uint8_t> &data)
{
    uint64_t digest = 1469598103934665603ULL;
    for (const uint8_t byte : data) {
        digest ^= byte;
        digest *= 1099511628211ULL;
    }
    return digest;
}

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_TYPES_HH__
