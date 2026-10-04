# 原生 CHI L2：RTL 参考与模型边界

本文记录重写 gem5 L2 时使用的固定 RTL 依据和必须保留的行为，不把设计要求当成实现或验证完成证明。目标接口为 `CPU / L1 → gem5 ChiNativeL2 → CHI NoC`：L2 自己持有缓存行、权限、MSHR、回填及替换状态，在发送端直接形成合法的 CHI REQ/RSP/DAT；不再由 L2 后面的无状态桥接器或离线展开器补出协议语义。

## 固定参考版本

2026-10-03 经 GitHub API 核实：

| 仓库 | 固定 commit | 用途 |
|---|---|---|
| [OpenXiangShan/XiangShan](https://github.com/OpenXiangShan/XiangShan/tree/e7bab53e66dfb3c4a1d11cf9519b0396f8576cae) | `e7bab53e66dfb3c4a1d11cf9519b0396f8576cae` | `kunminghu-v2` 主仓；`coupledL2` gitlink 指向下行版本 |
| [OpenXiangShan/CoupledL2](https://github.com/OpenXiangShan/CoupledL2/tree/55d8e69c690690dbc786e9ca2f3839a95555df95) | `55d8e69c690690dbc786e9ca2f3839a95555df95` | 原生 CHI L2 的参考实现，子目录 `src/main/scala/coupledL2/tl2chi` |

选择 v2 是因为 XiangShan 主仓 README 将其推荐给研究及下游工作；默认 v3 则在快速演进，使用 XSCache 子模块。这里只固定并参考 v2，不混用 v3 的 XSCache HEAD。参考源码已缓存于 `tmp/rtl-reference/coupledL2/`；文中的在线链接全部使用固定 commit，可重新获取。

CoupledL2 的 [CHI 版本配置](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/chi/Message.scala#L216-L284) 枚举 B/C/E.b，不能把参考该 RTL 解释为完整 Issue H 认证。本项目继续以所提供 Issue H 手册约束所实现的事务和字段；RTL 提供缓存控制机制、状态及协议发送行为的参考。

## 缓存状态由 L2 拥有

RTL `Directory.MetaEntry` 存储 dirty、state、clients，clients 是上游副本位图；具体上游状态不能由一个 CHI Resp 取代。[Directory.scala:29–34](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/Directory.scala#L29-L34)

| 内部稳定状态 | 意义 | 对外 CHI 状态 |
|---|---|---|
| INVALID | 本行无效 | I |
| BRANCH、clean | 只有共享权限 | SC |
| TRUNK、clean/dirty | 某一个上游 client 持独占权限 | UC / UD |
| TIP、clean/dirty | L2 持独占权限，上游仅可有共享副本 | UC / UD |

状态定义见 [Consts.scala:26–42](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/Consts.scala#L26-L42)，CHI 映射见 [MSHR.scala:117–126](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L117-L126)。RTL 不实现 SharedDirty/Owner 稳定状态，因此相应共享 snoop 将行降级至 BRANCH，脏数据需要交出。[MainPipe.scala:258–268](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MainPipe.scala#L258-L268)

gem5 可以用 `I/SC/UC/UD + owner/sharers` 表示等价功能，但 owner、上游 probe 和实际字节必须存在；不能把 L2 的旧副本当成 L1 dirty owner 的最新值。组相联 tag/data、容量、替换、dirty victim 和有限 MSHR 都应由新 L2 实例拥有。

## 普通 load/store 自然产生的请求

CPU 的一次 load/store 不与一个固定 CHI opcode 一一对应。L1 miss/权限请求到达 L2 后，L2 按现有权限、数据是否齐备和是否需要向下请求作决定。RTL 主流水线在 L2 miss 时获取数据，在 BRANCH hit 且需要独占时升级；已有足够权限的 hit 直接满足上游。[MainPipe.scala:235–256](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MainPipe.scala#L235-L256)

| 实际上游需求与 L2 条件 | RTL CHI 请求 | gem5 对应原则 |
|---|---|---|
| Get / AcquireBlock NtoB，需要下游数据 | ReadNotSharedDirty | 普通 load、取指及读预取的 miss 路径 |
| AcquireBlock NtoT，需要数据与独占 | ReadUnique | 写分配、独占读在本层缺数据时获取整行及权限 |
| 整行覆盖写的 AcquirePerm NtoT / BtoT，仅需要权限 | MakeUnique | CPU DCache 的 full_overwrite = isStore && store_mask.andR；必须保证写满全部字节，不能由“已有共享副本”直接推断 |
| 有效行、权限充足 | 无下游 CHI 请求 | L2 命中直接响应；L1 hit 甚至不应进入 L2 |
| dirty victim 替换 | WriteBackFull + CopyBackWrData | 缓存容量压力自然生成，保留 victim 字节直到传送结束 |
| clean victim 替换 | E.b 的 WriteEvictOrEvict，旧版本退化为 Evict | 默认发送 WEOE，write_evict_or_evict=False 可选择 B/C 的 Evict；分别处理 Comp+Ack 与 CompDBIDResp+DAT |
| 非缓存 Get | ReadNoSnp | 独立的非缓存/PMA 路径，不能用于替代一致性 miss |
| 非缓存 PutFullData / PutPartialData | WriteNoSnpPtl + NonCopyBackWrData | 保留访问 Size、字节掩码和设备顺序约束 |

前三项为 [MSHR.scala:374–394](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L374-L394) 中实际选择逻辑；替换见 [MSHR.scala:352–363](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L352-L363) 和 [510–523](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L510-L523)；非缓存路径见 [MMIOBridge.scala:229–268](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MMIOBridge.scala#L229-L268)。

普通 store 不等于 WriteUnique，也不等于每次向 NoC 发送写请求。测试接口显式指定 CHI opcode 只能验证被指定的协议流程，不能作为上述自然选择的证据。端到端验收必须包含无协议 opcode 覆盖标记的 CPU 普通访存程序，并从真实 L2 tag/权限/替换状态解释每一笔 CHI 请求。

补充沿 CPU 上游核对后的条件：固定 XiangShan DCache `MainPipe.scala:850` 由逐字节 `store_mask.andR` 设置 `full_overwrite`，`MissQueue.scala:265,845` 仅对此选择 AcquirePerm，其余选择 AcquireBlock。普通部分写的权限升级仍可能产生 ReadUnique。当前 classic L1 的非维护 InvalidateReq 表示完整行写承诺，由新 L2 映射 MakeUnique；它没有数据，完整写入数据仍在 L1 MSHR，回放在 L1 供数前保持该行 payload 未知。带 Request::INVALIDATE 的 CBO InvalidateReq 则属于 MakeInvalid，两者必须按 request flags 区分。

维护请求按真实 CPU CBO 进入：CBOClean → 必要的 WriteCleanFull → CleanShared；CBOFlush → 必要的 WriteBackFull/Evict → CleanInvalid；CBOInval → 必要的 Evict → MakeInvalid。L1 清理请求如果标记 satisfied，L2 等待同 PacketId 的 WriteClean 提交实际字节后才开始下游维护。写回与后续 CMO 使用独立协议 ID 和显式父子事务关系。

固定 gem5 原本缺少 Zicbom 解码，本项目安装 SE timing 模式下的三条 CBO 指令，走真实地址翻译、LSQ、L1 和普通缓存维护 Packet，不注入 CHI opcode。FS 的 envcfg 权限控制尚未实现，明确陷入非法指令。非缓存测试使用 Normal non-cacheable RAM 映射，RN 使用 ReadNoSnp / WriteNoSnpPtl、Order=2、ExpCompAck=0，并等待 ReadReceipt；STRICT_ORDER 是 CPU 排序标志，不能直接推导 Device MemAttr。本项目 RN-F/HN-F/SN-F 拓扑不实现 Device 外设/HN-I，HN/SN 明确拒绝 Device 请求。

## MSHR 和并发不变量

- MSHR 保存原请求、分配时目录结果、当前请求和 victim 的身份、回填字节/拍位图、上游 probe 状态、完成响应、CompAck/写数据发送状态以及 retry 信息。只有待发动作和待收响应同时清空才释放。依据：[MSHR.scala:1315–1328](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L1315-L1328)。
- CPU miss 不能占满所有处理 snoop 所需资源。RTL 给非 A 通道保留最后一个 MSHR；模型可用独立 snoop 槽和有限响应队列实现相同的前进保证。[MSHRCtl.scala:106–111](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHRCtl.scala#L106-L111)
- 同地址冲突同时覆盖 refill 地址和仍待写回的 victim 地址；不能只查当前 tag 数组。[RequestBuffer.scala:108–116](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/RequestBuffer.scala#L108-L116)
- 在 miss 未收到第一拍数据前，同地址 snoop 必须有独立处理路径，不能无条件等待 miss 完成；否则可能形成 L2 等待 HNF 数据、HNF 等待 L2 snoop 响应的死锁。首拍到达后，回填及上游 grant/probe 冲突可短暂阻塞。victim 的上游 probe 完成后，snoop 可从保存的 victim 数据响应。[RXSNP.scala:43–112](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/RXSNP.scala#L43-L112)
- 因 dirty owner 的响应而取得的最新数据必须优先于旧 data-array/refill 副本；dirty 被 snoop 交出后，未完成写回不能再次以旧 dirty 状态提交。RTL 专门区分 nested WriteBack/WriteClean 的元数据和数据。[MSHR.scala:183–233](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L183-L233)

## 在 L2 发送端形成的协议字段

| 字段或握手 | 必须由在线 L2 状态机完成 | RTL 依据 |
|---|---|---|
| REQ Size | `log2(bytes)`；64 B 为 6，不能在 NoC 仍保存 64 再由离线工具改写 | [MSHR:394](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L394) |
| DAT DataID | 256-bit DAT、64 B 行使用 0 和 2；按地址分片，不能用 0、1 当线协议值 | [TXDAT:156–165](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/TXDAT.scala#L156-L165)、[RXDAT:37–61](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/RXDAT.scala#L37-L61) |
| BE | copyback I/cancel 的 BE 清零并将数据对应字节清零；完整正常 copyback/snoop 使用完整 BE | [TXDAT:133–137](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/TXDAT.scala#L133-L137) |
| CompAck | 读 CompData 返回的 DBID → CompAck.TxnID，HomeNID → TgtID；不能复用原 request TxnID | [MSHR:1169–1181](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L1169-L1181) |
| 写数据 DBID | CompDBIDResp.DBID → CopyBackWrData.TxnID，响应 SrcID → DAT.TgtID；数据排队成功不等于发送成功 | [MSHR:1237–1241](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L1237-L1241)、[604–610](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L604-L610) |
| 初发与重试 | 初发 AllowRetry=1、PCrdType=0；收到 RetryAck 与匹配信用后重发 AllowRetry=0，不能只收到其中一个就重发 | [MSHR:264–274](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L264-L274)、[403–405](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/MSHR.scala#L403-L405) |
| PCrdGrant | 整个 L2 共享信用队列，按发信方 SrcID 与 PCrdType 匹配等待的 MSHR；提前信用可保存，TxnID 不参与信用归属 | [TL2CHICoupledL2:170–240](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/TL2CHICoupledL2.scala#L170-L240) |
| SNP 地址 | 原生 SNP flit 地址字段表示完整地址右移 3；进入缓存查询时左移 3 恢复 | [RXSNP:134–135](https://github.com/OpenXiangShan/CoupledL2/blob/55d8e69c690690dbc786e9ca2f3839a95555df95/src/main/scala/coupledL2/tl2chi/RXSNP.scala#L134-L135) |

协议 ID 的生命周期应绑定 MSHR/attempt 等稳定内部身份；不同节点同值 ID、重试之后 ID 复用或更换都不得混淆。保留 whole-line 回填缓冲以及独立 beat 接收位图，识别重复拍、缺拍、乱序和未知字节。采用收齐数据后再发 CompAck 是允许的保守策略，不需要仿制 RTL 的首拍提前确认时机；但握手字段和数据因果必须正确。

## 适度抽象的边界

保留：组相联容量、有效字节、目录权限及上游副本、有限 MSHR、请求/响应/数据独立队列和背压、回填/替换缓冲、snoop 与在途请求冲突、信用/ID/DBID 生命周期、实际 CHI opcode 和字段、CPU 读回结果。

抽象：SRAM 宏和 ECC 门级结构、具体流水级寄存器、RTL arbiter 电路、物理链路启停和逐拍布线。可用 gem5 事件延迟及有界队列替代；这些延迟需要独立配置，不能伪称 RTL 周期精确。上游仍可接 gem5 Packet/Request 端口，但该端口表达访存及权限需求，不得把预先指定的协议 opcode 作为正常工作必要条件。

原生接口应有公共、明确的 CHI wire 类型；若保留内部便捷数据，使用不同类型或字段名，禁止一个 `size/dataid/addr` 同时表示两种编码。兼容旧 HNF/SNF 的必要转换要有显式边界和验证，不能把它放到离线回放中并称为运行时协议。最终 L2→NoC 采集点应直接导出已经发送的原生字段，回放层只解析、索引及检查。

## 当前实现与 RTL 的差异

以下是当前实现采用的功能抽象选择。四核真实 KMH CPU 的 baseline 与有限 HN/SLC 压力运行已通过数据校验和原始协议检查，具体参数、报告及覆盖界限见 [原生 L2 验证记录](native-l2-validation.md)。这些结果不等于 RTL 周期等价或所有协议场景均已验证：

- **请求选择以实际上游承诺为准。** 完整行写走 MakeUnique，其余需要数据及写权限的请求走 ReadUnique。此前把所有“已有数据的写升级”归为 RTL MakeUnique 的说明过宽，已按 CPU 的 full_overwrite 条件修正。classic L1 与 RTL Sbuffer/MSHR 的合并时机仍不同，同一段程序未必产生逐笔完全相同的流量；不能据此声称周期级或逐条 flit 等价。
- **共享 snoop 对 classic L1 保守失效，L2 自己保留 SC。** classic L1 收到普通 ReadReq snoop 后可能保留 Owned dirty 副本；若同时对 CHI 表示已 PassDirty，则上游继续保持 dirty 会违反模型声明的责任转移。当前模型通过失效上游副本并取得最新数据，完成交脏后在 L2 保留共享干净行。相较于 CoupledL2 的精确上游降级，这会增加后续 L1 miss，但可明确保持对外 CHI 权限和数据责任的一致性。
- **clean victim 默认采用 E.b 的 WriteEvictOrEvict。** HN 完整 SLC 副本且非 unique 时选择 Comp-only，等待 CompAck_I；unique owner 时请求 CopyBackWriteData。带数据分支不再额外发送 CompAck。选择 B/C 兼容配置时发送 Evict。CAH 固定为 0，非 WEOE CopyBack 即使取消也用 DBID + CopyBackWriteData_I，不允许无依据的 Comp-only。

上述运行覆盖普通 CPU 访存、dirty owner 交接、容量替换和压力重试，但未穷举所有同地址 snoop/替换交错；也未导出并逐行比较全部缓存终态。保留这些覆盖边界，不能由当前通过的用例推导完整 RTL 等价、周期精确或完整 Issue H 一致性。

## 验收证据要求

1. 配置图和运行时类型证明 KMH CPU 的真实访存连接到新 L2，自带 tag/data/MSHR，并由其直接连接 CHI NoC。
2. 无 opcode 注入的真实 CPU 程序产生读 miss→hit、写 miss、已有共享行写升级、多核 snoop 供脏数据、容量驱动 dirty/clean victim。检查实际数据和最终权限，而非只看消息名称。
3. 在 L2 原生端口拦截的 REQ/DAT/RSP 符合 Size、DataID、BE、DBID、CompAck、信用规则；不运行 `chi-refine` 也成立。
4. 随机打乱可独立响应、注入端口背压、提前/错误类型信用、ID 复用及 snoop/miss/victim 竞态，证明不会提前释放、丢字节或死锁。
5. 原有组件及 CPU 运行回归通过；trace 导出开关不改变 tick、CPU 结果、数据和缓存终态。

这些条目是新实现的完成门槛。现有桥接器测试、协议示例条数或文档本身不能替代上述运行证据。
