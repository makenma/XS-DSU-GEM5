# AXI Mesh 双 lane Router Spec

## 1. 目标与适用配置

在现有 AXI Mesh 中实现一个逻辑 router、两套独立内部 lane。每个 router 仍可连接 2 个或 4 个本地 device，分别使用 `p0..p1` 或 `p0..p3`。device、NI、地址空间和逻辑 router ID 不因 lane 数量翻倍。

两个 lane 分别拥有 AW/W/B/AR/R 五通道的输入 FIFO、VC 状态、仲裁状态、交换数据通路和方向链路。五通道与 vnet 的映射沿用 [AXI 网络合同](../../src/doc/ai_mesh/AXI_GARNET_CODEX_SPEC.md#24-五个-vnet)，Garnet 内部只处理通用 vnet/VC，不引入 AXI 类型或第二套通道映射。

本规格的传输配置为：AXI 数据宽度 32 B、flit 宽度 32 B，W/R header 使用 sideband，五通道均为单 flit packet。配置来源为 [32 B profile](../../configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json)，实际长度由 [AXI packetization](../../src/mem/axi/axi_packetization.cc) 和 [Garnet packetization](../../src/mem/ruby/network/garnet/Packetization.hh) 计算。

必须从实际生效配置验证五通道的 `numFlits == 1`。双 lane 模式不符合该条件时在初始化阶段报配置错误；本任务不引入 packet 内拆 lane、packet 重组或按 burst 固定 lane。公共 Garnet 的其他配置不受此限制。

## 2. 端口与 lane 结构

| 逻辑方向 | lane 0 物理端口 | lane 1 物理端口 |
| --- | --- | --- |
| East | `e` | `e_ext` |
| West | `w` | `w_ext` |
| South | `s` | `s_ext` |
| North | `n` | `n_ext` |

每个方向端口均有对应的发送、接收及 credit 路径。每条有向连接使用独立资源，例如：

```text
A.w     -> B.e          B.e 的 credit     -> A.w
A.w_ext -> B.e_ext      B.e_ext 的 credit -> A.w_ext
```

反向数据传输另建有向链路。普通端口只连接普通端口，`_ext` 只连接 `_ext`；两组连接相同的相邻逻辑 router。边界不存在的方向不建链、不计 buffer，不能改道到其他方向代替。

每个有效 P 输入在两个 lane 内各有一份五通道输入 FIFO 组。来自方向端口的 flit 进入其物理 lane 对应的方向输入 FIFO：普通端口进入 lane 0，`_ext` 进入 lane 1；中途转发保持 lane，不跨 lane 迁移。

```text
                         ┌─ lane 0：p0 输入 FIFO 组 ─ 独立仲裁/交换 ─ e,w,s,n
device 0 ─ NI ─ p0 ─ DualLaneSelector
                         └─ lane 1：p0 输入 FIFO 组 ─ 独立仲裁/交换 ─ e_ext,w_ext,s_ext,n_ext

lane 0 的本地输出 ─┐
                  ├─ 对应 P 口的出口合并 ─ 同一个 NI/device
lane 1 的本地输出 ─┘
```

上述 P 输入结构对每个有效 `p_i` 相同。两套 lane 复用同一种组件实现，实例状态独立，不复制维护两份 router 源码。

## 3. DualLaneSelector 的选择规则

### 3.1 比较对象

对从 `p_i` 进入、属于通道 `c` 的 flit，只比较：

```text
count0 = lane 0 的 p_i 输入、通道 c 的 FIFO 占用
count1 = lane 1 的 p_i 输入、通道 c 的 FIFO 占用
```

当前 Garnet 每个 vnet 包含多个 VC，因此这里的通道 FIFO 是该输入、该 vnet 的 FIFO 组，`count` 定义为组内所有 VC 的实际 flit 数之和；单 VC 时自然等于该 FIFO 的长度。

count 不区分其中 flit 的目标方向。不能使用整个 lane 的总占用、其他 P 口占用、W 方向输入 FIFO、W 输出队列、下游 FIFO、credit 数或统计平均值代替。特别注意：AXI `W` 通道和 West 方向是不同维度。

count 由实际队列状态提供，保持唯一真相源；不能为 selector 维护一套独立可写的占用账本。

### 3.2 唯一选择策略

```text
selected_lane = 0 if count0 <= count1 else 1
```

每个 flit 独立选择；不轮询 lane，不随机、不哈希、不按 device 固定分配。相等时始终选择 lane 0。

逻辑路由方向由既有 XY 路由规则计算，lane 选择不改变目的地、下一跳或 XY 方向顺序。逻辑方向和 lane 共同决定物理输出端口。

例如，`p0` 的一个 AXI W flit 的逻辑路由结果为 West：

| p0 / AXI W / lane 0 count | p0 / AXI W / lane 1 count | 进入的 FIFO 组 | 最终方向端口 |
| --- | --- | --- | --- |
| 2 | 5 | lane 0 的 p0 / AXI W | `w` |
| 5 | 2 | lane 1 的 p0 / AXI W | `w_ext` |
| 3 | 3 | lane 0 的 p0 / AXI W | `w` |
| 0 | 0 | lane 0 的 p0 / AXI W | `w` |

AR、AW、R、B 以及其他 P 口采用同一规则，但读取各自对应的 count。单个 AXI burst 的不同数据 beat 可以选择不同 lane。

### 3.3 接收、背压与周期语义

selector 在本 router 接纳 flit 进入某个 lane 输入 FIFO 时作决定。仅当选中 FIFO 的容量与 VC 接收条件均满足时，才提交唯一一次入队及上游消费。

选择策略与可接收条件分开：先严格按 count 选 lane；选中 lane 暂不可接收时背压，不能转投 count 更大的 lane。相等时 lane 0 不可接收，也不能偷偷改选 lane 1。VC 可用性不能冒充占用数。

未成功接收的 flit 保留在实际持有它的有限队列中，下一次尝试重新比较。成功入队后固定所属 lane，不能重复入队或因后续拥塞迁移。

每个 router 周期，在本周期 P 口向 lane 入队及 lane FIFO 出队之前取得一致的占用快照。选择使用该快照；本周期成功入队、出队统一反映到下一快照。不能因 C++ 遍历 lane 的顺序读到混合的新旧状态，也不提前扣除尚未提交的出队。

选择成功不代表同周期已从方向端口发出。入队后的仲裁资格、交换与链路延迟遵循既有流水配置；新逻辑不得形成同 tick 跨 router 的零延迟穿透。

## 4. 独立仲裁与带宽

每个 lane 独立维护输入 VC 仲裁、输出端口仲裁、轮询指针、输出 VC 分配、crossbar 数据状态、输出 FIFO 和 credit 状态。lane 0 的 grant、stall 或轮询指针更新不能消耗 lane 1 的仲裁机会。

同周期允许 `w` 与 `w_ext` 各发送一个 flit，其他方向同理。测试必须使用两个 lane 都已有待发 flit、两侧下游都可接收的场景证明真实并行。

每个 lane 内五通道沿用 Garnet 的物理链路复用和仲裁规则：一条方向链路合计最多发送 1 flit/cycle。两条方向链路合计上限为 2 flits/cycle，即本规格下的 64 B/cycle 传输槽位；不能将五个 vnet 计作每 lane 五条独立物理链路，也不能据此宣称端到端有效吞吐必然翻倍。

P 口的外部接入带宽维持原单条本地链路带宽。两份内部 P 输入 FIFO 不允许从同一个外部 flit 生成两次接收。

## 5. 本地出口合并与顺序

两个 lane 都可将 flit 路由到同一个本地 `p_i`。每个 P 出口设置一个共享合并仲裁，向原本唯一的 NI/device 连接发送；不同 P 出口独立工作。

两边均请求且可接收时采用公平 round-robin，复位初始优先 lane 0。只有实际成功传输才更新指针；一边无请求时另一边可前进；目标背压时保留 flit。该公平合并规则与入口的 count 相等偏向 lane 0 是两个不同职责。

共享 P 出口每周期最多发送原本链路允许的 flit 数。未获合并 grant 的 lane 不得先出队到无容量约束的隐藏队列。输出 VC、credit 和返回路径必须能定位原 lane，不能混用两个 lane 中数值相同的 VC ID。

网络中不同 packet 可以乱序到达。AXI 的 AW/W 配对、beat 重组、同 ID ordering 与响应交付由既有 adapter 合同保证，见 [AXI 网络合同](../../src/doc/ai_mesh/AXI_GARNET_CODEX_SPEC.md)。不在 router 内增加 AXI 排序器，也不把整个 burst 锁到一个 lane。

## 6. 容量与流控约束

两份对应的输入 FIFO 组采用相同 VC 数和相同每 VC 深度，深度来自同一容量配置。深度单位为 flit/VC；双 lane 表示复制 FIFO 资源，不将一个 FIFO 简单加深为两倍。不同 P 口、方向或通道仍可使用既有容量配置能力。

每个 lane 独立的 receiver capacity 与发送 credit 必须绑定实际连接。上游 P 口、local ingress、两个 lane 输入 FIFO 之间的接收所有权和 credit 返还时刻必须明确：已经接收的 flit 始终有有限存储位置；只有对应槽位释放才返还 credit。

不得将两份 lane 的 credit 简单相加后无条件允许上游发送，因为目标由严格 count 策略决定。若接入协议需要暂存或 VC 映射，须使用有限资源，并纳入容量、背压、功能访问和 drain 检查；不能把它当作不计成本的中转区。

对每条物理数据链路及其接收 VC，验证 credit 守恒，覆盖已发送在途 flit、接收 FIFO 占用和返回在途 credit。合并后的 P 链路同样要验证映射后的所有权与守恒。全过程必须无丢失、无复制、无越界、无 credit 串 lane。

容量导出逐项列出 `(router, lane, input, vnet, vc, depth)`，只计算实际存在的输入。数据存储成本由实际 FIFO 容量求和再乘 flit 字节数；控制状态、sideband 和共享接入/合并暂存分别列出，不能将数据容量估算当作完整硬件面积。

## 7. 代码职责与实施入口

| 职责 | 应优先阅读和扩展的入口 |
| --- | --- |
| 逻辑 router、lane 状态封装及生命周期 | [Router](../../src/mem/ruby/network/garnet/Router.hh)、[Router 实现](../../src/mem/ruby/network/garnet/Router.cc) |
| 输入 FIFO、VC 状态和占用 | [InputUnit](../../src/mem/ruby/network/garnet/InputUnit.cc)、[VirtualChannel](../../src/mem/ruby/network/garnet/VirtualChannel.hh) |
| 逻辑路由及物理端口解析 | [RoutingUnit](../../src/mem/ruby/network/garnet/RoutingUnit.cc) |
| 两份仲裁和交换通路 | [LaneArbiter](../../src/mem/ruby/network/garnet/LaneArbiter.hh)、[SwitchAllocator](../../src/mem/ruby/network/garnet/SwitchAllocator.cc)、[CrossbarSwitch](../../src/mem/ruby/network/garnet/CrossbarSwitch.cc) |
| 输出、VC 和 credit | [OutputUnit](../../src/mem/ruby/network/garnet/OutputUnit.cc)、[NetworkInterface](../../src/mem/ruby/network/garnet/NetworkInterface.cc) |
| 容量与网络配置 | [InputCapacityConfig](../../src/mem/ruby/network/garnet/InputCapacityConfig.hh)、[GarnetNetwork 配置](../../src/mem/ruby/network/garnet/GarnetNetwork.py) |
| 本地端点与 mesh 建链 | [AxiMeshDie](../../configs/topologies/AxiMeshDie.py)、[AXI_MESH](../../configs/ruby/AXI_MESH.py) |
| 排空与观测 | [GarnetQuiescence](../../src/mem/ruby/network/garnet/GarnetQuiescence.hh)、[实验 observer](../../src/dev/ai_mesh/mesh_experiment_observer.cc) |

用统一的 `LaneId`、逻辑方向和物理端口身份表达资源，lane 与方向是不同维度。外部名字到身份的映射只有一个入口；`_ext` 不得落入未知方向兜底或覆盖普通方向索引。目的 router 为本地时继续按具体 endpoint 定位 P 口，不能仅凭 `Local` 字符串选择。

`DualLaneSelector` 只负责同一 P 输入、同一 vnet 两组实际占用的比较与选中接收条件，不负责修改逻辑路线、发送下游 flit 或 AXI ordering。输入入队有唯一写入口；两份仲裁复用实现、分别持有状态。

扩展现有生产路径和配置解析，避免通过新建一套 AXI 专用 Garnet router 复制核心逻辑。功能访问、统计重置、wakeup、drain 及已有 checkpoint 生命周期必须遍历所有真实资源，包括共享入口、出口合并和在途映射。

## 8. 必须通过的定向验收

| 场景 | 必须观察到的结果 |
| --- | --- |
| 2 device、4 device | P 口、NI 和 endpoint 身份正确；内部两份对应输入组；外部 device 数不翻倍 |
| 五通道 packetization | 按生效配置计算，五通道 `numFlits` 均为 1；不符合时初始化报错 |
| count 小于、大于、相等 | 对所有有效 P 口和五个 vnet 覆盖三种关系，选择符合唯一公式；相等始终 lane 0 |
| 连续空闲注入 | 两份 count 均为 0 时连续选择 lane 0，不能为平衡统计改成轮询 |
| 比较对象隔离 | 修改其他 P 口、其他 vnet、方向/下游队列占用不影响当前选择；多 VC 占用按组求和 |
| 逻辑方向与 lane | 四个方向均验证普通和 `_ext` 输出；目的 endpoint 与 XY 下一跳一致 |
| `_ext` 转发 | 跨至少两个 hop 保持 lane 1，方向变化时仍映射对应 `_ext` 端口 |
| 独立仲裁与背压 | lane 0 的方向链路阻塞时，已有待发 flit 的 lane 1 仍能发送；双方 ready 时同周期双发 |
| P 出口合并 | 两个 lane 同时请求同一个 P，最多单链路带宽、持续请求不饥饿、背压不丢数据；不同 P 可独立发送 |
| 满 FIFO / 无可用 VC | 选中 lane 不可接收时保留并背压；不改投另一 lane；恢复后恰好入队一次 |
| 同周期入队/出队 | count 快照与提交时刻一致；不同 lane 遍历顺序不改变选择或守恒结果 |
| credit 所有权 | 双方向链路和共享 P 链路都覆盖发送/返回延迟、重复 VC 编号及复用，无串 lane、超发或双返还 |
| AXI 数据与顺序 | 真实 DMA 读写、W/R 不同 beat 跨 lane、W 先于 AW、同 ID 多事务、目标背压均通过数据和 ordering 检查 |
| 拓扑与 drain | 角、边、内部 router 端口正确；所有实际队列、VC、在途 flit/credit 及合并映射最终排空 |

选择决策的可开关 trace 至少包含 `cycle/router/p/vnet/flit_id/count0/count1/selected_lane/accepted`。仲裁与发送 trace 应能关联同一 flit 的 lane 和物理输出端口；观测不得调用仲裁或改变选择。

先编写涉及真实 FIFO/VC 下一层依赖的定向单元测试，再实现模块；完成后通过真实 gem5 AXI Mesh 场景验证。测试入口优先扩展 [AXI Garnet 测试](../../tests/gem5/axi_garnet/) 与 [Mesh 集成测试](../../util/mesh_ir/tests/integration/)，不以 selector 公式测试或两组计数器代替真实双通路验收，不运行全量测试。

交付必须包含实现、定向测试、实际有效端口/容量配置、测试结果及关联 trace。开发流程以 [AGENTS.md](../../AGENTS.md) 为准。
