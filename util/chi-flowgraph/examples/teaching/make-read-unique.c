// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 原共享持有者
// 初始 RN0=SC，x=9；C 为示意
chi_upgrade(MakeReadUnique, &x);
// 等待期间收到 SnpUnique，原副本丢失
assert(READ_ONCE(x) == 9);

// P1 · 较早到达 Home 的竞争者
// 初始 RN1=I
uint64_t v = chi_read(ReadUnique, &x);
// 本例得到 UC 后尚未执行 store，随后被 P0 再失效
