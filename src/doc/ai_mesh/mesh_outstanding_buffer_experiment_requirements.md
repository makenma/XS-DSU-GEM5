/goal

在当前 XS-DSU-GEM5 仓库、用户已经完成的 Gate 3 基础上，设计、实现、实际运行并分析一套：

“5×5 AI Mesh：
AXI outstanding × Router 五通道 FIFO 深度 × buffer 空间分配”

的联合架构实验。

本任务替代此前仅研究 outstanding 的实验目标。
不要只输出实验计划；交付可复现代码、配置、原始结果、分析脚本和中文报告。
允许在本目标内连续实施，不必每个内部阶段等待确认。

==================================================
一、必须回答的研究问题
==================================================

固定架构：
- 5×5 mesh，25 个 router，XY routing；
- 每个 AI Core/tile 有本地 SRAM 和自己的 DMA 子系统；
- 主实验 25 核同时搬运；
- AXI RDATA/WDATA 均为 32 B/beat，即 256 bit；
- 对比 5 个 HBM 接入口与 10 个 HBM 接入口；
- 使用合成 tensor 搬运计划，不编译真实模型，不执行真实 AI 算术。

必须回答：

1. 两种 HBM 配置下，每核最佳/推荐 outstanding 是多少？

2. Router 内对应 AR、R、W、AW、B 的 buffer 各需要多深？

3. HBM 接入位置、热点位置、普通边缘、普通内部位置，
   是否需要不同深度？

4. 同一个 router 的不同 input port，
   例如 HBM 入口、core 入口、不同 mesh 方向入口，
   是否也应采用不同深度？

5. 相同总 Router buffer 容量下，
   非均匀分配是否优于全网统一深度？

6. 联合推荐配置下：
   - 有效 read/write/aggregate 带宽是多少？
   - AXI burst/s 是多少？
   - tensor tile/s 是多少？
   - P99 延迟与每核公平性如何？

7. 增加 HBM 口数、outstanding、buffer 总量，
   以及仅改变 buffer 分配位置，各自带来什么收益？

“最佳”必须同时考虑性能与有限资源。
不能把最大 outstanding、全部 FIFO 加深自动称为最优。

允许得到：
- 非均匀分配没有收益；
- 某些通道深度不敏感；
- 受到 HBM/NI/ROB/quota/SRAM 限制，继续加深 Router FIFO 无效。

不能为了得到预设结论修改测试或隐藏负结果。

真实数据路径必须是：

每核 workload producer
→ 本核现有 DMA
→ 真实 AXI adapter
→ Garnet NI/router/link
→ memory target/backend
→ 真实 R/B response
→ 本地 SRAM commit / 写事务完成。

禁止用 memcpy、固定 DMA 完成 timer、mock transport、
减少流量后乘倍率，替代正式性能仿真。

不实现 CPU Mesh、真实 UCIe、Agent、MoE、KV、
真实模型编译等与本实验无关的功能。

==================================================
二、先核实 Gate 3 基线
==================================================

读取适用的 AGENTS.md、构建说明、实际 Gate manifest，以及：

- AXI_GARNET_CODEX_SPEC.md
- TORCH_EXPORT_MESH_IR_CODEX_SPEC.md
- DUMMY_AI_CORE_AGENT_CODEX_SPEC.md

记录：
- branch、HEAD、dirty files；
- 实际 build target、测试入口；
- 真实 DMA/AXI/Garnet/SRAM commit 的支持情况；
- 当前 Router buffer 和 credit 参数如何生效；
- 当前统计与 drain 能力。

不同 Spec 的 Gate 编号不能混用。
不能仅凭“Gate 3”猜测有没有真实 DMA/Garnet。

当前用户工作树是本次新基线，
不回退到旧 Spec 的历史 SHA。

明确区分：
- 文档要求；
- 代码已实现；
- 本次实际运行验证。

本目标授权以下最小增量：

A. 合成 tensor benchmark、统计、runner、analysis。

B. 必要时增加有限、可流水的 synthetic memory backend。

C. 在保持协议无关的前提下，增加：
   per-router / per-input-port / per-vnet 的 VC 深度配置。

D. 配套修改：
   容量解析、credit 初始化、统计、校验、测试。

