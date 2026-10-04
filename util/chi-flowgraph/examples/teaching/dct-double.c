// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 共享读取
// C 示意；准备阶段 RN1 持有 UD，内存 x=0
// HN 本例选择 DCT，RetToSrc=1
uint64_t v = chi_read(ReadShared, &x);
assert(v == 9);

// P1 · 脏 owner
// 已完成的准备操作，不是本片段 CPU 实测
WRITE_ONCE(x, 9);
// 同时向 RN0 发 CompData_SC，向 HN 发带 PD 的数据
