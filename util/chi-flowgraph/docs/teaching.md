# CHI 教学目录

网页左侧“教学目录”和顶部会话选择器都按三组组织，共 50 个教学用例；加上原有 15 个会话，共 65 个可选示例。第三组内再按主题分级。

| 分组 | 数量 | 来源 |
|---|---:|---|
| 单 RN / REQ opcode | 20 | 新增真实 RV64 CPU 程序；19 个目标 opcode 加 ReadShared |
| HNF SLC / SF | 4 | 实际 lookup 的 miss/miss、miss/hit、hit/miss、hit/hit |
| Hazard 与特殊交错 | 26 | 8 个真实 CPU 运行节选，3 个 early snoop 推演，15 个新增特殊协议推演 |

简单例均有 **SLC miss** 的实际查询记录、一个 RN 主动发起的目标 REQ 及 RN 完成记录。SF 按合法初态决定是否命中。MakeUnique、Evict、WriteEvictFull、WriteBackFull 必须先准备真实 RN 缓存行；Stash 必须有目标 RN。不能将这些条件改成“所有缓存都为空”。准备与启动流量折叠在运行节选外，C 源码仍保留原程序和真实 PC / 调用点关联。

四种 HNF 命中组合按真实 `lookup.fields.slcHit/sfHit` 选择。SLC 命中只是数据可能在 HN；SF 命中表示还要考虑其他 RN 的副本，不能据此跳过 snoop。

复杂实测例包含 RetryAck / P-Credit / 重发、两个 RN 同地址排队、dirty victim、交替字节部分写、ReadOnceCleanInvalid 与 ReadOnceMakeInvalid 的后续内存值对比、Stash 拒绝，以及原请求 Comp 与目标 DataPull / 安装分离。每例的“观察重点”提供关键事件跳转、准备条件、结果和来源。

## CopyBack / Snoop 推演

这三例未由当前 gem5 运行验证；**所有事件、字段、代码与时间均标记为教学建模**，不计 CPU opcode 覆盖。C 片段描述并发意图，不是编译过的程序。布局使用同一个 3×2 mesh，时长仅用于展示。

初态：RN1 持有 UD，x=9，内存 x=0。RN0 读和 RN1 WriteBackFull 并行发出，读先到 HN，写回尚未收到 CompDBIDResp。

| RN0 请求 | RN1 snoop 后状态 | 后续 CopyBackWriteData | 本例结果 |
|---|---|---|---|
| ReadUnique / SnpUnique | I | I，BE=0，data=0 | 最新值经 SnpRespData_I_PD 转交；RN0 最终 UD；空写回不能覆盖最新值 |
| ReadShared / SnpShared | SC | SC，有效干净数据（选择一种允许的流程） | 本例 HN 把移交的脏数据写到内存；写回不能再次宣称脏责任 |
| ReadOnce / SnpOnce | UD | UD_PD，完整有效数据 | 一次读取不分配、不取走脏责任；之后真正写回才更新内存 |

