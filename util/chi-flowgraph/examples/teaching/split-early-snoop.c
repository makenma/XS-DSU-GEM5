// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 分离响应读方
// C 伪代码；通道延迟由教学调度设置
uint64_t v = chi_read(ReadShared, &x);
assert(v == 9);

// P1 · 随后的独占读
// 同址访问；并不由 C 代码控制网络到达次序
uint64_t v = chi_read(ReadUnique, &x);
assert(v == 9);
