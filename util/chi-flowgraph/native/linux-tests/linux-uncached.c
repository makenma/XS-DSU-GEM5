#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include "uncached-module/chi_uncached_uapi.h"

#ifndef LINUX_UNCACHED_BUILD_ID
#define LINUX_UNCACHED_BUILD_ID "unbound-source"
#endif

/* Separate non-inlined functions make the executed load/store encodings
 * auditable in the delivered ELF. All accesses are naturally aligned. */
#define ACCESS(BITS, LOAD, STORE) \
__attribute__((noinline)) static uint64_t nc_load##BITS(const void *address) \
{ uint64_t value; __asm__ volatile(LOAD " %0, 0(%1)" \
    : "=&r"(value) : "r"(address) : "memory"); return value; } \
__attribute__((noinline)) static void nc_store##BITS(void *address, uint64_t value) \
{ __asm__ volatile(STORE " %0, 0(%1)" : : "r"(value), "r"(address) : "memory"); }
ACCESS(8, "lbu", "sb")
ACCESS(16, "lhu", "sh")
ACCESS(32, "lwu", "sw")
ACCESS(64, "ld", "sd")
static void barrier(void) { __asm__ volatile("fence rw,rw" ::: "memory"); }

static int fail(const char *where) { perror(where); return 1; }
static uint64_t load(void *address, unsigned bytes)
{
    switch (bytes) {
    case 1: return nc_load8(address);
    case 2: return nc_load16(address);
    case 4: return nc_load32(address);
    default: return nc_load64(address);
    }
}
static void store(void *address, unsigned bytes, uint64_t value)
{
    switch (bytes) {
    case 1: nc_store8(address, value); break;
    case 2: nc_store16(address, value); break;
    case 4: nc_store32(address, value); break;
    default: nc_store64(address, value); break;
    }
}

int main(void)
{
    cpu_set_t allowed, one;
    int cpus[4], ncpus = 0, fd, errors = 0;
    unsigned completed = 0;
    struct chi_uncached_info info;
    const unsigned widths[] = {1, 2, 4, 8};
    const unsigned offsets[] = {5, 10, 20, 40};
    uint8_t *mapping;
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (sched_getaffinity(0, sizeof(allowed), &allowed)) return fail("getaffinity");
    for (int cpu = 0; cpu < CPU_SETSIZE; ++cpu)
        if (CPU_ISSET(cpu, &allowed)) {
            if (ncpus == 4) { fputs("expected exactly four CPUs\n", stderr); return 1; }
            cpus[ncpus++] = cpu;
        }
    if (ncpus != 4) { fputs("expected exactly four CPUs\n", stderr); return 1; }
    fd = open("/dev/chi-uncached", O_RDWR | O_CLOEXEC);
    if (fd < 0) return fail("open chi-uncached");
    mapping = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (mapping == MAP_FAILED) { close(fd); return fail("mmap chi-uncached"); }
    if (ioctl(fd, CHI_UNCACHED_INFO, &info)) {
        munmap(mapping, 4096); close(fd); return fail("CHI_UNCACHED_INFO");
    }
    if (info.abi != CHI_UNCACHED_ABI || info.bytes != 4096 ||
        info.physical_start % 4096 || !info.dt_no_map) {
        munmap(mapping, 4096); close(fd); fputs("invalid reserved page ABI\n", stderr); return 1;
    }
    printf("LINUX_NC_INFO {\"buildId\":\"%s\",\"abi\":%u,\"physicalStart\":\"0x%" PRIx64
           "\",\"bytes\":4096,\"dtNoMap\":true,\"mappingProtection\":\"0x%" PRIx64
           "\",\"cpus\":[%d,%d,%d,%d]}\n", LINUX_UNCACHED_BUILD_ID, info.abi,
           (uint64_t)info.physical_start, (uint64_t)info.mapping_protection,
           cpus[0], cpus[1], cpus[2], cpus[3]);
    for (unsigned index = 0; index < 4; ++index) {
        CPU_ZERO(&one); CPU_SET(cpus[index], &one);
        if (sched_setaffinity(0, sizeof(one), &one)) { ++errors; break; }
        for (unsigned line = 0; line < 4; ++line) {
            const unsigned line_offset = index * 256 + line * 64;
            uint8_t expected[64], actual[64];
            for (unsigned b = 0; b < 64; ++b)
                expected[b] = (uint8_t)(0xa5 ^ (index * 17) ^ (line * 7) ^ b);
            for (unsigned b = 0; b < 64; b += 8) {
                uint64_t word; memcpy(&word, expected + b, 8);
                nc_store64(mapping + line_offset + b, word);
            }
            barrier();
            for (unsigned test = 0; test < 4; ++test) {
                const unsigned width = widths[test], offset = offsets[test];
                const uint64_t mask = width == 8 ? UINT64_MAX : (UINT64_C(1) << (width * 8)) - 1;
                const uint64_t value = (UINT64_C(0x8877665544332200) ^ (index << 5) ^ (line << 3) ^ width) & mask;
                store(mapping + line_offset + offset, width, value);
                barrier();
                const uint64_t readback = load(mapping + line_offset + offset, width);
                memcpy(expected + offset, &value, width);
                for (unsigned b = 0; b < 64; b += 8) {
                    uint64_t word = nc_load64(mapping + line_offset + b);
                    memcpy(actual + b, &word, 8);
                }
                barrier();
                const int cpu = sched_getcpu();
                const int matches = readback == value && !memcmp(actual, expected, 64) && cpu == cpus[index];
                char data[129];
                for (unsigned b = 0; b < 64; ++b) snprintf(data + 2 * b, 3, "%02x", actual[b]);
                errors += !matches;
                ++completed;
                printf("LINUX_NC_SAMPLE {\"index\":%u,\"cpu\":%d,\"line\":%u,\"lineOffset\":%u,"
                       "\"width\":%u,\"offset\":%u,\"physicalAddress\":\"0x%" PRIx64
                       "\",\"written\":\"0x%" PRIx64 "\",\"read\":\"0x%" PRIx64
                       "\",\"lineData\":\"%s\",\"matches\":%s}\n",
                       index, cpu, line, line_offset, width, offset,
                       (uint64_t)info.physical_start + line_offset + offset,
                       value, readback, data, matches ? "true" : "false");
            }
        }
    }
    if (sched_setaffinity(0, sizeof(allowed), &allowed)) ++errors;
    if (munmap(mapping, 4096)) ++errors;
    if (close(fd)) ++errors;
    printf("LINUX_NC_RESULT {\"buildId\":\"%s\",\"completed\":%u,\"errors\":%d,\"status\":\"%s\"}\n",
           LINUX_UNCACHED_BUILD_ID, completed, errors, errors || completed != 64 ? "FAIL" : "PASS");
    return errors || completed != 64;
}
