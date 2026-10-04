# CHI session JSONL 1.0 / 1.1

UTF-8，每行一个 JSON 对象；首行且仅首行为 `manifest`。记录类型使用小写 `manifest`、`observed`、`protocol`、`validation`。粗粒度输入只需前两类。参考真实文件 `examples/coarse/miss-hit.coarse.jsonl` 与展开结果 `public/demos/miss-hit.chi.jsonl`。

## 1.1 扩展

兼容读取 1.0；四核会话采用 1.1 和 `h-extended-256`。Manifest 增加以下可选内容：

- `fabric.routers`：独立于 CHI 端点的 `{id,x,y,object}`；`links` 为 router 的 `{from,to}`；`attachments` 为 `{node,router}`。
- `litmus`：名称、四线程源码及核/节点映射、`pcMap`（键为 `hart:0xPC`）、初始值、实测结果、工具链、种子、地址轮换和运行元数据。

`instruction` 观测带 `instructionId` 和 `instruction:{id,hartId,iteration,dynamicSeq,pc,sourceId,line}`。动态 ID 为 `cpu:hart:iteration:seq`；PC、动态序号均使用字符串。RV64 数据结果使用十六进制字符串避免浏览器数值舍入；小整数计数和 IRIW 的 0/1 结果可使用安全整数。缓存命中也保留执行观测。`instruction_cancel` 指向已观测的撤销 load；已经发出的请求仍保留其真实动态来源。

`instruction_context.fields.callSite` 保存测试程序测量的实际调用 PC/源码行；它提供上下文，不替代 doorbell 的动态身份。`cause_add` 显式增补合并请求来源。CHI 观测和展开消息携带 `causeInstructionIds`，写回/snoop/victim 另携带 `dataProducerInstructionIds`。重试和子事务继承显式来源集合，未知来源不按地址或 PC 猜测。

`route` 观测通过 `messageId` 关联已有 flit，`fields` 保存转发 router 和方向；不重复生成协议 flit。Stash 的 `allocate` 可带 `implicitDataPull:true`，此分支没有 REQ，使用 SnpResp.DataPull → CompData → CompAck 并单独记录目标安装。

`h-extended-256` 支持自然对齐的 1/2/4/8/16/32/64 字节行内访问；小访问补齐到 256-bit wire 宽度，未采集字节的 validMask 为 0。旧 `h-core-256` 的整行限制不变。数据传输的 DataID 由实际行内拍位置得到；dataless 不产生数据拍。HN→SN 的 `sam.fields` 保存 masks、maskedInput、parities、index；RN→HN 保存 CMN PA 宽度、HN 列表、index 和 selectedNodeId，界面依这些实测参数计算并标注中间结果。

## 数值与身份

地址和实际 tick 一律为非负**整数字符串**，允许十进制或 `0x` 十六进制；前端用 BigInt 处理。序号、beat offset、NodeID 为安全整数。`seq` 表示采集顺序，tick 可相同但不能逆序。

`rootId` 标识一条完整上游逻辑事务；`legId` 标识上游、下游读、snoop 或 dirty victim 分支；`parentLegId` 为创建子事务时传递的父身份；`attemptId` 区分重试代次。原始 TxnID、地址不是稳定身份。

一次成功传输的端点 TX / RX 共享 `messageId`。它们可具有不同实际 tick；协议消息的 `sourceEventIds` 链接两条观测，以最早观测作锚点。1.0 未导出 router 中转，1.1 以 route 保存。不同消息、不同重试不能复用 messageId。

## Manifest

必需：`type`、`schemaVersion: "1.0" | "1.1"`、`title`、`source`、`protocol`、`profile`、`lineBytes`、`dataWidth`、`topology`。

`source` 保存采集种类、固定 commit、生成器名称；`component_harness`、`gem5_run`、`validation_fixture`、`performance_fixture` 分别标识不同来源。`topology` 的节点含 `{id, nodeId, role, label}`，role 为 RN-F / HN-F / SN-F。

可选 `sam`、`clock.tickHz`、`initialState`、`complete`、`captureFilter`、`refinement`。`initialState` 缺失或空表示未知，而非全 I。`complete=false` 表示采集可能截断。运行时专用的 `endpointPorts` 记录实际 Port 名到 NodeID 的映射。

## ObservedEvent

```json
{"type":"observed","id":"o:12","seq":12,"tick":"9007199254740993","kind":"flit","rootId":"r:1","legId":"r:1/up/memory:2","parentLegId":"r:1/up","attemptId":"0","address":"0x80004040","source":"HN0","target":"SN0","channel":"REQ","opcode":"ReadNoSnp","messageId":"message:4","observation":"TX","fields":{"txnid":42,"sizeBytes":64,"order":0,"allowRetry":1}}
```