C/D 是对旧 Spec 全网 per-vnet 深度配置的受控扩展，
不是新增 AXI-aware Router。

旧配置未启用新能力时，必须保持原有行为。
ordering、packetization、quota、response progress 等合同不变。

不覆盖用户已有修改，不 reset/clean/pull/rebase，
不 push 或创建 PR，不修改 CHI 协议路径。

==================================================
三、固定硬件、布局和时序假设
==================================================

【Mesh 与 endpoint】

坐标 x/y∈[0,4]，router_id=5*y+x。

保留全部 25 核/DMA。
HBM 作为额外 endpoint/NI/external-link 接到现有 router，
不替换 AI Core，不增加 mesh router 数。

优先使用仓库已经明确的 5/10 口布局。
没有现成布局时采用：

H5：
左边界 [0,5,10,15,20]。

H10：
上述 5 口，加右边界 [4,9,14,19,24]。

输出完整：
endpoint → router → input/output port → link 映射。

default error target 不计入 HBM 口数，
正常性能负载不得访问它。

本实验的 HBM 口是逻辑 memory-facing interface，
不得未经证明称为真实 HBM stack/channel/pseudo-channel。

【AXI 与网络】

固定：
- data_bytes=32；
- full-width INCR；
- max_burst_beats=16；
- 完整 burst useful bytes=512；
- 正确处理 4 KiB 边界、WSTRB、尾部和 completion。

明确各 adapter 每周期接受/交付能力，
不能一次 wakeup 无界处理多个 beat。

32 B AXI 数据宽度不等于 NoC flit 宽度。

优先继承当前已验证的：
clocks、NoC width、VC 数、latency、SRAM 和 backend 参数。

若缺少明确基线，本次默认实验假设为：

- core/AXI/NoC/synthetic memory 统一 2 GHz；
- flit/link=16 B；
- 每条有向链路每周期最多 1 flit；
- vcs_per_vnet=4，并在主搜索中固定；
- 起始每 VC 深度：
  {AW:4,W:8,B:4,AR:4,R:8}。

其他有限资源经 capacity audit 后冻结。

读写 vnet 共享实际物理网络，
不能虚构独立 R/W fabric。

所有 effective 参数必须输出，
不能只记录 CLI 请求值。

【Memory backend】

优先复用已验证 timing backend。

若只有协议验证用 simple memory，
实现最小 SyntheticHbmBackend：

- 每口 read/write base latency 默认 100 ns；
- 每口读写共享总服务率 32 B/backend cycle；
- 请求、服务、响应队列有限；
- 服务可流水，延迟与服务速率分开；
- 响应经真实 AXI/Garnet；
- 保留 commit、ordering 和背压语义。

禁止无界并发完成，也不能意外整笔串行。

没有 bank/row/refresh 模型就明确标 synthetic，
不能称为真实 HBM 实测。

H5/H10 主比较保持每口能力相同。
因此 H10 同时增加端口数与后端总资源，
必须明确这一对照含义。

==================================================
四、明确 Router FIFO 的层级与单位
==================================================

必须区分：

Router input VC：
    flits/VC。

AXI AW/AR/B FIFO：
    transactions/responses。

AXI W/R FIFO：
    beats。

Ruby/local-delivery MessageBuffer：
    messages。

ROB、target context/quota：
    各自声明的 transaction/beat slots。

本次新增主变量是 Router input VC 深度，
不是只修改 AXI endpoint FIFO。

若实际代码还有额外 router-local output queue，
单独列出并在主实验固定。
不凭空创建“五套 AXI output FIFO”。

端点 FIFO、MessageBuffer、ROB/quota 先冻结，
只在诊断中单独变化，
不能与 Router depth 同时暗改。

实验配置使用命名通道，
配置层转换为既有映射：

AW → vnet 0
W  → vnet 1
B  → vnet 2
AR → vnet 3
R  → vnet 4

CSV/vector 的实际顺序是：
[AW,W,B,AR,R]

不能按用户列举顺序错填。

Garnet C++ 只看 vnet/VC/capacity，
不 include AXI header，不 switch AXI channel。

定义：

D[router_id][input_port_id][vnet]
    = 该 input port/vnet 中每个 VC 的 flit 容量。

同一个 input port/vnet 的所有 VC 暂取相同深度。
VC 数固定，不同时搜索 VC 数。

