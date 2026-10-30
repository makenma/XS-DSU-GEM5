/* Two-hart native execution over the same CHI mesh used for Linux.
 * Fill/evict both RNFs, verify the other hart's data, exercise AMO and LR/SC,
 * checkpoint with warm coherent lines, then verify them again after restore.
 */
.section .text.init
.globl _start
_start:
    csrr s0, mhartid
    la sp, stacks_end
    slli t0, s0, 12
    sub sp, sp, t0
    tail main

.section .text
main:
    la s1, buffer
    slli t0, s0, 16
    add s1, s1, t0
    li s2, 1024
    mv t0, s1
    li t1, 0
1:  slli t2, s0, 32
    or t2, t2, t1
    sd t2, 0(t0)
    not t2, t2
    sd t2, 32(t0)
    addi t0, t0, 64
    addi t1, t1, 1
    blt t1, s2, 1b
    fence rw, rw
    la t0, ready
    li t1, 1
    amoadd.d.aqrl zero, t1, (t0)
2:  ld t1, 0(t0)
    li t2, 2
    bne t1, t2, 2b

    /* Read all lines written by the other owner, including dirty victims. */
    xori s3, s0, 1
    la s1, buffer
    slli t0, s3, 16
    add s1, s1, t0
    li t1, 0
3:  slli t2, s3, 32
    or t2, t2, t1
    ld t3, 0(s1)
    bne t2, t3, fail
    not t2, t2
    ld t3, 32(s1)
    bne t2, t3, fail
    addi s1, s1, 64
    addi t1, t1, 1
    blt t1, s2, 3b

    la t0, counter
    li t1, 100
4:  lr.d.aq t2, (t0)
    addi t2, t2, 1
    sc.d.rl t3, t2, (t0)
    bnez t3, 4b
    addi t1, t1, -1
    bnez t1, 4b
    la t0, finished
    li t1, 1
    amoadd.d.aqrl zero, t1, (t0)
5:  ld t1, 0(t0)
    li t2, 2
    bne t1, t2, 5b
    la t0, counter
    ld t1, 0(t0)
    li t2, 200
    bne t1, t2, fail
    bnez s0, secondary_wait

    li a0, 0
    li a1, 0
    .long 0x8600007b /* m5_checkpoint */
    la t0, phase
    li t1, 1
    sd t1, 0(t0)
    fence rw, rw
    /* Re-read warm lines after the checkpoint; original and restored runs
     * must reach the same completion signature. */
    la t0, buffer
    li t1, 0
6:  ld t2, 0(t0)
    bne t2, t1, fail
    addi t0, t0, 64
    addi t1, t1, 1
    blt t1, s2, 6b
    la t0, secondary_done
7:  ld t1, 0(t0)
    beqz t1, 7b
    la a0, success_message
    jal ra, puts
    li a0, 0
    .long 0x4200007b /* m5_exit */
8:  j 8b

secondary_wait:
    la t0, phase
9:  ld t1, 0(t0)
    beqz t1, 9b
    la t0, counter
    li t1, 1
    amoadd.d.aqrl t2, t1, (t0)
    li t3, 200
    bne t2, t3, fail
    la t0, secondary_done
    sd t1, 0(t0)
10: wfi
    j 10b

fail:
    la a0, failure_message
    jal ra, puts
    li a0, 0
    li a1, 1
    .long 0x4400007b /* m5_fail */
11: j 11b

puts:
    li t4, 0x10000000
12: lbu t5, 0(a0)
    beqz t5, 14f
13: lbu t6, 5(t4)
    andi t6, t6, 0x20
    beqz t6, 13b
    sb t5, 0(t4)
    addi a0, a0, 1
    j 12b
14: ret

.section .rodata
success_message: .asciz "CHI_MESH_SMOKE_PASS\n"
failure_message: .asciz "CHI_MESH_SMOKE_FAIL\n"
.section .bss
.balign 64
ready: .zero 64
finished: .zero 64
counter: .zero 64
phase: .zero 64
secondary_done: .zero 64
.balign 65536
buffer: .zero 131072
.balign 4096
stacks: .zero 8192
stacks_end:
