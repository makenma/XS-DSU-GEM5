#ifndef CHI_UNCACHED_UAPI_H
#define CHI_UNCACHED_UAPI_H
#include <linux/ioctl.h>
#include <linux/types.h>

#define CHI_UNCACHED_ABI 1
struct chi_uncached_info {
    __u64 physical_start;
    __u64 bytes;
    __u64 mapping_protection;
    __u32 abi;
    __u32 dt_no_map;
};
#define CHI_UNCACHED_INFO _IOR('N', 0x71, struct chi_uncached_info)
#endif
