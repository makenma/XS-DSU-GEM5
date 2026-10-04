/* Real S-mode fetch-boundary cases; this firmware is not a Linux test.
 * All instruction layouts are checked against the ELF by the builder.
 * The common startup/PTE/trap path follows native-fetch-stream-fs.S.
 */
#ifndef CASE_ID
#error CASE_ID is required
#endif
#ifndef TRAIN_CALLS
#define TRAIN_CALLS 512
#endif
#ifndef PREDICT_WIDTH
#error PREDICT_WIDTH must come from the actual gem5 configuration
#endif
#if PREDICT_WIDTH != 64
#error These reviewed layouts require the actual 64-byte predictWidth
#endif
#if CASE_ID == 0
#define CASE_NAME "control-interior"
#define EXPECTED_VALUE 1
#define PREFIX_VALUE 0
#elif CASE_ID == 1
#define CASE_NAME "cross-stale"
#define EXPECTED_VALUE 1
#define PREFIX_VALUE 3
#elif CASE_ID == 2
#define CASE_NAME "cross-clean"
#define EXPECTED_VALUE 1
#define PREFIX_VALUE 3
#elif CASE_ID == 3
#define CASE_NAME "prefix-interior"
#define EXPECTED_VALUE 1
#define PREFIX_VALUE 3
#elif CASE_ID == 4
#define CASE_NAME "amo-cross-stale"
#define EXPECTED_VALUE 24
#define PREFIX_VALUE 3
#elif CASE_ID == 5
#define CASE_NAME "legal-taken"
#define EXPECTED_VALUE 8
#define PREFIX_VALUE 0
#else
#error Unknown CASE_ID
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
    li a1, 0
train:
    jalr ra, 0(s0)
    addi s1, s1, -1
    bnez s1, train
    li t0, TRAIN_CALLS * 8
    bne a0, t0, supervisor_fail
    li t0, TRAIN_CALLS * PREFIX_VALUE
    bne a1, t0, supervisor_fail
    /* Data is in the common identity map, not either remapped code leaf. */
    la s4, atomic_word
    li s5, 7
    ld t0, 0(s4)
    li t1, 17
    bne t0, t1, supervisor_fail
    la t0, root_b
    srli t0, t0, 12
    li t1, 0x8000000000000000
    or t0, t0, t1
    csrw satp, t0
    sfence.vma zero, zero
    fence.i
    li a0, 0
    li a1, 0
    li a2, -1
.globl changed_mapping_call
changed_mapping_call:
    jalr ra, 0(s0)
    li t0, EXPECTED_VALUE
    bne a0, t0, supervisor_fail
    li t0, PREFIX_VALUE
    bne a1, t0, supervisor_fail
#if CASE_ID == 4
    li t0, 17
    bne a2, t0, supervisor_fail
    li t0, 24
#else
    li t0, -1
    bne a2, t0, supervisor_fail
    li t0, 17
#endif
    ld t1, 0(s4)
    bne t0, t1, supervisor_fail
    ld t0, -8(s4)
    li t1, 0x123456789abcdef
    bne t0, t1, supervisor_fail
    ld t0, 8(s4)
    li t1, 0xfedcba987654321
    bne t0, t1, supervisor_fail
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
    li t0, PREFIX_VALUE
    bne a1, t0, fail
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
.section .test_data,"aw"
    .quad 0x123456789abcdef
.globl atomic_word
atomic_word:
    .quad 17, 0xfedcba987654321

.section .code_a,"ax"
.globl code_a
code_a:
#if PREFIX_VALUE
    addi a1, a1, 3
#endif
#if CASE_ID == 1 || CASE_ID == 2 || CASE_ID == 4
    /* +62 is the last halfword in [entry, entry+64). */
    .option push
    .option rvc
    .rept (PREDICT_WIDTH - 2 - 4) / 2
    c.nop
    .endr
    .option pop
#endif
.globl a_instruction
a_instruction:
#if CASE_ID == 2
    addi a0, a0, 8
    ret
#else
    .option push
    .option rvc
    c.nop
.globl trained_branch
trained_branch:
    c.j code_a_target
    .option pop
    addi a0, a0, 32
    ret
#endif
    .balign 128
.globl code_a_target
code_a_target:
    addi a0, a0, 8
    ret

.section .code_b,"ax"
.globl code_b
code_b:
#if PREFIX_VALUE
    /* This valid prefix must survive recovery exactly once. */
    addi a1, a1, 3
#endif
#if CASE_ID == 1 || CASE_ID == 2 || CASE_ID == 4
    .option push
    .option rvc
    .rept (PREDICT_WIDTH - 2 - 4) / 2
    c.nop
    .endr
    .option pop
#endif
.globl b_instruction
b_instruction:
#if CASE_ID == 0
    /* A real 32-bit control instruction contains A's stale +2 slot. */
    jal zero, code_b_real_target
    addi a0, a0, 32
    ret
#elif CASE_ID == 4
    /* Strict AMO format expands to release/RMW/acquire micro-ops in gem5. */
    amoadd.d.aqrl a2, s5, (s4)
    add a0, a2, s5
    ret
#elif CASE_ID == 5
    .option push
    .option rvc
    c.nop
    c.j code_b_old_target
    .option pop
    addi a0, a0, 32
    ret
#else
    addi a0, a0, 1
    ret
#endif
    .balign 128
.globl code_b_old_target
code_b_old_target:
#if CASE_ID == 5
    addi a0, a0, 8
#else
    /* A stale predicted target can never satisfy mappedCodeChecked. */
    addi a0, a0, 64
#endif
    ret
    .balign 32
.globl code_b_real_target
code_b_real_target:
    addi a0, a0, 1
    ret
