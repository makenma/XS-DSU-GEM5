// Host-statistics storage regression, followed by the unchanged native CHI
// request workload. This SE program is not a substitute for Linux validation.
#define _start rtl_scenario_start
#include "native-l2-rtl-test.c"
#undef _start

#ifndef PHASE_EXPECT_STATE
#error The builder must supply independent reference values.
#endif

static __attribute__((noinline)) void phase_exercise(void) {
    u64 state = 0x9e3779b97f4a7c15ULL ^ HART;
    u64 checksum = 0;
    volatile u64 *const slots = shared + (0xd000 + HART * 0x400) / 8;
    for (unsigned n = 0; n < PHASE_ITERATIONS; ++n) {
        // The padding alone exceeds two 100,000-instruction main phases
        // per CPU. Actual completed phase counts are checked after execution.
        __asm__ volatile(".rept 12\n nop\n .endr" ::: "memory");
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        if (state & 1) checksum += state ^ n;
        else checksum ^= state + n;
        if ((n & 63) == 0) {
            slots[(n >> 6) & 15] = checksum;
            fence();
        }
    }
    if (state != PHASE_EXPECT_STATE || checksum != PHASE_EXPECT_CHECKSUM) leave(1);
    static const char ok[] = "BPU_PHASE_HART_" TEXT(HART) "_PASS\n";
    call(64, 1, (long)ok, sizeof(ok) - 1);
}

void _start(void) {
    phase_exercise();
    rtl_scenario_start();
}
