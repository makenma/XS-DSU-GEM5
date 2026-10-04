#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <linux/futex.h>
#include <linux/memfd.h>
#include <linux/mempolicy.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#if !defined(__riscv) || __riscv_xlen != 64
#error This workload requires real RV64 Linux; no host emulation substitutes.
#endif

enum { THREADS = 4, TESTS = 11 };
enum test_id { SPIN, MUTEX, FUTEX, AMO, LRSC, RELEASE, FALSE_SHARING,
               COUNTER, MEMCPY, ALIAS, MIGRATION };
static const char *const names[TESTS] = {
    "spinlock", "mutex", "futex", "amo", "lrsc", "release_acquire",
    "false_sharing", "atomic_counter", "memcpy_checksum", "alias_cache_pressure", "page_migration"
};
struct thread_result {
    uint64_t operations, errors, checksum, waits, slept, wakes, sc_failures;
    int cpu, pin_error;
} __attribute__((aligned(64)));
struct handoff {
    _Atomic unsigned ready, ack;
    uint64_t payload[8];
} __attribute__((aligned(64)));
static struct thread_result results[THREADS];
static struct handoff handoffs[2];
static _Atomic uint64_t counter;
static _Atomic uint64_t neighbors[8] __attribute__((aligned(64)));
static _Atomic unsigned active, turn;
static uint64_t protected_count;
static pthread_spinlock_t spin;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_barrier_t start_barrier, phase_barrier;
static unsigned iterations = 128, timeout_seconds = 120;
static size_t copy_bytes = 4096, pressure_bytes = 65536, page_bytes;
static int cpus[THREADS], selected_test;
static unsigned char *copy_source[THREADS], *copy_target[THREADS], *pressure[THREADS];
static unsigned char *alias_a, *alias_b, *migration_pages;
static uint64_t *tickets;
static int numa_nodes[64], numa_count, migration_state;
static uint64_t migration_attempted, migration_moved;
static int migration_errno;
static const char *migration_reason = "none";
#ifndef LINUX_SUITE_BUILD_ID
#define LINUX_SUITE_BUILD_ID "unrecorded"
#endif
static const char *build_id = LINUX_SUITE_BUILD_ID;