`kind` 包括 flit、sam、lookup、state、allocate、complete、wait_start、wait_end；节点内部事件使用 `node`，flit 使用 `source/target/channel/opcode`。`fields` 完整保留原始值，包括 gem5 的字节 Size、coarse DataID 和写 DAT 的单独 dbid。

DAT 可带 `payload`、`validMask`、`byteEnable`、`payloadOrigin`。每字节为两个 hex 字符，掩码每字节只使用 `ff` / `00`；缺失掩码视为未知。`fields.beatOffset` 为 line 内字节偏移，不能拿 coarse dataid 直接当 Issue H DataID。

来源可为 `memory_response`、`classic_cache_snoop`、`classic_packet`、`hnf_slc`、`hnf_dirty_victim`、`simulator_zero_fill`、`memory_response_with_simulator_padding` 或明确的 `test_*`。模拟器实际补零有单独来源，缺失 payload 不补零。

`state` 为提交后快照，包含 state、sfState、owner、sharers、dirty、data、validMask、set、way 中有证据的字段。wait_start / wait_end 共用 `waitId`，附原因、阻塞对象、可获得的 occupancy / capacity。未观察到 wait_end 仅代表采集末尾仍未闭合。

## ProtocolEvent

必需 `id/seq/kind/rootId/legId`、`source/target/channel/opcode`、`fields`、`sourceEventIds`、`dependencies`、`anchorTick/anchorSeq/substep`、`origin/rule`。kind 为 flit、binding 或 complete。

字段值包装为：

```json
{"value":2,"origin":"derived","sourceEventId":"p:7","sourceField":"DBID","rule":"B2.5.3"}
```

origin 为 observed（实测）、derived（推导）、modeled（建模）、unknown（未知）。`sourceEventId` 支持界面跳转。Addr 等可能超出 JS 安全整数的字段值继续使用字符串。AllowRetry 在 gem5 请求构造与导出接点补齐并使用 observed 来源；展开器不将明确记录的 0 改为 1。缺失字段或信用证据时保留原始视图并报告。

可选 `applicable: false` 明确表示字段不适用，与值未知不同。CompData 的 BE 不适用，不能据此抹掉已知读数据；是否采集到字节由 `validMask` 决定。写入和 snoop 的 BE 继续控制有效字节，BE=0 的已知字节必须为零；整行写与 SnpRespData 要求全部 BE=1，CopyBackWriteData_I 则全部为 0。

RN→HN 的 ReturnNID / ReturnTxnID 为不适用的 0；此配置中 HN→SN 的 ReadNoSnp 使用 HN 的 NodeID 和本次请求的协议 TxnID。gem5 导出的 MemAttr / SnpAttr / ExpCompAck 作为实测值保留；下游内存属性必须符合上游属性或 SLC eviction 的专门要求。请求缺少 Order 时，默认的 0 标为建模。

协议 ID 在节点、分支和重试生命周期内分配；本配置最多同时保留每节点 1024 个请求 ID。写 DAT.TxnID 来自 DBIDResp.DBID；CompAck.TxnID 来自 completion DBID。分开的 Comp / DBIDResp 必须具有同一个 DBID，等待 DBIDResp 时不得提前释放请求 ID。PCrdGrant 信用按 requester/completer/PCrdType 记账，TxnID 置 0，允许先于 RetryAck；只有二者都收到后才能重发。ReadReceipt.DBID 为不适用的 0，有序读收齐数据但缺少 ReadReceipt 时仍保留请求 ID。

协议排序为 `(anchorSeq, substep)`；原始节点状态事件仍保留采集顺序。补出的 flit 没有伪造 `tick`，只有锚点。动画时间不写回协议状态。

## ValidationResult

包含 `id/engine/status/rule/message/eventIds`，可带 rootId。engine 区分 `CHIron E.b`、`Issue H` 与前端 `import`。status 为 pass、fail、insufficient、unsupported。

未支持 opcode 或超出 profile 的事务整体保留原始视图。丢失请求、起始状态或截断消息不通过地址猜配；开放事务不直接判死锁。CHIron 的 field-mapping、transaction-joint 检查结果与自定义 Issue H 因果 / ID / beat / 数据检查分别输出。CHIron 内部的时间参数使用协议逻辑序号；不写成实测 tick。重发更换协议 ID 超出固定 CHIron 的关联实现时，只将其 transaction-joint 覆盖标为 unsupported，不隐藏仍可展开的消息。

浏览器还验证版本、唯一事件身份、节点引用、tick/地址字符串、序号顺序、掩码和因果引用，并用每 256 步的共享不可变快照支持回退和随机定位。
