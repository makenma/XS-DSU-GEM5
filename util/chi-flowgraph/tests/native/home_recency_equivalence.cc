#include "ChiHomeRecency.hh"

#include <algorithm>
#include <iostream>
#include <limits>
#include <map>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

using Address = uint64_t;
using Tick = uint64_t;
using Lines = std::map<Address, Tick>;
using gem5::Chi::HomeRecencyIndex;

namespace {
uint64_t comparisons = 0;
uint64_t legacyChecks = 0;

void require(bool condition, const std::string& reason)
{
    if (!condition)
        throw std::runtime_error(reason);
}

// Literal old ChiExtendedHome::start selection: address-ordered map, linear
// active-transaction scan for each line, strict less-than for the timestamp.
std::optional<Address>
legacyVictim(const Lines& lines, const std::vector<Address>& transactions)
{
    auto victim = lines.end();
    for (auto it = lines.begin(); it != lines.end(); ++it) {
        const bool busy = std::any_of(transactions.begin(), transactions.end(),
            [&](Address request) {
                ++legacyChecks;
                return (request & ~Address(63)) == it->first;
            });
        if (!busy && (victim == lines.end() || it->second < victim->second))
            victim = it;
    }
    return victim == lines.end() ? std::nullopt :
           std::optional<Address>(victim->first);
}

struct Fixture
{
    Lines lines;
    HomeRecencyIndex index;

    void touch(Address address, Tick tick)
    {
        auto line = lines.try_emplace(address, 0).first;
        index.update(address, line->second, tick);
        line->second = tick;
    }

    void erase(Address address)
    {
        const auto line = lines.find(address);
        if (line != lines.end()) {
            index.erase(address, line->second);
            lines.erase(line);
        }
    }

    std::optional<Address> check(const std::vector<Address>& transactions)
    {
        require(index.size() == lines.size(), "stale or missing recency key");
        std::set<Address> busy;
        for (auto request : transactions)
            busy.insert(request & ~Address(63));
        const auto expected = legacyVictim(lines, transactions);
        const auto actual = index.victim(busy);
        require(expected == actual, "old/new victim candidate mismatch");
        ++comparisons;
        return actual;
    }
};

void boundaries()
{
    Fixture f;
    require(!f.check({}), "empty cache selected a victim");
    f.touch(0, 0);
    require(f.check({}) == Address(0), "address zero is a valid victim");
    require(!f.check({63, 17}), "busy offsets/duplicate locks not masked");
    f.touch(128, 0);
    f.touch(64, 0);
    require(f.check({}) == Address(0), "equal timestamp address tie-break");
    require(f.check({0}) == Address(64), "skip oldest busy line");
    require(f.check({0, 64, 192}) == Address(128), "nonresident busy address");
    require(!f.check({0, 64, 128}), "all-busy cache selected a victim");
    f.touch(0, 1); // Lookup touch; the later response may be in the same tick.
    f.touch(0, 1);
    require(f.check({}) == Address(64), "lookup/response update retained old key");
    f.erase(64);
    f.erase(64);
    require(f.check({}) == Address(128), "erase left an old candidate");
    f.touch(64, 1);
    f.erase(128);
    require(f.check({}) == Address(0), "reinsert changed address tie-break");
    f.erase(0);
    f.erase(64);
    require(!f.check({}), "delete final line left a candidate");
    const auto last = std::numeric_limits<uint64_t>::max();
    const auto highAddress = last & ~Address(63);
    f.touch(highAddress, last);
    f.touch(64, last);
    require(f.check({}) == Address(64), "64-bit tick or address truncated");
    require(f.check({127}) == highAddress, "high address lost with low busy line");
}

void randomized()
{
    constexpr unsigned Seeds = 128;
    constexpr unsigned Operations = 2048;
    for (unsigned seed = 0; seed < Seeds; ++seed) {
        std::mt19937_64 rng(0x484e524543454e43ULL + seed);
        Fixture f;
        Tick now = (1ULL << 53) + seed;
        for (unsigned step = 0; step < Operations; ++step) {
            const Address address = (rng() % 128) * 64;
            // Frequent equal ticks exercise the map's original address tie.
            now += rng() % 3 == 0;
            switch (rng() % 5) {
              case 0: f.erase(address); break;
              case 1: // An allocating response followed by empty-line removal.
                f.touch(address, now);
                f.erase(address);
                break;
              default: f.touch(address, now); break;
            }
            std::vector<Address> transactions;
            if (step % 31 == 0) {
                for (const auto& line : f.lines)
                    transactions.push_back(line.first + (rng() % 64));
            } else {
                const auto count = rng() % 33;
                for (uint64_t i = 0; i < count; ++i)
                    transactions.push_back((rng() % 160) * 64 + rng() % 64);
            }
            f.check(transactions);
            if (step % 127 == 0)
                f.check({});
        }
    }
}

void largeCache()
{
    Fixture f;
    for (Address n = 0; n < 8192; ++n)
        f.touch(n * 64, n / 4);
    std::vector<Address> busy;
    for (Address n = 0; n < 254; ++n)
        busy.push_back(n * 64 + 63);
    require(f.check(busy) == Address(254 * 64), "8192-line busy prefix mismatch");
    f.touch(254 * 64, 8192);
    require(f.check(busy) == Address(255 * 64), "8192-line retouch mismatch");
    f.erase(255 * 64);
    require(f.check(busy) == Address(256 * 64), "8192-line erase mismatch");
    for (Address n = 254; n < 8192; ++n)
        busy.push_back(n * 64);
    require(!f.check(busy), "8192-line all-busy mismatch");
}
} // namespace

int main()
{
    try {
        boundaries();
        randomized();
        largeCache();
        std::cout << "{\"passed\":true,\"randomSeeds\":128,"
                     "\"randomOperationsPerSeed\":2048,\"candidateComparisons\":"
                  << comparisons << ",\"legacyTransactionChecks\":" << legacyChecks
                  << ",\"largeCacheLines\":8192,\"largestConfiguredBusyPrefix\":254}"
                  << std::endl;
    } catch (const std::exception& error) {
        std::cerr << "Home recency equivalence FAILED: " << error.what() << std::endl;
        return 1;
    }
}
