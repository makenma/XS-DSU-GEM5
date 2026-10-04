// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · clean 行逐出
// 初始 RN0=UC，x=9，内存也是 9
chi_evict_async(&x);
wait_completion();

// P1 · 读取同址
// C 伪代码；Evict 在 REQ 通道上延迟
uint64_t v = chi_read(ReadShared, &x);
assert(v == 9);
