# 第二轮 CHI 路由算法优化

本轮针对用户要求进一步减少 router 宿主执行开销。计时基线是第一轮已修正
credit、事件可见周期和有限缓冲行为的版本，不用原来错误的拥塞行为作比较。
下列数据对应 Linux mesh 接入前的优化快照 `/tmp/chi-review-round2.opt`。
完整二进制 SHA-256、测试器/配置 SHA-256、每个样本和严格等价检查均在报告中。

## 改动

- 端口绑定时验证并缓存 peer 类型，消除 flit 热路径的重复 `dynamic_cast`。
- 端口 FIFO 按原 credit 容量一次分配环形缓冲；入队仍复制借用的 DAT 数据，
  dequeue 和 slot 重用不会破坏所有权。容量和 credit 归还周期不变。
- Router 初始化时缓存已连接入口，保留 source、接口、RX/TX 的原采样顺序。
- VOQ 非空状态、输出请求和 grant 用位图表示；检查 credit 时引用已有 flit，
  减少完整 DAT 拷贝。
- 单槽 merge 使用 optional；PTX 按 D 保持 FIFO，同时共享原总容量，消除中间
  erase 和失败后的重新插入。每拍 D round-robin 结果不变。
- 使用在途 payload 计数和 credit 尾位图跳过完全空闲的通道。credit 阻塞的
  flit 继续逐拍推进；没有跳过有可能影响仲裁/返回时序的周期。
- SLCSF 另去掉几处重复 vector 分配，并移动已经终结的 lookup 结果；SEQ
  的当前/前拍快照仍分别保存，避免复用缓冲改变边沿语义。

这些位图与计数仅是宿主软件缓存的元数据，不代表新增硬件寄存级。
Router 自有 deque 数：第一轮 FIFO 384 / VOQ 2496，本轮 FIFO 416 / VOQ 2528；
增加来自 PTX 的按 D 分队列，换取每次发送 O(1) 访问，并未扩大可存储的 flit 数。
端口队列另由 deque 改为固定容量环形缓冲。

## 测量

每组两跳、1,572,864 个 flit，同一宿主 CPU 交替运行 baseline/candidate 五对，
分别取中位数；配置和测试器完全相同。hostSeconds 包含测试器执行成本。

| 场景 | 原 hostSeconds | 新 hostSeconds | 仿真耗时下降 | 全进程 wall 耗时下降 |
| --- | ---: | ---: | ---: | ---: |
| throughput，single，FIFO | 1.85 s | 1.35 s | 27.03% | 19.74% |
| throughput，dual，VOQ | 2.09 s | 1.37 s | 34.45% | 24.42% |
| congestion，single，VOQ | 4.56 s | 2.90 s | 36.40% | 32.07% |
| congestion，dual，FIFO | 4.49 s | 3.03 s | 32.52% | 28.19% |

全部 40 次运行的 flit 数、周期、完整事件/数据摘要，以及每项 router/relay
统计（包括 stall counters）严格相等。原始数据：
`m5out/chi-review-round2/router-performance-final/report.json`。
此前将 repetitions 提高到 65536 的试跑中，拥塞基线达到测试器固定 100 万周期
watchdog，未计入上述结果；该失败完整保留在 `router-performance/`，正式测量
采用脚本默认的 16384。大 repetitions 下两组吞吐的有效样本也保留供交叉检查。

HNF 30 万请求负载不经过 router，仅受共享端口和 SLCSF 分配优化影响：相对
第一轮 hostSeconds 6.07→5.62 s（下降 7.41%）；另一次直接对原始正确基线的
五对测量为 6.56→5.70 s（累计下降 13.11%）。不能将这些数字与路由加速相加。
分别见 `hnf-performance/report.json` 和 `hnf-performance-cumulative/report.json`，
均位于 `m5out/chi-review-round2/`。它们不是 Linux 或 CPU 应用的性能结果。

## 时序和内存验证

- 原 router 56 场景逐项比较签名、trace.csv 原始字节，以及单跳 116 / 双跳
  232 项 router/relay 统计，全部相同；28 对 normal/reversed 也相同。
- HNF 62 场景（46 主矩阵 + 16 额外响应时序）与第一轮签名全部相同。
- 单元测试 78/78：SLCSF 57、POCQ 7、端口/数据 5、router 9。
- SLCSF、端口/数据和 router 的 ASan + UBSan + 容器断言 71/71。
  环境不支持 LeakSanitizer，`detect_leaks=0`。

等价报告和原始日志索引位于 `m5out/chi-review-round2/validation/`。
新增环形缓冲对 deque 的随机比较覆盖 0/1/3/4/8 容量、wrap 和 slot 重用；
PTX 对原 shared deque 的比较覆盖 150000 周期和不同 credit/总容量。

```sh
python3 tests/gem5/chi/benchmark_router.py \
  --baseline /tmp/chi-review-round1.opt --candidate /tmp/chi-review-round2.opt \
  --out m5out/router-repeat --runs 5 --repetitions 16384
```

本报告是优化快照的测量记录。后续双核 Linux mesh 的协议扩展、checkpoint 和
运行结果单独记录，不把尚未运行的 Linux 速度外推为此处的路由收益。

## 最终交付二进制复测

完成双核 mesh 端点和 native checkpoint 修复后，对最终
`build/RISCV/gem5.opt` 重新执行相同四场景、每场景五对交替运行。二进制 SHA-256：
`b81e2129fd7d7a7067497a1717a98bd283dead7e7e293b847590f8c0b2624ff4`。
基线仍为 `/tmp/chi-review-round1.opt`，repetitions 为 16384，宿主固定 CPU 0；
同时运行的 Linux 验证固定在 CPU 8。下表保留历史快照测量之外的最终实测结果。

| 场景 | 原 hostSeconds | 最终 hostSeconds | 仿真耗时下降 | 全进程 wall 耗时下降 |
| --- | ---: | ---: | ---: | ---: |
| throughput，single，FIFO | 1.84 s | 1.34 s | 27.17% | 20.26% |
| throughput，dual，VOQ | 2.09 s | 1.37 s | 34.45% | 25.32% |
| congestion，single，VOQ | 4.55 s | 2.84 s | 37.58% | 32.57% |
| congestion，dual，FIFO | 4.49 s | 3.03 s | 32.52% | 28.03% |

40 次完整仿真全部通过严格等价检查：每次均有 1,572,864 个 flit，四场景周期
依次为 108492、86736、426174、393409，事件/完整数据摘要和每次 232 项
router/relay 统计（包括所有 stall counters）完全一致。测试前后对二进制、
配置及测试器做 SHA-256 检查，确认输入未改变。这里仍是 router 端口工作负载的
宿主执行时间，不是 Linux 应用加速比。

完整结果与逐次样本保存在
`m5out/linux-chi-mesh/router-performance-final/report.json`，执行日志在
`m5out/linux-chi-mesh/validation/router-performance-final.log`。历史快照报告及
上文数据保持原样。

最终二进制的独立 HNF 30 万请求复测也与最初正确基线逐事件等价：
2,625,051 cycles、300,000 completions、234,464 snoops，摘要
`102f41b96ed5b19`。五对交替运行的 hostSeconds 中位数为 6.51→5.65 秒，
累计耗时下降 **13.21%**；全进程 wall 为 7.0372→6.1350 秒，下降 12.82%。
报告在 `m5out/linux-chi-mesh/hnf-performance-final/report.json`。该负载不经过
router，收益不能与上表相加，也不代表 Linux 应用的加速。
