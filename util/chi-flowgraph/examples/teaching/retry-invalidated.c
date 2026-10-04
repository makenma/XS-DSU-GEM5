// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 等待重试的写回
// 初始 RN0=UD，x=9；内存仍为 0
chi_copyback_async(WriteBackFull, &x);
wait_completion_or_cancel();

// P1 · 接管脏行
// C 伪代码；HN 的信用资源由教学调度控制
uint64_t v = chi_read(ReadUnique, &x);
assert(v == 9);
