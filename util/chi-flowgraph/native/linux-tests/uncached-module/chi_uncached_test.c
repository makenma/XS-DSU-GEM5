// SPDX-License-Identifier: GPL-2.0
/* Test access to one DT-reserved Normal noncacheable RAM page. The simulator's
 * physical PMA supplies the memory type; this module never requests a Device
 * mapping, manufactures a CHI packet, or creates a kernel cacheable alias. */
#include <linux/capability.h>
#include <linux/fs.h>
#include <linux/ioport.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of_address.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include "chi_uncached_uapi.h"

static struct resource reserved;
static atomic_t opened = ATOMIC_INIT(0);
struct test_file {
    struct mutex lock;
    bool mapped;
    u64 protection;
};

static int test_open(struct inode *inode, struct file *file)
{
    struct test_file *state;
    if (!capable(CAP_SYS_RAWIO))
        return -EPERM;
    if (atomic_cmpxchg(&opened, 0, 1))
        return -EBUSY;
    state = kzalloc(sizeof(*state), GFP_KERNEL);
    if (!state) {
        atomic_set(&opened, 0);
        return -ENOMEM;
    }
    mutex_init(&state->lock);
    file->private_data = state;
    return 0;
}

static int test_release(struct inode *inode, struct file *file)
{
    kfree(file->private_data);
    /* VMAs retain the file reference, so exclusivity lasts until the mapping
     * disappears even if userspace closes its original descriptor first. */
    atomic_set(&opened, 0);
    return 0;
}

static long test_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
    struct test_file *state = file->private_data;
    struct chi_uncached_info info = {
        .physical_start = reserved.start,
        .bytes = resource_size(&reserved),
        .mapping_protection = state->protection,
        .abi = CHI_UNCACHED_ABI,
        .dt_no_map = 1,
    };
    if (cmd != CHI_UNCACHED_INFO)
        return -ENOTTY;
    return copy_to_user((void __user *)arg, &info, sizeof(info)) ? -EFAULT : 0;
}

static int test_mmap(struct file *file, struct vm_area_struct *vma)
{
    struct test_file *state = file->private_data;
    int result;
    if (!capable(CAP_SYS_RAWIO))
        return -EPERM;
    if (vma->vm_pgoff ||
        vma->vm_end - vma->vm_start != PAGE_SIZE ||
        (vma->vm_flags & (VM_READ | VM_WRITE | VM_SHARED)) !=
            (VM_READ | VM_WRITE | VM_SHARED) ||
        vma->vm_flags & VM_EXEC)
        return -EINVAL;
    mutex_lock(&state->lock);
    if (state->mapped) {
        mutex_unlock(&state->lock);
        return -EBUSY;
    }
    vm_flags_set(vma, VM_IO | VM_DONTEXPAND | VM_DONTDUMP | VM_DONTCOPY);
    vm_flags_clear(vma, VM_MAYEXEC);
    /* Leave the normal user PTE memory type intact. No Svpbmt or ioremap is
     * required: every CPU's PMAChecker owns this physical Normal-NC range. */
    result = remap_pfn_range(vma, vma->vm_start, reserved.start >> PAGE_SHIFT,
                            PAGE_SIZE, vma->vm_page_prot);
    if (!result) {
        state->mapped = true;
        state->protection = pgprot_val(vma->vm_page_prot);
    }
    mutex_unlock(&state->lock);
    return result;
}

static const struct file_operations test_ops = {
    .owner = THIS_MODULE,
    .open = test_open,
    .release = test_release,
    .unlocked_ioctl = test_ioctl,
    .mmap = test_mmap,
    .llseek = no_llseek,
};
static struct miscdevice test_device = {
    .minor = MISC_DYNAMIC_MINOR,
    .name = "chi-uncached",
    .fops = &test_ops,
    .mode = 0600,
};

static int __init test_init(void)
{
    struct device_node *node, *second, *parent;
    int result;
    node = of_find_compatible_node(NULL, NULL, "chi-flowgraph,uncached-test-ram");
    if (!node)
        return -ENODEV;
    parent = of_get_parent(node);
    if (!parent || strcmp(of_node_full_name(parent), "/reserved-memory")) {
        of_node_put(parent);
        of_node_put(node);
        return -EINVAL;
    }
    of_node_put(parent);
    second = of_find_compatible_node(of_node_get(node), NULL,
                                    "chi-flowgraph,uncached-test-ram");
    if (second) {
        of_node_put(second);
        of_node_put(node);
        return -EINVAL;
    }
    if (!of_device_is_available(node) ||
        !of_property_read_bool(node, "no-map") ||
        of_property_read_bool(node, "reusable")) {
        of_node_put(node);
        return -EINVAL;
    }
    result = of_address_to_resource(node, 0, &reserved);
    of_node_put(node);
    if (result)
        return result;
    if (PAGE_SIZE != 4096 || resource_size(&reserved) != PAGE_SIZE ||
        !IS_ALIGNED(reserved.start, PAGE_SIZE))
        return -EINVAL;
    result = misc_register(&test_device);
    if (!result)
        pr_info("CHI_UNCACHED_TEST reserved=%pa bytes=%lu no-map=1 normal-PTE PMA-required\n",
                &reserved.start, PAGE_SIZE);
    return result;
}
static void __exit test_exit(void)
{
    misc_deregister(&test_device);
}
module_init(test_init);
module_exit(test_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("CHI FlowGraph DT-reserved Normal-NC RAM CPU test mapping");