必须支持三种空间粒度：

U：空间均匀
全网所有实际 input port 使用同一五通道深度向量。
各通道之间可以不同。

R：按 router/区域分配
热点与其他 router 可以使用不同向量。

P：关键 router 按 input port 细分
例如 HBM 入口、core 入口和不同 mesh 方向入口。

先完成 U/R，最终候选再做有限 P 细化。
不要一开始穷举全部 router×port×channel。

不存在的端口不配置、不计 buffer。
不能假设每个 router 都恰好有 5 个 input port。

local core 与 HBM 若使用不同 external ports，
必须分别计入实际容量。

==================================================
五、异构深度的 credit 正确性
==================================================

不能只增加 YAML 字段。
异构深度必须真正作用于接收容量和上游 credit。

对每条有向数据链路：

u.out → v.in

必须满足：

上游 u 的对应 OutVcState initial/max credit
=
下游 v.in 的真实对应 VC 容量。

禁止拿 u 自己的 input depth，
初始化 u 发往 v 的 credit。

分别处理：

- NI→router：
  credit 匹配实际 router input 容量。

- router→NI：
  credit 匹配实际接收 NI 的合法容量。
  NI 容量在主搜索中固定。

- router→router：
  credit 匹配实际下游 router/inport/vnet/VC。

internal/external links 都需要明确的 receiver-capacity 解析。

occupancy、full 检查、credit bounds、
ledger、quiescence 使用同一个 resolved capacity。

输出 receiver-capacity map：

{
  sender endpoint/outport,
  receiver endpoint/inport,
  vnet,
  vc,
  depth,
  initial_credit
}

必须验证浅→深和深→浅两种方向。

若启用的 FaultModel/bridge 不支持异构容量，
显式拒绝或使用已声明的关闭配置，
不能静默把深度统一。

新增测试至少覆盖：

- 全网统一新配置与旧 fallback 行为一致；
- 相邻 router 深度不等的双向流量；
- 同一 router 不同 input port 深度不等；
- NI↔router 两端容量不等的合法情况；
- depth=1、packet flits>depth 的 wormhole 前进；
- 背压释放、容量/credit 无越界、最终全部归还；
- 无效 router/port/vnet、0/负深度、
  重复或冲突 override 被拒绝。

不能因为 R/W packet 大于 VC depth，
就错误要求“整包缓存后才能走”。

修改深度不自动修改：
link width、link throughput、router pipeline latency。

报告必须说明：
这是固定频率/时序模型下的容量探索，
不是 RTL PPA 或时序收敛结论。

==================================================
六、Outstanding 定义与固定资源审计
==================================================

N 是：

每核、每方向、跨全部目标合计的
AXI burst outstanding 上限。

Read：
本地 AR acceptance → 最后 RLAST local handshake。

Write：
本地 AW acceptance → 匹配 B local handshake。

N 不是：
descriptor 数、engine 数、beat 数，
也不是“每个 HBM target 分别有 N”。

主实验各核使用相同 N。
25 核读方向配置窗口合计为 25*N。

LOAD/STORE 分别扫对应方向。
MIXED 主扫 N_read=N_write=N。

必须审计：

- DMA 实际并发；
- descriptor/segment queue；
- axi_id 分配与复用；
- max_outstanding_per_id；
- R/B ROB；
- target per-source context/beat quota；
- backend 与 SRAM 服务能力。

特别防止：
只提交一个固定 ID=0 的 descriptor，
修改 N 后实际仍只有一笔 burst 在途。

遵守既有 descriptor/ID 语义，
不能为 benchmark 偷换生产 DMA 的含义。

若实现确实串行化所有 descriptor，
做有测试覆盖的最小修复/扩展，
或明确报告该实现限制。

正式扫描前冻结非研究资源 profile：

SRAM、DMA queues、AXI FIFOs、ROB、NI、
Ruby/local-delivery queues、target quota、backend queues。

若旧配置明显只适合功能 smoke，
允许一次性建立有限 performance profile，
列出变化与容量依据，然后冻结。

扫描 N 或 Router D 时，
不得自动扩大上述其他资源。

不支持的 N 标 unsupported。
需要扩容时使用新 profile_id，
不能拼接到原曲线。

