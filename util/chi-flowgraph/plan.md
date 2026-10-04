# CHI FlowGraph：gem5 + CHIron 三维协议回放网站

## 目标与首版范围

实现一条完整链路：

**gem5 产生粗粒度事务轨迹 → CHIron 协议细化层展开字段与消息 → 网页导入并三维回放。**

以 CHI/NoC 为中心，展示 RN-F、HN-F、SN-F，省略 router 和 CPU 流水线。CPU 的 fetch/read/write 仅作为请求来源标注，有记录时才显示。

首版覆盖：

| 场景 | 展示重点 |
|---|---|
| 地址映射 | 物理地址经过 RN SAM 选择 HNF，展示实际配置、计算结果 |
| HNF 命中 | SLC/SF 查询、数据返回、缓存状态及 CompAck |
| HNF miss | HNF 发起独立 SNF 读事务，数据返回并填入缓存 |
| snoop 供数 | 从其他 RNF 获取数据，关联 owner、sharers 和状态变化 |
| 获取独占权限 | ReadUnique、其他副本失效及确认过程 |
| 写回与下游写入 | RNF→HNF、HNF→SNF 两段事务及各自的 DBID 握手 |
| 有序读取 | HNF→SNF 使用合法的 `Order=0b01`，解释 ReadReceipt |
| 重试与等待 | RetryAck、PCrdGrant、重发，以及资源不足、背压、同地址冲突等等待原因 |

首版不扩展 RNI/HNI，也不实现完整 Issue H、DMT/DCT/DWT、Atomic、DVM 或实时控制 gem5。

## 后端与协议细化

**1. 给 gem5 增加可关闭的轨迹导出器。**

- 记录端点成功发送、接收的消息，以及 SAM 决策、HNF lookup、SLC/SF 状态提交、事务分配和完成。
- 显式记录上游请求、下游内存请求、snoop、dirty victim 的父子关系，不能仅用地址或 TxnID 猜测关联。
- 导出实际数据字节、有效掩码和来源。缺失数据保持未知；模拟器补零、测试数据与真实内存响应分别标识。
- 等待记录采用开始、结束事件，附原因、资源占用和阻塞对象。隐藏 router 后，只解释有证据支持的端点阻塞。
- 支持按时间窗口、节点、地址采集；起始缓存状态缺失时标为未知。
- 导出开关不改变仿真行为。现有 latency CSV 保留作交叉检查，新增事件格式承担详细回放。

**2. 编写独立 C++ 协议展开工具。**

CHIron 提供字段结构、事务关联及检查能力；我们补充有限场景的展开状态机，以 Issue H 为准构造合法流程。

- 保留 gem5 原始消息，独立生成协议消息；默认展示展开结果，详情可对照原始字段。
- 为上游、下游和 snoop 子事务分别管理协议 ID，并记录分配、映射、释放及重试尝试。
- 正确处理 `DBIDResp.DBID → 写 DAT.TxnID`、completion DBID → CompAck，以及 Size、DataID、BE 等转换。
- 默认采用 64B cache line、256-bit DAT；整行拆成两个 beat，DataID 为 **0、2**。
- PCrdGrant 按 requester、completer、PCrdType 管理信用，不能按 TxnID 绑定；允许 credit 先于 RetryAck 到达。
- 普通 trace 不自动改成有序读；内置有序读示例通过明确的展开配置补出 ReadReceipt。
- 保留实际 tick。补出的消息只有逻辑顺序与原事件锚点，不伪造实际 cycle；动画速度独立于协议时间。
- 不能展开的事务保留原始视图并说明原因。CHIron 检查结果与 Issue H 规则覆盖分别记录。

**3. 固定导入接口。**

提供离线命令：

```text
chi-refine --input coarse.jsonl --output session.chi.jsonl --profile h-core-256
```

`session.chi.jsonl` 使用版本化格式，包含：

- **Manifest**：来源版本、采集方式、拓扑、SAM、时钟、数据宽度、初始状态。
- **ObservedEvent**：实际 tick、原始字段、数据、状态变化及稳定事务身份。
- **ProtocolEvent**：展开后的 flit、因果关联、字段来源和规则引用。
- **ValidationResult**：通过、失败、证据不足或尚未支持。

