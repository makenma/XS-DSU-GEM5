#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
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
#include <unistd.h>

#if !defined(__riscv) || __riscv_xlen != 64
#error This test requires actual RV64 Linux.
#endif
#ifndef RANDOM_MEMORY_BUILD_ID
#define RANDOM_MEMORY_BUILD_ID "unrecorded"
#endif

enum { THREADS = 4, WRITERS = 3, LINE = 64 };
static const uint64_t fnv_offset = UINT64_C(14695981039346656037);
static const uint64_t fnv_prime = UINT64_C(1099511628211);
static unsigned iterations = 128, pages_per_writer = 64, pressure_samples = 64;
static unsigned seed = 1, timeout_seconds = 120;
static size_t page_bytes, pressure_bytes = 1048576;
static int cpus[THREADS], actual_cpus[THREADS], pin_errors[THREADS];
static int nodes[64], node_count;
static pthread_barrier_t ready;
static _Atomic int stop_writers;
static unsigned char *pool;

struct writer {
    unsigned id;
    _Atomic uint64_t completed;
    _Atomic uint64_t *epochs;
    volatile unsigned char *pressure_a, *pressure_b;
    uint64_t *pressure_epochs;
    unsigned char *seen_pages, *seen_pressure;
    uint32_t *pressure_order;
    uint32_t random;
    size_t pressure_cursor;
    uint64_t distinct_pages, distinct_pressure, pressure_writes, alias_checks;
    uint64_t initialization_checks, final_checks, pressure_errors, page_errors;
    uint64_t main_page_errors, epoch_sum, window_writes;
};
static struct writer writers[WRITERS];

struct migration {
    unsigned round, slot, writer, page;
    int before, target, after, page_status, error_number, status;
    long query_before_rc, move_rc, query_after_rc;
    uint64_t completed_before[WRITERS], completed_after[WRITERS];
};
static struct migration *moves;
static size_t move_count;
static uint64_t selection_digest, overlap_attempts;
static uint64_t distinct_migrated[WRITERS];
static unsigned char *migration_seen[WRITERS];
static uint32_t *migration_order[WRITERS], migration_random[WRITERS];

static uint32_t next_random(uint32_t *state)
{
    uint32_t x = *state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return *state = x;
}

static uint32_t stream_seed(unsigned id, uint32_t multiplier)
{
    uint32_t value = (uint32_t)seed ^ (multiplier * id);
    return value ? value : UINT32_C(0x6d2b79f5);
}

static void shuffle(uint32_t *order, size_t count, uint32_t *state)
{
    for (size_t i = 0; i < count; ++i) order[i] = (uint32_t)i;
    for (size_t i = count - 1; i; --i) {
        size_t j = next_random(state) % (i + 1);
        uint32_t tmp = order[i]; order[i] = order[j]; order[j] = tmp;
    }
}

static unsigned char pattern(unsigned owner, uint32_t page, uint64_t epoch, size_t offset)
{
    uint64_t word = ((uint64_t)seed << 32)
        ^ (uint64_t)owner * UINT64_C(0x9e3779b97f4a7c15)
        ^ (uint64_t)page * UINT64_C(0xd1b54a32d192ed03)
        ^ epoch * UINT64_C(0x94d049bb133111eb)
        ^ (uint64_t)(offset / 8) * UINT64_C(0xbf58476d1ce4e5b9);
    return (unsigned char)(word >> (8 * (offset % 8)));
}

static unsigned char *page_address(unsigned owner, unsigned page)
{
    return pool + (((size_t)owner - 1) * pages_per_writer + page) * page_bytes;
}

static uint64_t hash_byte(uint64_t value, unsigned char byte)
{
    return (value ^ byte) * fnv_prime;
}

static void select_hash(uint32_t value)
{
    for (unsigned i = 0; i < 4; ++i)
        selection_digest = hash_byte(selection_digest, (unsigned char)(value >> (i * 8)));
}

static void abort_test(const char *reason)
{
    printf("RANDOM_MEMORY_ABORT {\"status\":\"FAIL\",\"reason\":\"%s\",\"exitCode\":1}\n", reason);
    _exit(1);
}

