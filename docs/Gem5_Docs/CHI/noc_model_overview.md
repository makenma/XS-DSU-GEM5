# XS-GEM5 CHI NoC 建模概览

> 面向同事的技术分享文档。目标读者：对 gem5 有一定了解、但不一定熟悉 CHI 协议与本仓库改动的同学。
> 读完本文，你应该能回答三个问题：
> 1. 这个 NoC 模型里有哪些组件、每个组件干什么？
> 2. 模型里跑着哪些事务（transaction）？
> 3. 时序是怎么"算"出来的，而不是拍脑袋写死的？

---

## 1. 背景与动机

### 1.1 gem5 经典 Cache 模型的时序短板

gem5 经典内存路径（`packet` + `xbar`）的时序模型是**延迟近似**：

- 请求在总线/交叉开关上只花一个固定延迟（`latency` 参数）；
- 没有 flit、没有通道（channel）、没有 credit 流控；
- 拥塞、背压、仲裁等网络行为**不会涌现**——它们根本不存在；
- 因此"多核争抢互连"这类性能问题，在这个模型里是看不见的。

### 1.2 Ruby 的问题

Ruby 协议栈（含 Garnet 网络）有完整的 flit 级网络模型，但它：

- 协议以 SLICC 描述，与 CHI 的 RN/HN/SN 语义对齐成本高；
- 定制一个节点（比如对齐自研 RTL 的 HNF）侵入面大、调试困难。

### 1.3 我们的目标

在 gem5 中构建一条**独立的、CHI 语义的、flit 级时序 NoC 路径**：

```
经典 Cache 侧 ──桥──> 自研 CHI 组件（RN/HN/SN）──自研 Router──> 2×4 mesh
```

- 协议语义：ARM CHI（REQ/RSP/SNP/DAT 四通道）；
- 时序粒度：flit 级流水 + credit 流控 + 事件驱动，拥塞是模型行为而非参数；
- 可参数化：流水深度、QoS 阈值、队列容量全部可配，支持扫描实验；
- 集成方式：桥接 gem5 经典 Cache，尽量少侵入 Ruby。

---

## 2. 总体设计

### 2.1 层次结构

```text
┌─────────────────────────────────────────────────────────────┐
│  协议层（消息定义）                                            │
│  ReqOpcode / RspOpcode / SnoopOpcode / DatOpcode            │
│  ChiChannel（REQ / RSP / SNP / DAT 通道抽象）                 │
├─────────────────────────────────────────────────────────────┤
│  组件层（节点实现）                                            │
│  Cache2ChiBridge (RN-F)    HomeNodeFull (HN-F)              │
│  ├─ HomeLinkLayer（链路时序）                                 │
│  └─ HomePocq（一致性顺序点）                                  │
│  ChiRouterRefModel (自研 Router)    ChiFlitSink (哑终端)      │
├─────────────────────────────────────────────────────────────┤
│  基础设施                                                      │
│  BasicChiComponent / ChiCommonPort / DirectedGraph          │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 组件一句话清单

| 组件 | 角色 | 一句话职责 |
|---|---|---|
| `Cache2ChiBridge` | RN-F | 把经典 Cache 的 miss 请求翻译成 CHI flit，发到 REQ 通道 |
| `ChiRouterRefModel` | Router | 自研路由器参考模型：按事务路由 + 输出仲裁 + 背压 |
| `HomeNodeFull` (HNF) | HN-F | 家庭节点容器：LinkLayer + PoCQ + 收包口 |
| `HomeLinkLayer` | HN 链路 | flit 入/出流水、credit 流控、retry 重试、QoS |
| `HomePocq` | HN 顺序点 | 每条在途事务一个事件驱动状态机（DirectedGraph） |
| `ChiFlitSink` | 测试终端 | 收 flit、计数，用于无后端时的链路测试 |
| 4 个 `*Opcode.hh` | 协议 | CHI 消息类型定义（含变体位解析） |
| `ChiChannel` / `ChiCommonPort` | 基座 | 通道枚举、flit 级端口抽象 |
| `DirectedGraph` | 基座 | 通用有向图，PoCQ 状态机拓扑的载体 |

**代码位置**：`src/mem/cache/CHI/`（含 `base/` 子目录）。

---

## 3. CHI 协议速览：本项目用到的事务

### 3.1 四条通道与三类节点

| 通道 | 方向 | 承载内容 |
|---|---|---|
| REQ | RN → HN / SN | 读、写、原子等请求 |
| RSP | RN ↔ HN | 对请求的响应（完成、转发等） |
| SNP | HN → RN | 窥探（cache 查找、状态迁移） |
| DAT | 数据提供方 → 请求方 | 数据返回 / 写数据 |

节点：**RN**（请求节点，本项目中是 Cache2ChiBridge）、**HN**（家庭节点，HomeNodeFull）、**SN**（从节点，内存/DMA/IO）。

### 3.2 已实现的事务类型（opcode 清单）

**REQ 通道**（`ReqOpcode.hh`，含变体位 v1 解析）：

| 类别 | Opcode |
|---|---|
| 读 | `ReadShared`、`ReadClean`、`ReadOnce`、`ReadNoSnp` |
| 读-改 | `MakeReadUnique` |
| 写 | `WriteUnique`、`WriteUniqueZero`、`WriteNoSnpZero`、`WriteEvict`/`Evict` |
| 原子 | `Atomic*` 族（0x28–0x37 段） |

RSP / SNP / DAT 通道各有对应的响应、窥探、数据 opcode 枚举，与 REQ 一一配对。

### 3.3 一条事务的完整旅程（ReadShared 为例）

```text
CPU miss
  │
  ▼