static void timeout_handler(int signal_number) {
    (void)signal_number;
    const char line[] = "LINUX_SUITE_ABORT {\"status\":\"TIMEOUT\",\"exitCode\":124}\n";
    ssize_t written = write(STDOUT_FILENO, line, sizeof(line)-1);
    (void)written;
    _exit(124);
}
static void relax_cpu(void) { __asm__ volatile("nop" ::: "memory"); }
static unsigned char byte_pattern(unsigned owner, unsigned round, size_t offset) {
    return (unsigned char)(owner * 37 + round * 13 + offset * 17 + (offset >> 8));
}
static void barrier(pthread_barrier_t *object) {
    int code = pthread_barrier_wait(object);
    if (code && code != PTHREAD_BARRIER_SERIAL_THREAD) {
        puts("LINUX_SUITE_ABORT {\"status\":\"FAIL\",\"reason\":\"pthread_barrier_wait\"}");
        _exit(1);
    }
}
static __attribute__((noinline)) uint64_t explicit_amo(_Atomic uint64_t *address) {
    uint64_t old, one = 1;
    __asm__ volatile("amoadd.d.aqrl %0,%2,%1" : "=r"(old), "+A"(*address) : "r"(one) : "memory");
    return old;
}
static __attribute__((noinline)) uint64_t explicit_lrsc(_Atomic uint64_t *address, uint64_t *failures) {
    uint64_t old, next, failed, attempts = 0;
    __asm__ volatile("1: lr.d.aq %0,(%4)\n"
                     "addi %1,%0,1\n"
                     "sc.d.rl %2,%1,(%4)\n"
                     "addi %3,%3,1\n"
                     "bnez %2,1b"
                     : "=&r"(old), "=&r"(next), "=&r"(failed), "+&r"(attempts)
                     : "r"(address) : "memory");
    *failures += attempts - 1;
    return old;
}
static void critical(struct thread_result *r) {
    if (atomic_fetch_add_explicit(&active, 1, memory_order_relaxed) != 0) ++r->errors;
    ++protected_count;
    for (unsigned j = 0; j < 8; ++j) relax_cpu();
    if (atomic_fetch_sub_explicit(&active, 1, memory_order_relaxed) != 1) ++r->errors;
}
static void migration_step(void) {
    if (migration_state) return;
    void *pages[THREADS];
    int before[THREADS], after[THREADS], target[THREADS], status[THREADS];
    for (unsigned h = 0; h < THREADS; ++h) pages[h] = migration_pages + h * page_bytes;
    if (syscall(SYS_move_pages, 0, THREADS, pages, NULL, before, 0) != 0) {
        migration_errno = errno;
        migration_state = (errno == ENOSYS || errno == EPERM || errno == EACCES) ? 2 : 1;
        migration_reason = "move_pages_query_failed";
        return;
    }
    for (unsigned h = 0; h < THREADS; ++h) {
        if (before[h] < 0) {
            migration_state = 1; migration_reason = "page_not_resident_before_migration"; return;
        }
        target[h] = before[h] == numa_nodes[0] ? numa_nodes[1] : numa_nodes[0];
    }
    migration_attempted += THREADS;
    long rc = syscall(SYS_move_pages, 0, THREADS, pages, target, status, MPOL_MF_MOVE);
    if (rc != 0) {
        migration_errno = rc < 0 ? errno : 0;
        migration_state = rc < 0 && (errno == ENOSYS || errno == EPERM || errno == EACCES) ? 2 : 1;
        migration_reason = "move_pages_did_not_migrate_all_pages";
        return;
    }
    if (syscall(SYS_move_pages, 0, THREADS, pages, NULL, after, 0) != 0) {
        migration_errno = errno; migration_state = 1; migration_reason = "post_migration_query_failed"; return;
    }
    for (unsigned h = 0; h < THREADS; ++h) {
        if (status[h] != target[h] || after[h] != target[h] || after[h] == before[h]) {
            migration_state = 1; migration_reason = "page_node_did_not_change_to_target";
        } else ++migration_moved;
    }
}
static void *worker(void *argument) {
    const unsigned id = (unsigned)(uintptr_t)argument;
    struct thread_result *r = &results[id];
    cpu_set_t affinity;
    CPU_ZERO(&affinity); CPU_SET(cpus[id], &affinity);
    r->pin_error = pthread_setaffinity_np(pthread_self(), sizeof(affinity), &affinity);
    r->cpu = sched_getcpu();
    if (r->pin_error || r->cpu != cpus[id]) ++r->errors;
    if (selected_test == MEMCPY)
        for (size_t j = 0; j < copy_bytes; ++j) copy_source[id][j] = byte_pattern(id, 0, j);
    if (selected_test == MIGRATION)
        for (size_t j = 0; j < page_bytes; ++j) migration_pages[id * page_bytes + j] = byte_pattern(id, 0, j);
    barrier(&start_barrier);
    for (unsigned round = 0; round < iterations; ++round) {
        switch (selected_test) {
        case SPIN:
            if (pthread_spin_lock(&spin)) ++r->errors;
            critical(r);
            if (pthread_spin_unlock(&spin)) ++r->errors;
            break;
        case MUTEX:
            if (pthread_mutex_lock(&mutex)) ++r->errors;
            critical(r);
            if (pthread_mutex_unlock(&mutex)) ++r->errors;
            break;
        case FUTEX:
            for (;;) {
                unsigned observed = atomic_load_explicit(&turn, memory_order_acquire);
                if (observed == id) break;
                ++r->waits;
                long rc = syscall(SYS_futex, &turn, FUTEX_WAIT_PRIVATE, observed, NULL, NULL, 0);
                if (rc == 0) ++r->slept;
                else if (errno != EAGAIN && errno != EINTR) ++r->errors;
            }
            critical(r);
            atomic_store_explicit(&turn, (id + 1) % THREADS, memory_order_release);
            ++r->wakes;
            if (syscall(SYS_futex, &turn, FUTEX_WAKE_PRIVATE, INT_MAX, NULL, NULL, 0) < 0) ++r->errors;
            break;
        case AMO:
            tickets[id * iterations + round] = explicit_amo(&counter);
            break;
        case LRSC:
            tickets[id * iterations + round] = explicit_lrsc(&counter, &r->sc_failures);
            break;
        case COUNTER:
            tickets[id * iterations + round] = atomic_fetch_add_explicit(&counter, 1, memory_order_relaxed);
            break;
        case RELEASE: {
            struct handoff *h = &handoffs[id / 2];
            if (!(id & 1)) {
                while (atomic_load_explicit(&h->ack, memory_order_acquire) != round) relax_cpu();
                for (unsigned j = 0; j < 8; ++j) h->payload[j] = ((uint64_t)(id / 2) << 48) | ((uint64_t)(round + 1) << 8) | j;
                atomic_store_explicit(&h->ready, round + 1, memory_order_release);
            } else {
                while (atomic_load_explicit(&h->ready, memory_order_acquire) != round + 1) relax_cpu();
                for (unsigned j = 0; j < 8; ++j)
                    r->errors += h->payload[j] != (((uint64_t)(id / 2) << 48) | ((uint64_t)(round + 1) << 8) | j);
                atomic_store_explicit(&h->ack, round + 1, memory_order_release);
            }
            break;
        }
        case FALSE_SHARING:
            atomic_fetch_add_explicit(&neighbors[id], 1, memory_order_relaxed);
            break;
        case MEMCPY: {
            const unsigned owner = (id + 1) % THREADS;
            memcpy(copy_target[id], copy_source[owner], copy_bytes);
            for (size_t j = 0; j < copy_bytes; ++j) {
                r->errors += copy_target[id][j] != byte_pattern(owner, 0, j);
                r->checksum += copy_target[id][j];
            }
            break;
        }
        case ALIAS: {
            for (size_t j = 0; j < page_bytes; ++j) alias_a[id * page_bytes + j] = byte_pattern(id, round, j);
            for (size_t j = 0; j < pressure_bytes; j += 64)
                ((volatile unsigned char *)pressure[id])[j] = byte_pattern(id, round, j);
            barrier(&phase_barrier);
            const unsigned owner = (id + 1) % THREADS;
            for (size_t j = 0; j < page_bytes; ++j)
                r->errors += alias_b[owner * page_bytes + j] != byte_pattern(owner, round, j);
            for (size_t j = 0; j < pressure_bytes; j += 64)
                r->errors += ((volatile unsigned char *)pressure[id])[j] != byte_pattern(id, round, j);
            barrier(&phase_barrier);
            break;
        }
        case MIGRATION:
            barrier(&phase_barrier);
            if (id == 0) migration_step();
            barrier(&phase_barrier);
            if (migration_state) continue;
            for (size_t j = 0; j < page_bytes; ++j)
                r->errors += migration_pages[id * page_bytes + j] != byte_pattern(id, 0, j);
            break;
        }
        ++r->operations;
    }
    if (sched_getcpu() != cpus[id]) ++r->errors;
    return NULL;
}

