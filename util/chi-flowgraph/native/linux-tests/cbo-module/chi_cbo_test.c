// SPDX-License-Identifier: GPL-2.0
/* Temporary validation helper for Linux 6.6.52 on the four-hart CHI model.
 * No kernel default or user ISA policy is changed. Only a CAP_SYS_ADMIN
 * session can modify senvcfg; close/unload and CPU-offline restore old values.
 */
#include <linux/capability.h>
#include <linux/cpu.h>
#include <linux/cpuhotplug.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/percpu.h>
#include <linux/smp.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <asm/asm-extable.h>
#include <asm/csr.h>
#include <asm/pgtable.h>
#include <asm/tlbflush.h>
#include "chi_cbo_uapi.h"

#define CHI_SENVCFG 0x10a
#define CHI_CBO_BITS 0x70UL
static DEFINE_MUTEX(session_lock);
static bool session_open, permissions_active;
static unsigned long requested_env;
static int hotplug_state;
static DEFINE_PER_CPU(unsigned long, original_env);
static DEFINE_PER_CPU(bool, original_valid);
struct chi_session { struct page *pages[2]; };

static void set_local_env(void *unused)
{
    unsigned long old = csr_read(CHI_SENVCFG);
    if (!this_cpu_read(original_valid)) {
        this_cpu_write(original_env, old);
        this_cpu_write(original_valid, true);
    }
    csr_write(CHI_SENVCFG, (old & ~CHI_CBO_BITS) | READ_ONCE(requested_env));
}

static void restore_local_env(void *unused)
{
    if (this_cpu_read(original_valid)) {
        csr_write(CHI_SENVCFG, this_cpu_read(original_env));
        this_cpu_write(original_valid, false);
    }
}

static int chi_cpu_online(unsigned int cpu)
{
    if (READ_ONCE(permissions_active))
        set_local_env(NULL);
    return 0;
}

static int chi_cpu_offline(unsigned int cpu)
{
    restore_local_env(NULL);
    return 0;
}

static int chi_open(struct inode *inode, struct file *file)
{
    int error = 0;
    struct chi_session *session;
    if (!capable(CAP_SYS_ADMIN))
        return -EPERM;
    mutex_lock(&session_lock);
    if (session_open)
        error = -EBUSY;
    else {
        session = kzalloc(sizeof(*session), GFP_KERNEL);
        if (!session) { error = -ENOMEM; goto out; }
        session->pages[0] = alloc_page(GFP_KERNEL | __GFP_ZERO);
        session->pages[1] = alloc_page(GFP_KERNEL | __GFP_ZERO);
        if (!session->pages[0] || !session->pages[1]) {
            if (session->pages[0]) __free_page(session->pages[0]);
            if (session->pages[1]) __free_page(session->pages[1]);
            kfree(session); error = -ENOMEM; goto out;
        }
        file->private_data = session;
        session_open = true;
    }
out:
    mutex_unlock(&session_lock);
    return error;
}

static int chi_release(struct inode *inode, struct file *file)
{
    struct chi_session *session = file->private_data;
    mutex_lock(&session_lock);
    cpus_read_lock();
    WRITE_ONCE(permissions_active, false);
    on_each_cpu(restore_local_env, NULL, 1);
    cpus_read_unlock();
    session_open = false;
    __free_page(session->pages[0]);
    __free_page(session->pages[1]);
    kfree(session);
    mutex_unlock(&session_lock);
    return 0;
}

static int chi_mmap(struct file *file, struct vm_area_struct *vma)
{
    struct chi_session *session = file->private_data;
    if (vma->vm_end - vma->vm_start != PAGE_SIZE || vma->vm_pgoff > 1 ||
        !(vma->vm_flags & VM_SHARED) || (vma->vm_flags & VM_EXEC))
        return -EINVAL;
    vm_flags_set(vma, VM_IO | VM_DONTEXPAND | VM_DONTDUMP);
    vm_flags_clear(vma, VM_MAYEXEC);
    // VMA holds a file reference: pages and saved permissions outlive every
    // mapping, including a caller closing its descriptor before munmap().
    return remap_pfn_range(vma, vma->vm_start,
        page_to_pfn(session->pages[vma->vm_pgoff]), PAGE_SIZE, vma->vm_page_prot);
}

static void flush_local_page(void *argument)
{
    local_flush_tlb_page(*(unsigned long *)argument);
}

/* The exception table converts any actual instruction/address fault into an
 * error while the PTE lock is held. pagefault_disable() prevents fault-in and
 * makes a wrong D-bit check observable rather than letting Linux repair it.
 */
#define EXEC_CBO(imm) asm volatile( \
    "1: .insn i 0x0f, 2, x0, %1, " #imm "\n" \
    "2:\n" _ASM_EXTABLE_UACCESS_ERR(1b, 2b, %0) \
    : "+r" (error) : "r" (address) : "memory")