Cache2ChiBridge ──封装 ReadShared flit──▶ REQ 通道
  │                                          │
  ▼                                          ▼
[Router] 按 (node, txn) 查路由字段 ──▶ 输出仲裁 ──▶ 下一跳
                                              │
                                              ▼
                              HomeNodeFull 收包（RX credit 门控）
                                              │
                                              ▼
                              HomeLinkLayer 入流水（多阶段推进）
                                              │
                                              ▼
                              trigger ──▶ HomePocq 状态机推进
                              （记录在途事务，判定共享者/数据来源）
                                              │
              ┌───────────────────────────────┤
              ▼                               ▼
        需要时发 SNP（经 Router 到各 RN）   数据/完成响应
                                              │
                                              ▼
                              DAT/RSP 通道返回 RN ──▶ Cache2ChiBridge 完成 miss
```

> 这条旅程是理解整个模型的主线：**组件、通道、时序机制全部挂在它上面**。

---

## 4. 组件详解

### 4.1 Cache2ChiBridge（RN-F）

把 gem5 经典 Cache 的请求/响应转换成 CHI flit：

- 请求方向：Cache miss → 组装 REQ flit（opcode、地址、事务号）→ 发往 REQ 通道；
- 响应方向：收 DAT/RSP flit → 还原成 packet 语义 → 回填 Cache；
- 承担 RN-F 侧的流控适配（credit 收发）。

### 4.2 ChiRouterRefModel（自研 Router，非 Garnet）

本模型的网络层**不是**复用 Garnet，而是自研的 flit 级路由器参考模型：

- **端口结构**：12 入 + 12 出 = 4 个本地端口 `P0–P3`（挂 RN/HN/SN 节点）+ 4 个方向端口 `N/E/S/W`（mesh 邻居）+ 4 个外部扩展端口 `N_EXT/E_EXT/S_EXT/W_EXT`；
- **按事务路由**：以 `TxnRouteKey(node, txn)` 为键维护路由上下文，同一事务的 REQ/RSP/SNP/DAT flit 走一致路径；
- **路由决策**：`routeFieldFor()` 按通道类型（REQ/RSP/SNP/DAT）从 flit 中解析路由字段；
- **输出仲裁**：`arbReqIdxToIp()` 把仲裁请求映射回入端口，多输入竞争同一输出时仲裁；
- **驱动检查**：`routerCanDriveOut()` 检查输出可驱动性，配合 credit 形成背压；
- **队列**：每端口 Rx/Tx 队列分离，flit 带序号（`seq`）保证同事务有序。

设计定位是**参考模型（RefModel）**：行为级但时序可见，作为后续细粒度流水路由器的对照基线。

### 4.3 HomeNodeFull（HNF）

家庭节点容器，组合两个核心子模块：

```text
HomeNodeFull
 ├── HomeLinkLayer   ← 链路时序（见 4.4）
 ├── HomePocq        ← 一致性顺序点（见 4.5）
 └── ChiCommonPort   ← RX 收包口（网络侧）