==================================================
七、Router buffer 成本与预算
==================================================

按真实硬件配置计算：

C_router_bytes
=
flit_bytes
*
sum_over_actual_router_inputs_and_vnets(
    vcs_per_vnet[v] * D[router][inport][v]
)

每个实际 buffer 只计一次。

注意：

- credit 是下游空槽镜像，
  不能再算一份数据 buffer。

- 不用 C++ sizeof、flit 对象或 host RSS
  代表硬件 buffer 面积。

- 单列 flit storage bytes、slots、
  metadata 假设。

- NI/AXI/ROB/target/SRAM 容量单独报告。
  不能在这些地方偷偷补回裁剪的 Router 容量。

- 输出每 router、每通道与全网容量。

按各拓扑默认统一配置计算：

C0(H5)
C0(H10)

同一拓扑先探索预算上限：

0.5*C0
1.0*C0
2.0*C0

不足最小合法容量的预算标 infeasible。
必要时补 4*C0，并声明搜索范围扩展。

H10 可能多出 HBM 对应 input buffers，
不能假定 C0(H5)=C0(H10)。

同预算比较必须同时报告：
预算上限、实际使用容量、未使用容量。

==================================================
八、合成 tensor 与热点负载
==================================================

使用：
- 手工合法 Mesh IR；或
- 有限 per-core descriptor producer。

必须调用本核现有 DMA，
不由中央 generator 绕过 DMA 发包。

正式运行 FULL_TIMING：
每个 burst/beat/packet/flit/SRAM service 都存在。

小型 correctness 使用 FUNCTIONAL_BYTES。
大型实验可用已验证 DIGEST_ONLY，
但不能减少时序或网络动作。

默认：
logical tile=64 KiB；
每核总 useful bytes 初值=1 MiB。

pilot 检查是否足够稳态。
不足时统一增加整个正式比较组的数据量后冻结。

同一 workload 的 H5/H10、所有 N/D：
保持每核 useful bytes、tile 数和逻辑计划一致。

SRAM 使用有限 ring/ping-pong。
等待前次 commit/pin 释放后复用，
不能无限分配或覆盖 live buffer。

不能每 tile 插全核 barrier/global drain。
不能用逐代 drain 的 REPEAT 冒充跨代流水。

三个主负载：

A. LOAD_ONLY
HBM → 本地 SRAM。

B. STORE_ONLY
预先合法初始化的 SRAM → HBM。
setup 与正式测量分开。

C. MIXED_1_1
useful bytes 读写各 50%，
合计仍为该组每核总数据量。

读写使用独立地址和 buffer，允许重叠，
不是串行 LOAD→STORE memcpy。

混合 producer 可使用有限数量的成对读写 work items：
一对内读写独立发起，多对可流水，
只限制任一方向不能无限领先。

不能加入全核 barrier，
不能把读完成作为配对写的发起条件。
该流水容量预先冻结并做并发审计。

主地址分布使用确定性均匀 striping，
输出 core→HBM bytes/bursts 矩阵。

每个 burst 完整落在一个 target。
可通过离线 segments 映射实现，
不强制新增复杂硬件地址交织。

segment/descriptor 大小和并发
不能意外限制所研究的 outstanding。

【热点定义】

热点不是单纯几何中心：

- HBM 接入边缘可能是热点；
- 非 HBM 边缘可能承担大量过境流量；
- AW/AR/W 与返回 R/B 的热点方向可能不同；
- input FIFO 满可能是下游瓶颈的结果，
  不能直接推断本 FIFO 应该加深。

用默认配置与代表性较高 N 的 pilot，
分别按通道统计：

路由 offered hop-flits、
link busy、
occupancy/full 时间、
credit stall、
VC allocation stall、
SA lost、
downstream service stall。

冻结各 topology/workload 的 hotspot map，
记录识别规则与 map_digest。

区域策略可以使用有优先级的互斥组：

HBM_ATTACH
HOT_NON_HBM
OTHER_EDGE
OTHER_INTERIOR

同时保留 edge/corner/hot 的原始可重叠标签。

不能硬编码“中心 3×3 是热点”。

【热点验证】

每种拓扑对最终代表候选增加 HOTSPOT_TARGET：

