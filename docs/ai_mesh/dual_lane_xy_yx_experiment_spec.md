# 读 XY、写 YX 的双 lane Mesh 完整实验 Spec

## 1. 任务与职责

在 `H10_EAST2` 的 5×5 Mesh 上，固定读 XY、写 YX，完成单 lane 对照与双 lane 的 LOAD_ONLY、STORE_ONLY、MIXED_1_1 全套 outstanding / Router FIFO 联合实验。回答双 lane 对整个任务完成时间、稳态吞吐、读写时延和资源需求分别改善多少，以及瓶颈是否转移到 HBM、共享本地端口或 NI。

执行 agent 负责运行实验、核验正确性、整理原始数据并生成可复核的汇总。所有新产物写入独立实验目录；不修改 [正式实验报告](../../src/doc/ai_mesh/goal_mesh_outstanding_buffer_experiment.md)。实验交付后，由当前对话的主 agent 负责最终分析和向该报告追加结果。

| 角色 | 负责内容 |
| --- | --- |
| 执行 agent | 构建现有代码、生成实验 profile/map、调度实验、恢复任务、校验、数据表与图表、交付索引 |
| 当前对话的主 agent | 审核实验数据与比较口径、撰写结论、更新正式报告 |

执行范围允许在实验目录生成调度与分析脚本，并复用仓库现有接口；不修改生产代码、测试逻辑、路由或仲裁实现，不新增功能。若遇到必须修改代码才能解决的问题，记录为 `needs_code_fix` 并交回主 agent，继续其他不受影响的实验。不提交 Git commit。本 spec 不包含待测配置的性能结论。

“全套”指本 spec 的 A–F 阶段、三种负载、配对比较及最终验证。拓扑限定为当前报告的东侧十口 `H10_EAST2`；原 H5/H10 拓扑不加入本次矩阵。搜索结论限于已测集合，不要求穷举所有深度地图，也不要求运行仓库全量回归。

## 2. 配置与唯一来源

从 [east_dual_hbm_32B_profile.json](../../configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json) 派生两个冻结 profile。端点位置采用 [Topology](../../util/mesh_ir/mesh_ir/experiment/config.py)，双 lane 结构采用 [Router Spec](dual_lane_router_spec.md)；本次路由取下表规定的 vnet 设置。

| 项目 | S1：单 lane 对照 | S2：双 lane 实验 |
| --- | --- | --- |
| `network.dual_lane` | `false` | `true` |
| `network.yx_vnets` | `[0, 1]` | `[0, 1]` |
| AW / W | YX | YX |
| B / AR / R | XY | XY |
| 每相邻 router、每方向的数据传输槽位 | 1 flit/cycle | 两条独立 lane，各 1 flit/cycle |
| 外部 P 接入与本地出口 | 原有单条连接 | 原有共享连接 |
| HBM 后端 | 每口读写共享 32 B/cycle | 相同 |

两组 profile 除标识和 `dual_lane` 外必须一致。保持 25 核、25 router、2 GHz、东侧十个 HBM endpoint、32 B flit、W/R sideband、每输入每 vnet 4 VC。五种 AXI packet 均为单 flit；AW/W/B/AR/R 五通道共享各 lane 的物理输出。

路由入口为 [RoutingUnit](../../src/mem/ruby/network/garnet/RoutingUnit.cc)，profile 接入为 [实验配置](../../configs/example/ai_mesh/run_mesh_experiment.py)。写响应 B 继续 XY。不将 lane 固定分给读或写；沿用现有入口占用选择、平局选 lane 0、途中不换 lane、共享本地出口合并规则。

HBM 读写合计理想服务预算保持 `32 B/cycle × 2 GHz × 10 = 640 GB/s`。增加 Mesh lane 不增加 HBM 口数、服务速率、外部 NI 带宽、DMA/SRAM/ROB/端点队列容量或 VC 数。两套内部输入 FIFO 的实际存储成本须计入。

负载通过 [WorkloadSpec 与 workload builder](../../util/mesh_ir/mesh_ir/experiment/workload.py) 生成，保持当前报告的均匀地址分配、有限 SRAM 槽和 DMA 语义：正式长度 16 MiB/core、tile 64 KiB；MIXED 合计仍为 16 MiB/core，读写各半、可并行。长验证使用 20 MiB/core。主搜索 `N_read = N_write`，N 为每核每方向的 AXI outstanding 上限。

