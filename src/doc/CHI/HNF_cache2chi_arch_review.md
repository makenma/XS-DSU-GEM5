# HNF Gem5 建模架构评审意见

> 范围：只评审建模方案风险，不重新设计微架构。重点检查 `classic cache -> cache2chi bridge -> CHI flit -> router -> HNF(LinkLayer/PoCQueue/SLCSF)` 这条路径的协议覆盖、cycle timing、RTL 对齐和文档缺口。

## 0. 总体结论

你的三层 HNF 拆法方向是对的：`LinkLayer / PoCQueue / SLCSF` 是 Gem5 HNF 的主干。但当前描述里有三个高风险点：

1. **cache2chi bridge 不能只是“classic packet 到 CHI flit”的字段翻译器。** 它至少要是一个 RNF-side transaction agent：能发 REQ/DAT/RSP，能接收 HNF 发来的 SNP，能产生 SnpResp/SnpRespData，并维护 TxnID/DBID/CompAck/Retry/PCrdGrant 状态。
2. **Retry/P-Credit 流程不能被建模成 HNF 内部把原请求直接塞进静态 PoC。** 协议语义上是 HNF 返回 RetryAck，之后给 PCrdGrant，Requester 再重发请求；Gem5 里可以做“synthetic reissue”简化，但必须显式标注为抽象。
3. **HNF 子模块少了 SLC Data 或者需要明确 SLCSF 内含 SLC Data。** 规格里 HNF 由 CHI Link Layer、PoC Queue、SLC SF 和 SLC Data 四块组成。若类里只有 `linklayer/pocq/slcsf`，则 `slcsf` 必须包含 tag/data/sf/seq/victim/replay 子结构，否则后面数据 beat、dbuf、fill、victim timing 会对不上。

## 1. cache2chi bridge 能否覆盖所有 CHI 事务？

结论：**不能天然覆盖所有 CHI 事务；建议第一版明确支持子集。**

### 1.1 可以优先覆盖的 classic cache -> CHI 子集

| Classic/Gem5 侧意图 | 建议 CHI 事务候选 | 主要风险 |
|---|---|---|
| Load miss / read shared line | ReadShared / ReadClean / ReadNotSharedDirty，取决于 XiangShan cache 一致性语义 | classic packet 通常没有足够信息区分 ReadClean、ReadShared、RNSD；需要桥内策略或上游传 intent |
| Store miss / upgrade to writable | ReadUnique 或 MakeUnique | 如果只是 classic `WriteReq`，无法判断是否需要 data、是否整行写、是否可以 MakeUnique |
| Full-line write / non-coherent write | WriteUniqueFull / WriteNoSnpFull | 需要区分 coherent memory 与 non-coherent/device memory；WriteNoSnp 不能乱用于 coherent cacheable 区域 |
| Partial write | WriteUniquePtl / WriteNoSnpPtl | 必须正确生成 Size、CCID、BE、QW/OW mask |
| Dirty eviction | WriteBackFull / WriteEvictFull / WriteCleanFull | 需要保留 data Resp 语义，dead CopyBack 条件也要处理 |
| Clean eviction | Evict / CleanShared / CleanInvalid，取决于状态 | classic clean evict 是否需要 HNF 更新 SF，要结合目录状态 |
| Snoop from HNF to RNF | bridge 接收 Snp*，返回 SnpResp 或 SnpRespData | 这是 bridge 最容易遗漏的部分；没有它 HNF 的 SF/Snoop 流跑不完整 |

### 1.2 不建议第一版声称覆盖的事务

第一版可以先不支持或固定关闭：DVM、CMO/Persist、Atomic、Exclusive monitor 细节、Stash/WrStash、DCT、ReadSpec、PrefetchTgt、跨 die/CML 下 SF M 态、ECC 错误 replay、复杂 Order/Barrier。若这些请求从 classic 侧进来，bridge 应该报 unsupported、退化为非 cacheable 路径，或者在 config 中禁止生成。

### 1.3 最容易误翻译的地方

- **ReadNoSnp vs ReadOnce/ReadShared/RNSD**：ReadNoSnp 是 HNF 到 SNF 常见 downstream 访问，不应把 CPU cacheable load miss 简单翻成 ReadNoSnp，否则会绕过一致性。
- **WriteNoSnp vs WriteUnique**：WriteNoSnp 适合 non-coherent/no-snoop 访问；coherent write 应优先走 Unique 类事务。
- **MakeUnique vs ReadUnique**：MakeUnique 是无数据获取、只要唯一权限；store miss 需要数据时不能误翻成 MakeUnique。
- **Evict vs WriteBackFull**：clean eviction 和 dirty eviction 必须分开；否则会导致 HNF/SF 目录和 SLC data 状态不一致。
- **CompAck optional/required**：不要简单统一丢弃。HNF PoC retire 依赖 CompAck busy 的清除。
- **RetryAck/PCrdGrant**：RetryAck 是响应，PCrdGrant 是协议信用授权；它们不是 link credit。
- **DBID/TXNID**：写数据后续 DAT flit 使用 DBID/TxnID 关联，bridge 必须维护映射表。
- **Snoop responder**：HNF 会向 RNF 发 SnpUnique/SnpCleanInvalid/SnpNotSharedDirty 等，bridge 必须能查询 classic cache line state 并返回正确 Resp/Data。

