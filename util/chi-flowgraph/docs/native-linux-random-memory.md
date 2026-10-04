# Linux 随机页迁移与并发写补充验收

原冻结 core 的迁移用例真实调用 `move_pages`，但只反复迁移四页、没有随机选页，迁移时其他线程等待 barrier。本补充项单独完成随机迁移要求；原运行保留，编译和检查器合成数据不计作 guest 通过。

默认参数为 seed 1、128 轮、每 writer 64 个 4KiB 页、每 writer 1MiB 压力区、每次版本更新采样 64 条 cache line、guest timeout 120 秒。四个线程固定在 CPU 0–3：CPU0 按可复现 PRNG 洗牌选页，执行 query → `move_pages(MPOL_MF_MOVE)` → query；CPU1–3 同时写各自页池的版本化 payload。要求384次成功跨节点迁移，全部192个页均实际迁移。

每页只有所属 writer 写入，完整更新后发布 epoch。迁移窗口记录 writer 完成计数，不能靠等待伪造重叠。停止只在完整更新边界处理；全部线程 join 后，主线程固定到CPU0，按每页最终 epoch 逐字节复核。host 从seed独立重算选页及最终页摘要，检查完整清单、次数、节点变化、并发进度和零字节错误。

压力区采用同一 memfd 的双共享映射，从第一映射写、第二映射读回。初始化及最终检查覆盖全部1MiB；迁移期间随机采样并单独报告实际不同cache line数。分配容量、初始化扫描和并发期工作集分开报告。迁移池使用私有匿名页，避免双重映射影响普通 `MPOL_MF_MOVE` 的页面资格。

补充运行使用相同model05、4 KMH CPUs、4 native L2、3×2 CHI NoC、4 HN-F、2 SN-F、1GiB RAM和2 NUMA nodes。先在实际Linux中运行原6项litmus冒烟（`-a 4 -s 8 -r 1`），再执行随机迁移，最后一次正常`m5_exit`。独立检查器重读实际串口，不接受孤立PASS文件。

## 产物与状态

- 输入bundle：`29334d7e5240758eba884c86d70ad0a4c65ec5407cef100e54313b14f1e0eb4a`。
- 固件SHA256：`b261fcf1513f9a4277a319446605d5362bf71d625f42b00d9cbaa6712d7a63d3`。
- 内核配置只改`CONFIG_INITRAMFS_SOURCE`；构建记录：`build/linux-validation/random-memory-build/29334d7e5240/build-result.json`。
- `build/linux-validation/linux-random-memory-01`已启动实际仿真，尚无guest通过结果。构建记录的`guestExecuted=false`仅说明构建范围。

## WSL命令

在`/mnt/d/proj/chi_flowgraph`执行：

```sh
python3 scripts/build-linux-random-memory.py
python3 scripts/build-native-linux-random-memory.py \
  --work /root/chi-flowgraph-work/linux-validation \
  --suite-inputs /root/chi-flowgraph-work/linux-validation/artifacts/linux-suite-inputs/76aa90c405b1a84843542040de750d9bcb9380205267bd87ffb1e0eb20a1baac/index.json \
  --jobs 2
python3 scripts/run-native-linux-random-memory.py \
  --build-result build/linux-validation/random-memory-build/29334d7e5240/build-result.json \
  --gem5 /root/chi-flowgraph-work/linux-validation/artifacts/fs-model-05-nonspec/gem5.opt \
  --gem5-sha256 4b8b39640ef6f2f70fec1824f6f3254bc15a41e45aada746d290de825692ad7d \
  --gem5-source /root/chi-flowgraph-work/gem5 \
  --output build/linux-validation/linux-random-memory-01
```

构建器复用独立复制的对象，默认拒绝已有构建；核对同输入的失败构建后才使用`--resume`。上述仿真目录已经运行，重跑必须明确使用新目录。源文件变化会产生新bundle和构建目录。

```sh
python3 scripts/check-native-linux-random-memory.py \
  --run build/linux-validation/linux-random-memory-01 \
  --output build/qa/linux-random-memory-01.json
```

低层20组、实际Linux绑定层16组、最终组合层19组检查器测试已通过；这些合成测试只检查验收器识别错误的能力。实际Linux绑定层额外固定完整12项输入清单，验证全部SHA后才加载其中的检查代码，并复核实际启动命令、拓扑、日志顺序、正常guest退出和宿主退出码。完整目标仍要求原core、原生内核构建、此补充项及实际宿主退出码全部通过；不据此证明全部CHI/RTL并发交错正确。

最终组合验收重新读取三次实际运行的证据，并要求原core/kernel驱动session 30470的真实成功终态：

```sh
python3 scripts/check-native-linux-complete.py \
  --core build/linux-validation/linux-suite-05 \
  --kernel-build build/linux-validation/linux-kernel-build-06 \
  --random-memory build/linux-validation/linux-random-memory-01 \
  --driver-completion build/linux-validation/model05-driver-completion.json \
  --output build/linux-validation/native-linux-complete-05.json
```

`model05-driver-completion.json`只能在宿主工具返回session 30470的终态后保存，包含原始工具返回值。运行中的session、缺失的终态记录和准备好的产物均不能替代实际完成；原core/kernel报告单独通过也不能完成含随机迁移的最终验收。