## 3. 正式运行前的必要验收

执行入口复用 [run_mesh_experiment.py](../../tests/gem5/ai_mesh/run_mesh_experiment.py)，搜索与测量复用 [experiment 包](../../util/mesh_ir/mesh_ir/experiment/)。先阅读 [开发规则](../../AGENTS.md) 和 [实验设计索引](outstanding_router_fifo_experiment_design.md)，核对 `Agents.custome.md` 是否存在；当前工作树中未找到该扩展文件。保留其他任务的未提交修改。

### 3.1 双 lane 容量地图与异构搜索

此项是启动 C/D/E/F 前的必要条件，不能仅用 uniform smoke 代替。

[Router::inputDepthSource / addInPort](../../src/mem/ruby/network/garnet/Router.cc) 使两个对应 lane 输入采用同一深度来源；统计则导出两份真实 bank。[搜索器](../../util/mesh_ir/mesh_ir/experiment/search.py) 的 `equal_capacity_exchanges` 当前按独立 `(router, inport)` 项交换，不能直接视为合法的双 lane 候选生成器。

可复核的反例：router 0 的 Local bank 0/1、East bank 2/3 初始 W 深度均为 8；单独把 bank 0 改为 9、bank 2 改为 7，bank 1/3 仍填 8，表面总容量相同，但对应 lane 深度不一致，不能按该表实际实例化。

执行 agent 必须完成以下配置核验，使用现有接口生成合法候选：

- 根据实际 bank、`ingress_port`、方向与 lane 身份建立对应关系；实验 map 中共同配置项是唯一写入口，物理 bank 深度和容量由它派生。不得按端口编号奇偶推断对应关系。
- C/D 的坐标修改和等容量交换同时作用于对应 lane，按全部实际 flit slots 计账。requested map、resolved map、接收容量和 credit 一致；不提交冲突配置，也不把未生效请求算作测量点。
- 对同容量空间交换、增加容量、按端口细化各运行一个小型真实双 lane MIXED case。随后将导出的 map 交给 `--buffer-map` 重跑，核对容量、路由、数据和 drain。
- 同时验证单 lane map 重跑。配置预检识别对应 lane 深度冲突和不存在的输入身份；不修改 verifier 以接受冲突配置。

现有 stage D 会将独立 bank 作为交换组，本次关闭自动 D 候选。使用真实端口身份将对应 lane 组成完整交换组，调用现有 `equal_capacity_exchanges`，再通过 `run --buffer-map` 执行合法 D 点。该步骤属于实验配置生成；不修改生产 Router 或现有搜索器。自动 C 使用完整 router 区域，仍需预检其候选保持 lane 对应关系。

若缺少合法候选，先检查其他已测合法种子；确实受最小深度或整数容量粒度限制时保存具体证据。搜索器尚未支持双 lane 不是 `no_legal_variant` 的物理结论。

### 3.2 路由、图表与守恒

