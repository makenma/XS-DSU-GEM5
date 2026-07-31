# 6×4 CHI HN-F NoC 与 SLC 替换策略实验

## 1. 模型范围

可运行入口为：

```text
configs/example/kmhv2_chi_6x4_hnf.py
```

NoC 主体严格保留架构图第 23.2 页中的 Router 与 HN-F：

- 6×4、共 24 个 `ChiRouterRefModel`；
- 38 条最近邻双向 mesh 链路；
- 16 个 `HomeNodeFull`，均连接对应 Router 的 P1/D0；
- HN-F 坐标为 `(x=0..4,y=0..2)` 加 `(1,3)`；
- 每个 HN-F 配置 2 MiB、16-way SLC 和 2 MiB、16-way SF；
- CPU 固定为 2.3 GHz，Router/HN-F 固定为 1.8 GHz，与图 23.2
  标注一致；
- CCG、HNI、RNI、Debug 与 I/O 节点不进入 NoC 模型。

真实 workload 仍需要边界适配器。每个 CPU 的 post-L2
`Cache2ChiBridge` 沿西边界接入，共享 `Chi2ClassicMemBridge` 从
R(5,0) P0/D0 接到 classic membus/DDR。边界适配器不是架构图中的新增
NoC 节点。

## 2. HN-F 选择 Hash

`Cache2ChiBridge` 在请求进入 mesh 前执行 Arm CMN SCG HN-F target
selection。Router 接收已经确定的 `REQ.TgtID`，随后执行确定性 XY 路由。

16 HN-F、48-bit PA 的 index 为：

```text
index[0] = PA[6] ^ PA[10] ^ PA[14] ^ ...
index[1] = PA[7] ^ PA[11] ^ PA[15] ^ ...
index[2] = PA[8] ^ PA[12] ^ PA[16] ^ ...
index[3] = PA[9] ^ PA[13] ^ PA[17] ^ ...
```

对应 XOR mask：

```text
0x444444444440
0x888888888880
0x111111111100
0x222222222200
```

Hash index 查 16 项 HNF Node-ID table。REQ、写 DAT、HomeNID 与 CompAck
均使用事务已经选择的同一 HN-F。连续 1024 条 64-byte cache line 的测试
会在 16 个 HN-F 上产生严格的 `64 × 16` 分布。

## 3. SLC 替换策略

运行入口支持：

```text
--slc-replacement-policy={pseudo_random,lru,lsu}
--slc-replacement-seed=<UInt64>
```

`lsu` 是用户命令行兼容别名，语义等同于 LRU；报告统一写作
“LRU（lsu alias）”。

- `pseudo_random` 使用固定定义的 SplitMix64 序列；相同 seed 可复现；
- `lru` 选择最小 `lastUse`；
- 两种策略均优先选择 invalid way；
- 随机状态只在成功替换一个有效 SLC line 后推进；
- SF 始终保持 LRU，不随本实验变化；
- checkpoint v2 保存 policy、seed 与 RNG state；LRU 可读取 v1。

## 4. 打拍与时钟逻辑

`SlcSnoopFilter` 是 `HomeNodeFull` 的 child `ClockedObject`。请求和响应
具有两个显式寄存边界：

```text
CC -> reqIngress -> reqReady -> in-flight -> respPending -> respVisible -> CC
        accept       issue       complete      registered visibility
```

一个 child edge 内固定按以下顺序推进：

1. `respPending -> respVisible`；
2. `reqIngress -> reqReady`；
3. 完成旧的 in-flight operation；
4. 发射 ready request；
5. 更新下一周期可见的 registered credit。

因此新发射的操作不能在同一个 wakeup 完成，并维持：

```text
accepted < issue < complete < visible
```

Mutation 还包含 `U0 decode/token -> U1 resource/lock -> U2 array write ->
U3 check/latch`。默认 child-cycle latency 为 lookup 4、fill 4、update 3、
dirty victim 附加 3、SF eviction 附加 2、Replay 2、cold init 16。

