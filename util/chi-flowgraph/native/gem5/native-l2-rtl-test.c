// Real RV64 instructions only. CBOs use their architectural encodings and
// travel through the CPU LSQ/L1; no CHI opcode or simulator MMIO injection.
typedef unsigned long long u64;
typedef unsigned char u8;
#ifndef HART
#error HART must identify a CPU process
#endif
static volatile u64 *const shared = (volatile u64 *)0x100000000ULL;
static volatile u64 *const uncached = (volatile u64 *)0x200000000ULL;
static inline void fence(void) { __asm__ volatile("fence rw,rw" ::: "memory"); }
static inline void relax(void) { __asm__ volatile(".rept 8\n nop\n .endr" ::: "memory"); }
static inline long call(long n, long x, long y, long z) {
    register long a0 __asm__("a0") = x;
    register long a1 __asm__("a1") = y;
    register long a2 __asm__("a2") = z;
    register long a7 __asm__("a7") = n;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a1), "r"(a2), "r"(a7) : "memory");
    return a0;
}
#define TEXT_(x) #x
#define TEXT(x) TEXT_(x)
static __attribute__((noreturn)) void leave(unsigned errors) {
    static const char ok[] = "NATIVE_RTL_HART_" TEXT(HART) "_PASS\n";
    static const char bad[] = "NATIVE_RTL_FAIL hart=" TEXT(HART) "\n";
    call(64, 1, (long)(errors ? bad : ok), errors ? sizeof(bad)-1 : sizeof(ok)-1);
    call(93, errors ? 1 : 0, 0, 0);
    for (;;) relax();
}
static void barrier(unsigned phase) {
    fence();
    shared[(0x100 + 64 * HART) / 8] = phase;
    fence();
    for (unsigned h = 0; h < 4; ++h) {
        unsigned spins = 0;
        while (shared[(0x100 + 64 * h) / 8] < phase) {
            relax();
            if (++spins == 2000000) leave(1);
        }
    }
    fence();
}
static __attribute__((noinline)) void clean(volatile u64 *p) {
    __asm__ volatile(".insn i 0x0f, 2, x0, %0, 1" :: "r"(p) : "memory");
}
static __attribute__((noinline)) void flush(volatile u64 *p) {
    __asm__ volatile(".insn i 0x0f, 2, x0, %0, 2" :: "r"(p) : "memory");
}
static __attribute__((noinline)) void inval(volatile u64 *p) {
    __asm__ volatile(".insn i 0x0f, 2, x0, %0, 0" :: "r"(p) : "memory");
}
static __attribute__((noinline)) void write_line(volatile u64 *p, u64 value) {
    // Eight real stores with no intermediate fence: all 64 bytes can merge in
    // the KMH store buffer. L1 emits its whole-line overwrite permission only
    // when its actual byte mask is complete.
    __asm__ volatile("sd %1,0(%0)\n sd %1,8(%0)\n sd %1,16(%0)\n sd %1,24(%0)\n"
                     "sd %1,32(%0)\n sd %1,40(%0)\n sd %1,48(%0)\n sd %1,56(%0)"
                     :: "r"(p), "r"(value) : "memory");
}
static u64 pattern(unsigned h, unsigned line) {
    return 0xc001000000000000ULL | ((u64)h << 40) | ((u64)line << 8) | 0x5a;
}
void _start(void) {
    unsigned errors = 0;
    volatile u64 *const c = shared + 0x1000 / 8;
    volatile u64 *const f = shared + 0x1100 / 8;
    volatile u64 *const i = shared + 0x1200 / 8;
    barrier(1);
    // Partial writes preserve all untouched bytes and require real data
    // acquisition. Other CPUs subsequently read dirty owner data.
    if (HART == 0) {
        c[0] = 0x1122334455667788ULL; fence();
        f[0] = 0x8899aabbccddeeffULL; fence();
        i[0] = 0x0123456789abcdefULL; fence();
    }
    barrier(2);
    errors += c[0] != 0x1122334455667788ULL;
    errors += f[0] != 0x8899aabbccddeeffULL;
    errors += i[0] != 0x0123456789abcdefULL;
    for (unsigned w = 1; w < 8; ++w) errors += c[w] != 0;
    barrier(3);
    // Re-dirty after all readers have shared a copy, then clean/flush through
    // the real ISA. The dirty WriteClean companion must arrive before CMO ack.
    if (HART == 0) {
        c[0] = 0xaabbccdd01020304ULL; fence(); clean(c); fence();
        f[0] = 0xddccbbaa04030201ULL; fence(); flush(f); fence();
        clean(i); fence(); inval(i); fence();
    }
    barrier(4);
    errors += c[0] != 0xaabbccdd01020304ULL;
    errors += f[0] != 0xddccbbaa04030201ULL;
    errors += i[0] != 0x0123456789abcdefULL;
    barrier(5);
    // A private dirty line is cleaned while its RN remains the sole owner.
    // Other harts never read it here. The following capacity pressure must
    // replace this retained clean unique line, exercising the WEOE data path
    // when the HN requests data from its unique owner.
    volatile u64 *const private_clean = shared + (0x3000 + HART * 0x100) / 8;
    write_line(private_clean, pattern(HART, 255));
    fence(); clean(private_clean); fence();
    // Cold full-line writes: MakeUnique is legal only after actual whole-line
    // merging, not merely because the CPU executed an arbitrary store.
    for (unsigned line = 0; line < 80; ++line)
        write_line(shared + (0x4000 + HART * 0x2000 + line * 64) / 8,
                   pattern(HART, line));
    fence();
    for (unsigned w = 0; w < 8; ++w) errors += private_clean[w] != pattern(HART, 255);
    barrier(6);
    // All CPUs check all bytes of the dirty/capacity victims, then the long
    // shared read stream naturally produces clean shared and unique victims.
    for (unsigned h = 0; h < 4; ++h)
        for (unsigned line = 0; line < 80; ++line) {
            volatile u64 *p = shared + (0x4000 + h * 0x2000 + line * 64) / 8;
            for (unsigned w = 0; w < 8; ++w) errors += p[w] != pattern(h, line);
        }
    barrier(7);
    // Normal noncacheable RAM, separate from cached pages. Byte stores test
    // that Size/BE only write the requested bytes, despite a 256-bit DAT beat.
    volatile u64 *u = uncached + HART * 8;
    *u = 0x1020304050607080ULL;
    fence();
    ((volatile u8 *)u)[3] = 0xab;
    fence();
    errors += *u != 0x10203040ab607080ULL;
    barrier(8);
    for (unsigned h = 0; h < 4; ++h)
        errors += uncached[h * 8] != 0x10203040ab607080ULL;
    shared[(0x400 + HART * 64) / 8] = errors;
    barrier(9);
    if (HART == 0)
        for (unsigned h = 1; h < 4; ++h)
            errors += shared[(0x400 + h * 64) / 8] != 0;
    leave(errors);
}
