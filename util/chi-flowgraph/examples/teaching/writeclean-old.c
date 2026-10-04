// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 一次性读取
// C 示意；HN 先处理这次读
uint64_t v = chi_read(ReadOnce, &x);
assert(v == 9);

// P1 · 清理快照与后续写
// 初始 RN1=UD，x=7，内存 x=0
chi_copyback_async(WriteCleanFull, &x); // 冻结旧快照 7
WRITE_ONCE(x, 9); // 后续 dirty 世代，SnpOnce 可观察到 9
wait_copyback(); // 只清理快照 7，不抹去新世代的 dirty
