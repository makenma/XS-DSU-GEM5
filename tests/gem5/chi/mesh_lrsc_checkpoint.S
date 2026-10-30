/* Preserve a live LR reservation through a native CHI checkpoint.
 * Hart 1 sleeps. The runner checks first_sc=0 for the original and restored
 * run, and first_sc=1 after removing only the legacy-missing ISA field.
 * Both paths must complete a fresh LR/SC and verify the resulting value.
 */
.option norelax
.section .text.init
.globl _start
_start:
    csrr t0, mhartid
    bnez t0, secondary
    la s0, datum
    li s2, 0x1234
    sd s2, 0(s0)
    /* Let the secondary reach WFI and warm the primary instruction/data
     * lines before creating the reservation tested by the checkpoint. */
    li t0, 64
1:  ld t1, 0(s0)
    addi t0, t0, -1
    bnez t0, 1b
    la t0, reservation_sequence
    ld t1, 0(t0)
    ld t1, 32(t0)
    li a0, 0
    li a1, 0
    j reservation_sequence

.balign 64
reservation_sequence:
    lr.d.aq s1, (s0)
    .long 0x8600007b /* m5_checkpoint: snapshot after LR, before SC */
    addi s1, s1, 1
    sc.d.rl s3, s1, (s0)
    li t0, 1
    bgtu s3, t0, fail
    /* A missing legacy reservation must leave the original value intact. */
    addi t0, s2, 1
    sub t0, t0, s3
    ld t1, 0(s0)
    bne t0, t1, fail
    /* Both normal and legacy restore must support a new LR/SC immediately. */
    lr.d.aq t1, (s0)
    addi t1, t1, 1
    sc.d.rl t2, t1, (s0)
    bnez t2, fail
    ld t3, 0(s0)
    bne t1, t3, fail
    bnez s3, legacy_success
    la a0, normal_message
    j report
legacy_success:
    la a0, legacy_message
report:
    jal ra, puts
    li a0, 0
    .long 0x4200007b /* m5_exit */
2:  j 2b

.balign 64
secondary:
3:  wfi
    j 3b

fail:
    la a0, failure_message
    jal ra, puts
    li a0, 0
    li a1, 1
    .long 0x4400007b /* m5_fail */
4:  j 4b

puts:
    li t4, 0x10000000
5:  lbu t5, 0(a0)
    beqz t5, 7f
6:  lbu t6, 5(t4)
    andi t6, t6, 0x20
    beqz t6, 6b
    sb t5, 0(t4)
    addi a0, a0, 1
    j 5b
7:  ret

.section .rodata
normal_message: .asciz "CHI_LRSC_CHECKPOINT first_sc=0 retry_sc=0\n"
legacy_message: .asciz "CHI_LRSC_CHECKPOINT first_sc=1 retry_sc=0\n"
failure_message: .asciz "CHI_LRSC_CHECKPOINT_FAIL\n"
.section .bss
.balign 64
datum: .zero 64
