#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <sched.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include "cbo-module/chi_cbo_uapi.h"

#ifndef LINUX_CBO_BUILD_ID
#define LINUX_CBO_BUILD_ID "unidentified"
#endif
static sigjmp_buf recovery;
static volatile sig_atomic_t armed, caught_signal, caught_code;
static volatile uintptr_t caught_address;
static unsigned errors, rows;
static const char *operation_names[] = {"inval", "clean", "flush"};

static void caught(int number, siginfo_t *info, void *context)
{
    (void)context;
    if (!armed) _exit(128 + number);
    caught_signal = number;
    caught_code = info->si_code;
    caught_address = (uintptr_t)info->si_addr;
    siglongjmp(recovery, 1);
}

__attribute__((noinline)) static void cbo_inval(void *address)
{ asm volatile("fence rw,rw\n.insn i 0x0f,2,x0,%0,0\nfence rw,rw" :: "r"(address) : "memory"); }
__attribute__((noinline)) static void cbo_clean(void *address)
{ asm volatile("fence rw,rw\n.insn i 0x0f,2,x0,%0,1\nfence rw,rw" :: "r"(address) : "memory"); }
__attribute__((noinline)) static void cbo_flush(void *address)
{ asm volatile("fence rw,rw\n.insn i 0x0f,2,x0,%0,2\nfence rw,rw" :: "r"(address) : "memory"); }
static void (*const instructions[])(void *) = {cbo_inval, cbo_clean, cbo_flush};

static void invoke(unsigned operation, void *address)
{
    caught_signal = 0; caught_code = 0; caught_address = 0;
    if (!sigsetjmp(recovery, 1)) {
        armed = 1;
        instructions[operation](address);
    }
    armed = 0;
}

static void abort_test(const char *where)
{
    printf("LINUX_CBO_ABORT {\"where\":\"%s\",\"errno\":%d,\"signal\":%d}\n",
           where, errno, caught_signal);
    exit(1);
}

static void pin(int cpu)
{
    cpu_set_t mask;
    CPU_ZERO(&mask); CPU_SET(cpu, &mask);
    if (sched_setaffinity(0, sizeof(mask), &mask) || sched_getcpu() != cpu)
        abort_test("cpu-affinity");
}

static uint64_t env_record(int fd, int cpu, const char *stage)
{
    struct chi_cbo_env value = {0};
    if (ioctl(fd, CHI_CBO_GET_ENV, &value) || value.cpu != (unsigned)cpu)
        abort_test("read-senvcfg");
    printf("LINUX_CBO_ENV {\"stage\":\"%s\",\"cpu\":%d,\"senvcfg\":\"0x%016" PRIx64 "\"}\n",
           stage, cpu, (uint64_t)value.value);
    return value.value;
}

static void case_record(const char *phase, int cpu, const char *name, unsigned operation,
                        unsigned char *address, int fault, int code, int readable,
                        unsigned char *memory)
{
    invoke(operation, address);
    int ok = caught_signal == fault;
    if (fault == SIGSEGV)
        ok = ok && caught_code == code && caught_address == (uintptr_t)address;
    if (!strcmp(phase, "disabled"))
        ok = ok && caught_code == ILL_ILLOPC;
    unsigned mismatches = 0;
    if (readable) {
        const unsigned char *line = (const unsigned char *)((uintptr_t)address & ~(uintptr_t)63);
        for (unsigned byte = 0; byte < 64; ++byte)
            if (line[byte] != (unsigned char)((uintptr_t)(line + byte - memory) * 13 + 7)) ++mismatches;
        ok = ok && !mismatches;
    }
    ++rows; errors += !ok;
    printf("LINUX_CBO_RESULT {\"phase\":\"%s\",\"cpu\":%d,\"case\":\"%s\",\"operation\":\"%s\","
           "\"address\":\"0x%016" PRIxPTR "\",\"signal\":%d,\"siCode\":%d,\"siAddress\":\"0x%016" PRIxPTR "\","
           "\"expectedSignal\":%d,\"expectedCode\":%d,\"byteMismatches\":%u,\"pass\":%s}\n",
           phase, cpu, name, operation_names[operation], (uintptr_t)address, caught_signal, caught_code,
           (uintptr_t)caught_address, fault, code, mismatches, ok ? "true" : "false");
}

