// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 连续两次访问
// C 伪代码；缓存压力使 clean 行静默逐出
uint64_t first = chi_read(ReadShared, &x);
cache_pressure_until_silent_evict(&x);
uint64_t second = chi_read(ReadShared, &x);
assert(first == 9 && second == 9);