static void alarm_handler(int signum)
{
    (void)signum;
    const char message[] = "RANDOM_MEMORY_ABORT {\"status\":\"TIMEOUT\",\"reason\":\"guest_alarm\",\"exitCode\":124}\n";
    ssize_t result = write(STDOUT_FILENO, message, sizeof(message) - 1);
    (void)result;
    _exit(124);
}

static void wait_ready(void)
{
    int rc = pthread_barrier_wait(&ready);
    if (rc && rc != PTHREAD_BARRIER_SERIAL_THREAD) abort_test("barrier");
}

static void pin_thread(unsigned id)
{
    cpu_set_t mask;
    CPU_ZERO(&mask); CPU_SET(cpus[id], &mask);
    pin_errors[id] = pthread_setaffinity_np(pthread_self(), sizeof(mask), &mask);
    actual_cpus[id] = sched_getcpu();
    if (!pin_errors[id] && actual_cpus[id] != cpus[id]) pin_errors[id] = EINVAL;
}

static void finish_cpu(unsigned id)
{
    if (sched_getcpu() != cpus[id] && !pin_errors[id]) pin_errors[id] = EINVAL;
}

static void write_page(struct writer *w, unsigned page, uint64_t epoch)
{
    volatile unsigned char *address = page_address(w->id, page);
    for (size_t offset = 0; offset < page_bytes; ++offset)
        address[offset] = pattern(w->id, page, epoch, offset);
    /* Only this owner writes the page. Readers validate after stop + join.
     * Kernel migration may concurrently copy/move the mapping via Linux's
     * normal PTE locking, migration entries and TLB shootdowns. */
    atomic_store_explicit(&w->epochs[page], epoch, memory_order_release);
}

static void pressure_line(struct writer *w, size_t line, uint64_t epoch)
{
    size_t start = line * LINE;
    uint32_t tag = UINT32_C(0x80000000) | (uint32_t)line;
    for (size_t byte = 0; byte < LINE; ++byte)
        w->pressure_a[start + byte] = pattern(w->id, tag, epoch, byte);
    for (size_t byte = 0; byte < LINE; ++byte)
        w->pressure_errors += w->pressure_b[start + byte] != pattern(w->id, tag, epoch, byte);
    w->pressure_epochs[line] = epoch;
}

static void *write_worker(void *argument)
{
    struct writer *w = argument;
    pin_thread(w->id);
    for (unsigned page = 0; page < pages_per_writer; ++page) write_page(w, page, 1);
    size_t lines = pressure_bytes / LINE;
    shuffle(w->pressure_order, lines, &w->random);
    /* Warm and check every byte of both aliases, not merely their allocation. */
    for (size_t index = 0; index < lines; ++index) {
        pressure_line(w, w->pressure_order[index], 1);
        w->initialization_checks += LINE;
    }
    w->pressure_cursor = lines; /* First runtime sweep gets a new permutation. */
    wait_ready();
    while (!atomic_load_explicit(&stop_writers, memory_order_acquire)) {
        unsigned page = next_random(&w->random) % pages_per_writer;
        uint64_t epoch = atomic_load_explicit(&w->epochs[page], memory_order_relaxed) + 1;
        write_page(w, page, epoch);
        if (!w->seen_pages[page]) { w->seen_pages[page] = 1; ++w->distinct_pages; }
        atomic_fetch_add_explicit(&w->completed, 1, memory_order_release);
        for (unsigned sample = 0; sample < pressure_samples; ++sample) {
            if (w->pressure_cursor == lines) {
                shuffle(w->pressure_order, lines, &w->random);
                w->pressure_cursor = 0;
            }
            size_t line = w->pressure_order[w->pressure_cursor++];
            pressure_line(w, line, w->pressure_epochs[line] + 1);
            ++w->pressure_writes;
            w->alias_checks += LINE;
            if (!w->seen_pressure[line]) { w->seen_pressure[line] = 1; ++w->distinct_pressure; }
        }
    }
    /* Stop is checked only between complete page + pressure epochs. No page
     * can be abandoned partially written. Different owners have disjoint pools. */
    for (unsigned page = 0; page < pages_per_writer; ++page) {
        uint64_t epoch = atomic_load_explicit(&w->epochs[page], memory_order_acquire);
        volatile unsigned char *address = page_address(w->id, page);
        w->epoch_sum += epoch;
        for (size_t offset = 0; offset < page_bytes; ++offset)
            w->page_errors += address[offset] != pattern(w->id, page, epoch, offset);
    }
    for (size_t line = 0; line < lines; ++line) {
        uint32_t tag = UINT32_C(0x80000000) | (uint32_t)line;
        for (size_t byte = 0; byte < LINE; ++byte) {
            unsigned char expected = pattern(w->id, tag, w->pressure_epochs[line], byte);
            w->pressure_errors += w->pressure_b[line * LINE + byte] != expected;
            ++w->final_checks;
        }
    }
    finish_cpu(w->id);
    return NULL;
}

