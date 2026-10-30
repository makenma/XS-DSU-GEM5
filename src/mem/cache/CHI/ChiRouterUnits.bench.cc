// Standalone host benchmark; compile with -O3 -DNDEBUG -Isrc.
// The reference is the original router's pairwise age-matrix algorithm.
#include "mem/cache/CHI/ChiRouterUnits.hh"

#include <chrono>
#include <cstdlib>
#include <iostream>

using gem5::Chi::ChiAgeArbiter;

struct MatrixReference {
    std::array<std::array<bool, 8>, 8> age{};
    MatrixReference() {
        for (unsigned i = 0; i < 8; ++i)
            for (unsigned j = 0; j < 8; ++j) age[i][j] = i < j;
    }
    int choose(const std::array<bool, 8> &requests) const {
        for (unsigned i = 0; i < 8; ++i) {
            if (!requests[i]) continue;
            bool oldest = true;
            for (unsigned j = 0; j < 8; ++j)
                if (i != j && requests[j] && !age[i][j]) {
                    oldest = false;
                    break;
                }
            if (oldest) return i;
        }
        return -1;
    }
    void grant(unsigned winner) {
        for (unsigned j = 0; j < 8; ++j) {
            if (j == winner) continue;
            age[winner][j] = false;
            age[j][winner] = true;
        }
    }
};

template <class Arbiter>
std::pair<double, uint64_t> run(uint64_t iterations)
{
    Arbiter arbiter;
    uint64_t digest = 0;
    uint32_t random = 0x12345678;
    const auto begin = std::chrono::steady_clock::now();
    for (uint64_t cycle = 0; cycle < iterations; ++cycle) {
        random ^= random << 13;
        random ^= random >> 17;
        random ^= random << 5;
        std::array<bool, 8> requests;
        for (unsigned i = 0; i < 8; ++i) requests[i] = random & (1U << i);
        const int winner = arbiter.choose(requests);
        if (winner >= 0) arbiter.grant(winner);
        digest = (digest ^ (winner + 1)) * 1099511628211ULL;
    }
    return {std::chrono::duration<double>(std::chrono::steady_clock::now() - begin).count(), digest};
}

int main(int argc, char **argv)
{
    const uint64_t iterations = argc > 1 ? std::strtoull(argv[1], nullptr, 10) : 10000000;
    for (unsigned repeat = 0; repeat < 5; ++repeat) {
        // Alternate execution order to reduce warm-up/frequency bias.
        std::pair<double, uint64_t> reference, optimized;
        if (repeat & 1) {
            optimized = run<ChiAgeArbiter<8>>(iterations);
            reference = run<MatrixReference>(iterations);
        } else {
            reference = run<MatrixReference>(iterations);
            optimized = run<ChiAgeArbiter<8>>(iterations);
        }
        if (reference.second != optimized.second) return 1;
        std::cout << "{\"repeat\":" << repeat << ",\"iterations\":" << iterations
                  << ",\"reference_seconds\":" << reference.first
                  << ",\"optimized_seconds\":" << optimized.first
                  << ",\"digest\":" << optimized.second << "}\n";
    }
}