## 5. 构建与 smoke

```bash
scons build/RISCV/gem5.opt -j1

build/RISCV/gem5.opt \
  --outdir=/tmp/chi-6x4-smoke \
  configs/example/kmhv2_chi_6x4_hnf.py \
  --num-cpus=4 \
  --generic-rv-cpt=/path/to/chi-litmus-riscv64-xs.bin \
  --raw-cpt --disable-difftest \
  --slc-replacement-policy=pseudo_random \
  --slc-replacement-seed=7 \
  -m 5000000
```

Smoke 应检查：24 个 Router 与 16 个 HNF 统计全部存在、所有 HNF 接收到
lookup、无 panic/fatal，以及 tick limit 正常退出。

## 6. SPEC CPU2006 批跑

前置资产：

- XiangShan RISC-V SPEC CPU2006 `.zstd` checkpoint 根目录；
- 与 checkpoint ISA 匹配的 `gcpt.bin` restorer；
- 当前构建的 `build/RISCV/gem5.opt`。

批跑脚本从 checkpoint 文件名读取 SimPoint 权重，只发现 libquantum 和
omnetpp，并为每个切片分别运行两种策略：

```bash
python3 util/xs_scripts/chi_hnf_spec06.py run \
  --checkpoint-root /path/to/zstd-checkpoint-0-0-0 \
  --restorer /path/to/gcpt.bin \
  --output results/chi-hnf-spec06 \
  --jobs 1
```

当前 6×4 模型单个 gem5 进程的实测常驻内存约为 9 GiB，因此脚本默认
`--jobs 1`。只有确认主机内存足够后才应提高并发度。

脚本按 workload 校验 SimPoint 权重和为 1（容差 `1e-4`），从而拒绝缺失
切片或混入额外切片的输入。命令行写 `lsu` 时会规范化为 `lru`；两者在
模型中是同一替换策略，最终数据统一以 `lru` 标识。

每个 run 目录保存 `stats.txt`、`simout`、`simerr`、准确命令和
`run.json`。顶层 `manifest.json` 固化 binary、config、checkpoint、policy、
seed、weight 与输出路径。失败切片不会被静默跳过。
再次使用同一输出目录时，manifest 必须完全一致，否则需换目录或显式
使用 `--force`，避免混用不同 binary、checkpoint、seed 或参数的旧数据。

## 7. 公开源码代理负载（SE）

如果没有授权的 SPEC CPU2006 checkpoint，可以用公开上游源码验证完整
6×4 路径并形成描述性性能结果。该路径不包含 SPEC 462/471 的授权源码、
reference input、运行规则或计分过程，因此输出必须标为“公开源码代理负载”，
不能称为 SPEC CPU2006 成绩。

当前可复现输入为：

- libquantum 0.2.4 的公开 Shor 程序，参数 `1397 8`。源码 tarball
  SHA-256 为
  `28a444fc472818229602730c28708b46f25446fb22278ce04b7faf5635189936`；
  本地只把 wall-clock RNG seed 固定为 1，保证成对策略运行走相同路径。
- OMNeT++ 3.3.2 官方仓库 commit
  `2199a8f838156406c03144bf244eb710f44dcf9f` 的公开 Token Ring sample，
  使用 Run 3、100 stations。它是网络事件代理，不是 471.omnetpp Ethernet
  模型或 reference input。

构建入口与许可证边界记录在：

```text
../workloads/README.md
../workloads/libquantum-0.2.4/build-rv64-static.sh
../workloads/omnetpp-3.3.2/build-rv64-static.sh
../workloads/omnetpp-3.3.2/BUILD_RV64.md
```

两个产物都是 RV64 Linux static ELF，`readelf -d` 不含 dynamic section。
OMNeT++ 采用 host NED/message tool + target static library 两阶段构建，并将
NED topology 编译进 ELF。