static long run_cbo(unsigned long address, unsigned int operation)
{
    long error = 0;
    unsigned long status;
    pagefault_disable();
    status = csr_read_set(CSR_STATUS, SR_SUM);
    switch (operation) {
    case 0: EXEC_CBO(0); break;
    case 1: EXEC_CBO(1); break;
    case 2: EXEC_CBO(2); break;
    }
    if (!(status & SR_SUM))
        csr_clear(CSR_STATUS, SR_SUM);
    pagefault_enable();
    return error;
}

static int test_pte(struct file *file, struct chi_cbo_pte *test)
{
    struct mm_struct *mm = current->mm;
    struct vm_area_struct *vma;
    unsigned long address = test->address;
    pte_t *pte, before, after;
    spinlock_t *ptl;
    int error = -EFAULT;
    if (!mm || test->operation > 2 || !access_ok((void __user *)address, 1))
        return -EINVAL;
    mmap_read_lock(mm);
    vma = find_vma(mm, address);
    if (!vma || address < vma->vm_start || !(vma->vm_flags & VM_READ) ||
        vma->vm_file != file || !(vma->vm_flags & VM_PFNMAP))
        goto unlock_mm;
    test->writable_vma = !!(vma->vm_flags & VM_WRITE);
    error = follow_pte(mm, address, &pte, &ptl);
    if (error) goto unlock_mm;
    before = ptep_get(pte);
    if (!pte_present(before) || !pte_young(before) || !(pte_val(before) & _PAGE_READ)) {
        error = -EAGAIN; /* Caller must fault in its own readable base page. */
        goto unlock_pte;
    }
    test->cpu = raw_smp_processor_id();
    test->before = pte_val(before);
    set_pte(pte, pte_mkclean(before));
    test->cleared = pte_val(ptep_get(pte));
    /* The test task is pinned; no other thread shares these private mappings.
     * Local SFENCE ensures this CBO cannot reuse a cached D=1 translation.
     * Restore and globally flush before releasing the PTE/mm locks.
     */
    local_flush_tlb_page(address);
    asm volatile("fence rw, rw" ::: "memory");
    test->error = run_cbo(address, test->operation);
    asm volatile("fence rw, rw" ::: "memory");
    after = ptep_get(pte);
    test->after = pte_val(after);
    // Preserve a surprising hardware dirty update even when reporting failure.
    set_pte(pte, pte_dirty(after) ? pte_mkdirty(before) : before);
    test->restored = pte_val(ptep_get(pte));
    error = 0;
unlock_pte:
    pte_unmap_unlock(pte, ptl);
    if (!error)
        on_each_cpu(flush_local_page, &address, 1);
unlock_mm:
    mmap_read_unlock(mm);
    return error;
}

static long chi_ioctl(struct file *file, unsigned int command, unsigned long argument)
{
    void __user *arg = (void __user *)argument;
    long error = 0;
    if (!capable(CAP_SYS_ADMIN)) return -EPERM;
    mutex_lock(&session_lock);
    if (command == CHI_CBO_SET_ENV) {
        __u32 value;
        if (copy_from_user(&value, arg, sizeof(value))) { error = -EFAULT; goto out; }
        if ((value & ~CHI_CBO_BITS) || (value & 0x30) == 0x20) { error = -EINVAL; goto out; }
        cpus_read_lock();
        WRITE_ONCE(requested_env, value);
        WRITE_ONCE(permissions_active, true);
        on_each_cpu(set_local_env, NULL, 1);
        cpus_read_unlock();
    } else if (command == CHI_CBO_GET_ENV) {
        struct chi_cbo_env env = {};
        preempt_disable();
        env.cpu = raw_smp_processor_id();
        env.value = csr_read(CHI_SENVCFG);
        preempt_enable();
        if (copy_to_user(arg, &env, sizeof(env))) error = -EFAULT;
    } else if (command == CHI_CBO_TEST_PTE) {
        struct chi_cbo_pte test;
        if (copy_from_user(&test, arg, sizeof(test))) { error = -EFAULT; goto out; }
        error = test_pte(file, &test);
        if (!error && copy_to_user(arg, &test, sizeof(test))) error = -EFAULT;
    } else error = -ENOTTY;
out:
    mutex_unlock(&session_lock);
    return error;
}

static const struct file_operations operations = {
    .owner = THIS_MODULE, .open = chi_open, .release = chi_release,
    .unlocked_ioctl = chi_ioctl, .llseek = no_llseek, .mmap = chi_mmap,
};
static struct miscdevice device = {
    .minor = MISC_DYNAMIC_MINOR, .name = "chi-cbo", .fops = &operations, .mode = 0600,
};
static int __init chi_init(void)
{
    int error;
    hotplug_state = cpuhp_setup_state(CPUHP_AP_ONLINE_DYN, "chi-cbo:test", chi_cpu_online, chi_cpu_offline);
    if (hotplug_state < 0) return hotplug_state;
    error = misc_register(&device);
    if (error) cpuhp_remove_state(hotplug_state);
    return error;
}
static void __exit chi_exit(void)
{
    misc_deregister(&device);
    cpuhp_remove_state(hotplug_state);
}
module_init(chi_init);
module_exit(chi_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Temporary Zicbom permission and D-bit validation helper");