```

实现为 `ruby::Consumer`，由事件驱动（`wakeup()`），不是每拍自由运行。

### 4.4 HomeLinkLayer：链路时序核心

- **4 类在途流水队列**：`std::array<std::deque<FlitVariant>, 4>`，REQ/RSP/SNP/DAT 各一条；同类多个 flit 可同时处于不同流水阶段（flit0 在 H3、flit1 在 H1 并行）；
- **Credit 流控**：入流水受 **RX credit** 限制（防对端灌爆）；出流水受 **TX credit** 背压（对端 buffer 满则停）——这正是真实 CHI 链路的 credit 语义；
- **Retry 机制**：发送被拒的 flit 进 retry FIFO / pending retry 池，**round-robin 仲裁**重试，避免饿死；
- **QoS 阈值**：4 个阈值参数（`thresholds[4]`）按 flit 类型/优先级控制准入与重试；
- **按需唤醒**：无 flit 时不 tick，有事件才 `wakeup()`——行为等价于事件驱动，同时省仿真时间。

### 4.5 HomePocq：一致性顺序点

- **职责**：作为家庭节点内所有在途事务的顺序点（Point of Coherence），决定事务何时完成、何时需要 SNP、数据从哪来；
- **实现**：每条在途事务一个状态机（`PocqMachine`），状态拓扑用自研 `DirectedGraph`（CRTP 节点/边）构建；
- **驱动方式**：classic-cache 风格事件驱动——`trigger()` 只记录触发（来源 srcid / QoS / 通道），排 `processEvent` 到**下一拍**统一 `process()`，同拍多个触发合并推进；
- **时序含义**：触发到处理有一拍延迟，对应硬件 PoCQ 的处理拍数；不做级联、不做自由运行。

> 当前 PoCQ 仍标记了部分 TODO（事务上下文 txnid/addr/srcid、跳转条件细化），是下一步重点。

### 4.6 ChiFlitSink

无后端时的测试终端：接收 flit、按通道计数，用于单独验证链路层/路由器的收发与流控，不参与一致性。

---

## 5. 时序评估方法（重点）

### 5.1 总原则

> **拥塞是模型行为，不是模型参数。**

模型不预设"网络延迟 = N 拍"；延迟由以下机制**涌现**：

```
注入率 ──▶ credit 可用性 ──▶ 仲裁竞争 ──▶ retry/背压 ──▶ 队列深度 ──▶ 端到端延迟
```

### 5.2 逐拍发生了什么（wakeup 流程）

1. 事件到达（flit 进 RX 端口 / 内部定时器 / 对端 credit 释放）；
2. 组件 `wakeup()`：
   - LinkLayer：RX credit 够 → flit 入对应类型在途流水，逐阶段推进（H1→H3）；
   - 出流水前查 TX credit，不够 → 进 retry 池（round-robin 仲裁，QoS 阈值参与）；
   - PoCQ：收到触发 → 记录 → 下一拍 `process()` 推进状态机；
3. Router：收 flit → `routeFieldFor` 定输出 → 仲裁 → credit 足则转发，否则背压；
4. 统计更新（延迟采样、队列深度、retry 计数）。

### 5.3 各机制 ↔ 硬件语义 ↔ 可调参数

| 模型机制 | 模拟的硬件行为 | 对应参数/位置 |
|---|---|---|
| flit 级多阶段流水 | 链路收包→处理的流水拍数 | `HomeLinkLayer` 在途队列深度 |
| RX/TX credit | CHI 链路 credit 流控 | `ChiCommonPort` / LinkLayer 收发 |
| retry FIFO + pending retry 池 | 通道级 retry 机制 | `retryfifo_num`、池容量 |
| round-robin 仲裁 | 端口/通道公平仲裁 | LinkLayer 仲裁器 |
| QoS 阈值 | 优先级准入控制 | `thresholds[4]`（`HomeNodeFull` 参数） |
| 按需唤醒 | 事件驱动等价 | 无参数（行为） |
| 按 (node,txn) 路由 | 事务级一致路由 | Router `TxnRouteKey` |
| PoCQ 一拍合并处理 | PoCQ 处理延迟 | `HomePocq` 事件调度 |

### 5.4 评估口径（可输出的统计量）

- **端到端事务延迟分解**：RN 发出 → Router 逐跳 → HNF 入流水 → PoCQ 处理 → 数据返回，每段可单独统计；
- **队列/流水占用**：4 类在途队列深度、retry 池深度分布 → 定位拥塞点；
- **retry 与背压计数**：被拒次数、按 QoS 级别拆分 → 评估公平性与优先级效果；
- **吞吐 vs 注入率**：不同 QoS/流控参数下的饱和点。

---

## 6. 系统集成与配置

### 6.1 2×4 mesh 拓扑

```text
 0 ─── 1 ─── 2 ─── 3
 │     │     │     │
 4 ─── 5 ─── 6 ─── 7