static void sample_completed(uint64_t destination[WRITERS])
{
    for (unsigned w = 0; w < WRITERS; ++w)
        destination[w] = atomic_load_explicit(&writers[w].completed, memory_order_acquire);
}

static int unsupported_errno(int value)
{
    return value == ENOSYS || value == EPERM || value == EACCES;
}

static void *migration_worker(void *argument)
{
    (void)argument;
    pin_thread(0);
    wait_ready();
    for (unsigned round = 0; round < iterations; ++round) {
        for (unsigned slot = 0; slot < WRITERS; ++slot) {
            if (!(round % pages_per_writer))
                shuffle(migration_order[slot], pages_per_writer, &migration_random[slot]);
            struct migration *m = &moves[move_count++];
            m->round = round; m->slot = slot; m->writer = slot + 1;
            m->page = migration_order[slot][round % pages_per_writer];
            m->before = m->target = m->after = m->page_status = -9999;
            m->query_before_rc = m->move_rc = m->query_after_rc = -9999;
            select_hash(round); select_hash(slot); select_hash(m->writer); select_hash(m->page);
            void *address = page_address(m->writer, m->page);
            /* Counts bracket only real query -> MOVE -> query. Never wait for
             * writer progress to manufacture an overlap observation. */
            sample_completed(m->completed_before);
            errno = 0;
            m->query_before_rc = syscall(SYS_move_pages, 0, 1, &address, NULL, &m->before, 0);
            if (m->query_before_rc || m->before < 0) {
                m->error_number = m->query_before_rc < 0 ? errno : -m->before;
                m->status = unsupported_errno(m->error_number) ? 2 : 1;
            } else {
                m->target = m->before == nodes[0] ? nodes[1] : nodes[0];
                errno = 0;
                m->move_rc = syscall(SYS_move_pages, 0, 1, &address, &m->target, &m->page_status, MPOL_MF_MOVE);
                if (m->move_rc || m->page_status != m->target) {
                    m->error_number = m->move_rc < 0 ? errno : m->page_status < 0 ? -m->page_status : EIO;
                    m->status = unsupported_errno(m->error_number) ? 2 : 1;
                } else {
                    errno = 0;
                    m->query_after_rc = syscall(SYS_move_pages, 0, 1, &address, NULL, &m->after, 0);
                    if (m->query_after_rc || m->after != m->target || m->after == m->before) {
                        m->error_number = m->query_after_rc < 0 ? errno : EIO;
                        m->status = 1;
                    }
                }
            }
            sample_completed(m->completed_after);
            uint64_t progress = 0;
            for (unsigned w = 0; w < WRITERS; ++w) {
                uint64_t delta = m->completed_after[w] - m->completed_before[w];
                writers[w].window_writes += delta;
                progress += delta;
            }
            overlap_attempts += progress != 0;
            if (!m->status && !migration_seen[slot][m->page]) {
                migration_seen[slot][m->page] = 1; ++distinct_migrated[slot];
            }
            if (m->status) goto finished;
        }
    }
finished:
    atomic_store_explicit(&stop_writers, 1, memory_order_release);
    finish_cpu(0);
    return NULL;
}

static unsigned parse(const char *text, unsigned low, unsigned high)
{
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno || !end || *end || value < low || value > high) abort_test("invalid_argument");
    return (unsigned)value;
}

static void *allocate(size_t count, size_t size)
{
    void *value = calloc(count, size);
    if (!value) abort_test("allocation");
    return value;
}

