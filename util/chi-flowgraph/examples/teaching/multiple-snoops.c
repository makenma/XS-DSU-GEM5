// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 被阻塞的写回
// 初始 RN0=UD，x=9，内存=0
chi_copyback_async(WriteBackFull, &x);
wait_completion();

// P1 · 第一个共享读
// C 伪代码；先由 HN 处理这个读
uint64_t a = chi_read(ReadShared, &x);

// P2 · 随后的独占读
// C 伪代码；此读排在写回之前
uint64_t b = chi_read(ReadUnique, &x);
