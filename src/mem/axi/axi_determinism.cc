#include "mem/axi/axi_determinism.hh"

namespace gem5
{
namespace axi
{
namespace deterministic
{

uint64_t
splitmix64(uint64_t value)
{
    uint64_t z = value + 0x9e3779b97f4a7c15ULL;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

uint64_t
keyedRandom(uint64_t master_seed, uint64_t key_id, Stage stage,
            uint64_t beat_index, DrawKind draw_kind)
{
    uint64_t state = master_seed;
    const uint64_t words[] = {
        key_id,
        static_cast<uint64_t>(stage),
        beat_index,
        static_cast<uint64_t>(draw_kind),
    };
    for (const uint64_t word : words)
        state = splitmix64(state ^ word);
    return state;
}

} // namespace deterministic
} // namespace axi
} // namespace gem5