## 2. 哪些地方可能不符合 CHI 协议

### 2.1 bridge 只转 flit，不建 RNF 事务状态机

CHI 不是单报文协议。REQ 发出后可能收到 RetryAck、PCrdGrant、DBIDResp/CompDBIDResp、CompData、Comp、ReadReceipt，还可能被 SNP 打断。bridge 如果只“生成一个 CHI flit”，就无法正确处理：

- write data 何时发；
- CompAck 是否要回；
- Retry 后何时重发；
- snoop 到来时如何响应；
- DMT/DCT 时数据是否绕过 HNF；
- downstream HNF/SNF 返回的 TxnID/DBID 如何匹配。

### 2.2 Retry/P-Credit 抽象方式有协议风险

你的描述里“已经发送 retry 的事务进入 retry pending，等待 pocq 释放资源，如果释放就做 QoS 防止饿死的仲裁，把事务发出去做 pcrdgrant，随后这个输入进入一个静态 pocq 队列”。这里要拆成两种建模语义：

- **协议真实语义**：HNF 发 RetryAck，稍后发 PCrdGrant；Requester 收到匹配的协议信用后，重新发送原请求。HNF 不能仅靠之前缓存的原 flit 直接完成事务。
- **模型简化语义**：Gem5 内部可以用 `retry_pending_request` 保存原请求，PCrdGrant 事件触发 synthetic reissue。这样可行，但必须在文档里写清楚：这是 bridge/RNF 的重发抽象，不是 HNF 自己把旧请求塞入 PoC。

### 2.3 link credit 和 protocol credit 混淆

你写“bridge 会发送 credit 和 flit”，要明确有两类 credit：

- **Link/channel credit**：每个 CHI 通道的 flit flow-control，影响能不能收发 flit。
- **Protocol credit / P-Credit**：Retry 流程里的 PCrdGrant，表示后续某个请求类型/资源保证可被接受。

两者若混成一个计数器，Retry 场景一定会和 RTL/协议对不上。

### 2.4 HNF linklayer 不能只处理 RXREQ

HNF linklayer 有 RxREQ/RxRSP/RxDAT 三个输入通道，RxRSP/RxDAT 是推进已存在 PoC entry 的关键事件。若 HNF 只从 RXREQ 创建 entry，而没有 RXRSP/RXDAT 对 entry 的异步更新，就会漏掉：CompAck、DBID、CRDGNT、SnpResp、SnpRespData、MC CompData、CBWrData、WRCancel。

### 2.5 Snoop 不能被 Retry

作为 RNF bridge，接收到 TXSNP 后要能处理或阻塞自己的输入队列，但不能把 snoop 当普通 request 走 RetryAck。Snoop 侧资源最好独立，否则容易死锁或严重拥塞。

### 2.6 Ordered Read / ReadReceipt 不可完全忽略

如果 classic cache/CPU 侧会产生强顺序或 acquire/release 语义，bridge 至少要决定 Order 域如何映射，以及何时要求/处理 ReadReceipt。第一版可以把 Order 固定为 0，但必须写成限制。

## 3. 哪些地方时序不够精确

### 3.1 HNF link timing

需要显式建模：

- RXREQ H0/H1/H2：接收、拆包、分类、fastpath 候选；
- TXRSP fastpath vs PoC path：RetryAck、PCrdGrant、ReadReceipt、DBID/CompDBID、Comp 的最早发出周期不同；
- TXREQ/TXSNP/TXDAT 的 H-stage 对齐；
- TXSNP/TXREQ 小 FIFO 或 staging buffer；
- 每个 TX 通道的 link credit gating。

第一版可简化为固定 latency，但不能写成“进入 linklayer 后立即进 PoC 或立即发响应”。

### 3.2 PoC timing

必须影响 cycle 的点：

- QoS pool / FVB pool / PoC entry 满导致 Retry；
- dynamic/static allocation 的 H1/H2 时序；
- AgeQ oldest、rotating、find-first 仲裁；
- L3/MC/TXDAT/TXSNP/TXRSP/MC-Retry 每类每周期最多一个 entry；
- Sleep/Wake：同地址 entry 被 sleep 后不参与任何调度；
- Busy bits 清除时机决定 retire；
- dbuf_ctl 两个 buffer 和 TXDAT credit 反压；
- RXDAT beat 收齐时间决定 fill/MC/TXDAT 何时 ready。