static void *allocate_bytes(size_t count) {
    void *memory = NULL;
    if (posix_memalign(&memory, 64, count)) return NULL;
    memset(memory, 0, count);
    return memory;
}
static unsigned long argument(const char *text, unsigned long low, unsigned long high) {
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno || !end || *end || value < low || value > high) {
        fprintf(stderr, "invalid numeric argument: %s\n", text); exit(2);
    }
    return value;
}
static int run_test(int test) {
    selected_test = test;
    memset(results, 0, sizeof(results));
    memset(handoffs, 0, sizeof(handoffs));
    atomic_store(&counter, 0); atomic_store(&active, 0); atomic_store(&turn, 0);
    for (unsigned h = 0; h < 8; ++h) atomic_store(&neighbors[h], 0);
    protected_count = 0;
    migration_state = 0; migration_attempted = migration_moved = 0; migration_errno = 0;
    migration_reason = "none";
    if (test == MIGRATION && numa_count < 2) {
        migration_state = 2; migration_reason = "fewer_than_two_memory_numa_nodes";
    }
    pthread_t threads[THREADS];
    unsigned started = 0;
    if (pthread_barrier_init(&start_barrier, NULL, THREADS + 1) ||
        pthread_barrier_init(&phase_barrier, NULL, THREADS)) {
        puts("LINUX_SUITE_ABORT {\"status\":\"FAIL\",\"reason\":\"barrier_init\"}"); return 1;
    }
    for (unsigned h = 0; h < THREADS; ++h) {
        int code = pthread_create(&threads[h], NULL, worker, (void *)(uintptr_t)h);
        if (code) {
            printf("LINUX_SUITE_ABORT {\"status\":\"FAIL\",\"reason\":\"pthread_create\",\"threadsStarted\":%u,\"error\":%d}\n", started, code);
            _exit(1);
        }
        ++started;
    }
    barrier(&start_barrier);
    uint64_t errors = 0, operations = 0, waits = 0, slept = 0, wakes = 0, failures = 0, checksum = 0;
    for (unsigned h = 0; h < THREADS; ++h) {
        errors += pthread_join(threads[h], NULL) != 0;
        errors += results[h].errors; operations += results[h].operations;
        waits += results[h].waits; slept += results[h].slept; wakes += results[h].wakes;
        failures += results[h].sc_failures; checksum += results[h].checksum;
    }
    const uint64_t expected = (uint64_t)THREADS * iterations;
    uint64_t observed = operations;
    if (test == SPIN || test == MUTEX || test == FUTEX) observed = protected_count;
    if (test == AMO || test == LRSC || test == COUNTER) {
        observed = atomic_load(&counter);
        unsigned char *seen = calloc((size_t)expected, 1);
        if (!seen) ++errors;
        else {
            for (uint64_t i = 0; i < expected; ++i) {
                if (tickets[i] >= expected || seen[tickets[i]]) ++errors;
                else seen[tickets[i]] = 1;
            }
            free(seen);
        }
    }
    if (test == FALSE_SHARING)
        for (unsigned h = 0; h < THREADS; ++h) errors += atomic_load(&neighbors[h]) != iterations;
    if (test == RELEASE)
        for (unsigned p = 0; p < 2; ++p) errors += atomic_load(&handoffs[p].ack) != iterations;
    if (test == FUTEX) errors += waits == 0 || slept == 0 || wakes != expected;
    if (test == MIGRATION && migration_state == 1) ++errors;
    if (migration_state != 2) errors += observed != expected || operations != expected;
    int status = errors ? 1 : migration_state == 2 ? 2 : 0;
    printf("LINUX_TEST_RESULT {\"test\":\"%s\",\"status\":\"%s\",\"threadsRequested\":4,\"threadsStarted\":%u,\"iterations\":%u,\"operationsExpected\":%" PRIu64 ",\"operations\":%" PRIu64 ",\"observed\":%" PRIu64 ",\"errors\":%" PRIu64,
           names[test], status == 0 ? "PASS" : status == 2 ? "UNSUPPORTED" : "FAIL", started, iterations, expected, operations, observed, errors);
    printf(",\"threadOperations\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]", results[0].operations, results[1].operations, results[2].operations, results[3].operations);
    printf(",\"threadErrors\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]", results[0].errors, results[1].errors, results[2].errors, results[3].errors);
    printf(",\"threadCpuIds\":[%d,%d,%d,%d],\"threadPinErrors\":[%d,%d,%d,%d]", results[0].cpu, results[1].cpu, results[2].cpu, results[3].cpu,
           results[0].pin_error, results[1].pin_error, results[2].pin_error, results[3].pin_error);
    printf(",\"metrics\":{\"futexWaitCalls\":%" PRIu64 ",\"futexSlept\":%" PRIu64 ",\"futexWakeCalls\":%" PRIu64 ",\"scFailures\":%" PRIu64 ",\"checksum\":%" PRIu64 ",\"copyBytesPerOperation\":%zu,\"pressureBytesPerThread\":%zu,\"aliasMappings\":2,\"pageBytes\":%zu,\"numaMemoryNodes\":%d,\"migrationPagesAttempted\":%" PRIu64 ",\"migrationPagesMoved\":%" PRIu64 ",\"migrationErrno\":%d,\"migrationReason\":\"%s\"}}\n",
           waits, slept, wakes, failures, checksum, copy_bytes, pressure_bytes, page_bytes, numa_count,
           migration_attempted, migration_moved, migration_errno, migration_reason);
    pthread_barrier_destroy(&start_barrier); pthread_barrier_destroy(&phase_barrier);
    return status;
}
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    int only = -1;
    for (int i = 1; i < argc; ++i) {
        if (i + 1 == argc) { fprintf(stderr, "missing option value\n"); return 2; }
        const char *value = argv[++i];
        if (!strcmp(argv[i-1], "--iterations")) iterations = argument(value, 1, 1000000);
        else if (!strcmp(argv[i-1], "--timeout")) timeout_seconds = argument(value, 1, 86400);
        else if (!strcmp(argv[i-1], "--copy-bytes")) copy_bytes = argument(value, 64, 1048576);
        else if (!strcmp(argv[i-1], "--pressure-bytes")) pressure_bytes = argument(value, 65536, 16777216);
        else if (!strcmp(argv[i-1], "--test")) {
            if (!strcmp(value, "all")) only = -1;
            else {
                only = -2;
                for (int test = 0; test < TESTS; ++test) if (!strcmp(value, names[test])) only = test;
                if (only == -2) { fprintf(stderr, "unknown test\n"); return 2; }
            }
        } else { fprintf(stderr, "unknown option\n"); return 2; }
    }
    struct sigaction action;
    memset(&action, 0, sizeof(action)); action.sa_handler = timeout_handler;
    if (sigaction(SIGALRM, &action, NULL)) return 1;
    alarm(timeout_seconds);
    cpu_set_t allowed;
    CPU_ZERO(&allowed);
    if (sched_getaffinity(0, sizeof(allowed), &allowed)) return 1;
    unsigned count = 0;
    for (int cpu = 0; cpu < CPU_SETSIZE && count < THREADS; ++cpu)
        if (CPU_ISSET(cpu, &allowed)) cpus[count++] = cpu;
    if (count != THREADS) {
        printf("LINUX_SUITE_ABORT {\"status\":\"UNSUPPORTED\",\"reason\":\"need_four_allowed_online_cpus\",\"available\":%u}\n", count);
        return 2;
    }
    page_bytes = (size_t)sysconf(_SC_PAGESIZE);
    if (page_bytes < 4096 || pthread_spin_init(&spin, PTHREAD_PROCESS_PRIVATE)) return 1;
    for (unsigned h = 0; h < THREADS; ++h) {
        copy_source[h] = allocate_bytes(copy_bytes); copy_target[h] = allocate_bytes(copy_bytes);
        pressure[h] = allocate_bytes(pressure_bytes);
        if (!copy_source[h] || !copy_target[h] || !pressure[h]) return 1;
    }
    tickets = calloc((size_t)THREADS * iterations, sizeof(*tickets));
    if (!tickets) return 1;
    int fd = (int)syscall(SYS_memfd_create, "chi-linux-alias", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, (off_t)(THREADS * page_bytes))) return 1;
    alias_a = mmap(NULL, THREADS * page_bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    alias_b = mmap(NULL, THREADS * page_bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    migration_pages = mmap(NULL, THREADS * page_bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (alias_a == MAP_FAILED || alias_b == MAP_FAILED || migration_pages == MAP_FAILED || alias_a == alias_b) return 1;
    close(fd);
    for (int node = 0; node < 64; ++node) {
        char path[128], line[256];
        snprintf(path, sizeof(path), "/sys/devices/system/node/node%d/meminfo", node);
        FILE *stream = fopen(path, "r");
        if (!stream) continue;
        unsigned long long memory = 0;
        while (fgets(line, sizeof(line), stream)) {
            char *total = strstr(line, "MemTotal:");
            if (total) { (void)sscanf(total, "MemTotal: %llu", &memory); break; }
        }
        fclose(stream);
        if (memory) numa_nodes[numa_count++] = node;
    }
    printf("LINUX_SUITE_BEGIN {\"schemaVersion\":1,\"suite\":\"native-linux-pthreads\",\"buildId\":\"%s\",\"threadsRequested\":4,\"iterations\":%u,\"timeoutSeconds\":%u,\"onlineCpus\":%ld,\"affinityCpus\":[%d,%d,%d,%d],\"selectedTests\":[", build_id, iterations, timeout_seconds, sysconf(_SC_NPROCESSORS_ONLN), cpus[0], cpus[1], cpus[2], cpus[3]);
    unsigned selected = 0;
    for (int test = 0; test < TESTS; ++test) if (only < 0 || only == test)
        printf("%s\"%s\"", selected++ ? "," : "", names[test]);
    puts("]}");
    unsigned passed = 0, failed = 0, unsupported = 0;
    for (int test = 0; test < TESTS; ++test) {
        if (only >= 0 && only != test) continue;
        int status = run_test(test);
        if (status == 0) ++passed; else if (status == 2) ++unsupported; else ++failed;
    }
    int exit_code = failed ? 1 : unsupported ? 2 : 0;
    printf("LINUX_SUITE_END {\"status\":\"%s\",\"testsCompleted\":%u,\"passed\":%u,\"failed\":%u,\"unsupported\":%u,\"exitCode\":%d}\n", exit_code == 0 ? "PASS" : failed ? "FAIL" : "INCOMPLETE", passed + failed + unsupported, passed, failed, unsupported, exit_code);
    return exit_code;
}