地址和 tick 使用字符串保存，前端采用 BigInt。字段来源区分实测、推导、建模、未知。

## 三维交互

前端采用 **React、TypeScript、Vite、Three.js**，本地导入文件即可使用，不需要持续运行 gem5。

- **中央三维场景**：参考网站的深色、发光节点与流动粒子风格；RNF、HNF、SNF 分区排列，支持旋转、缩放和聚焦事务。拓扑从轨迹读取。
- **通道表现**：REQ、RSP、DAT、SNP 使用固定颜色和形状；选中事务时突出其完整关联路径。
- **节点展开**：RNF 展示 SAM；HNF 展示 SLC、SF、在途事务及等待；SNF 展示请求和数据缓冲。状态来自轨迹。
- **flit 详情**：显示 opcode、适用字段、数据字节、beat、状态变化及手册依据。点击 TxnID/DBID 可跳转到提供该值的消息。
- **底部回放区**：播放、暂停、变速、前后单步、时间定位及事务时序图；明确区分实际 tick 与协议展开步骤。
- **定位工具**：按地址、节点、opcode、事务筛选；点击 flit 自动暂停，方便检查。

导入、索引及状态计算放入 Worker；协议状态与动画分离。采用确定性状态更新和共享快照支持回退、随机跳转，密集流量聚合显示数量，不丢弃底层事件。

## 实施顺序与验收

1. **贯通最小链路**：完成格式、导出器、CHIron 编译验证和展开工具，以一次 miss 后同地址 hit 验证数据与事务关联。
2. **完成首版事务集**：复用 gem5 现有组件测试驱动，生成确定性的示例轨迹；再接入实际 gem5 运行采集。
3. **完成三维交互**：使用同一导入格式实现场景、详情、时序图、筛选和回放。
4. **完成回归与运行说明**：提供示例、采集命令、展开命令和本地启动方式。

验收必须覆盖：

- 原始数据经 SNF/HNF/RNF 传递后逐字节一致，dirty owner 数据不会被旧内存值覆盖。
- 写 DAT 使用正确 DBID；ReadReceipt 的 DBID 无效；不同节点、不同生命周期的同值 ID 不串事务。
- RetryAck 后 ID 复用、重试换 ID、PCrdGrant 提前到达、错误信用类型均正确处理。
- DAT 乱序、缺失或重复 beat、部分有效字节及未知 payload 能正确识别。
- 中途开始或截断的轨迹不伪造初态，也不直接判为死锁。
- 单步播放与随机跳转的节点状态一致；大于 `2^53` 的地址和 tick 不失真。
- 开关导出器前后的仿真 tick、事务结果、数据及缓存终态一致。
- 使用 10 万事件、200 个同时显示 flit 做性能验收，桌面浏览器目标达到 30 FPS。

## 环境与依据

- 本项目先在本机使用，暂不发布公网。实施时准备 **WSL2 + Ubuntu 22.04**；gem5 的 Linux 构建目录放在 WSL 文件系统中，Windows 浏览器查看网页。
- gem5 保持原 C++17 构建；协议展开工具独立使用 C++20、GCC 13 或更新的配套工具链，不修改 gem5 的全局编译标准。
- gem5 固定到 [`agent/linux-checkpoint-restore-fix` 的 `65f3241`](https://github.com/makenma/XS-DSU-GEM5/tree/65f3241a6cd486dffddaca251536f5c691af9a1f)；CHIron 固定到 [`55cd1e1`](https://github.com/RISMicroDevices/CHIron/tree/55cd1e1d92142960af2b57c686fc9f35ce7286be)。
- 首版示例由真实 gem5 组件测试驱动产生，标明组件测试来源；不依赖外部 Linux 镜像、checkpoint 或未提供的程序。
- 界面以中文讲解，保留英文协议字段和 opcode。协议依据为所提供手册，重点引用 B2.4、B2.5、B2.7、B2.9、B2.10：:codex-file-citation{path="D:/proj/chi_flowgraph/IHI0050H_amba_chi_architecture_spec.pdf" purpose="source"}。