static void pte_record(int fd, int cpu, const char *name, unsigned operation, unsigned char *address)
{
    struct chi_cbo_pte result = {.address = (uintptr_t)address, .operation = operation};
    int call = ioctl(fd, CHI_CBO_TEST_PTE, &result), saved_errno = call ? errno : 0;
    int writable = !strcmp(name, "pte-rw");
    int ok = !call && !result.error && result.cpu == (unsigned)cpu &&
        result.writable_vma == (unsigned)writable && !(result.cleared & 0x80) &&
        !(result.after & 0x80) && (result.cleared & 0x40) &&
        result.cleared == (result.before & ~UINT64_C(0x80)) &&
        result.after == result.cleared && result.restored == result.before;
    ++rows; errors += !ok;
    printf("LINUX_CBO_PTE {\"cpu\":%d,\"case\":\"%s\",\"operation\":\"%s\","
           "\"address\":\"0x%016" PRIxPTR "\",\"ioctlError\":%d,\"instructionError\":%d,\"writableVma\":%u,"
           "\"before\":\"0x%016" PRIx64 "\",\"cleared\":\"0x%016" PRIx64 "\",\"after\":\"0x%016" PRIx64 "\","
           "\"restored\":\"0x%016" PRIx64 "\",\"pass\":%s}\n",
           cpu, name, operation_names[operation], (uintptr_t)address, saved_errno, result.error,
           result.writable_vma, (uint64_t)result.before, (uint64_t)result.cleared,
           (uint64_t)result.after, (uint64_t)result.restored, ok ? "true" : "false");
}

