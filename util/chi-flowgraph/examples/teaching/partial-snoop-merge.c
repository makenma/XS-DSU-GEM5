// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 获取完整独占行
// C 示意；内存整行为 0xaa
// RN1 仅有 [8,16) 与 [40,48) 的脏数据
line64 v = chi_read_line(ReadUnique, &x);
assert(v[8] == 0xbb && v[0] == 0xaa);

// P1 · 部分有效的脏 owner
// 测试模型预置 UDP，普通 C 不保证得到该状态
chi_seed_udp(&x, 8, 8, 0xbb);
chi_seed_udp(&x, 40, 8, 0xcc);
