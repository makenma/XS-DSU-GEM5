// Four ordinary RV64 SE processes, sharing cacheable VA 0x100000000.
// No test MMIO, protocol-opcode injection, atomics, or host-generated traffic.
typedef unsigned long long u64;
#ifndef HART
#error HART must identify one of four CPU processes
#endif
#ifndef LITMUS_ITERATIONS
#define LITMUS_ITERATIONS 128
#endif
#ifndef LITMUS_SEED
#define LITMUS_SEED 1
#endif
#ifndef LITMUS_FENCED
#define LITMUS_FENCED 0
#endif
#if HART < 0 || HART > 3 || LITMUS_ITERATIONS < 1
#error Invalid litmus build parameters
#endif

enum { STRIDE = 64, ARRIVAL = 0x100, RESULTS = 0x400, FINAL_STATUS = 0x800 };
static volatile u64 *const shared = (volatile u64 *)0x100000000ULL;
static unsigned completed_rounds;
static char *append(char *out, const char *text) {
    while (*text) *out++ = *text++;
    return out;
}
static char *number(char *out, u64 value) {
    char digits[24]; unsigned count = 0;
    do { digits[count++] = '0' + value % 10; value /= 10; } while (value);
    while (count) *out++ = digits[--count];
    return out;
}
static inline volatile u64 *slot(unsigned byte_offset) { return shared + byte_offset / 8; }
static inline u64 load_word(const volatile u64 *address) {
    u64 value;
    __asm__ volatile("ld %0,0(%1)" : "=r"(value) : "r"(address) : "memory");
    return value;
}
static inline void store_word(volatile u64 *address, u64 value) {
    __asm__ volatile("sd %1,0(%0)" :: "r"(address), "r"(value) : "memory");
}
static inline void full_fence(void) { __asm__ volatile("fence rw,rw" ::: "memory"); }
static inline void relax(void) { __asm__ volatile(".rept 8\n nop\n .endr" ::: "memory"); }
static inline long syscall3(long nr, long x, long y, long z) {
    register long a0 __asm__("a0") = x;
    register long a1 __asm__("a1") = y;
    register long a2 __asm__("a2") = z;
    register long a7 __asm__("a7") = nr;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a1), "r"(a2), "r"(a7) : "memory");
    return a0;
}
#define STRINGIFY_INNER(x) #x
#define STRINGIFY(x) STRINGIFY_INNER(x)
static __attribute__((noreturn)) void leave(unsigned errors) {
    static const char pass[] = "NATIVE_LITMUS_HART_" STRINGIFY(HART) "_PASS completed=";
    static const char fail[] = "NATIVE_LITMUS_HART_" STRINGIFY(HART) "_FAIL completed=";
    char buffer[96];char *out=append(buffer,errors?fail:pass);
    out=number(out,completed_rounds);*out++='\n';
    syscall3(64, 1, (long)buffer, out-buffer);
    syscall3(93, errors ? 1 : 0, 0, 0);
    for (;;) relax();
}

// Every hart owns a separate cache line. Increasing epochs allow a slow hart
// to observe a later arrival without mistaking an old flag for this round.
static void barrier(unsigned epoch) {
    full_fence();
    store_word(slot(ARRIVAL + HART * STRIDE), epoch);
    full_fence();
    for (unsigned hart = 0; hart < 4; ++hart) {
        unsigned spins = 0;
        while (load_word(slot(ARRIVAL + hart * STRIDE)) < epoch) {
            relax();
            if (++spins == 2000000) leave(1);
        }
    }
    full_fence();
}

// This delay uses registers and NOPs only, before the experiment. It adds no
// shared accesses or ordering instructions between either reader's two loads.
static void delay(unsigned iteration, unsigned role) {
    u64 value = ((u64)iteration + 1) * 0x9e3779b1ULL;
    value ^= ((u64)LITMUS_SEED << 17) ^ ((u64)role * 0x85ebca6bULL);
    value ^= value >> 13;
    for (unsigned n = 0; n < (unsigned)(value & 15); ++n) relax();
}

static inline void read_pair(const volatile u64 *first_address,
                             const volatile u64 *second_address,
                             u64 *first, u64 *second) {
    u64 a, b;
    // One compiler-visible unit preserves the exact intended instruction pair.
    // Early-clobber outputs cannot overwrite either still-needed address.
    __asm__ volatile(
        "native_litmus_pair_begin_%=:\n"
        "ld %0,0(%2)\n"
#if LITMUS_FENCED
        "fence r,r\n"
#endif
        "ld %1,0(%3)\n"
        "native_litmus_pair_end_%=:\n"
        : "=&r"(a), "=&r"(b)
        : "r"(first_address), "r"(second_address)
        : "memory");
    *first = a;
    *second = b;
}