int main(int argc, char **argv)
{
    const char *phase = "disabled", *device = "/dev/chi-cbo";
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--phase") && i + 1 < argc) phase = argv[++i];
        else if (!strcmp(argv[i], "--device") && i + 1 < argc) device = argv[++i];
        else { fprintf(stderr, "usage: linux-cbo --phase disabled|enabled [--device /dev/chi-cbo]\n"); return 2; }
    }
    const int enabled = !strcmp(phase, "enabled");
    if (!enabled && strcmp(phase, "disabled")) return 2;
    setvbuf(stdout, NULL, _IOLBF, 0);
    struct sigaction handler = {.sa_sigaction = caught, .sa_flags = SA_SIGINFO};
    sigemptyset(&handler.sa_mask);
    if (sigaction(SIGILL, &handler, NULL) || sigaction(SIGSEGV, &handler, NULL) ||
        sigaction(SIGBUS, &handler, NULL)) abort_test("signal-handler");
    cpu_set_t affinity;
    if (sched_getaffinity(0, sizeof(affinity), &affinity)) abort_test("get-affinity");
    int cpus[4], count = 0;
    for (int cpu = 0; cpu < CPU_SETSIZE && count < 4; ++cpu)
        if (CPU_ISSET(cpu, &affinity)) cpus[count++] = cpu;
    if (count != 4) abort_test("four-online-cpus-required");
    long page = sysconf(_SC_PAGESIZE);
    if (page < 64 || page % 64) abort_test("page-size");
    unsigned char *memory = mmap(NULL, 4 * page, PROT_READ | PROT_WRITE,
                                MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (memory == MAP_FAILED) abort_test("anonymous-mmap");
    for (long i = 0; i < 2 * page; ++i) memory[i] = (unsigned char)(i * 13 + 7);
    if (mprotect(memory + page, page, PROT_READ) ||
        mprotect(memory + 2 * page, 2 * page, PROT_NONE))
        abort_test("mapping-permissions");
    printf("LINUX_CBO_BEGIN {\"schemaVersion\":1,\"buildId\":\"%s\",\"phase\":\"%s\","
           "\"cpus\":[%d,%d,%d,%d],\"lineBytes\":64,\"pageBytes\":%ld,\"pteHelper\":%s}\n",
           LINUX_CBO_BUILD_ID, phase, cpus[0], cpus[1], cpus[2], cpus[3], page, enabled ? "true" : "false");
    int fd = -1;
    uint64_t saved_env[4] = {0};
    unsigned char *pte_maps[2] = {MAP_FAILED, MAP_FAILED};
    if (enabled) {
        fd = open(device, O_RDWR | O_CLOEXEC);
        if (fd < 0) abort_test("open-test-helper");
        for (unsigned i = 0; i < 4; ++i) { pin(cpus[i]); saved_env[i] = env_record(fd, cpus[i], "before"); }
        uint32_t value = 0x70;
        if (ioctl(fd, CHI_CBO_SET_ENV, &value)) abort_test("enable-user-cbo");
        for (unsigned i = 0; i < 4; ++i) {
            pin(cpus[i]);
            if ((env_record(fd, cpus[i], "enabled") & 0x70) != 0x70) abort_test("senvcfg-warl-readback");
        }
        for (unsigned i = 0; i < 2; ++i) {
            pte_maps[i] = mmap(NULL, page, PROT_READ | PROT_WRITE, MAP_SHARED, fd, i * page);
            if (pte_maps[i] == MAP_FAILED) abort_test("helper-mmap");
            memset(pte_maps[i], 0x5a + i, page);
            invoke(1, pte_maps[i]);
            if (caught_signal) abort_test("helper-page-initial-clean");
        }
        if (mprotect(pte_maps[1], page, PROT_READ)) abort_test("helper-readonly");
        // Establish actual memory bytes before testing invalidate, so the
        // data check does not demand preservation of discarded dirty data.
        const long tested_lines[] = {64, 128, page};
        for (unsigned b = 0; b < 3; ++b) {
            invoke(1, memory + tested_lines[b]);
            if (caught_signal) abort_test("known-memory-initial-clean");
        }
    }
    // Keep this hole reserved until the helper's mmap calls are complete;
    // otherwise the kernel could legitimately reuse it for a helper page.
    if (munmap(memory + 3 * page, page)) abort_test("unmapped-test-hole");
    for (unsigned i = 0; i < 4; ++i) {
        pin(cpus[i]);
        for (unsigned operation = 0; operation < 3; ++operation) {
            case_record(phase, cpus[i], "aligned-rw", operation, memory + 64,
                        enabled ? 0 : SIGILL, enabled ? 0 : ILL_ILLOPC, 1, memory);
            case_record(phase, cpus[i], "unaligned-rw", operation, memory + 141,
                        enabled ? 0 : SIGILL, enabled ? 0 : ILL_ILLOPC, 1, memory);
            case_record(phase, cpus[i], "readonly", operation, memory + page + 17,
                        enabled ? 0 : SIGILL, enabled ? 0 : ILL_ILLOPC, 1, memory);
            case_record(phase, cpus[i], "noaccess", operation, memory + 2 * page + 37,
                        enabled ? SIGSEGV : SIGILL, enabled ? SEGV_ACCERR : ILL_ILLOPC, 0, memory);
            case_record(phase, cpus[i], "unmapped", operation, memory + 3 * page + 53,
                        enabled ? SIGSEGV : SIGILL, enabled ? SEGV_MAPERR : ILL_ILLOPC, 0, memory);
            if (enabled) {
                pte_record(fd, cpus[i], "pte-rw", operation, pte_maps[0] + 13);
                pte_record(fd, cpus[i], "pte-ro", operation, pte_maps[1] + 17);
            }
        }
    }
    if (enabled) {
        if (munmap(pte_maps[0], page) || munmap(pte_maps[1], page) || close(fd)) abort_test("helper-close");
        // Open without SET_ENV to verify close restored each hart's original
        // value. A successful enable test must not silently change policy.
        fd = open(device, O_RDWR | O_CLOEXEC);
        if (fd < 0) abort_test("helper-restore-check");
        for (unsigned i = 0; i < 4; ++i) {
            pin(cpus[i]);
            if (env_record(fd, cpus[i], "restored") != saved_env[i]) ++errors;
        }
        if (close(fd)) abort_test("restore-check-close");
    }
    if (sched_setaffinity(0, sizeof(affinity), &affinity)) abort_test("restore-affinity");
    printf("LINUX_CBO_END {\"phase\":\"%s\",\"rows\":%u,\"errors\":%u,\"pass\":%s}\n",
           phase, rows, errors, errors ? "false" : "true");
    return errors ? 1 : 0;
}