### 3.3 SLCSF timing

必须影响 cycle 的点：

- SLC/SF tag latency；
- SLC data RAM 读写 latency；
- QW0/1 与 QW2/3 错拍；
- slc_busy 造成 pipeline bubble；
- SLC/SF setway hazard replay；
- SF victim -> SEQ，SEQ full replay；
- SLC dirty victim 触发 MC writeback；
- SLC/SF init 每 8 周期处理 set 的行为。

第一版可以把 SLCSF 抽象成 `lookup_latency + optional_fill_latency + optional_replay_delay`，但需要配置化，否则后面 waveform 对不上。

## 4. 哪些地方会导致模型和 RTL 对不上

| 风险 | 为什么会对不上 | 建议 |
|---|---|---|
| HNF 只有三子类，没有 SLC Data | RTL/规格中 SLC Data 独立参与 data return/fill/victim | `SLCSF` 内部至少拆 `slc_tag/sf_tag/slc_data/seq/replay` |
| 不保留 `orig_opcode` | RTL 内部大量使用 orig opcode + mapped opcode 双轨判断 | PoC entry 同时保存 `orig_opcode` 和 `int_opcode` |
| Retry 后直接进 PoC | 协议上应由 RNF 重发 | 在 bridge 侧建 retry table 和 reissue |
| 忽略 RXRSP/RXDAT | CompAck、SnpResp、DBID、MC data 都从这里来 | LinkLayer 三个 RX 都要接 PoC encoder |
| 忽略 FVB/SEQ 注入 | SF victim/back invalidation 和 init 不来自外部 RNF | HNF 内部请求源单独建模，与 RXREQ 竞争 PoC allocation |
| 不建 sleep/wake | 同地址 ordering 会错 | PoC allocation hazard 必须实现 |
| 不建 replay | SLC/SF pipeline hazard 和 SEQ full 会错 | SLCSF replay 返回 PoC，重新置 L3 req busy |
| 不建 snoop count | Snoop response 未收齐就完成 | 每 entry 维护 outstanding snoop counter |
| DMT 简化成普通 MC read | HNF data path 占用和完成条件不同 | DMT flag 改变 RetNID/ReturnTxnID、dbuf、CompAck 等行为 |
| partial write 只按整行 | BE/QW/OW 和 MC read-modify-write 路径会错 | 先支持 full-line，partial 明确 unsupported 或建 BE/QW |

## 5. 哪些地方需要拆模块

### 5.1 cache2chi bridge 建议拆

1. `ClassicPacketClassifier`：把 classic packet 变成 memory intent。
2. `ChiReqMapper`：intent -> CHI opcode + fields。
3. `RnTxnTable`：TxnID、DBID、CompAck、Retry、outstanding data beat 状态。
4. `RetryPcreditManager`：RetryAck/PCrdGrant 匹配和 synthetic reissue。
5. `RnSnoopResponder`：接 TXSNP，查询 classic cache/state，返回 SnpResp/SnpRespData。
6. `DataBeatPacker`：BE/QW/OW、DataID、ChunkV、write data/copyback data。
7. `ChiLinkPort`：REQ/RSP/DAT/SNP channel credit + flit serializer/deserializer。

### 5.2 HNF 建议拆

1. `HnfLinkLayer`
   - RXREQ/RXRSP/RXDAT parser
   - TXREQ/TXRSP/TXDAT/TXSNP packer
   - link credit
   - fastpath TXRSP
   - dynamic/static allocation, retry FIFO/bank, PCrdGrant queue
   - FVB/SEQ injection arbiter

2. `HnfPoCQueue`
   - alloc/retire/AgeQ
   - hazard sleep/wake
   - busy bits
   - address buffer/data buffer
   - RX encoders / TX decoders
   - L3/MC/TXDAT/TXSNP/TXRSP/MC-Retry selectors

3. `HnfSLCSF`
   - SLC tag
   - SLC data
   - SF tag
   - SLC/SF state update
   - replay detector
   - victim/SEQ/FVB
   - snoop decision
   - init/flush

## 6. 哪些地方可以先简化

### 第一版建议支持

- RNSD
- ReadUnique
- MakeUnique
- Evict
- WriteBackFull
- ReadNoSnp / WriteNoSnp only for non-coherent/device path
- Full-line data first，partial write/read 后补
- DMT 可以参数开关，先支持基本 DMT/non-DMT 分支
- SF directed/broadcast 先按 SLCSF 返回结果，不自行推完整真值表

### 第一版可以简化但要写进限制

