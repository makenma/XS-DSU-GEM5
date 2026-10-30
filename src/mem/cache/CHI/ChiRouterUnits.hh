#ifndef __MEM_CACHE_CHI_ROUTER_UNITS_HH__
#define __MEM_CACHE_CHI_ROUTER_UNITS_HH__

#include <algorithm>
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <vector>
#include <unordered_map>

namespace gem5::Chi
{

/** Routing unit: fixed node routing plus the legacy transaction alias path.
 * Normally a source ID already names its physical attachment. Those requests
 * need no per-transaction metadata, regardless of how many IDs are issued.
 */
class ChiRouterRoutingUnit
{
  public:
    void addRoute(uint32_t node, uint32_t route) { routes[node] = route & 0x7ff; }
    void remember(uint32_t source, uint32_t txn, uint32_t physical) {
        const auto key = transaction(source, txn);
        if (staticRoute(source) == (physical & 0x7ff)) {
            if (!reverse.empty()) reverse.erase(key);
            return;
        }
        reverse[key] = physical & 0x7ff;
    }
    uint32_t route(uint32_t target, uint32_t txn, bool request) const {
        if (!request && !reverse.empty()) {
            auto found = reverse.find(transaction(target, txn));
            if (found != reverse.end()) return found->second;
        }
        return staticRoute(target);
    }
    size_t staticEntries() const { return routes.size(); }
    size_t aliasEntries() const { return reverse.size(); }
    auto aliases() const {
        std::vector<std::pair<uint64_t, uint32_t>> result(reverse.begin(), reverse.end());
        std::sort(result.begin(), result.end());
        return result;
    }
    void restoreAlias(uint64_t key, uint32_t route) {
        reverse[key] = route & 0x7ff;
    }
  private:
    static uint64_t transaction(uint32_t node, uint32_t txn) {
        return (uint64_t(node) << 32) | txn;
    }
    uint32_t staticRoute(uint32_t node) const {
        auto found = routes.find(node);
        return found == routes.end() ? node & 0x7ff : found->second;
    }
    std::unordered_map<uint32_t, uint32_t> routes;
    std::unordered_map<uint64_t, uint32_t> reverse;
};

/** The reference age matrix is a total order: a grant moves its winner last.
 * Keep that policy (not Garnet's round-robin policy) in linear space/time.
 */
template <size_t N>
class ChiAgeArbiter
{
  public:
    ChiAgeArbiter() { reset(); }
    void reset() {
        for (size_t i = 0; i < N; ++i)
            priority[i] = i;
    }
    int choose(const std::array<bool, N> &requests) const {
        for (auto candidate : priority)
            if (requests[candidate])
                return candidate;
        return -1;
    }
    int chooseMask(uint16_t requests) const {
        static_assert(N <= 16);
        for (auto candidate : priority)
            if (requests & (1U << candidate))
                return candidate;
        return -1;
    }
    void grant(unsigned winner) {
        auto at = std::find(priority.begin(), priority.end(), winner);
        assert(at != priority.end());
        std::rotate(at, at + 1, priority.end());
    }
    const auto &order() const { return priority; }
  private:
    std::array<uint8_t, N> priority{};
};

/** Input unit with either a shared FIFO or one FIFO per output. Only the
 * selected configuration allocates queues. Control tokens are kept separate
 * because the CHI reference pipeline admits them one cycle before payload.
 */
template <class Flit, class Output, size_t Outputs>
class ChiRouterInputUnit
{
  public:
    void configure(bool per_output) {
        static_assert(Outputs <= 16);
        assert(payloadCount == 0 && controlCount == 0);
        payloadMask = controlMask = 0;
        split = per_output;
        data.resize(split ? Outputs : 1);
        control.resize(split ? Outputs : 1);
    }
    void clear() {
        for (auto &q : data) q.clear();
        for (auto &q : control) q.clear();
        payloadCount = controlCount = 0;
        payloadMask = controlMask = 0;
    }
    size_t size() const { return payloadCount; }
    size_t controls() const { return controlCount; }
    bool empty() const { return !payloadCount && !controlCount; }
    void push(const Flit &flit) {
        data[index(flit.out)].push_back(flit);
        payloadMask |= 1U << static_cast<unsigned>(flit.out);
        ++payloadCount;
    }
    void pushControl(Output out) {
        control[index(out)].push_back(out);
        controlMask |= 1U << static_cast<unsigned>(out);
        ++controlCount;
    }
    const Flit *peek(Output out) const {
        const auto &q = data[index(out)];
        return q.empty() || q.front().out != out ? nullptr : &q.front();
    }
    bool pop(Output out, Flit &flit) {
        if (!peek(out)) return false;
        auto &q = data[index(out)];
        flit = std::move(q.front());
        discard(out);
        return true;
    }
    void discard(Output out) {
        assert(peek(out));
        auto &q = data[index(out)];
        q.pop_front();
        --payloadCount;
        if (q.empty()) payloadMask &= ~(1U << static_cast<unsigned>(out));
    }
    bool popControl(Output out) {
        auto &q = control[index(out)];
        if (q.empty() || q.front() != out) return false;
        q.pop_front();
        --controlCount;
        if (q.empty()) controlMask &= ~(1U << static_cast<unsigned>(out));
        return true;
    }
    bool requests(Output out, bool internal) const {
        if (split && !peek(out)) return false;
        if (!internal) return peek(out) != nullptr;
        const auto &q = control[index(out)];
        return !q.empty() && q.front() == out;
    }
    uint16_t requestMask(bool internal) const {
        if (!split) {
            if (internal)
                return control[0].empty() ? 0 :
                    uint16_t(1U << static_cast<unsigned>(control[0].front()));
            return data[0].empty() ? 0 :
                uint16_t(1U << static_cast<unsigned>(data[0].front().out));
        }
        return internal ? payloadMask & controlMask : payloadMask;
    }
    size_t queueCount() const { return data.size(); }
  private:
    size_t index(Output out) const {
        const auto i = static_cast<size_t>(out);
        assert(i < Outputs);
        return split ? i : 0;
    }
    bool split = false;
    size_t payloadCount = 0, controlCount = 0;
    uint16_t payloadMask = 0, controlMask = 0;
    std::vector<std::deque<Flit>> data;
    std::vector<std::deque<Output>> control;
};

/** Shared-capacity output buffer, indexed by endpoint. The reference deque
 * selects its first matching endpoint, then removes it or returns it to the
 * front when blocked. Both operations preserve the projection onto each
 * endpoint, so separate FIFOs preserve every winner without middle erases.
 */
template <class Flit, size_t Endpoints>
class ChiRouterOutputUnit
{
  public:
    void clear() {
        for (auto &q : queues) q.clear();
        count = 0;
    }
    size_t size() const { return count; }
    bool empty() const { return !count; }
    void push(const Flit &flit) {
        assert(static_cast<size_t>(flit.destDevice) < Endpoints);
        queues[flit.destDevice].push_back(flit);
        ++count;
    }
    const Flit *peek(size_t endpoint) const {
        assert(endpoint < Endpoints);
        const auto &q = queues[endpoint];
        return q.empty() ? nullptr : &q.front();
    }
    void pop(size_t endpoint) {
        assert(peek(endpoint));
        queues[endpoint].pop_front();
        --count;
    }
  private:
    std::array<std::deque<Flit>, Endpoints> queues;
    size_t count = 0;
};

} // namespace gem5::Chi
#endif
