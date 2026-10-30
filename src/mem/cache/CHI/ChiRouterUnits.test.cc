#include "mem/cache/CHI/ChiRouterUnits.hh"

#include <numeric>
#include <random>

#include <gtest/gtest.h>

namespace gem5::Chi
{
namespace {
template <size_t N>
struct MatrixReference {
    std::array<std::array<bool, N>, N> age{};
    MatrixReference() {
        for (unsigned i = 0; i < N; ++i)
            for (unsigned j = 0; j < N; ++j) age[i][j] = i < j;
    }
    int choose(const std::array<bool, N> &requests) const {
        for (unsigned i = 0; i < N; ++i) {
            if (!requests[i]) continue;
            bool oldest = true;
            for (unsigned j = 0; j < N; ++j)
                if (i != j && requests[j] && !age[i][j]) {
                    oldest = false;
                    break;
                }
            if (oldest) return i;
        }
        return -1;
    }
    void grant(unsigned winner) {
        for (unsigned j = 0; j < N; ++j) {
            if (j == winner) continue;
            age[winner][j] = false;
            age[j][winner] = true;
        }
    }
};

template <size_t N>
void exhaustiveArbiter()
{
    std::array<unsigned, N> order;
    std::iota(order.begin(), order.end(), 0);
    do {
        MatrixReference<N> matrix;
        ChiAgeArbiter<N> arbiter;
        for (auto i : order) {
            matrix.grant(i);
            arbiter.grant(i);
        }
        for (unsigned mask = 0; mask < (1U << N); ++mask) {
            std::array<bool, N> requests;
            for (unsigned i = 0; i < N; ++i) requests[i] = mask & (1U << i);
            if (arbiter.choose(requests) != matrix.choose(requests) ||
                arbiter.chooseMask(mask) != matrix.choose(requests)) {
                FAIL() << "Age order/request mask mismatch mask=" << mask;
            }
        }
        // Grant updates must maintain exactly the same pairwise relation.
        for (unsigned winner = 0; winner < N; ++winner) {
            auto after = arbiter;
            auto reference = matrix;
            after.grant(winner);
            reference.grant(winner);
            const auto &priority = after.order();
            for (unsigned a = 0; a < N; ++a)
                for (unsigned b = a + 1; b < N; ++b)
                    ASSERT_TRUE(reference.age[priority[a]][priority[b]]);
        }
    } while (std::next_permutation(order.begin(), order.end()));
}

struct Flit { int out; uint64_t id; };
void bufferReference(bool split)
{
    ChiRouterInputUnit<Flit, int, 12> unit;
    unit.configure(split);
    EXPECT_EQ(unit.queueCount(), split ? 12 : 1);
    std::deque<Flit> reference;
    std::deque<int> controls;
    std::mt19937 rng(42);
    uint64_t next = 0;
    for (unsigned step = 0; step < 50000; ++step) {
        const int out = rng() % 12;
        if (reference.empty() || (rng() % 3) == 0) {
            reference.push_back({out, next});
            controls.push_back(out);
            unit.pushControl(out);
            unit.push({out, next++});
        } else {
            auto at = split ? std::find_if(reference.begin(), reference.end(),
                [out](const auto &f) { return f.out == out; }) : reference.begin();
            const bool match = at != reference.end() && at->out == out;
            EXPECT_EQ(unit.requests(out, false), match);
            EXPECT_EQ(unit.requests(out, true), match);
            EXPECT_EQ(bool(unit.requestMask(false) & (1U << out)), match);
            EXPECT_EQ(bool(unit.requestMask(true) & (1U << out)), match);
            Flit value{};
            ASSERT_EQ(unit.pop(out, value), match);
            if (match) {
                EXPECT_EQ(value.id, at->id);
                reference.erase(at);
                auto ctrl = split ? std::find(controls.begin(), controls.end(), out) : controls.begin();
                ASSERT_NE(ctrl, controls.end());
                ASSERT_EQ(*ctrl, out);
                controls.erase(ctrl);
                ASSERT_TRUE(unit.popControl(out));
            }
        }
        ASSERT_EQ(unit.size(), reference.size());
        ASSERT_EQ(unit.controls(), controls.size());
    }
}
} // namespace

TEST(ChiRouterArbiter, TwoWayExhaustive) { exhaustiveArbiter<2>(); }
TEST(ChiRouterArbiter, FourWayExhaustive) { exhaustiveArbiter<4>(); }
TEST(ChiRouterArbiter, EightWayExhaustive) { exhaustiveArbiter<8>(); }
TEST(ChiRouterInput, SharedFifoReference) { bufferReference(false); }
TEST(ChiRouterInput, PerOutputReference) { bufferReference(true); }
TEST(ChiRouterInput, ControlAndPayloadRemainSeparate) {
    ChiRouterInputUnit<Flit, int, 12> fifo, split;
    fifo.configure(false);
    split.configure(true);
    fifo.pushControl(3);
    split.pushControl(3);
    EXPECT_TRUE(fifo.requests(3, true));
    EXPECT_FALSE(fifo.requests(3, false));
    EXPECT_FALSE(split.requests(3, true));
    EXPECT_EQ(fifo.peek(3), nullptr);
    split.push({3, 7});
    EXPECT_TRUE(split.requests(3, true));
    EXPECT_EQ(split.peek(3)->id, 7);
    EXPECT_EQ(split.requestMask(true), 1U << 3);
    EXPECT_TRUE(split.popControl(3));
    EXPECT_EQ(split.requestMask(true), 0);
    EXPECT_EQ(split.requestMask(false), 1U << 3);
    split.discard(3);
    EXPECT_EQ(split.requestMask(false), 0);
    split.pushControl(5);
    split.push({5, 8});
    split.clear();
    EXPECT_EQ(split.requestMask(true), 0);
    EXPECT_EQ(split.requestMask(false), 0);
}

TEST(ChiRouterOutput, EndpointProjectionMatchesSharedDeque) {
    struct OutputFlit { unsigned destDevice; uint64_t id; };
    for (unsigned capacity : {1, 4, 16}) {
        ChiRouterOutputUnit<OutputFlit, 4> unit;
        std::deque<OutputFlit> reference;
        std::mt19937 rng(93 + capacity);
        uint64_t next = 0;
        unsigned rr = 0;
        for (unsigned cycle = 0; cycle < 50000; ++cycle) {
            // A blocked endpoint's element is returned to the front in the
            // reference, deliberately perturbing cross-endpoint order.
            const unsigned credit = rng() & 15;
            for (unsigned k = 0; k < 4; ++k) {
                const unsigned d = (rr + k) % 4;
                auto at = std::find_if(reference.begin(), reference.end(),
                    [d](const auto &f) { return f.destDevice == d; });
                const auto *actual = unit.peek(d);
                ASSERT_EQ(actual != nullptr, at != reference.end());
                if (!actual) continue;
                ASSERT_EQ(actual->id, at->id);
                const auto value = *at;
                reference.erase(at);
                if (!(credit & (1U << d))) {
                    reference.push_front(value);
                    continue;
                }
                unit.pop(d);
                rr = (d + 1) % 4;
                break;
            }
            // All endpoint FIFOs share one capacity and one enqueue slot.
            if (reference.size() < capacity && (rng() & 3)) {
                OutputFlit flit{static_cast<unsigned>(rng() % 4), next++};
                reference.push_back(flit);
                unit.push(flit);
            }
            ASSERT_EQ(unit.size(), reference.size());
            ASSERT_EQ(unit.empty(), reference.empty());
        }
        unit.clear();
        EXPECT_TRUE(unit.empty());
        for (unsigned d = 0; d < 4; ++d) EXPECT_EQ(unit.peek(d), nullptr);
    }
}
TEST(ChiRouterRouting, NormalTrafficDoesNotAccumulateTransactions) {
    ChiRouterRoutingUnit unit;
    for (uint32_t txn = 0; txn < 100000; ++txn) {
        unit.remember(0x95, txn, 0x95);
        EXPECT_EQ(unit.route(0x95, txn, false), 0x95);
    }
    EXPECT_EQ(unit.aliasEntries(), 0);
}
TEST(ChiRouterRouting, StaticMapAndLegacyAliasPrecedence) {
    ChiRouterRoutingUnit unit;
    unit.addRoute(0x12345678, 0x95);
    unit.remember(0x12345678, 7, 0x95);
    EXPECT_EQ(unit.aliasEntries(), 0);
    EXPECT_EQ(unit.route(0x12345678, 7, false), 0x95);
    // An alias can override the static map for responses only.
    unit.remember(0x12345678, 7, 0x96);
    unit.remember(0x12345678, 8, 0x97);
    EXPECT_EQ(unit.route(0x12345678, 7, false), 0x96);
    EXPECT_EQ(unit.route(0x12345678, 8, false), 0x97);
    EXPECT_EQ(unit.route(0x12345678, 7, true), 0x95);
    // Reusing a transaction with a static attachment erases stale aliases.
    unit.remember(0x12345678, 7, 0x95);
    EXPECT_EQ(unit.aliasEntries(), 1);
    EXPECT_EQ(unit.route(0x12345678, 7, false), 0x95);
    EXPECT_EQ(unit.route(0x12345678, 8, false), 0x97);
}
} // namespace gem5::Chi