约 50% useful bytes 到一个冻结的 HBM 口，
其余均匀分到其他口。

输出精确整数分配与 digest。

再使用另一个 HBM 热点位置作为 holdout；
H10 尽量测试另一侧。

不重新训练就测试原 buffer map 的泛化。
重新优化的结果另列，
不能冒充同一套静态硬件。

热点诊断与均匀主负载分开，
不能混到同一个 B_max 中。

==================================================
九、分阶段联合搜索
==================================================

禁止直接穷举：
25×端口×5通道×全部深度×全部N。

实现可复现、支持续跑的分阶段搜索，
保存每个候选及选择/淘汰理由。

初始 N：

{1,2,4,8,16,32,64,128}

若上沿仍增长且冻结资源允许，追加 256。
否则报告尚未找到饱和。

初始 D：

{1,2,4,8,16,32} flits/VC

必要时扩展 64。
拐点附近可补少量整数点。

【阶段 A：基线 outstanding】

固定基线统一深度，完整扫 N。

得到：
H5/H10 × LOAD/STORE/MIXED 六组基线。

先完成 LOAD，再扩展 STORE/MIXED。

【阶段 B：全网统一五通道向量】

起始候选至少包括：

{AW:1,W:1,B:1,AR:1,R:1}
{AW:2,W:4,B:2,AR:2,R:4}
{AW:4,W:8,B:4,AR:4,R:8}
{AW:8,W:16,B:8,AR:8,R:16}

围绕较好向量逐通道增减。
不能一直强制：
AW=AR=B，或 W=R。

先在初步拐点附近及一个更高 N 筛选，
必要时扩展。

LOAD 主要筛 AR/R。
STORE 筛 AW/W/B。
MIXED 检查五类竞争。

不活跃通道不能靠纯 LOAD/STORE
推出最佳深度，标记：

not_identifiable_in_this_workload

【阶段 C：区域非均匀分配】

从多个较好统一配置出发，
在冻结热点区域上做有预算的坐标搜索/贪心交换。

分别探索通道和区域。

允许：
- 增深热点；
- 减少热点；
- 增深非热点；
- 从低收益位置转移容量。

方向由实测决定。

至少比较：

1. 增加总 buffer 量的 heterogeneous 配置。

2. 同一容量预算内重新分配的 heterogeneous 配置。

3. 同预算下已经调过五通道向量的最佳 uniform。

不能只和一个明显不佳的默认 uniform 比较。

区域大小、端口数不同，
一组 depth+1 与另一组 depth-1 未必等容量。
必须按实际 flit slots 精确计账。

【阶段 D：关键 input port 细化】

选择实际压力最大且有改善可能的少数 router，
按入口方向和通道细化深度。

至少保留一个：

相同 N、
严格相同总 Router 容量、
其他资源不变

的 uniform→heterogeneous 对照，
用于隔离位置分配的收益。

若配置粒度不允许 exact match，
报告容量差和未使用预算，
只能称同预算上限，
不能声称严格等容量。

【阶段 E：回扫 outstanding】

对保留的非支配 buffer maps
重新扫描 N 的相关区间。

buffer 改变后不能沿用旧 N95。

再用新 N 检查 buffer 增减收益，
至少做两轮交替搜索，
或达到明确无改进/预算截止。

加入小型交叉点：

(N低/N高) × (D浅/D深)

验证 outstanding 与 depth 的交互。

不能用两条互不相交的一维曲线
宣布联合最优。

至少保留两个不同起点，
降低单次 greedy 的局部最优风险。

报告 tested-set/Pareto/local-search 结论，
不声称数学全局最优。

【阶段 F：共同硬件与泛化】

每种 HBM 拓扑选一个静态 buffer map 和共同 N，
在 LOAD/STORE/MIXED 三种负载都实际运行。

再测热点位置 holdout。

必要时测试 burst_beats=1/4 的少量代表点，
检查控制通道结论的适用范围。

主实验仍为 16-beat burst，
不同 burst length 不共用性能归一化基准。

MIXED 主搜索限制 N_read=N_write。
少量非对称组合可作为补充，
但不能声称对称最优是全部二维组合的全局最优。

==================================================
十、最佳配置与 Pareto 推荐规则
==================================================

延续 95% 近峰值准则。

