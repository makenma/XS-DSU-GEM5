// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 同一有序写流
// C 意图示意；由 RN 的 OWO 测试接口建立关联写流
chi_owo_write(&x, 9); // 映射到 HN0
chi_owo_write(&flag, 1); // 映射到 HN1
// 第二次 CompAck 等待第一次 Comp，不等待第二次 Comp
