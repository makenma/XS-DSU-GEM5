#include <gem5/m5ops.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <termios.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "stage")) {
        printf("CHI_LINUX_STAGE_DONE=%s\n", argv[2]);
        fflush(stdout);
        if (tcdrain(STDOUT_FILENO)) return 3;
        m5_exit(0); /* Host validates this stage before resuming this guest. */
        return 0;
    }
    if (argc == 2 && !strcmp(argv[1], "exit")) {
        m5_exit(0);
        return 0;
    }
    if (argc == 3 && !strcmp(argv[1], "fail")) {
        m5_fail(0, strtoul(argv[2], 0, 0));
        return 1;
    }
    if (argc == 2 && !strcmp(argv[1], "resetstats")) {
        m5_reset_stats(0, 0);
        return 0;
    }
    if (argc == 2 && !strcmp(argv[1], "dumpstats")) {
        m5_dump_stats(0, 0);
        return 0;
    }
    return 2;
}
