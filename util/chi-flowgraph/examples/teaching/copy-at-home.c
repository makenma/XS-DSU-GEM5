// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 逐出未修改的行
// 准备：先前 CompData_UC 携带 CAH=1，x=9
// 此后没有 store；HN 仍持有相同的隐藏副本
chi_copyback(WriteEvictFull, &x);
// 协议完成：Comp 后仍必须发 CompAck