依据为本地 **ARM IHI 0050 Issue H，B4.11.1（PDF p.283–284）与 B5.6（p.313–318）**；重点图为 B5.25、B5.26、B5.28。网页来源链接打开本地手册对应页。香山的实现背景见 [MSHR 嵌套 Snoop 文档](https://docs.xiangshan.cc/projects/design/zh-cn/kunminghu-v3/cache/l2cache/MSHR/)。协议定义与某一版本香山支持的具体分支不能混同。

## 播放与代码

新增 15 个场景位于“③ Hazard 与特殊交错”的六个主题子目录。它们均为 **Issue H 协议推演**，不是当前 gem5 支持能力或新 CPU 运行结果的证明。C 是意图和控制器测试接口示意，不能独自保证网络仲裁或可选分支。

| 主题 | 场景 | 要观察的区别 | Issue H PDF 页 |
|---|---|---|---|
| 越序与握手 | 提前 CompAck 后 snoop 追上 DAT | 可以提前 Ack；回答 snoop 仍要等全行数据 | 137, 283–284 |
| 越序与握手 | 新 REQ 超车旧 CompAck | RN 已发 Ack 不代表 HN 已收到 | 318 |
| 越序与握手 | DAT 先于 RespSepData | 数据到了也不能凭空推断完成响应 | 54–58, 139–140 |
| 重试信用 | WB 重试期间被失效 | 可以 PCrdReturn 取消；不能泄漏信用 | 179–181 |
| 重试信用 | PCrdGrant 先于 RetryAck | 信用按节点和类型记账，不绑定 TxnID | 178–182 |
| Snoop 竞争 | 一个 WB 接连遇到两次 snoop | UD→SC→I，最终回写状态由后续交错决定 | 284 |
| Snoop 竞争 | Evict 在途仍遇到 snoop | 响应 SnpResp_I；SF 尚有记录不代表 RN 仍持有行 | 314 |
| 转发 | DCT 双路数据 | RN→RN 与 RN→HN 并行；CompAck 回 Home | 277, 281, 289 |
| 转发 | forwarding snoop 降级 | Fwd 请求不保证实际发生直传；HN 后备供数 | 58–59, 272–273 |
| 数据 | UDP 部分脏数据合并 | 按 BE 取新字节，其余来自旧内存 | 154–155, 241, 291–292 |
| 数据 | WriteDataCancel | 取消仍发送所有原定拍；BE 和载荷全部零 | 137, 239–240 |
| 越序与握手 | Streaming OWO | 后写 Ack 等前写 Comp；不等后写自己的 Comp | 144–146 |
| 高级缓存 | WriteCleanFull 旧快照 | snoop 返回 9、后来 copyback 返回 7；新世代继续 dirty | 262–263, 283–284 |
| 高级缓存 | CopyAtHome 无 DAT 逐出 | CAH=1 可选 Comp→CompAck；ExpCompAck=0 仍需 Ack | 69, 158–161, 245–246 |
| 高级缓存 | MakeReadUnique 途中丢行 | 发出时有 SC，完成时可能必须补 CompData | 255–258, 283–284 |

“准备条件、结果与依据”列出能力前提和手册直达链接。MakeReadUnique、WriteCleanFull、DCT、UDP、CAH 等是扩展教学能力，不加入原 19 类真实 CPU 覆盖。原始记录和展开视图共享消息身份与时刻；检查点不会切换当前视图，RX 检查点保留到达时刻。全量初态和 DAT 字节保留在轨迹，代码窗只摘要展示初态。

- 默认“时刻步进 · 并行”：按唯一 TX / router 转发 / RX 时刻前进；一次“下一步”让所有在途消息沿各自路径同步移动，然后暂停。自动播放在每个边界停留 350ms。默认 0.9 秒 / 步，可调到 2.5 秒。步间时间被整体拉伸，不改变同时性，也不宣称展示倍率等于硬件延迟。
- “等比例仿真时间”使用固定 tick / 墙钟时间倍率。暂停、定位与视图切换保持同一 playhead。
- TX、RX、route 通过 messageId 组成一次物理传输；router 输出 tick 对应其 fromRouter。两锚点之间仅做几何插值。缺少 RX 的采样无法恢复真实飞行时长，不补造延迟。
- 代码行点击直接选择有请求的动态实例，并定位到本核 RN→HN 发出时刻；无 CHI 的 cache hit 定位到指令事件。C 调用点与 doorbell 动态身份保持分离。
- IRIW 默认 C 视图是与实际汇编逐行映射的等价示意；ASM 标签保留实际汇编。C 编译程序直接展示真实 C；没有反汇编的会话不会伪造 ASM。
- 时序图与事件列表并排展示，点击选中后分别居中；多事务教学场景保留全部相关分支。
- 时序图与 mesh 共用同一 `TransportTimeline`：箭尾是 TX、箭头是 RX，迟到的数据拍会斜跨后发先到的 snoop。纵轴按唯一 TX/RX tick 等距排列以压缩空闲段，不宣称像素距离等于硬件延迟；相同 tick 共用一行。点击箭身定位发送，点击实心箭头定位到达，白色虚线标记当前共享时钟。
- 原始 TX/RX 只画一条逻辑箭头；缺少接收证据时以虚线标明未知，不补造到达时刻。推导事件的因果引用不等同于同一消息身份，例如由 REQ 推导的 ReadReceipt 仍保留独立消息。完整的 26 例校对见 [教学案例时序校对](teaching-timing-audit.md)。

## 重现

使用既有固定 gem5、GCC 13.3.0 / binutils 2.42 环境，无需重编 gem5：

```bash
bash scripts/run-litmus.sh tutorial
CHI_LITMUS_OUT="$PWD/build/litmus-hazards" bash scripts/run-litmus.sh stress --hn-entries 32 --slc-lines 16
python3 scripts/build-teaching.py
python3 scripts/model-teaching-hazards.py
python3 scripts/model-teaching-specials.py
```

构建程序包括 `native/gem5/tutorial-test.c`。发布器调用本机 C++ 展开器，检查 CPU 完成状态、部分写读回值、最新写回数据以及所有简单例的实测 SLC miss。发布器从已完成的根事务提取消息、子事务、指令及引用证据，保留原始 ID / tick / 字段，不创造 CPU 事件。补充压力运行实际出现 50 次同地址等待，四核交叉检查均无错误。

运行 `pnpm test`、`pnpm build`、`pnpm test:replay`、`pnpm test:teaching`、`pnpm test:specials` 和 `pnpm test:browser`。浏览器脚本使用 Playwright，可通过 `CHI_NODE_MODULES` 指定已有运行时依赖。动画回归检查实际 Three.js instance matrix，验证多 flit 同时移动、路由锚点、暂停静止，而非仅检查 FPS。性能测试为 100,000 个合成观测和 200 个**正在移动**的可见 flit，明确不属于 CPU 协议覆盖。特殊场景测试检查具体握手、数据掩码、终态和跨视图跳转，是这些固定示例的回归检查，不是完整 Issue H 合规验证。

特殊推演可单独运行 `python3 scripts/model-teaching-specials.py` 重建，不需要 gem5。生成器位于 `scripts/teaching_flow.py`、`teaching_races.py`、`teaching_data.py`、`teaching_control.py`；C 示例输出到 `examples/teaching/`。重复生成不会重复目录项；重建实测片段会保留已生成的协议推演。

报告：`build/qa/teaching-captures.json`、`teaching-browser-report.json`、`specials-browser-report.json`、`replay-browser-report.json`、`browser-report.json`。