#if HART == 0
static u64 samples[LITMUS_ITERATIONS][4];
static void report_samples(void) {
    for(unsigned iteration=0;iteration<LITMUS_ITERATIONS;++iteration) {
        const unsigned rotation=(iteration+(unsigned)LITMUS_SEED)&3;
        const unsigned x_home=(iteration/4+(unsigned)LITMUS_SEED)&3;
        const unsigned y_home=(x_home+1+(iteration/16)%3)&3;
        char buffer[320],*out=append(buffer,"NATIVE_LITMUS_SAMPLE {\"round\":");
        out=number(out,iteration);out=append(out,",\"values\":[");
        for(unsigned v=0;v<4;++v) {if(v)*out++=',';out=number(out,samples[iteration][v]);}
        out=append(out,"],\"readerHarts\":[");out=number(out,(2+4-rotation)&3);
        *out++=',';out=number(out,(3+4-rotation)&3);
        out=append(out,"],\"xOffset\":");out=number(out,0x2000+x_home*STRIDE);
        out=append(out,",\"yOffset\":");out=number(out,0x3000+y_home*STRIDE);
        out=append(out,"}\n");syscall3(64,1,(long)buffer,out-buffer);
    }
}
static void report(const u64 *histogram, unsigned completed,
                   unsigned invalid_values, unsigned errors) {
    char buffer[1024], *out = buffer;
    out = append(out, "NATIVE_LITMUS_RESULT {\"variant\":\"");
    out = append(out, LITMUS_FENCED ? "iriw-fenced" : "iriw");
    out = append(out, "\",\"iterations\":"); out = number(out, LITMUS_ITERATIONS);
    out = append(out, ",\"completed\":"); out = number(out, completed);
    out = append(out, ",\"seed\":"); out = number(out, LITMUS_SEED);
    out = append(out, ",\"histogram\":{");
    for (unsigned outcome = 0; outcome < 16; ++outcome) {
        if (outcome) *out++ = ',';
        *out++ = '"';
        for (unsigned bit = 4; bit; --bit) *out++ = '0' + ((outcome >> (bit - 1)) & 1);
        out = append(out, "\":"); out = number(out, histogram[outcome]);
    }
    out = append(out, "},\"forbidden\":"); out = number(out, LITMUS_FENCED ? histogram[10] : 0);
    out = append(out, ",\"weakOutcomeCount\":"); out = number(out, histogram[10]);
    out = append(out, ",\"invalidValues\":"); out = number(out, invalid_values);
    out = append(out, ",\"errors\":"); out = number(out, errors);
    out = append(out, "}\n");
    syscall3(64, 1, (long)buffer, out - buffer);
}
#endif

void _start(void) {
#if HART == 0
    u64 histogram[16];
    for(unsigned i=0;i<16;++i)store_word(histogram+i,0);
    unsigned completed = 0, invalid_values = 0;
#endif
    for (unsigned iteration = 0; iteration < LITMUS_ITERATIONS; ++iteration) {
        const unsigned rotation = (iteration + (unsigned)LITMUS_SEED) & 3;
        const unsigned role = (HART + rotation) & 3;
        // Separate lines/pages, with independent RN rotation and HN selection.
        // The shared physical pages are page-aligned; PA[6:7] selects the HN.
        const unsigned x_home = (iteration / 4 + (unsigned)LITMUS_SEED) & 3;
        const unsigned y_home = (x_home + 1 + (iteration / 16) % 3) & 3;
        volatile u64 *const x = slot(0x2000 + x_home * STRIDE);
        volatile u64 *const y = slot(0x3000 + y_home * STRIDE);
#if HART == 0
        store_word(x, 0); store_word(y, 0);
#endif
        // All resets precede all test operations. This barrier does not wait
        // for either test writer to publish its value of 1.
        barrier(2 * iteration + 1);
        // Initialize only after the start barrier: the coordinator has then
        // finished reading the previous round's slots. Missing stores remain
        // observable rather than inheriting a valid result from an earlier run.
        store_word(slot(RESULTS + HART * STRIDE), ~0ULL);
        store_word(slot(RESULTS + HART * STRIDE + 8), ~0ULL);
        delay(iteration, role);
        u64 first = 0, second = 0;
        if (role == 0) store_word(x, 1);
        else if (role == 1) store_word(y, 1);
        else if (role == 2) read_pair(x, y, &first, &second);
        else read_pair(y, x, &first, &second);
        if(role>=2) {
            store_word(slot(RESULTS + HART * STRIDE), first);
            store_word(slot(RESULTS + HART * STRIDE + 8), second);
        }
        barrier(2 * iteration + 2);
        ++completed_rounds;
#if HART == 0
        const unsigned reader2 = (2 + 4 - rotation) & 3;
        const unsigned reader3 = (3 + 4 - rotation) & 3;
        const u64 a = load_word(slot(RESULTS + reader2 * STRIDE));
        const u64 b = load_word(slot(RESULTS + reader2 * STRIDE + 8));
        const u64 c = load_word(slot(RESULTS + reader3 * STRIDE));
        const u64 d = load_word(slot(RESULTS + reader3 * STRIDE + 8));
        samples[iteration][0]=a;samples[iteration][1]=b;
        samples[iteration][2]=c;samples[iteration][3]=d;
        const unsigned invalid = (a > 1) + (b > 1) + (c > 1) + (d > 1);
        invalid_values += invalid;
        if (!invalid) ++histogram[(a << 3) | (b << 2) | (c << 1) | d];
        ++completed;
        // Peers can reach the next start barrier, but cannot overwrite their
        // result slots until this coordinator finishes counting and arrives.
#endif
    }
#if HART == 0
    u64 total = 0;
    for (unsigned i = 0; i < 16; ++i) total += histogram[i];
    const unsigned errors = invalid_values + (LITMUS_FENCED ? histogram[10] : 0)
        + (completed != LITMUS_ITERATIONS) + (total != LITMUS_ITERATIONS);
    report_samples();
    report(histogram, completed, invalid_values, errors);
    store_word(slot(FINAL_STATUS), errors);
#endif
    barrier(2 * LITMUS_ITERATIONS + 1);
    leave(load_word(slot(FINAL_STATUS)) != 0);
}
