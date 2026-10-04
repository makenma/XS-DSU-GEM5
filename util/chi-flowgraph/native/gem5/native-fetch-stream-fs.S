/* Bare-FS instruction-boundary regression. No Linux acceptance claim.
 * Hart 0 runs two ordinary S-mode code layouts through Sv39; others park.
 * Page A has a compressed branch at virtual entry + 2. Page B either keeps
 * that layout (control) or starts with one 32-bit non-control instruction.
 */
#ifndef SAME_LAYOUT
#define SAME_LAYOUT 0
#endif
#ifndef TRAIN_CALLS
#define TRAIN_CALLS 512
#endif
#if SAME_LAYOUT
#define CASE_NAME "same-layout"
#define EXPECTED_VALUE 8
#else
#define CASE_NAME "changed-layout"
#define EXPECTED_VALUE 1
#endif
.option norvc
.option norelax
.section .text.start,"ax"
.globl _start
_start:
    csrr a0, mhartid
    bnez a0, park
    csrw mie, zero
    csrw medeleg, zero
    la t0, trap_handler
    csrw mtvec, t0
    li t0, -1
    csrw pmpaddr0, t0
    li t0, 15
    csrw pmpcfg0, t0
    la a0, begin_message
    jal ra, puts
    la t0, root_a
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

supervisor_entry:
    li s0, 0x40000000
    li s1, TRAIN_CALLS
    li a0, 0
train:
    jalr ra, 0(s0)
    addi s1, s1, -1
    bnez s1, train
    li t0, TRAIN_CALLS * 8
    bne a0, t0, supervisor_fail
    /* The common supervisor code remains identically mapped. */
    la t0, root_b
    srli t0, t0, 12
    li t1, 0x8000000000000000
    or t0, t0, t1
    csrw satp, t0
    sfence.vma zero, zero
    fence.i
    li a0, 0
.globl changed_mapping_call
changed_mapping_call:
    jalr ra, 0(s0)
    li t0, EXPECTED_VALUE
    bne a0, t0, supervisor_fail
.globl expected_supervisor_ecall
expected_supervisor_ecall:
    ecall
    j supervisor_fail
supervisor_fail:
    ecall
    j supervisor_fail

trap_handler:
    csrr s2, mcause
    csrr s3, mepc
    li t0, 9
    bne s2, t0, fail
    la t0, expected_supervisor_ecall
    bne s3, t0, fail
    li t0, EXPECTED_VALUE
    bne a0, t0, fail
    la a0, pass_message
    jal ra, puts
    li a0, 0
    .word 0x4200007b
    j park
fail:
    la a0, fail_message
    jal ra, puts
    li a0, 0
    li a1, 1
    .word 0x4400007b
park:
    wfi
    j park

/* Only M-mode emits UART text; neither code-layout page uses MMIO. */
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
    .asciz "FETCH_STREAM_BEGIN case=" CASE_NAME "\n"
pass_message:
    .asciz "FETCH_STREAM_RESULT {\"case\":\"" CASE_NAME "\",\"passed\":true,\"trainingChecked\":true,\"mappedCodeChecked\":true,\"trapPcChecked\":true}\n"
fail_message:
    .asciz "FETCH_STREAM_RESULT {\"case\":\"" CASE_NAME "\",\"passed\":false}\n"

/* Fixed linker positions make each PTE independently inspectable.
 * Both roots identity-map low MMIO and the common 0x80000000 RAM segment.
 * VA 0x40000000 selects a different 2 MiB leaf in each address space.
 */
.section .root_a,"a"
.globl root_a
root_a:
    .quad 0xcf, ((0x80011000 >> 12) << 10) | 1, 0x200000cf
    .zero 4096-24
.section .middle_a,"a"
    .quad ((0x80200000 >> 12) << 10) | 0xcf
    .zero 4096-8
.section .root_b,"a"
.globl root_b
root_b:
    .quad 0xcf, ((0x80013000 >> 12) << 10) | 1, 0x200000cf
    .zero 4096-24
.section .middle_b,"a"
    .quad ((0x80400000 >> 12) << 10) | 0xcf
    .zero 4096-8

.section .code_a,"ax"
.globl code_a
code_a:
    .option push
    .option rvc
    c.nop
.globl trained_branch
trained_branch:
    c.j code_a_target
    .option pop
    .word 0x00150513
    .word 0x00008067
    .balign 16
code_a_target:
    .word 0x00850513
    .word 0x00008067

.section .code_b,"ax"
.globl code_b
code_b:
#if SAME_LAYOUT
    .option push
    .option rvc
    c.nop
    c.j code_b_target
    .option pop
    .word 0x00150513
    .word 0x00008067
#else
    .word 0x00150513 /* addi a0,a0,1: covers the old branch slot at +2 */
    .word 0x00008067 /* ret: correct sequential completion */
#endif
    .balign 16
code_b_target:
#if SAME_LAYOUT
    .word 0x00850513
#else
    .word 0x04050513 /* Wrong old target cannot produce the expected value. */
#endif
    .word 0x00008067
