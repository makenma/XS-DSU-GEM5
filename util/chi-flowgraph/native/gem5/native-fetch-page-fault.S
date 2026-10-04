/* Real FS fetch-page regression. Hart 0 executes; the other three park.
 * This is not Linux or a multicore workload result. No BTB state is injected. */
#ifndef TEST_CASE
#define TEST_CASE 0
#endif
#if TEST_CASE == 0
#define CASE_NAME "page-tail-return"
#define ENTRY_VA 0x40000fc0
#elif TEST_CASE == 1
#define CASE_NAME "last-halfword-return"
#define ENTRY_VA 0x40000ffe
#elif TEST_CASE == 2
#define CASE_NAME "straddling-instruction-fault"
#define ENTRY_VA 0x40000ffe
#else
#error Unknown TEST_CASE
#endif

.option norvc
.option norelax
.file "native-fetch-page-fault.S"
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
    la t0, root_table
    srli t0, t0, 12
    li t1, 0x8000000000000000
    or t0, t0, t1
    csrw satp, t0
    sfence.vma zero, zero
    fence.i
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
    li a0, 42
    li t0, ENTRY_VA
    jalr ra, t0, 0
.globl supervisor_return
supervisor_return:
    fence rw, rw
.globl expected_supervisor_ecall
expected_supervisor_ecall:
    ecall
    j fail

/* Traps stay in M-mode. Print the actual architectural state before checking. */
.balign 4
trap_handler:
    csrr s0, mcause
    csrr s1, mepc
    csrr s2, mtval
    mv s3, a0
    mv s4, ra
    la a0, cause_message
    jal ra, puts
    mv a0, s0
    jal ra, print_hex
    la a0, epc_message
    jal ra, puts
    mv a0, s1
    jal ra, print_hex
    la a0, tval_message
    jal ra, puts
    mv a0, s2
    jal ra, print_hex
    la a0, value_message
    jal ra, puts
    mv a0, s3
    jal ra, print_hex
    la a0, newline
    jal ra, puts
#if TEST_CASE == 2
    li t0, 12
    bne s0, t0, fail
    li t0, ENTRY_VA
    bne s1, t0, fail
    li t0, 0x40001000
    bne s2, t0, fail
#else
    li t0, 9
    bne s0, t0, fail
    la t0, expected_supervisor_ecall
    bne s1, t0, fail
    bnez s2, fail
    li t0, 42
    bne s3, t0, fail
    la t0, supervisor_return
    bne s4, t0, fail
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

/* UART helpers use no stack, only a0/t0..t5. Saved trap registers survive. */
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
print_hex:
    li t2, 0x10000000
    li t3, 60
4:
    srl t0, a0, t3
    andi t0, t0, 15
    li t4, 10
    bltu t0, t4, 5f
    addi t0, t0, 87
    j 6f
5:
    addi t0, t0, 48
6:
    lbu t1, 5(t2)
    andi t1, t1, 0x20
    beqz t1, 6b
    sb t0, 0(t2)
    addi t3, t3, -4
    bgez t3, 4b
    fence rw, rw
    ret

.section .rodata,"a"
begin_message:
    .asciz "FETCH_STREAM_BEGIN case=" CASE_NAME "\n"
cause_message: .asciz "FETCH_PAGE_TRAP cause=0x"
epc_message: .asciz " epc=0x"
tval_message: .asciz " tval=0x"
value_message: .asciz " a0=0x"
newline: .asciz "\n"
pass_message:
#if TEST_CASE == 2
    .asciz "FETCH_STREAM_RESULT {\"case\":\"" CASE_NAME "\",\"passed\":true,\"causeChecked\":true,\"trapPcChecked\":true,\"tvalChecked\":true,\"expectedTrap\":12}\n"
#else
    .asciz "FETCH_STREAM_RESULT {\"case\":\"" CASE_NAME "\",\"passed\":true,\"causeChecked\":true,\"trapPcChecked\":true,\"tvalChecked\":true,\"returnValueChecked\":true,\"returnAddressChecked\":true,\"expectedTrap\":9,\"returnedValue\":42}\n"
#endif
fail_message:
    .asciz "FETCH_STREAM_RESULT {\"case\":\"" CASE_NAME "\",\"passed\":false}\n"

/* Three-level Sv39 mapping: only one 4KiB test page is executable.
 * S-mode startup/return code retains its separate 1GiB identity mapping. */
.section .root_table,"a"
.balign 4096
root_table:
    .quad 0
    .quad ((0x80011000 >> 12) << 10) | 1
    .quad 0x200000cf
    .zero 4096-24
.section .middle_table,"a"
.balign 4096
middle_table:
    .quad ((0x80012000 >> 12) << 10) | 1
    .zero 4096-8
.section .leaf_table,"a"
.balign 4096
leaf_table:
    .quad ((0x80200000 >> 12) << 10) | 0xcb
    .zero 4096-8
.section .test_page,"ax"
.balign 4096
#if TEST_CASE == 0
    .zero 0xfc0
.globl test_entry_physical
test_entry_physical:
    .word 0x00008067 /* 32b jalr zero,0(ra), immediately returns. */
    .zero 4096-0xfc0-4
#elif TEST_CASE == 1
    .zero 0xffe
.globl test_entry_physical
test_entry_physical:
    .hword 0x8082 /* 16b c.jr ra, entirely inside the mapped page. */
#else
    .zero 0xffe
.globl test_entry_physical
test_entry_physical:
    .hword 0x0013 /* First halfword of 32b addi x0,x0,0; remaining half unmapped. */
#endif
