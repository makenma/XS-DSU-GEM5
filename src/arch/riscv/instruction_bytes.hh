#ifndef __ARCH_RISCV_INSTRUCTION_BYTES_HH__
#define __ARCH_RISCV_INSTRUCTION_BYTES_HH__

#include <cassert>
#include <cstdint>

namespace gem5::RiscvISA
{

/** Assemble 16/32-bit instructions from either actual-PC fetches (O3) or
 * aligned 32-bit fetch words (Simple/Minor/Checker). Input is host endian.
 */
class InstructionBytes
{
  public:
    struct Result {
        uint32_t bits;
        bool ready;
        bool exhausted;
    };

    void reset() { partial = false; }

    Result feed(uint64_t pc, uint64_t fetch_pc, uint32_t word)
    {
        if (fetch_pc == pc) {
            // Preserve the actual-PC path: all four instruction bytes are
            // already present, even when pc is not word aligned.
            partial = false;
            const bool compressed = (word & 3) != 3;
            return {compressed ? word & 0xffff : word, true, !compressed};
        }
        if (fetch_pc < pc) {
            assert(pc - fetch_pc == 2);
            const uint32_t half = word >> 16;
            partial = (half & 3) == 3;
            if (partial) {
                partialPC = pc;
                lowHalf = half;
            }
            return {half, !partial, true};
        }

        // A 32-bit instruction started in the previous word's upper half.
        assert(partial && partialPC == pc && fetch_pc == pc + 2);
        partial = false;
        // The upper half of this fetch remains available to Minor's next
        // instruction, so do not advance its input word yet.
        return {uint32_t(lowHalf) | (word << 16), true, false};
    }

  private:
    bool partial = false;
    uint16_t lowHalf = 0;
    uint64_t partialPC = 0;
};

} // namespace gem5::RiscvISA
#endif
