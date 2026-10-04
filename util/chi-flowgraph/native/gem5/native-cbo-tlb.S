/* Focused FS CPU reproducer: hart0 enters S-mode with a cold Sv39 data mapping.
 * Other harts park. This is not a Linux boot or multicore suite result. */
#ifndef CBO_OP
#define CBO_OP 2
#endif
#ifndef FAULT_MAPPING
#define FAULT_MAPPING 0
#endif
#if CBO_OP == 0
#define CASE_NAME "inval"
#elif CBO_OP == 1
#define CASE_NAME "clean"
#else
#define CASE_NAME "flush"
#endif
.option norvc
.option norelax
.section .text.start,"ax"
.globl _start
_start:
    csrr a0, mhartid
    bnez a0, park
    csrw mie, zero
    la t0, trap_handler
    csrw mtvec, t0
    csrw medeleg, zero
    li t0, -1
    csrw pmpaddr0, t0
    li t0, 15
    csrw pmpcfg0, t0
    li t0, 0x70
    csrw 0x30a, t0
    la a0, begin_message
    jal ra, puts
    /* First S-mode data access uses a different 1GiB virtual mapping from
       instruction fetch, avoiding even a shared ITLB/L2-TLB mapping hit. */
    la t0, root_table
    srli t0, t0, 12
    li t1, 0x8000000000000000
    or t0, t0, t1
    csrw satp, t0
    sfence.vma zero, zero
    la t0, supervisor_entry
    csrw mepc, t0
    csrr t0, mstatus
    li t1, -6145
    and t0, t0, t1
    li t1, 0x800
    or t0, t0, t1
    csrw mstatus, t0
    mret

.balign 4
supervisor_entry:
    li a0, 0x40020000
.globl cold_cbo_instruction
cold_cbo_instruction:
    .word (CBO_OP << 20) | (10 << 15) | (2 << 12) | 0x0f
    fence rw, rw
.globl expected_supervisor_ecall
expected_supervisor_ecall:
    ecall
    j fail

.balign 4
trap_handler:
    csrr s0, mcause
    csrr s1, mepc
    csrr s2, mtval
#if FAULT_MAPPING
    li t0, 15
    bne s0, t0, fail
    la t0, cold_cbo_instruction
    bne s1, t0, fail
    li t0, 0x40020000
    bne s2, t0, fail
#else
    li t0, 9
    bne s0, t0, fail
    la t0, expected_supervisor_ecall
    bne s1, t0, fail
#endif
    la a0, pass_message
    jal ra, puts
    li a0, 0
    .word 0x4200007b /* m5_exit(0) */
    j park

fail:
    la a0, fail_message
    jal ra, puts
    li a0, 0
    li a1, 1
    .word 0x4400007b /* m5_fail(0, 1) */
park:
    wfi
    j park

/* Called only in M-mode; no stack and no translated data access. */
puts:
    li t2, 0x10000000
1:
    lbu t0, 0(a0)
    beqz t0, 3f
2:
    lbu t1, 5(t2)
    andi t1, t1, 0x20
    beqz t1, 2b
    sb t0, 0(t2)
    addi a0, a0, 1
    j 1b
3:
    fence rw, rw
    ret

.section .rodata,"a"
begin_message:
    .asciz "CBO_TLB_BEGIN case=" CASE_NAME "\n"
pass_message:
#if FAULT_MAPPING
    .asciz "CBO_TLB_RESULT {\"case\":\"flush-fault\",\"passed\":true,\"expectedTrap\":15,\"tvalChecked\":true,\"pcChecked\":true}\n"
#else
    .asciz "CBO_TLB_RESULT {\"case\":\"" CASE_NAME "\",\"passed\":true,\"expectedTrap\":9,\"pcChecked\":true}\n"
#endif
fail_message:
    .asciz "CBO_TLB_RESULT {\"passed\":false}\n"

.section .page_tables,"a"
.balign 4096
root_table:
    .quad 0xcf /* VA 0..1GiB -> same PA, V/R/W/X/A/D. */
#if FAULT_MAPPING
    .quad 0
#else
    .quad 0x200000cf /* VA 0x40000000 -> PA 0x80000000. */
#endif
    .quad 0x200000cf /* Instruction VA 0x80000000 -> same PA. */
    .zero 4096-24
.section .test_data,"aw"
    .quad 0xaabbccdd01020304
