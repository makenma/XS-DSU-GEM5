// Protocol teaching pseudocode; not a compiled CPU test.

// P0 · 读方
// 初始：RN0 为 I，内存 x = 0
// 两核并发；本请求先到 HN0
uint64_t v = chi_read(ReadShared, x);
assert(v == 9);

// P1 · 写回方
// 准备阶段，RN1 持有 UD
WRITE_ONCE(x, 9);
// 写回等待期间仍能接收 snoop
chi_copyback_async(WriteBackFull, x);
wait_completion();