特别禁止：
每张 buffer map 用自己的低峰值归一化，
然后都声称“达到95%，所以同样好”。

对固定：

topology、
backend、
非研究资源 profile、
workload

定义：

B_ref
=
所有正确、稳定、完整 drain 的
已测联合候选 (N,D) 的最大有效带宽。

新候选更新 B_ref 后，
对整个候选集重新计算推荐。

LOAD 用读带宽。
STORE 用写带宽。
MIXED 用读+写，并报告实际比例及各方向性能。

对固定 D，另报：

conditional_N95(D)
=
达到该 D 自身已测峰值95%的最小已测N。

它只说明该 map 的窗口需求，
不等于联合推荐。

联合近峰值集合：

F95 = {(N,D): B(N,D) >= 0.95*B_ref}

保存非支配候选，至少包含：

bandwidth、
C_router、
N_read/N_write、
P99。

不能把 N 和 bytes 直接相加，
伪造一个没有依据的面积/成本函数。

每组给出：

1. 峰值方案
带宽最高，列 N、完整 D、容量、P99。

2. 默认资源节约推荐
在 F95 中：
先最小 C_router，
再最小活跃方向 outstanding 总数；
并列看 P99，再看配置复杂度。

3. Outstanding 节约方案
在 F95 中：
先最小 outstanding，
再最小 C_router。
与上一项相同就明确说明。

分别报告 0.5/1/2 倍预算的：
最佳已测性能、实际容量、配置，
以及是否达到统一 B_ref 的95%。

不能只与小预算自身峰值比较，
就声称与高容量方案同样好。

没有 P99 SLA 时不捏造硬门槛，
但必须展示延迟代价。

【共同静态配置】

定义：

Q(N,D)
=
min_over_LOAD_STORE_MIXED[
    B_workload(N,D) / B_ref(workload)
]

对 Q>=0.95 的候选实际验证，
按资源节约规则选择。

不存在已测共同候选时：
报告场景专用方案和最佳折中。

不能拼接三张不同深度地图，
当成一套可运行硬件。

Router buffer 不运行时动态 resize。
改变 map 必须重新 instantiate/run。

所有“最佳/最小”仅限于：
已声明配置、预算、负载与已测试集合。

上沿还在明显提高带宽，或未收敛时，
不得宣称饱和/最佳已经确认。

==================================================
十一、测量与统计口径
==================================================

分开：

setup、
warm-up、
统一 ROI、
停止发起、
drain。

同时报告：

1. 固定完整数据量的 makespan、
   端到端平均有效带宽。

2. 全部25核仍有工作时，
   共同ROI的稳态指标。

不能把快核已经结束的尾段当全核稳态。
不能相加各核不同测量窗口的带宽。

MIXED ROI 必须有两方向持续工作，
实测 useful read 占比须在预先声明的：

50%±2个百分点

范围内。

不满足则延长窗口或统一增加负载。
仍不满足标 ratio_unconverged，
不能用偏单向的高合计带宽
参加1:1最优选择。

完整等量任务的 makespan 仍可单独报告。

pilot 确定并冻结 ROI 规则与足够数据量。

至少三个连续子窗口检查稳定性，
默认速率相对均值偏差≤2%。

推荐点、峰值点及相邻点
用更长窗口/工作量复验。

确定性重复不是独立随机样本，
不伪造置信区间。

【带宽】

Read：
ROI 内真正 commit 到本地 SRAM 的 useful bytes
/ ROI seconds。

Write：
ROI 内 source 收到成功 B 的 burst
对应 useful bytes / ROI seconds。

另报 target write commit bytes。

Mixed：
read+write，并单列两方向。
不能称为单向拷贝带宽。

【吞吐】

AXI burst/s：
完成的 read/write burst 数 / ROI seconds，
分别以 RLAST/B local handshake 为边界。

Tensor tile/s：
完成的逻辑 tile 数 / ROI seconds，
一个 tile 的全部子 descriptor 必须完成。

固定 tile 大小时，tile/s 与 GB/s 可换算，
不能当两个独立性能收益。

GB/s=10^9 B/s。
ticks、network cycles、seconds、
host wall time 必须分开。

【必须统计】

