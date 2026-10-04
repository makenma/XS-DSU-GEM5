#include "RiscvCbo.hh"
#include <cstdlib>
#include <iostream>

using namespace gem5::RiscvISA::Cbo;
unsigned checks = 0;
void require(bool condition, const char* name)
{
    ++checks;
    if (!condition) {
        std::cerr << "CBO control test failed: " << name << '\n';
        std::exit(1);
    }
}

int main()
{
    constexpr uint64_t Enabled = Cbie | Cbcfe;
    struct Mode { unsigned p; bool v; };
    const Mode modes[] = {{3, false}, {1, false}, {0, false}, {1, true}, {0, true}};
    const Operation ops[] = {Operation::Invalidate, Operation::Clean, Operation::Flush};
    const Decision allowed[] = {Decision::Invalidate, Decision::Clean, Decision::Flush};
    // Expected ISA trap table when the named xenvcfg is disabled. This
    // explicitly distinguishes HS from VS and U from VU, including priority.
    const Decision disabledM[] = {Decision::Invalidate, Decision::IllegalInstruction,
        Decision::IllegalInstruction, Decision::IllegalInstruction, Decision::IllegalInstruction};
    const Decision disabledH[] = {Decision::Invalidate, Decision::Invalidate,
        Decision::Invalidate, Decision::VirtualInstruction, Decision::VirtualInstruction};
    const Decision disabledS[] = {Decision::Invalidate, Decision::Invalidate,
        Decision::IllegalInstruction, Decision::Invalidate, Decision::VirtualInstruction};
    for (unsigned op = 0; op < 3; ++op) {
        for (unsigned m = 0; m < 5; ++m) {
            const auto mode = modes[m];
            require(permission(ops[op], mode.p, mode.v, Enabled, Enabled, Enabled) == allowed[op],
                    "all applicable permissions enabled");
            const auto expectation = [op, &allowed](Decision d) {
                return d == Decision::Invalidate ? allowed[op] : d;
            };
            require(permission(ops[op], mode.p, mode.v, 0, Enabled, Enabled) == expectation(disabledM[m]),
                    "menvcfg disabled");
            require(permission(ops[op], mode.p, mode.v, Enabled, 0, Enabled) == expectation(disabledH[m]),
                    "henvcfg disabled");
            require(permission(ops[op], mode.p, mode.v, Enabled, Enabled, 0) == expectation(disabledS[m]),
                    "senvcfg disabled");
            require(permission(ops[op], mode.p, mode.v, 0, 0, 0) == expectation(disabledM[m]),
                    "illegal has priority over virtual trap");
        }
    }
    constexpr uint64_t Flush = 1ULL << 4;
    for (unsigned m = 0; m < 5; ++m) {
        const auto mode = modes[m];
        require(permission(Operation::Invalidate, mode.p, mode.v, Flush, Enabled, Enabled) ==
                (m == 0 ? Decision::Invalidate : Decision::Flush), "machine forced flush");
        require(permission(Operation::Invalidate, mode.p, mode.v, Enabled, Flush, Enabled) ==
                (m >= 3 ? Decision::Flush : Decision::Invalidate), "hypervisor forced flush");
        require(permission(Operation::Invalidate, mode.p, mode.v, Enabled, Enabled, Flush) ==
                (m == 2 || m == 4 ? Decision::Flush : Decision::Invalidate), "supervisor forced flush");
    }
    // Inval's conversion to flush does not depend on CBCFE, while explicit
    // clean/flush do. A single shared enable would violate this distinction.
    require(permission(Operation::Invalidate, 0, false, Flush, 0, Flush) == Decision::Flush,
            "inval conversion independent from clean/flush enable");
    require(permission(Operation::Clean, 0, false, Flush, 0, Flush) == Decision::IllegalInstruction,
            "explicit clean still disabled");
    for (uint64_t value = 0; value < 256; ++value) {
        const auto normalized = legalizeEnvcfg(value);
        require(!(normalized & Cbze), "unsupported Zicboz is read-only zero");
        require((normalized & Cbie) != (2ULL << 4), "reserved CBIE cannot persist");
        require((normalized & Cbcfe) == (value & Cbcfe), "CBCFE independently writable");
        require(legalizeEnvcfg(normalized) == normalized, "WARL write/read stable");
        require(!(legalizeEnvcfg(value, true) & ~(Cbie | Cbcfe)), "senvcfg unsupported fields zero");
    }
    require(legalizeEnvcfg(0x8000000000000070ULL) == 0x8000000000000070ULL,
            "preserve preexisting unrelated machine/hypervisor fields");
    require(accessible(true, false, false, false), "read-only PTE permits management");
    require(accessible(true, true, false, false), "writable PTE permits management");
    require(!accessible(false, false, false, true), "no access still faults");
    require(!accessible(false, false, true, false), "execute-only without MXR rejected");
    require(accessible(false, false, true, true), "MXR permits execute-only as readable");
    std::cout << "{\"test\":\"riscv-cbo-control\",\"checks\":" << checks << ",\"errors\":0}\n";
}
