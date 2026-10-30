/* Native checkpoint with live Sv39 translation, PMP permissions and instret.
 * The second hart sleeps; the first enters S mode with a writable identity
 * mapping, then requires PMP to reject a store both before and after restore.
 */
.option norelax
.section .text.init
.globl _start
_start:
    csrr t0, mhartid
    bnez t0, secondary
    la t0, machine_trap
    csrw mtvec, t0
    li s4, 0
    la s0, protected_page
    li s1, 0x1234
    sd s1, 0(s0)
    /* PMP0: 4KiB read-only NAPOT. PMP1: remainder of address space RWX. */
    srli t0, s0, 2
    ori t0, t0, 511
    csrw pmpaddr0, t0
    li t0, -1
    csrw pmpaddr1, t0
    li t0, 0x1f19
    csrw pmpcfg0, t0
    la t0, root_page_table
    li t1, 0xcf
    sd t1, 0(t0)              /* 0..1GiB: UART identity mapping. */
    li t1, 0x200000cf
    sd t1, 16(t0)             /* 2..3GiB: RAM identity mapping. */
    srli t0, t0, 12
    li t1, 8
    slli t1, t1, 60
    or t0, t0, t1
    csrw satp, t0
    sfence.vma
    li t0, 7
    csrw mcounteren, t0
    csrr t0, mstatus
    li t1, 0x1800
    not t1, t1
    and t0, t0, t1
    li t1, 0x800
    or t0, t0, t1
    csrw mstatus, t0
    la t0, supervisor
    csrw mepc, t0
    mret

.section .text
.balign 64
supervisor:
    ld t0, 0(s0)
    bne t0, s1, fail
.option push
.option norvc
    sd zero, 0(s0)            /* PTE allows store; PMP must raise cause 7. */
.option pop
    li t0, 1
    bne s4, t0, fail
    la s2, writable_page
    li s3, 0x5678
    sd s3, 0(s2)
    ld t0, 0(s2)
    bne t0, s3, fail
    csrr s5, instret
    li a0, 0
    li a1, 0
    .long 0x8600007b          /* m5_checkpoint */
    csrr t0, instret
    bltu t0, s5, fail
    ld t0, 0(s2)
    bne t0, s3, fail
.option push
.option norvc
    sd zero, 0(s0)
.option pop
    li t0, 2
    bne s4, t0, fail
    ld t0, 0(s0)
    bne t0, s1, fail
    la a0, success_message
    jal ra, puts
    li a0, 0
    .long 0x4200007b          /* m5_exit */
1:  j 1b

.balign 4
machine_trap:
    csrr t0, mcause
    li t1, 7
    bne t0, t1, fail
    csrr t0, mtval
    bne t0, s0, fail
    addi s4, s4, 1
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret

.balign 64
secondary:
2:  wfi
    j 2b

fail:
    la a0, failure_message
    jal ra, puts
    li a0, 0
    li a1, 1
    .long 0x4400007b          /* m5_fail */
3:  j 3b
puts:
    li t4, 0x10000000
4:  lbu t5, 0(a0)
    beqz t5, 6f
5:  lbu t6, 5(t4)
    andi t6, t6, 0x20
    beqz t6, 5b
    sb t5, 0(t4)
    addi a0, a0, 1
    j 4b
6:  ret

.section .rodata
success_message: .asciz "CHI_MMU_CHECKPOINT_PASS pmp_traps=2 instret_monotonic=1\n"
failure_message: .asciz "CHI_MMU_CHECKPOINT_FAIL\n"
.section .bss
.balign 4096
root_page_table: .zero 4096
protected_page: .zero 4096
writable_page: .zero 4096