static void print_begin(void)
{
    printf("RANDOM_MEMORY_BEGIN {\"schemaVersion\":1,\"suite\":\"native-linux-random-memory\",\"buildId\":\"%s\",\"seed\":%u,\"iterations\":%u,\"pagesPerWriter\":%u,\"pageBytes\":%zu,\"pressureBytesPerWriter\":%zu,\"pressureSamplesPerEpoch\":%u,\"timeoutSeconds\":%u,\"threadsRequested\":4,\"affinityCpus\":[%d,%d,%d,%d],\"numaMemoryNodes\":[",
           RANDOM_MEMORY_BUILD_ID, seed, iterations, pages_per_writer, page_bytes,
           pressure_bytes, pressure_samples, timeout_seconds, cpus[0], cpus[1], cpus[2], cpus[3]);
    for (int n = 0; n < node_count; ++n) printf("%s%d", n ? "," : "", nodes[n]);
    puts("],\"prng\":\"xorshift32\",\"selector\":\"per-writer-fisher-yates-v1\"}");
}

static void print_moves(void)
{
    for (size_t i = 0; i < move_count; ++i) {
        const struct migration *m = &moves[i];
        printf("RANDOM_MEMORY_MIGRATION {\"round\":%u,\"slot\":%u,\"writer\":%u,\"page\":%u,\"beforeNode\":%d,\"targetNode\":%d,\"afterNode\":%d,\"queryBeforeRc\":%ld,\"moveRc\":%ld,\"queryAfterRc\":%ld,\"pageStatus\":%d,\"errorNumber\":%d,\"status\":\"%s\",\"completedBefore\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "],\"completedAfter\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]}\n",
               m->round, m->slot, m->writer, m->page, m->before, m->target, m->after,
               m->query_before_rc, m->move_rc, m->query_after_rc, m->page_status, m->error_number,
               m->status == 0 ? "PASS" : m->status == 2 ? "UNSUPPORTED" : "FAIL",
               m->completed_before[0], m->completed_before[1], m->completed_before[2],
               m->completed_after[0], m->completed_after[1], m->completed_after[2]);
    }
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    for (int a = 1; a < argc; ++a) {
        const char *name = argv[a];
        if (++a == argc) abort_test("missing_argument_value");
        if (!strcmp(name, "--iterations")) iterations = parse(argv[a], 64, 100000);
        else if (!strcmp(name, "--seed")) seed = parse(argv[a], 1, UINT32_MAX);
        else if (!strcmp(name, "--pages-per-writer")) pages_per_writer = parse(argv[a], 64, 4096);
        else if (!strcmp(name, "--pressure-bytes")) pressure_bytes = parse(argv[a], 1048576, 16777216);
        else if (!strcmp(name, "--pressure-samples")) pressure_samples = parse(argv[a], 64, 16384);
        else if (!strcmp(name, "--timeout")) timeout_seconds = parse(argv[a], 1, 86400);
        else abort_test("unknown_argument");
    }
    if (iterations < pages_per_writer || pressure_bytes % LINE) abort_test("invalid_working_set");
    long system_page = sysconf(_SC_PAGESIZE);
    if (system_page < 4096) abort_test("invalid_page_size");
    page_bytes = (size_t)system_page;
    struct sigaction action;
    memset(&action, 0, sizeof(action)); action.sa_handler = alarm_handler;
    if (sigaction(SIGALRM, &action, NULL)) abort_test("sigaction");
    alarm(timeout_seconds);
    cpu_set_t allowed;
    CPU_ZERO(&allowed);
    if (sched_getaffinity(0, sizeof(allowed), &allowed)) abort_test("getaffinity");
    unsigned found = 0;
    for (int cpu = 0; cpu < CPU_SETSIZE && found < THREADS; ++cpu)
        if (CPU_ISSET(cpu, &allowed)) cpus[found++] = cpu;
    if (found != THREADS) abort_test("need_four_allowed_cpus");
    for (int node = 0; node < 64; ++node) {
        char path[128], line[256];
        snprintf(path, sizeof(path), "/sys/devices/system/node/node%d/meminfo", node);
        FILE *file = fopen(path, "r");
        if (!file) continue;
        unsigned long long memory = 0;
        while (fgets(line, sizeof(line), file)) {
            char *total = strstr(line, "MemTotal:");
            if (total) { (void)sscanf(total, "MemTotal: %llu", &memory); break; }
        }
        fclose(file);
        if (memory) nodes[node_count++] = node;
    }
    print_begin();
    if (node_count < 2) {
        puts("RANDOM_MEMORY_END {\"status\":\"UNSUPPORTED\",\"exitCode\":2,\"threadsStarted\":0,\"reason\":\"fewer_than_two_memory_nodes\"}");
        return 2;
    }
    size_t pool_size = (size_t)WRITERS * pages_per_writer * page_bytes;
    pool = mmap(NULL, pool_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (pool == MAP_FAILED) abort_test("migration_mmap");
    if (madvise(pool, pool_size, MADV_NOHUGEPAGE)) abort_test("disable_thp");
    moves = allocate((size_t)iterations * WRITERS, sizeof(*moves));
    selection_digest = fnv_offset;
    for (unsigned index = 0; index < WRITERS; ++index) {
        struct writer *w = &writers[index];
        w->id = index + 1;
        w->random = stream_seed(w->id, UINT32_C(0x85ebca6b));
        w->epochs = allocate(pages_per_writer, sizeof(*w->epochs));
        for (unsigned page = 0; page < pages_per_writer; ++page) atomic_init(&w->epochs[page], 0);
        atomic_init(&w->completed, 0);
        w->seen_pages = allocate(pages_per_writer, 1);
        size_t lines = pressure_bytes / LINE;
        w->pressure_epochs = allocate(lines, sizeof(*w->pressure_epochs));
        w->seen_pressure = allocate(lines, 1);
        w->pressure_order = allocate(lines, sizeof(*w->pressure_order));
        int fd = (int)syscall(SYS_memfd_create, "chi-random-pressure", MFD_CLOEXEC);
        if (fd < 0 || ftruncate(fd, (off_t)pressure_bytes)) abort_test("pressure_memfd");
        w->pressure_a = mmap(NULL, pressure_bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        w->pressure_b = mmap(NULL, pressure_bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (w->pressure_a == MAP_FAILED || w->pressure_b == MAP_FAILED || w->pressure_a == w->pressure_b)
            abort_test("pressure_alias_mmap");
        close(fd);
        migration_order[index] = allocate(pages_per_writer, sizeof(*migration_order[index]));
        migration_seen[index] = allocate(pages_per_writer, 1);
        migration_random[index] = stream_seed(w->id, UINT32_C(0x9e3779b9));
    }
    if (pthread_barrier_init(&ready, NULL, THREADS)) abort_test("barrier_init");
    pthread_t threads[THREADS];
    if (pthread_create(&threads[0], NULL, migration_worker, NULL)) abort_test("migrator_create");
    for (unsigned id = 1; id < THREADS; ++id)
        if (pthread_create(&threads[id], NULL, write_worker, &writers[id - 1])) abort_test("writer_create");
    for (unsigned id = 0; id < THREADS; ++id)
        if (pthread_join(threads[id], NULL)) abort_test("thread_join");
    cpu_set_t verifier_mask;
    CPU_ZERO(&verifier_mask); CPU_SET(cpus[0], &verifier_mask);
    if (sched_setaffinity(0, sizeof(verifier_mask), &verifier_mask)) abort_test("verifier_affinity");
    int verifier_cpu = sched_getcpu();
    if (verifier_cpu != cpus[0]) abort_test("verifier_cpu");
    /* All application payload reads below are after every writer has stopped.
     * A second CPU verifies the actual mapped pages against the published
     * version, so an owner reading its own stale TLB mapping is insufficient. */
    print_moves();
    uint64_t errors = 0, total_page_errors = 0, total_pressure_errors = 0;
    uint64_t passed = 0, unsupported = 0;
    for (size_t i = 0; i < move_count; ++i) {
        passed += moves[i].status == 0;
        unsupported += moves[i].status == 2;
        errors += moves[i].status == 1;
    }
    for (unsigned id = 0; id < THREADS; ++id) errors += pin_errors[id] != 0;
    for (unsigned index = 0; index < WRITERS; ++index) {
        struct writer *w = &writers[index];
        for (unsigned page = 0; page < pages_per_writer; ++page) {
            uint64_t epoch = atomic_load_explicit(&w->epochs[page], memory_order_acquire);
            volatile unsigned char *address = page_address(w->id, page);
            uint64_t byte_errors = 0, hash = fnv_offset;
            for (size_t offset = 0; offset < page_bytes; ++offset) {
                unsigned char actual = address[offset];
                byte_errors += actual != pattern(w->id, page, epoch, offset);
                hash = hash_byte(hash, actual);
            }
            w->main_page_errors += byte_errors;
            printf("RANDOM_MEMORY_PAGE {\"writer\":%u,\"page\":%u,\"epoch\":%" PRIu64 ",\"bytes\":%zu,\"byteErrors\":%" PRIu64 ",\"fnv64\":\"%016" PRIx64 "\"}\n",
                   w->id, page, epoch, page_bytes, byte_errors, hash);
        }
        uint64_t runtime = atomic_load_explicit(&w->completed, memory_order_acquire);
        printf("RANDOM_MEMORY_WRITER {\"writer\":%u,\"cpu\":%d,\"pinError\":%d,\"pagesWritten\":%" PRIu64 ",\"runtimePagesWritten\":%" PRIu64 ",\"distinctRuntimePages\":%" PRIu64 ",\"epochSum\":%" PRIu64 ",\"finalPageByteErrors\":%" PRIu64 ",\"mainPageByteErrors\":%" PRIu64 ",\"pressureAllocatedBytes\":%zu,\"pressureInitializedBytes\":%zu,\"pressureInitializationChecks\":%" PRIu64 ",\"pressureDistinctRuntimeLines\":%" PRIu64 ",\"pressureRuntimeWrites\":%" PRIu64 ",\"pressureRuntimeAliasChecks\":%" PRIu64 ",\"pressureFinalChecks\":%" PRIu64 ",\"pressureByteErrors\":%" PRIu64 ",\"migrationWindowWrites\":%" PRIu64 "}\n",
               w->id, actual_cpus[w->id], pin_errors[w->id], runtime + pages_per_writer,
               runtime, w->distinct_pages, w->epoch_sum, w->page_errors, w->main_page_errors,
               pressure_bytes, pressure_bytes, w->initialization_checks, w->distinct_pressure,
               w->pressure_writes, w->alias_checks, w->final_checks, w->pressure_errors, w->window_writes);
        total_page_errors += w->page_errors + w->main_page_errors;
        total_pressure_errors += w->pressure_errors;
        errors += w->epoch_sum != runtime + pages_per_writer;
        errors += w->initialization_checks != pressure_bytes || w->final_checks != pressure_bytes;
        errors += w->pressure_writes != runtime * pressure_samples || w->alias_checks != w->pressure_writes * LINE;
        if (!unsupported) {
            errors += !runtime || !w->window_writes || w->distinct_pressure < pressure_samples;
            errors += distinct_migrated[index] != pages_per_writer;
        }
    }
    errors += total_page_errors + total_pressure_errors;
    if (!unsupported) errors += move_count != (size_t)iterations * WRITERS || !overlap_attempts;
    int exit_code = errors ? 1 : unsupported ? 2 : 0;
    printf("RANDOM_MEMORY_END {\"status\":\"%s\",\"exitCode\":%d,\"threadsStarted\":4,\"verifierCpu\":%d,\"threadCpuIds\":[%d,%d,%d,%d],\"threadPinErrors\":[%d,%d,%d,%d],\"migrationAttempts\":%zu,\"migrationsPassed\":%" PRIu64 ",\"distinctMigratedPages\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "],\"overlapAttempts\":%" PRIu64 ",\"writerWindowWrites\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "],\"selectionDigest\":\"%016" PRIx64 "\",\"totalPageByteErrors\":%" PRIu64 ",\"totalPressureByteErrors\":%" PRIu64 ",\"errors\":%" PRIu64 "}\n",
           exit_code == 0 ? "PASS" : exit_code == 2 ? "UNSUPPORTED" : "FAIL", exit_code,
           verifier_cpu,
           actual_cpus[0], actual_cpus[1], actual_cpus[2], actual_cpus[3],
           pin_errors[0], pin_errors[1], pin_errors[2], pin_errors[3], move_count, passed,
           distinct_migrated[0], distinct_migrated[1], distinct_migrated[2], overlap_attempts,
           writers[0].window_writes, writers[1].window_writes, writers[2].window_writes,
           selection_digest, total_page_errors, total_pressure_errors, errors);
    alarm(0);
    pthread_barrier_destroy(&ready);
    return exit_code;
}
