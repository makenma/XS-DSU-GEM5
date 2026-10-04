// Protocol teaching pseudocode; NOT a compiled CPU test.

// P0 · 可取消的部分写
// 协议测试接口示意；Normal memory，不是 Device
auto w = chi_write_async(WriteUniquePtl, &x, 64, mask, data);
chi_cancel_before_data(w); // RN 控制器操作，不是普通 C store
wait_completion(w);