- 全局/per-core/per-HBM 的 bytes、GB/s、burst/s、tile/s；
- 时间加权平均与峰值 actual outstanding；
- 区分已接受、quota等待、已注入、
  已回数但待SRAM commit的工作；
- AXI transaction、DMA tile 延迟 mean/P50/P95/P99；
- 每条有向链路利用率；
- per-vnet flit/packet；
- 每 router/inport/vnet 的 depth、
  平均/P95/P99 occupancy、
  full fraction、高水位、
  enqueue/dequeue rate；
- credit stall、无可用VC、SA lost 分开；
- endpoint/ROB/quota/SRAM/backend stall 分开；
- per-core 最小/平均/最大吞吐、Jain fairness；
- 每通道、每位置、全网 buffer 成本。

occupancy 分位数使用时间直方图：
统计在各占用状态停留多久，
不能只在 enqueue 时采样。

occupancy 积分在状态变化和 ROI/stats 边界 flush。
VC-cycle 可以超过模拟 cycle，分母必须正确。

保存最终容量 map 和 baseline hotspot map，
报告瓶颈是否迁移。

延迟使用 ROI 内发起的事务 cohort，
继续运行至其完成，补齐长尾。

ROI 内允许 issued!=completed，
记录边界在途量。
整个 run drain 后才做精确守恒。

独立 oracle 从冻结 workload、地址、burst、
packetization、路由计划计算 expected。
不得用 DUT counters 回填 expected。

packet/flit 注入与弹出守恒。
每条有向链路流量单独核对，
不能要求不同跳数链路的计数彼此相等。

==================================================
十二、理论上限与因果诊断
==================================================

从 effective config 计算：

AXI接口、
backend、
memory NI、
NoC有向链路/横截面、
core/SRAM

的上限。

R/W 每 beat 一个 packet 时，
计入每 beat header 和 flit rounding。
burst 变长不自动摊薄每 beat header。

AW/AR/B 和共享链路竞争也要计入。

不能把：
HBM口数×32B×频率
直接当实测结果。

用低负载完整事务延迟
给出 per-core 在途窗口估计。

必要时测 credit round-trip 和浅 buffer 空泡。
这些用于解释和确定搜索范围，
不能代替仿真。

对最终代表配置至少做：

A. 单活跃 DMA 的近/远路径诊断。
保留其他24套硬件及静态quota。

B. 相同 N、相同总 Router 容量的空间分配对照。

C. 固定 D 改 N、固定 N 改 D 的邻域对照。

D. H10 每口 backend 服务率减半的少量点，
使总 backend 服务率与 H5 一致。

D 只隔离后端总服务率，
不意味着 NI/队列/Router buffer 等其他资源全相同。

若 quota、ROB、SRAM 或 memory port 已饱和，
如实记录，不为证明 FIFO 有用而偷偷扩容。

必要的单因素扩容诊断使用新 profile_id。

必须解释：

- 增深 FIFO 后，是改善 credit 空泡/突发吸收，
  还是只延长排队？

- 热点更深是否真的提高吞吐？

- 哪些边缘端口可以裁剪，哪些不能？

- 若 FIFO 深度不影响带宽，为什么？

==================================================
十三、验证、drain 与回归
==================================================

顺序：

小型逐 byte LOAD/STORE/MIXED correctness
→ 25核 H5/H10 smoke
→ 正式搜索
→ 推荐配置复验
→ 相关回归。

正常结束必须：

- 全部计划 useful bytes 完成；
- 无 error/drop/duplicate；
- LOAD SRAM commit 完整；
- STORE B 全部成功；
- DMA/AXI/ROB/context/orphan/service/
  response/MessageBuffer/SRAM 队列排空；
- NI/router/link/credit 和业务事件 drain；
- 每条有向链路×VC 的 credit
  恢复自己的 resolved initial value；
- 连续两个 network edges 验证 quiescent。

不要求 gem5 全局 EventQueue 为空，
非业务 stats/watchdog event 不作为未排空依据。

max tick、host timeout、no-progress 是失败，
不是性能结果。

失败、未运行、未收敛点保留状态与日志，
不能填0混入优化。

执行：

- 本次触及模块单测；
- 当前已完成 Gate≤3 的适用回归；
- 通用 Garnet 修改要求的 legacy/CHI build 回归；
- 新增异构容量与credit测试。

