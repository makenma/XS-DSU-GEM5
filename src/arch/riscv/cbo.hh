#ifndef __ARCH_RISCV_CBO_HH__
#define __ARCH_RISCV_CBO_HH__

#include <cstdint>

namespace gem5::RiscvISA::Cbo {
// RISC-V Zicbom 1.0, CMO CSR controls and address-translation rules.
// Pure helpers are shared by generated instructions, CSR writes and tests.
enum class Operation { Invalidate, Clean, Flush };
enum class Decision { Invalidate, Clean, Flush, IllegalInstruction, VirtualInstruction };
constexpr uint64_t Cbie = 3ULL << 4;
constexpr uint64_t Cbcfe = 1ULL << 6;
constexpr uint64_t Cbze = 1ULL << 7;

constexpr uint64_t legalizeEnvcfg(uint64_t value, bool supervisor = false)
{
    // Zicboz is not implemented. Reserved CBIE=2 deterministically reads 0.
    value &= ~Cbze;
    if ((value & Cbie) == (2ULL << 4))
        value &= ~Cbie;
    // Only Zicbom fields are implemented in the newly added senvcfg.
    return supervisor ? value & (Cbie | Cbcfe) : value;
}

constexpr Decision permission(Operation op, unsigned privilege, bool virt,
                              uint64_t menv, uint64_t henv, uint64_t senv)
{
    if (privilege == 3)
        return op == Operation::Invalidate ? Decision::Invalidate :
               op == Operation::Clean ? Decision::Clean : Decision::Flush;
    const bool user = privilege == 0;
    const auto enabled = [op](uint64_t env) {
        return op == Operation::Invalidate ?
            ((env & Cbie) == (1ULL << 4) || (env & Cbie) == Cbie) :
            bool(env & Cbcfe);
    };
    // Illegal-instruction causes take priority over virtualization traps.
    if (!enabled(menv) || (!virt && user && !enabled(senv)))
        return Decision::IllegalInstruction;
    if (virt && (!enabled(henv) || (user && !enabled(senv))))
        return Decision::VirtualInstruction;
    if (op != Operation::Invalidate)
        return op == Operation::Clean ? Decision::Clean : Decision::Flush;
    const auto flush = [](uint64_t env) { return (env & Cbie) == (1ULL << 4); };
    return flush(menv) || (virt && flush(henv)) || (user && flush(senv)) ?
        Decision::Flush : Decision::Invalidate;
}

constexpr bool accessible(bool read, bool write, bool execute, bool mxr)
{
    return read || write || (execute && mxr);
}
} // namespace gem5::RiscvISA::Cbo
#endif
