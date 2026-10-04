# 教学案例时序校对（2026-10-03）

本次核对 26 个复杂教学案例中的 283 条逻辑消息，逐条检查 TX、RX、`anchorTick`、`messageId` 和 router 转发时刻，并核对 91 个 snoop 响应、DBID 后数据和 CompAck 的触发条件。结果见 `build/qa/teaching-timing-audit.json`。这不是完整 Issue H 合规认证。

8 个 gem5 节选的每条 observed / protocol 记录均与 `source.excerptOf` 中同 ID 的记录完全相同；没有修改实测时间来迎合时序图。其余 18 例继续明确标记为协议推演。

## 修正内容

- `split-early-snoop` 改成第一拍正常送达，第二拍在网络中延迟，突出“未收齐”的真实区别：DataID=0 在 5000 发出、7000 到达；DataID=2 在 6000 发出、48000 到达；SnpUnique 在 17000 发出、19000 到达；RN0 到 49000 才开始 SnpRespData。两拍均先于后续 snoop 发出。RN 回应 snoop 的时间仍由全行最后到达时刻决定。
- 标为“到达”“取得 DBID”“数据到齐”“内存更新完成”的检查点改为引用 RX；不再跳回相应消息的发送时刻。两种视图均保留到达时钟。
- `same-address` 原来错误包含另一个排队者 rnf:140。rnf:141 在 HN0 等待 602000→686000，实际释放它的事务是 rnf:130。节选改为同时包含这两个真实根事务，选例器以同 HN、同地址、wait_end 对应的事务完成记录确定阻塞者。
- `victim` 原来误选了只有 clean snoop 的替换，标题却写 Dirty victim。改取原压力轨迹的 rnf:31，包含真实 victim 分支的 WriteNoSnpFull、两拍写数据和完成响应。选例器现在要求受害行分支确实有下游写回。
- 实测节选的“事务完成”检查点选择最后一个主事务完成，避免提前停在 victim 或内存子事务的完成事件；“HNF 查询”优先主请求而非受害行查询。

## 全部场景覆盖

| 场景 | 核对的关键先后关系 / 结果 |
|---|---|
| RetryAck → P-Credit → 重发（实测） | RetryAck、匹配信用均到达后才重新发起；最后 CompAck 在完整读数据后 |
| 同地址竞争（实测） | 包含真正 blocker；HN 完成 blocker 的时刻等于被阻塞者 wait_end |
| Dirty victim（实测） | 受害行 snoop 完成后进行真实 HN→SN 写回，随后继续新行请求 |
| 部分写（实测） | DBID 后发送有效字节；内存补行后合并；后续读回保留未选中字节 |
| ReadOnceCleanInvalid（实测） | dirty 数据被接收并写回内存后再返回；后续 ReadNoSnp 验证保留值 |
| ReadOnceMakeInvalid（实测） | 当前读取最新值；按 hint 不写回，后续内存读取仍为旧值 |
| Stash 拒绝（实测） | 目标拒绝，无 DataPull；原请求正常 Comp |
| Stash DataPull（实测） | 原请求 Comp 与目标收齐数据、安装、CompAck 分离 |
| ReadUnique × WriteBackFull | WB 未获 DBID 时处理 snoop；UD→I；之后空 CopyBack 的 BE/data 为零 |
| ReadShared × WriteBackFull | UD→SC 后写回不得再次移交 dirty；HN 写内存与读数据分支可重叠 |
| ReadOnce × WriteBackFull | SnpOnce 保留 UD；一次读取完成后，真实写回才把内存更新为 9 |
| 提前 CompAck 后 snoop 追上数据 | 第一拍到达 < snoop 到达 < 第二拍到达 < snoop 数据响应发送 |
| 新 REQ 超过旧 CompAck | 新请求先到 HN，但其处理等待旧 CompAck 到达 |
| WB Retry 后被失效 | 等待信用时继续响应 snoop；信用晚到后 PCrdReturn，没有重复 dirty 写回 |
| 一个 WB 连续收到两个 snoop | 同一 snoopee 的前一个完整响应结束后才发第二个；最终 I 决定空 CopyBack |
| DAT 先于完成响应 | 全部 DataSepResp 到达后仍等待 RespSepData，随后才发 CompAck |
| Evict 在途遭遇 snoop | 已逐出 RN 返回 SnpResp_I；后来 Evict 不删除新的 RN owner |
| PCrdGrant 先于 RetryAck | 信用到达仅记账，RetryAck 到达后按匹配来源/类型重发 |
| WriteDataCancel | 先取得 DBID，再发两拍零 BE；收齐取消拍后完成，内存保持不变 |
| Streaming OWO | 后写请求与数据可提前；后写 Ack 等前写 Comp；可见性等后写 Ack 到 HN |
| CopyAtHome | Comp→CompAck，无 DAT；收到 Ack 后才把 HN 隐藏副本作为有效行公开 |
| DCT 双路返回 | RN→RN 与 RN→HN 同时在途；CompAck 回 Home；HN 分别等待数据及 Ack |
| DCT 降级 | 实际普通 SnpRespData 到 HN；由 HN 后备供数，不虚构 RN→RN 传输 |
| UDP 部分数据合并 | snoop 与内存两路取数；按 BE 合并后再向新 owner 交付完整行 |
| WriteCleanFull 旧快照 | 先 snoop 返回新值 9，后 copyback 回旧快照 7；新 store 世代仍为 dirty |
| MakeReadUnique 途中丢行 | 先前竞争者 CompAck 到 HN 后才发下一轮 snoop；以 CompData 给失效请求者补行 |

## 协议依据与回归

依据为仓库内 `IHI0050H_amba_chi_architecture_spec.pdf`，重点为 B4.11.1–B4.11.2（PDF p.283–284）、B2.7 的完成握手和 OWO、B2.10 的重试信用，以及各案例 manifest 中列出的对应章节。

B4.11 明确区分发送和接收：RN 已收到任何读数据拍或 RespSepData 时必须等全部读数据到达后才能回答同址 snoop；HN 后续同址调度还须满足完成确认及多拍发送/接收约束。因此单纯按照 TX 列表画水平箭头不足以呈现这些案例。

回归：`node node_modules/vitest/vitest.mjs run tests/web/special-scenarios.test.ts tests/web/teaching.test.ts`，67 项通过。新增断言固定上述第二拍越过 snoop、RX 检查点、真实 blocker 和 dirty victim 选例，既有测试继续验证特殊握手、缓存终态、数据掩码和来源。
