/* Minimal static RISC-V guest helper for requesting a gem5 checkpoint. */

    .section .text
    .globl _start
_start:
    li a0, 0
    li a1, 0
    .word 0x8600007b

    /* exit(0), used only if the simulator chooses to continue. */
    li a0, 0
    li a7, 93
    ecall
