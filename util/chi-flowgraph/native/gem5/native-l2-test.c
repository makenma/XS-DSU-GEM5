// Real cacheable CPU traffic only: no opcode override, test MMIO or host requests.
// Four independently loaded SE processes map VA 0x100000000 to the same pages.
typedef unsigned long long u64;
#ifndef HART
#error HART must identify one of the four CPU processes
#endif
static volatile u64 *const shared = (volatile u64 *)0x100000000ULL;
enum { LINES = 80, STRIDE = 64, PRIVATE_BASE = 0x4000, PRIVATE_SPAN = 0x2000 };
static inline void fence(void) { __asm__ volatile("fence rw,rw" ::: "memory"); }
static inline void relax(void) { __asm__ volatile(".rept 8\n nop\n .endr" ::: "memory"); }
static inline long syscall3(long nr, long x, long y, long z) {
    register long a0 __asm__("a0") = x;
    register long a1 __asm__("a1") = y;
    register long a2 __asm__("a2") = z;
    register long a7 __asm__("a7") = nr;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a1), "r"(a2), "r"(a7) : "memory");
    return a0;
}
static __attribute__((noreturn)) void leave(unsigned errors) {
#if HART == 0
    static const char pass[] = "NATIVE_L2_HART_0_PASS\n";
#elif HART == 1
    static const char pass[] = "NATIVE_L2_HART_1_PASS\n";
#elif HART == 2
    static const char pass[] = "NATIVE_L2_HART_2_PASS\n";
#elif HART == 3
    static const char pass[] = "NATIVE_L2_HART_3_PASS\n";
#endif
    static const char fail[] = "NATIVE_L2_FAIL\n";
    if (errors) syscall3(64, 1, (long)fail, sizeof(fail) - 1);
    else syscall3(64, 1, (long)pass, sizeof(pass) - 1);
    syscall3(93, errors ? 1 : 0, 0, 0);
    for (;;) relax();
}
static void wait_at(unsigned word, u64 value) {
    unsigned spins = 0;
    while (shared[word] < value) {
        relax();
        if (++spins == 2000000) leave(1);
    }
    fence();
}
static void barrier(unsigned phase) {
    fence();
    shared[(0x100 + HART * STRIDE) / 8] = phase;
    fence();
    for (unsigned hart = 0; hart < 4; ++hart)
        wait_at((0x100 + hart * STRIDE) / 8, phase);
}
static u64 pattern(unsigned hart, unsigned line, unsigned word) {
    return 0xcafe000000000000ULL | ((u64)hart << 40) | ((u64)line << 8) | word;
}
void _start(void) {
    unsigned errors = 0;
    volatile u64 *const target = shared + 0x1000 / 8;
    barrier(1);
    // Successive reads must obtain the dirty predecessor's newest data.
    // Each write after that read requires a real permission upgrade if shared.
#if HART == 0
    {
        for (unsigned word = 0; word < 8; ++word) target[word] = pattern(0, 0, word);
        fence(); shared[0] = 1; fence();
    }
#else
    {
        wait_at(0, HART);
        for (unsigned word = 0; word < 8; ++word) {
            u64 expected = pattern(0, 0, word);
            for (unsigned previous = 1; previous < HART; ++previous)
                if (word == previous - 1) expected = 0x1100000000000000ULL * previous;
            errors += target[word] != expected;
        }
        target[HART - 1] = 0x1100000000000000ULL * HART;
        fence(); shared[0] = HART + 1; fence();
    }
#endif
    wait_at(0, 4);
    for (unsigned word = 0; word < 8; ++word)
        errors += target[word] != (word < 3
            ? 0x1100000000000000ULL * (word + 1) : pattern(0, 0, word));
    barrier(2);

    // More lines than the configured L2 capacity force inclusive dirty victims.
    for (unsigned line = 0; line < LINES; ++line) {
        volatile u64 *p = shared + (PRIVATE_BASE + HART * PRIVATE_SPAN + line * STRIDE) / 8;
        for (unsigned word = 0; word < 8; ++word) p[word] = pattern(HART, line, word);
    }
    barrier(3);
    // Check every byte through ordinary CPU reads, including evicted lines.
    for (unsigned hart = 0; hart < 4; ++hart)
        for (unsigned line = 0; line < LINES; ++line) {
            volatile u64 *p = shared + (PRIVATE_BASE + hart * PRIVATE_SPAN + line * STRIDE) / 8;
            for (unsigned word = 0; word < 8; ++word)
                errors += p[word] != pattern(hart, line, word);
        }
    shared[(0x400 + HART * STRIDE) / 8] = errors;
    barrier(4);
    // All error slots are checked on CPU 0; the host also requires four PASSes.
    if (HART == 0)
        for (unsigned hart = 1; hart < 4; ++hart)
            errors += shared[(0x400 + hart * STRIDE) / 8] != 0;
    leave(errors);
}