- QoS pool：先按总 entry + per-priority cap，不做完全 RTL 防饿死细节。
- 仲裁：先实现每类通道单发 + oldest/round-robin，find-first/HH/H 精确优先级后补。
- Victim：先 random，不做完整 LRU/SRRIP/BRRIP。
- Init：仿真启动时直接清空 tag，cycle-accurate init 后补。
- ECC replay：默认不触发。
- Stash、DCT、CMO、DVM、Atomic：先 unsupported。
- L3 bypass：先关闭，全部走 PoC scheduled。

## 7. 哪些地方需要文档补全

| 文档缺口 | 为什么必须补 |
|---|---|
| classic packet -> CHI opcode 完整映射表 | bridge 是否协议正确取决于这个表 |
| 每个 CHI opcode 的字段默认值 | QoS、MemAttr、Order、ExpCompAck、SnpAttr、LPID/LID、Trace、SrcType、RetNID 等不能隐式猜 |
| 支持/不支持 opcode 列表 | 避免“覆盖所有 CHI”的误声明 |
| RetryAck/PCrdGrant 建模语义 | 真实重发还是 synthetic reissue 必须写明 |
| RNF snoop responder 状态表 | TXSNP -> SnpResp/SnpRespData 是 bridge 的关键功能 |
| SLC/SF snoop broadcast/directed 真值表 | 当前文档表格未完全展开，不能编造 |
| self cancel 完整条件 | Stash/NoSnp cancel 对 FSM 有强影响 |
| force_s 条件 | 文档中已有不清楚之处，模型应只按返回信号处理 |
| DMT 条件 | 决定 MC 返回到 RN 还是 HN |
| PoC QoS pool 参数 | 影响 Retry 和 cycle timing |
| SLC/SF latency 和 slc_busy 规则 | 影响 L3 lookup/fill 周期 |
| data width / beat / BE / QW 规则 | 影响 TXDAT/RXDAT 周期和 partial correctness |
| transaction table 的 golden path 名称 | 方便和 RTL waveform 对齐 |

## 8. 事务建模流程文档

### 8.1 Bridge 侧流程

```text
Classic packet
  -> classify memory intent
  -> check supported subset
  -> map to CHI opcode + fields
  -> allocate RN transaction table entry
  -> send CHI REQ/DAT flit through router
  -> handle response:
       RetryAck -> wait PCrdGrant -> reissue request
       DBID/CompDBID -> send write data if needed
       CompData -> return data to classic cache, maybe send CompAck
       Comp -> complete dataless/write transaction, maybe send CompAck
       SNP from HNF -> query local cache state -> send SnpResp/Data
```

### 8.2 HNF LinkLayer 流程

```text
RX channel accepts flit if link credit/enable allows
  -> parse fields
  -> RXREQ: classify opcode/memattr/stash/order/expcompack
  -> dynamic/static/FVB resource allocation
       resource available -> send to PoC allocation
       no resource -> RetryAck path + Retry QoS Bank
  -> TXRSP fastpath candidate arbitration
  -> route RXRSP/RXDAT into existing PoC entry encoder
```

### 8.3 PoCQueue 流程

```text
PoC allocation
  -> assign entry, valid/static/pool/AgeQ
  -> write address/data/control fields
  -> same-line hazard check
       hazard -> sleep entry
       no hazard -> set busy bits and active
  -> scheduled arbitration:
       L3 request
       MC request / MC retry
       TXDAT
       TXSNP
       TXRSP
  -> RXRSP/RXDAT update entry state
  -> busy bits clear
  -> retire entry and release resource
```

### 8.4 SLCSF 流程

```text
L3 request from PoC
  -> SLC/SF tag lookup
  -> replay check
       replay -> cancel pipeline -> notify PoC -> reissue L3 req
  -> hit/miss matrix:
       SLC hit -> return data/update/invalidate/fill state
       SF hit -> directed/broadcast snoop decision
       SLC+SF miss -> MC ReadNoSnp or DMT path
       victim -> dirty victim MC writeback / SF victim SEQ BI
  -> return h9/h10 result to PoC
  -> optional second-round update/fill
```

## 9. 建议第一版验收标准

1. 所有 supported opcode 都有 bridge mapping 表。
2. 每个事务都有 start/finish 条件：Comp、CompData、CompAck、DBID、SnpResp、MC CompData、MC Comp。
3. 每个 PoC entry 有 busy bitmap，retire 只由 busy 全清触发。
4. 同 cacheline 请求必须串行化。
5. Retry 走 RetryAck -> PCrdGrant -> reissue。
6. Snoop responder 可返回至少 I/SC/UC/UD 和 data/no-data。
7. DMT/non-DMT 可通过同一个测试切换并观察 HNF dbuf/TXDAT 占用差异。
8. SLC/SF replay 可注入并导致同一 L3 req 重新调度。
