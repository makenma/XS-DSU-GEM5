// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 读请求
// C 示意；Home 资源不足，测试调度让信用先到
uint64_t v = chi_read(ReadShared, &x);
assert(v == 9);