使用实际 test manifest 和独立 GTest binary。
不能猜测 unittests.opt 是可执行文件。

不得删除测试、放宽守恒、关闭断言、
扩大 watchdog 掩盖停滞，
或通过无限 queue 获得通过。

==================================================
十四、交付物与报告格式
==================================================

按实际仓库结构落地，至少包含：

docs/ai_mesh/outstanding_router_fifo_experiment_design.md
docs/ai_mesh/outstanding_router_fifo_experiment_report.md

以及：

- H5/H10 topology；
- 固定非研究资源 profile；
- uniform/region/per-port buffer profiles；
- workload generator；
- 分阶段 sweep/search runner；
- verifier、analysis/plot scripts；
- 每case实际命令、退出码、raw stats；
- source/build/config/workload/map digest；
- resolved capacity/credit map；
- 守恒/drain 验证；
- sweep_summary.csv/json；
- pareto_frontier.csv；
- best_configs.json；
- per_core/per_port/per_link/per_router_inport_vnet 数据；
- candidate history；
- 失败/未运行/预算截断清单。

至少生成：

1. 不同buffer profile的 bandwidth-vs-outstanding，
   H5/H10 分开并对比。

2. P99-vs-outstanding。

3. bandwidth-vs-total-router-buffer，
   uniform/heterogeneous Pareto 对比。

4. 推荐配置每个通道独立的5×5深度图。

5. 链路利用率、热点/普通边缘占用和stall对照。

per-port 不一致时不能用均值图掩盖差异，
必须附方向图或端口表。

深度单位 flits/VC。
容量单位 B/KiB。
N 标明每核/每方向。

报告开头直接回答研究问题。

表一：联合推荐与性能

HBM口数 | workload | 推荐类型 |
N_read/N_write | buffer_map_id |
Router总KiB | 读/写/合计GB/s |
burst/s | tile/s | P99 |
相对B_ref | 稳态/饱和是否确认 | 主要瓶颈

表二：深度分配

HBM口数 | map_id |
区域/router/input-port |
AR | R | W | AW | B |
单位flits/VC | VC数 |
实际buffer bytes | 选择依据

所有差异必须输出逐router/inport机器可读map，
不能只写“热点16，边缘4”。

表三：同预算和同N对照

HBM口数 | workload | N |
uniform/heterogeneous |
预算上限 | 实际buffer bytes |
带宽 | P99 | 公平性 |
带宽提升/容量节省

另外报告：

- 5→10口在相同N/策略下的提升；
- 5→10口在各自联合优化后的提升；
- 两类对照严格分开；
- 推荐相对峰值损失多少带宽；
- 节省多少buffer与outstanding；
- 是否有覆盖三种负载的共同静态配置；
- 原map在不同热点位置holdout下的表现；
- 对真实HBM/NPU、PPA、其他负载的外推限制。

交付实际可复制执行的：

build、
smoke、
sweep、
resume、
verify、
analyze、
推荐配置单点重跑

命令。

推荐map必须能直接instantiate，
不是只存在于报告里的表。

==================================================
十五、执行预算与验收原则
==================================================

先探针和pilot，再冻结profile/ROI/workload，
随后按阶段执行。

runner提供：
max_cases、受控host并行度、
每case timeout、断点续跑。

根据pilot确定并记录明确搜索预算。

优先保障：
六组主基线、
uniform/heterogeneous公平对照、
最终候选复验。

不要盲跑巨型矩阵。

仅在 source/build/effective-config/workload
全部digest匹配且verifier成功时复用结果。
旧结果注明provenance。

预算截止时交付当前真实Pareto和未搜索空间。
不能把预算用尽当作找到最优。

默认关闭海量逐beat/flit trace。
失败或诊断时按router/core/channel/time过滤。

仅稳定、正确、完整drain的
真实FULL_TIMING结果参与推荐。

未运行：not_run。
失败：failed。
环境阻塞：blocked。
不稳定：unconverged。

理论、mock、低成本screening、
正式测量必须分开。
不能用理论数字填measured。

受阻时交付已有代码、有效数据和具体blocker，
不得声称得到完整最优结论。

现在开始核实当前Gate 3能力，
实施这套outstanding与空间非均匀Router五通道深度的联合实验。