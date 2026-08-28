/*
 * Tiny libc-free init shell used to validate CHI Linux checkpoint/restore.
 *
 * The normal SPEC initramfs takes a long time to decompress in the detailed
 * 16-core model.  This program is intended to be installed as /bin/sh in a
 * minimal initramfs and selected with rdinit=/bin/sh.  Reaching its prompt
 * therefore proves that Linux completed initialization, while the checkpoint
 * command executes the gem5 pseudo instruction from a live guest context.
 */

typedef unsigned long size_t;

enum {
    SYS_MOUNT = 40,
    SYS_OPENAT = 56,
    SYS_CLOSE = 57,
    SYS_READ = 63,
    SYS_WRITE = 64,
    SYS_EXIT = 93,
    SYS_UNAME = 160,
};

enum { AT_FDCWD = -100 };

struct utsname {
    char sysname[65];
    char nodename[65];
    char release[65];
    char version[65];
    char machine[65];
    char domainname[65];
};

static long
syscall1(long nr, long arg0)
{
    register long a0 __asm__("a0") = arg0;
    register long a7 __asm__("a7") = nr;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7) : "memory");
    return a0;
}

static long
syscall3(long nr, long arg0, long arg1, long arg2)
{
    register long a0 __asm__("a0") = arg0;
    register long a1 __asm__("a1") = arg1;
    register long a2 __asm__("a2") = arg2;
    register long a7 __asm__("a7") = nr;
    __asm__ volatile("ecall" : "+r"(a0)
                     : "r"(a1), "r"(a2), "r"(a7) : "memory");
    return a0;
}

static long
syscall5(long nr, long arg0, long arg1, long arg2, long arg3, long arg4)
{
    register long a0 __asm__("a0") = arg0;
    register long a1 __asm__("a1") = arg1;
    register long a2 __asm__("a2") = arg2;
    register long a3 __asm__("a3") = arg3;
    register long a4 __asm__("a4") = arg4;
    register long a7 __asm__("a7") = nr;
    __asm__ volatile("ecall" : "+r"(a0)
                     : "r"(a1), "r"(a2), "r"(a3), "r"(a4), "r"(a7)
                     : "memory");
    return a0;
}

static size_t
string_length(const char *s)
{
    size_t len = 0;
    while (s[len])
        ++len;
    return len;
}

static void
write_all(int fd, const char *data, size_t len)
{
    while (len) {
        long done = syscall3(SYS_WRITE, fd, (long)data, len);
        if (done <= 0)
            return;
        data += done;
        len -= done;
    }
}

static void
print(const char *s)
{
    write_all(1, s, string_length(s));
}

