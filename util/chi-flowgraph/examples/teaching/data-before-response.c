// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 分离响应读方
// C 伪代码；RSP 通道延迟由教学调度设置
uint64_t v = chi_read(ReadShared, &x);
assert(v == 9);