公开代理矩阵运行命令：

```bash
python3 util/xs_scripts/chi_hnf_spec06.py run-se \
  --libquantum-bin=../workloads/libquantum-0.2.4/shor-rv64-static \
  --omnetpp-bin=../workloads/omnetpp-3.3.2/rv64/bin/tokenring-rv64-static \
  --omnetpp-ini=../workloads/omnetpp-3.3.2/rv64/share/tokenring/omnetpp.ini \
  --output=results/chi-hnf-public-proxy-final \
  --jobs=1
```

默认每个 workload/policy 先运行 100k committed instructions 并 reset
statistics，再采集约 5M committed instructions。每个 run 使用独立 cwd，
guest 非零退出码会使 gem5 run 失败。manifest 固化两份 ELF、INI、gem5、
主配置及所有直接导入 Python 配置、runner 自身的 SHA-256；分析前会重新
计算并拒绝任何输入漂移。

公开代理的 `weight=1` 只表示单一固定指令区间，不是 SimPoint 权重。
如果 pseudo-random 或 LRU 任一运行没有产生 SLC victim，IPC、HNF balance
和 Router hotspot 仍可描述，但不能把 IPC 差异归因于替换算法。

本仓库已完成的公开代理结果位于
`results/chi-hnf-public-proxy-final/`。四个 run 全部成功，两个 workload
在两种策略下的 committed instruction 数完全成对一致。libquantum IPC
为 2.287361，OMNeT++ Token Ring IPC 为 2.233796；两者的策略差都是
0.00%。但四个 run 的 clean/dirty SLC victim 均为 0，所以该零差异只说明
当前区间尚未进入替换阶段，不证明两种 replacement algorithm 等价。

Hash 均衡度方面，libquantum 的 16 个 HNF lookup 为 5553–5568、
CV=0.000717；OMNeT++ proxy 为 573–612、CV=0.019899。四个 run 的最热
Router 都是 R(1,0)。完整数值和归因边界见
`results/chi-hnf-public-proxy-final/README.md`。

## 8. 汇总口径

```bash
python3 util/xs_scripts/chi_hnf_spec06.py analyze \
  --output results/chi-hnf-spec06
```

对于等长 SimPoint instruction interval，程序性能按 CPI 加权：

```text
normalized_weight[i] = weight[i] / sum(weight)
program_CPI = sum(normalized_weight[i] * slice_CPI[i])
program_IPC = 1 / program_CPI
```

默认运行 40M 指令，并在前 20M 指令后 reset statistics；分析器要求最终
测量区间至少提交 19M 指令。缺少成功的 `run.json`、非零返回码、过短
区间、少于 16 个 HNF 或少于 24 个 Router 统计都会使分析直接失败。

分析输出：

- `slices.csv`：每个切片的 IPC、HNF、victim、latency 与 Router 指标；
- `workload_summary.csv`：程序级加权指标；
- `policy_comparison.csv`：LRU 相对 pseudo-random 的性能变化；
- `hnf_balance.csv`：16 个 HNF 的加权 lookup 分布；
- `router_hotspots.csv`：24 个 Router 的 flit/stall 与方向明细；
- `summary.json`：PPT 的唯一结构化入口。

## 9. PPT 生成

安装 `python-pptx` 后运行：

```bash
python3 util/xs_scripts/chi_hnf_presentation.py \
  --summary results/chi-hnf-spec06/analysis/summary.json \
  --output results/chi-hnf-spec06/HNF_6x4_SLC替换策略性能分析.pptx
```

生成器要求 `2 workload × 2 policy × 16 HNF × 24 Router` 数据完整，缺项
会直接报错，不生成带占位或合成性能数值的最终报告。12 页内容覆盖：

1. HNF/Router 建模架构；
2. CMN Hash 与性能方法；
3. 打拍位置和推进逻辑；
4. 两种替换算法的性能与机制差异；
5. Router 流量热点。