static int
string_equal(const char *a, const char *b)
{
    while (*a && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

static int
starts_with(const char *s, const char *prefix)
{
    while (*prefix) {
        if (*s++ != *prefix++)
            return 0;
    }
    return 1;
}

static char *
trim(char *s)
{
    char *end;
    while (*s == ' ' || *s == '\t')
        ++s;
    end = s + string_length(s);
    while (end != s && (end[-1] == ' ' || end[-1] == '\t'))
        --end;
    *end = '\0';
    return s;
}

static long
parse_number(const char **text)
{
    const char *s = *text;
    long value = 0;
    long sign = 1;

    while (*s == ' ' || *s == '\t')
        ++s;
    if (*s == '-') {
        sign = -1;
        ++s;
    }
    while (*s >= '0' && *s <= '9') {
        value = value * 10 + (*s - '0');
        ++s;
    }
    *text = s;
    return value * sign;
}

static void
print_number(long value)
{
    char buf[32];
    unsigned long magnitude;
    size_t pos = sizeof(buf);

    if (value < 0)
        magnitude = (unsigned long)(-(value + 1)) + 1;
    else
        magnitude = value;
    do {
        buf[--pos] = '0' + magnitude % 10;
        magnitude /= 10;
    } while (magnitude);
    if (value < 0)
        buf[--pos] = '-';
    write_all(1, buf + pos, sizeof(buf) - pos);
}

static void
print_uname(void)
{
    struct utsname name;
    if (syscall1(SYS_UNAME, (long)&name) < 0) {
        print("uname: syscall failed\n");
        return;
    }
    print(name.sysname);
    print(" ");
    print(name.nodename);
    print(" ");
    print(name.release);
    print(" ");
    print(name.version);
    print(" ");
    print(name.machine);
    print("\n");
}

static void
cat_file(const char *path)
{
    char buf[1024];
    long fd = syscall3(SYS_OPENAT, AT_FDCWD, (long)path, 0);
    if (fd < 0) {
        print("cat: cannot open ");
        print(path);
        print("\n");
        return;
    }
    for (;;) {
        long len = syscall3(SYS_READ, fd, (long)buf, sizeof(buf));
        if (len <= 0)
            break;
        write_all(1, buf, len);
    }
    syscall1(SYS_CLOSE, fd);
}

static void
print_nproc(void)
{
    char buf[1024];
    char prefix[] = "processor";
    size_t matched = 0;
    long count = 0;
    int line_start = 1;
    long fd = syscall3(SYS_OPENAT, AT_FDCWD, (long)"/proc/cpuinfo", 0);

    if (fd < 0) {
        print("nproc: /proc is unavailable\n");
        return;
    }
    for (;;) {
        long len = syscall3(SYS_READ, fd, (long)buf, sizeof(buf));
        long i;
        if (len <= 0)
            break;
        for (i = 0; i < len; ++i) {
            char c = buf[i];
            if (line_start) {
                if (c == prefix[matched]) {
                    if (++matched == sizeof(prefix) - 1) {
                        ++count;
                        line_start = 0;
                        matched = 0;
                    }
                } else {
                    line_start = 0;
                    matched = 0;
                }
            }
            if (c == '\n') {
                line_start = 1;
                matched = 0;
            }
        }
    }
    syscall1(SYS_CLOSE, fd);
    print_number(count);
    print("\n");
}

static void
guest_checkpoint(void)
{
    print("CHECKPOINT_REQUESTED\n");
    __asm__ volatile(
        "fence rw, rw\n"
        "li a0, 0\n"
        "li a1, 0\n"
        ".word 0x8600007b\n"
        : : : "a0", "a1", "memory");
    print("CHECKPOINT_RESTORED\n");
}

static void
execute_command(char *command)
{
    const char *numbers;
    long first;
    long second;
    command = trim(command);

    if (!*command || *command == '#')
        return;
    if (string_equal(command, "help")) {
        print("commands: echo, uname -a, nproc, cat PATH, add A B, "
              "checkpoint, pwd, help\n");
    } else if (string_equal(command, "uname") ||
               string_equal(command, "uname -a")) {
        print_uname();
    } else if (string_equal(command, "nproc")) {
        print_nproc();
    } else if (string_equal(command, "pwd")) {
        print("/\n");
    } else if (string_equal(command, "checkpoint") ||
               string_equal(command, "/bin/m5_checkpoint")) {
        guest_checkpoint();
    } else if (starts_with(command, "echo ")) {
        print(command + 5);
        print("\n");
    } else if (string_equal(command, "echo")) {
        print("\n");
    } else if (starts_with(command, "cat ")) {
        cat_file(trim(command + 4));
    } else if (starts_with(command, "add ")) {
        numbers = command + 4;
        first = parse_number(&numbers);
        second = parse_number(&numbers);
        print_number(first + second);
        print("\n");
    } else if (string_equal(command, "true")) {
        return;
    } else if (string_equal(command, "exit")) {
        print("exit: PID 1 shell remains active\n");
    } else {
        print("sh: ");
        print(command);
        print(": command not found\n");
    }
}

static void
execute_line(char *line)
{
    char *command = line;
    char *cursor = line;
    for (;;) {
        if (*cursor == ';' || *cursor == '\0') {
            char saved = *cursor;
            *cursor = '\0';
            execute_command(command);
            if (!saved)
                return;
            command = cursor + 1;
        }
        ++cursor;
    }
}

__attribute__((used, noreturn)) static void
shell_main(void)
{
    char line[8192];
    size_t used = 0;
    int previous_cr = 0;

    /* /proc is optional for the shell, but enables nproc and cpuinfo. */
    syscall5(SYS_MOUNT, (long)"proc", (long)"/proc", (long)"proc", 0, 0);

    print("CHI16_LINUX_READY\n");
    print("Minimal guest shell is running as Linux PID 1\n");
    print("/ # ");

    for (;;) {
        char input[1024];
        long len = syscall3(SYS_READ, 0, (long)input, sizeof(input));
        long i;
        if (len <= 0)
            continue;
        for (i = 0; i < len; ++i) {
            char c = input[i];
            if (c == '\n' && previous_cr) {
                previous_cr = 0;
                continue;
            }
            previous_cr = c == '\r';
            if (c == '\r' || c == '\n') {
                line[used] = '\0';
                execute_line(line);
                used = 0;
                print("/ # ");
            } else if (used + 1 < sizeof(line)) {
                line[used++] = c;
            } else {
                used = 0;
                print("\nsh: input line too long\n/ # ");
            }
        }
    }
}

__attribute__((naked, noreturn, section(".text.start"))) void
_start(void)
{
    __asm__ volatile(
        "la gp, __global_pointer$\n"
        "call shell_main\n"
        "li a0, 0\n"
        "li a7, %0\n"
        "ecall\n"
        : : "i"(SYS_EXIT) : "memory");
}