沿用 [真实 XY/YX 测试](../../util/mesh_ir/tests/integration/test_mesh_experiment_measurement.py) 及 [双 lane 定向验收](mesh_ir_test_commands.md#dual-lane-router)。小型 LOAD、STORE、MIXED 均检查数据、ordering、实际配置、packet/flit、credit 与最终 drain。

[独立 oracle](../../util/mesh_ir/mesh_ir/experiment/workload.py) 预测每条逻辑有向路由的五通道总 flit 数；校验物理 lane 0＋lane 1 的和，同时保留各 lane 身份。lane 分流由运行时占用决定，不强制两边各 50%，也不用实测计数回填 oracle。

[通用绘图入口](../../util/mesh_ir/mesh_ir/experiment/plots.py) 的链路图和 CSV 需要核对 lane 展示：相同逻辑路由的两个物理 lane 不得重叠成一条线或丢失 lane 列。已有 [双 lane 绘图入口](../../.tmp/dual-lane-load-09s4v2wv/README.md) 可作为格式参考；正式分析使用统一入口，保留原始数据。

数据汇总和绘图可以在实验目录复用现有字段完成；若核心功能或校验器存在缺陷，保留最小复现和日志交回主 agent，不承担实现修复。

## 4. 实验矩阵

S1、S2 分别完成下列阶段，采用相同初始搜索预算和选择规则，热点图由各自 pilot 重新识别。

| 阶段 | 必须覆盖的内容 | 每组初始唯一配置预算 |
| --- | --- | ---: |
| A | 三种负载 × N={1,2,4,8,16,32,64,128}；固定基线深度 `[4,8,4,4,8]` | 24 |
| B | 拐点及更高 N 的四档 uniform，随后独立调整活跃通道 | 44 |
| C | 至少两个合法种子的区域搜索，分别覆盖同容量重分配与增加容量 | 12 |
| D | 压力较高的实际输入端口细化，保留相同 N、相同总容量的 uniform 对照 | 6 |
| E | 保留 map 回扫 N，完成两轮 N/深度交替搜索或有证据的无改进终止 | 12 |
| F | 共同静态硬件、两处热点、长验证、近远距离诊断 | 22 |

阶段定义和选择函数索引见 [实验要求 §九至十二](../../src/doc/ai_mesh/mesh_outstanding_buffer_experiment_requirements.md) 与 [search.py](../../util/mesh_ir/mesh_ir/experiment/search.py)；本 spec 的拓扑、路由、长度和执行职责优先，不承接旧规格的开发任务。四档 uniform 的唯一列表是 `INITIAL_VECTORS`，顺序为 AW/W/B/AR/R，不在其他配置文件复制维护。

每组初始预算 120 个唯一配置，其中自动 A/B/C/E/F 为 114，合法 D 补点预留 6，两组合计 240；`--snapshot-roi` 通常为每点实际运行 probe 和正式回放两次，因此最多约 480 次 gem5 进程，另计 smoke 和补点。物理进程次数、候选请求数、去重后配置数分别报告。

预算是搜索范围，不是完成证明。结合 runner 的 `family_coverage.json` 和顶层 `coverage.json` 检查完整矩阵：A 的 24 点必须全部实际执行；B 的四档、C/D 的合法公平对照、E 的交互点和 F 的必测项不能因预算截断被省略。D 后保留的候选继续补 E 的 N 邻域和 F 的最终验证。预先记录补点清单和预算修订后补齐这些项目；其他搜索空间可以保留 `not_run`，不声称穷举或全局最优。

F 的具体覆盖：

- 每组选择同一静态 map 和同一 N，实际运行三种负载，报告是否全部达到各自有效峰值的 95%；没有合格点则报告最佳折中。
- 冻结该硬件，三种负载分别运行热点 target 0 和 target 9。位置由 Topology 派生，约一半流量到热点，第二处作为不重新优化的 holdout。
- 每种负载的推荐、峰值及一个相邻点进行 20 MiB/core 验证；角色重合可去重，但保留角色关联。
- LOAD 的 target 0 近/远单活跃 DMA 诊断使用 `Topology.diagnostic_cores()`，保留其他硬件与该组共同配置。带负载延迟不标为空载 RTT。

当前 runner 的半速 backend 诊断只对旧 `H10` 自动生成，本次 `H10_EAST2` 主矩阵不包含这一项。若结果分析确有需要，另建诊断 profile、另列结果，保持主组 640 GB/s 预算。

N=128 已覆盖当前 64 KiB descriptor 的 128 个 burst。不要仅修改 supported_n 加入 N=256；上沿仍增长时报告尚未确认饱和，不能改 DMA 语义扩大本次搜索。

## 5. 单 lane 与双 lane 的比较方法

必须分开报告以下三类比较：

| 比较 | 控制变量 | 用途 |
| --- | --- | --- |
| 同 N、同逻辑输入深度 | 相同 workload、路由与非研究资源，S2 复制对应输入资源 | 衡量现有双 lane 设计的整体收益，同时列出容量增加 |
| 同 N、相同 Router FIFO 数据总容量 | 调整每 lane 深度，实际容量精确相等 | 比较同数据存储预算下的吞吐；VC 状态和交换硬件成本仍分别列示 |
| 各自搜索后的峰值与推荐 | 各组独立优化，仍采用相同搜索规则 | 比较两种设计可以达到的性能与资源需求 |

固定锚点为三种负载的 N=32、深度 `[4,8,4,4,8]`，两组均需有结果。等数据容量锚点为 S1 的 `[4,8,4,4,8]` 对 S2 的 `[2,4,2,2,4]`，保持 N=32；容量相等必须由实际 map 核验，缺点则补跑。

再对两组各负载最终峰值和推荐的 `(N, 逻辑输入深度图)` 取并集，在另一组补对应点，避免拿不同 N 或不同 map 冒充 lane 单因素增益。跨 lane 数转换 map 时按 router、真实本地端点、逻辑方向匹配，不复用数字 inport ID；记录对应关系和转换后的完整 map。

补点若改变有效峰值，重新计算整个候选集的峰值、95% 集合和推荐，并对新最终点补长验证。16 MiB 与 20 MiB、uniform 与 hotspot、不同 profile 分组分析。

历史 [单 lane STORE 路由对照](../../.tmp/write-yx-5wys1_tg/README.md)、[单 lane MIXED 路由对照](../../.tmp/mixed-xy-yx-gzah3ty3/README.md) 和 [双 lane LOAD](../../.tmp/dual-lane-load-09s4v2wv/README.md) 仅作历史参照。两组正式结论采用本次同构建的新测数据；不将“改路由”和“增加 lane”的收益混算。

## 6. 测量、上界与推荐

测量与正确性以 [verify.py](../../util/mesh_ir/mesh_ir/experiment/verify.py)、[metrics.py](../../util/mesh_ir/mesh_ir/experiment/metrics.py) 为唯一计算入口。保持现有 ROI 选择规则：共同活跃区间、舍弃前段、三个连续子窗口，稳定性阈值 2%，MIXED 实际读占比 50%±2 个百分点。每点 probe 确定 ROI 后正式回放；两种设计按同一规则各自确定窗口，不强行使用相同绝对 tick。

主表首先列全程有效带宽和 makespan，再列 ROI 读、写、合计带宽。带宽使用十进制 GB/s；读按 SRAM commit、写按成功 B 计数。STORE 预填不计入正式 makespan。

同时输出读、写各自 AXI mean/P50/P95/P99、tile 延迟、实际 outstanding 平均/峰值、每核带宽与 Jain、每 HBM 服务率/busy/queue full、NI 和端点背压。延迟使用 ROI 内发起的 cohort，继续运行到完成；不得因读写合计 P99 下降而省略读侧恶化。

Router 统计按 lane / 输入 / vnet 保存占用时间直方图、full fraction、credit 失败、无 VC 和 SA-II 失选。压力同时报告每 ROI 周期值和每完成 MiB 值；R 用读完成量归一化，W 用写完成量归一化。这些失选/检查计数不是阻塞周期百分比。

图表与理论上界必须满足：

- 每条物理有向 lane 利用率分母为 1 flit/cycle；两 lane 合并利用率分母为 2 flits/cycle。共享 P 链路只计一次，仍用单链路容量。
- 同色标绘制 lane 0、lane 1 及合并热点图，保留 AW/W/B/AR/R 贡献和局部端口表。分别说明 W 与 R 的瓶颈位置。
- 从实际工作计划的有效字节 V、逻辑有向路由总 flit 数 F 和链路容量求“仅链路约束的全任务有效吞吐上界”。双 lane 内部连接可用 `V × 2 × 2 GHz / F` 作为理想合并容量必要上界；共享外部连接仍为 `V × 2 GHz / F`。此估计不假设 selector 必然均衡，不保证可达。
- 单独计算 HBM 按实际端口流量份额的服务上界，以及当前实现可证明的 NI/共享 P 约束。综合上界取最小值；不能把旧单 lane NI 周转公式直接乘二。
- “仅链路上界”允许高于 640 GB/s，不能据此宣称整体超过 HBM 预算。完整计划上界与全程带宽比较；ROI 的流量和完成边界不同，不直接用全程上界归一化。

仅正确、稳定、完整 drain 的 `valid` 点进入 ROI 峰值和 95% 推荐。推荐顺序采用现有资源优先规则；两种设计分别计算 B_ref，同时报告跨设计的直接绝对值和提升百分比。全程带宽提升为 `B_S2 / B_S1 - 1`，时间减少为 `1 - T_S2 / T_S1`。

16 MiB 未收敛点保留实测全程结果与状态，不参与稳态推荐，不调整阈值。20 MiB 验证独立列示；若还需延长，用新比较组对单/双 lane 一起运行，保留原长度结果，不把不同长度混成一组排名。

## 7. 可执行入口与冻结流程

以下命令在仓库根目录执行。先通过第 3 节的功能与搜索验收，再启动正式 sweep。依赖安装、日志与临时工具采用当前环境和已有依赖，不另起一套仿真流水线。

构建和针对性检查入口：

```bash
scons build/AXI_MESH/gem5.opt -j8
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
    util/mesh_ir/tests/unit/test_mesh_experiment_config.py \
    util/mesh_ir/tests/unit/test_mesh_experiment_search.py \
    util/mesh_ir/tests/unit/test_mesh_experiment_runner.py \
    util/mesh_ir/tests/unit/test_mesh_experiment_verify.py \
    util/mesh_ir/tests/unit/test_mesh_experiment_workload.py \
    util/mesh_ir/tests/unit/test_dual_lane_checks.py
python3 -m pytest -q util/mesh_ir/tests/integration/test_mesh_experiment_measurement.py
```

双 lane 组件验证入口见 [定向测试](mesh_ir_test_commands.md#dual-lane-router)。已有验证只有在源码和构建身份匹配时可引用。测试通过后创建新目录并从唯一基础 profile 派生输入：

```bash
export MESH_DUAL_EXPERIMENT_ROOT="$(mktemp -d "$PWD/.tmp/mesh-dual-xy-yx.XXXXXX")"
python3 - <<'PY'
import copy
import json
import os
from pathlib import Path

root = Path(os.environ["MESH_DUAL_EXPERIMENT_ROOT"])
base = json.loads(Path("configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json").read_text())
(root / "profiles").mkdir()
(root / "provenance").mkdir()
for label, enabled in (("single", False), ("dual", True)):
    profile = copy.deepcopy(base)
    profile["profile_id"] = base["profile_id"] + "_read_xy_write_yx_" + label
    profile["network"].update(dual_lane=enabled, yx_vnets=[0, 1])
    (root / "profiles" / (label + ".json")).write_text(json.dumps(profile, indent=2) + "\n")
PY
cp build/AXI_MESH/gem5.opt "$MESH_DUAL_EXPERIMENT_ROOT/provenance/gem5.opt"
```

用 [execution.source_identity / file_digest](../../util/mesh_ir/mesh_ir/experiment/execution.py) 保存源码清单、源码归档与二进制 SHA256；保存正式报告的完整文件摘要。整个比较组使用同一二进制和源码。`source_identity` 包含 `src/doc`，执行 agent 全程不修改报告；源码变化时先隔离新组，不能继续混用旧身份。

小型六点 smoke；小工作量可能不满足稳态判据，此处验收 correctness、路由和 drain：

```bash
for lane_mode in single dual; do
    for workload in LOAD_ONLY STORE_ONLY MIXED_1_1; do
        python3 tests/gem5/ai_mesh/run_mesh_experiment.py smoke \
            --topology H10_EAST2 --workload "$workload" \
            --profile "$MESH_DUAL_EXPERIMENT_ROOT/profiles/$lane_mode.json" \
            --gem5 "$MESH_DUAL_EXPERIMENT_ROOT/provenance/gem5.opt" \
            --bytes-per-core 1310720 --tile-bytes 65536 --n 4 \
            --outdir "$MESH_DUAL_EXPERIMENT_ROOT/smoke/$lane_mode/$workload" || exit 1
    done
done
```

自动 A/B/C/E/F 矩阵命令；D 按第 3.1 节单独生成合法交换组执行。默认两组顺序运行，每组最多 8 个 worker。将实际 argv、环境、源码和构建身份写入顶层 `launch.json`，为两组分别保存日志和父进程身份：

```bash
for lane_mode in single dual; do
    python3 -u tests/gem5/ai_mesh/run_mesh_experiment.py sweep \
        --outdir "$MESH_DUAL_EXPERIMENT_ROOT/$lane_mode" \
        --gem5 "$MESH_DUAL_EXPERIMENT_ROOT/provenance/gem5.opt" \
        --profile "$MESH_DUAL_EXPERIMENT_ROOT/profiles/$lane_mode.json" \
        --topologies H10_EAST2 \
        --workloads LOAD_ONLY STORE_ONLY MIXED_1_1 \
        --bytes-per-core 16777216 --validation-bytes-per-core 20971520 \
        --tile-bytes 65536 --windows 1 2 4 8 16 32 64 128 \
        --depths 4 8 4 4 8 --stages A B C E F \
        --max-cases 114 --stage-budgets A=24 B=44 C=12 D=0 E=12 F=22 \
        --snapshot-roi --parallelism 8 --timeout 3600 \
        > "$MESH_DUAL_EXPERIMENT_ROOT/$lane_mode-launch.log" 2>&1 || exit 1
done
```

正式单点补跑使用 `run`，设置单数 `--topology`、`--workload`、`--n` 或 `--n-read/--n-write`、`--depths` 或 `--buffer-map`，其余采用该组冻结值，并加 `--snapshot-roi`。保存完整参数数组，不靠 case 名称猜配置。共享 host 并行度调整必须在正式冻结前完成；两组同时运行时按总 worker 数限流。

恢复前检查已保存 PID 的启动时间、命令、进程组和实际终态，确保没有仍运行的相同任务。用 `launch.json` 中原 sweep argv 把子命令改为 `resume`，其余参数保持原值；普通单点重试使用原 `run` argv 加 `--resume`。仅身份与文件摘要匹配、独立 verifier 成功的结果可复用，失败尝试保留日志。

`verify` 的 `--outdir` 必须指向单个 case；`analyze` 指向各组 sweep 根目录。使用现有 `verified_measurement` / `verify_resume` 批量检查正式点和 ROI probe。CLI 返回 0 也可能是 `unconverged`，必须读取 measurement 状态。

```bash
python3 tests/gem5/ai_mesh/run_mesh_experiment.py analyze \
    --outdir "$MESH_DUAL_EXPERIMENT_ROOT/single"
python3 tests/gem5/ai_mesh/run_mesh_experiment.py analyze \
    --outdir "$MESH_DUAL_EXPERIMENT_ROOT/dual"
```

补点可以放在独立子目录，但最终分析必须通过原始 case 与 execution 身份统一收录；不得只分析 `cases/*` 而遗漏最终补点。推荐导出的 map 必须真实重跑过。

## 8. 数据交付与完成条件

每组保留 runner 生成的 summary、Pareto、best_configs、recommended maps、candidate history、family coverage、hotspot maps、per-core/HBM/link/input 数据及所有原始 case。顶层交付以下文件，供主 agent 后续分析并撰写正式报告：

| 文件 | 内容 |
| --- | --- |
| `README.md` | 目录索引、冻结配置与构建、复现命令、各组完成状态 |
| `handoff.md` | 有效实验数、失败/未收敛/未完成项、关键实测差异、需主 agent 处理的问题；保持简短 |
| `analysis/comparison.json`、`analysis/comparison.csv` | 同 N/深度、等容量、各自优化三类配对；原始 case 路径、状态和差异计算输入 |
| `analysis/summary.md` | 下列六项数据摘要及对应证据链接，不替代正式报告 |
| `coverage.json` | 自动阶段与 D/邻域/长验证补点的统一覆盖清单 |
| `analysis/completion_audit.json` | 进程终态、身份、文件摘要、正确性、容量和图表核验结果，正式报告未修改证明 |
| 分析脚本与图表 | 可从原始文件重新生成表格与图片，保存依赖和命令 |

`analysis/summary.md` 至少整理：

1. 三种负载的 N=32 固定锚点：全程/ROI 带宽、makespan、读写 P99、Jain、实际容量及提升率。
2. 两组 N 曲线、uniform/异构 FIFO 对照、同容量锚点、Pareto、各自峰值/推荐及其跨组配对结果。
3. 三种负载的共同静态硬件、两处 hotspot holdout、长验证和近远诊断。
4. lane 0/1 的链路利用率与 R/W 压力图、HBM 和共享 NI/P 服务状态，以及瓶颈迁移的证据。
5. 分开的仅链路上界、HBM 上界与综合必要上界；每个数字关联计算输入和单位。
6. 全部失败、未收敛、未执行、去重和预算截止记录；推荐严格标明已测集合范围。

所有 worker 与 probe 均退出、原始文件摘要验证成功、必测覆盖闭合、最终推荐/峰值完成长验证并如实标记稳定性后，交付实验根目录的绝对路径和 `handoff.md`。完成审计检查：正确性与全程守恒、逐链路 credit/drain、source/build 一致、两组非研究资源一致、地图可实例化、图表数据一致、正式报告的完整文件摘要不变和链接可访问。实验产物应保留到主 agent 完成报告。

若无法满足必要条件，交付已有真实数据和具体缺口，不将预算耗尽、返回码为 0 或理论预测写成“全部实验完成”。执行 agent 不编辑正式报告，也不提前替主 agent 宣告最终结论。
