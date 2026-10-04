// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 读方
// C 示意；RN1=UC，x=9，内存也是 9
// HN 请求直传，但 RN1 选择合法降级
uint64_t v = chi_read(ReadShared, &x);
assert(v == 9);

// P1 · forwarding 目标
// 准备阶段已有干净行
uint64_t cached_value = READ_ONCE(x);
// 本例控制器选择普通 SnpShared 处理路径
