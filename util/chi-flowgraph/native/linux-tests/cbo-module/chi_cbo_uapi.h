#ifndef CHI_CBO_UAPI_H
#define CHI_CBO_UAPI_H
#include <linux/ioctl.h>
#include <linux/types.h>
struct chi_cbo_env { __u64 value; __u32 cpu; __u32 reserved; };
struct chi_cbo_pte {
    __u64 address;
    __u32 operation; /* 0=inval, 1=clean, 2=flush */
    __s32 error;
    __u32 cpu;
    __u32 writable_vma;
    __u64 before, cleared, after, restored;
};
#define CHI_CBO_SET_ENV _IOW('C', 0x31, __u32)
#define CHI_CBO_GET_ENV _IOR('C', 0x32, struct chi_cbo_env)
#define CHI_CBO_TEST_PTE _IOWR('C', 0x33, struct chi_cbo_pte)
#endif