```

### 6.2 节点绑定（`configs/example/noc_config/2x4.py`）

| 节点类型 | 挂载 Router | 说明 |
|---|---|---|
| CHI_RNF | 1, 2, 5, 6 | 每核一个 RN-F 桥 |
| CHI_HNF | 1, 2, 5, 6 | 家庭节点与 RN 同处（就近一致性） |
| CHI_MN | 4 | 杂项节点 |
| CHI_SNF_MainMem | 0, 4 | 主存从节点 |
| CHI_SNF_BootMem | 3 | Boot ROM |
| CHI_RNI_DMA / IO | 7 | DMA / IO 接口 |

另有 2×2 router smoke 拓扑（`kmhv2_chi_2x2_router.py`，`chi_2x2_router_test_mode`）用于快速冒烟：RNF 挂 (0,0)、SN 挂 (1,0)、HNF 挂 (1,1)。

### 6.3 运行形态

- 16 核 checkpoint restore 全系统运行（`m5out/chi-16core-restore`）；
- 与 XiangShan 内核配置集成（`configs/example/kmhv3.py` 体系），`chi_test_mode` 开关可单独测桥/链路。

---

## 7. 验证与结果

【待补充：请填写你跑出来的数据，建议放 2–3 张图】

1. **功能正确性**：单事务/多事务 trace 正确性（opcode、通道、完成路径）；
2. **时序结果**：
   - 端到端 ReadShared 延迟分解（每段周期数）；
   - 注入率-延迟曲线 / 饱和点；
   - QoS 阈值与 retry 行为对照；
3. **与对照模型的差异**（如有）：与固定延迟模型（经典 xbar）或理想流水模型的对比。

---

## 8. 现状与后续工作

**已完成**
- CHI 四通道 opcode 定义、flit 级端口/通道抽象；
- Cache2ChiBridge（RN-F）桥接经典 Cache；
- HNF（LinkLayer + PoCQ）完整链路时序（流水/credit/retry/QoS/按需唤醒）；
- 自研 ChiRouterRefModel（按事务路由 + 仲裁 + 背压）；
- 2×4 mesh 集成配置与 16 核运行通路。

**进行中 / TODO**
- PoCQ 状态机上下文完善（txnid/addr/srcid 绑定、跳转条件细化）；
- SNP 广播 / 目录查找语义补全；
- 与 RTL 参数对照校准（流水拍数、QoS 阈值）；
- 定量实验（延迟分解、饱和点扫描）。

---

## 9. 附录：代码导航

| 想找什么 | 去哪里 |
|---|---|
| 组件总览 | `src/mem/cache/CHI/` |
| 消息定义 | `src/mem/cache/CHI/base/*Opcode.hh` |
| 通道/端口抽象 | `src/mem/cache/CHI/base/ChiChannel.hh`、`ChiCommonPort.hh` |
| 路由参考模型 | `src/mem/cache/CHI/ChiRouterRefModel.hh/.cc` |
| 链路时序 | `src/mem/cache/CHI/HomeLinkLayer.hh/.cc` |
| 一致性顺序点 | `src/mem/cache/CHI/HomePocq.hh/.cc`、`base/DirectedGraph.hh` |
| 拓扑配置 | `configs/example/noc_config/2x4.py` |
| 运行示例 | `m5out/chi-16core-restore/` |

---

*文档维护：跟随 `src/mem/cache/CHI/` 代码演进；行为以代码为准，本文只负责地图。*
