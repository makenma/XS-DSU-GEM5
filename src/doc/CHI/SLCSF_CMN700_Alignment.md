# SLCSF 与 CMN700 r3p0 的对齐依据

## 基准、配置与验证边界

参考源码：`/nfs/home/lilongfei/tl_test/cmn/CMN700_3x2_4C_SBSX`，源码头标识
`RELEASE: r3p0-00eac0`。生成顶层 `logical/cmn700/verilog/cmn700_3x2_4c_sbsx.v`
3868–3887 行给出 SLC size=1、SF size=3、16 ways、tag/data RAM latency=2、
DSLICE=0、POCQ=16、RNF ID width=2；分别对应 1024×16 SLC、4096×16 SF、4 RNF。
`logical/shared/verilog/cmn_hns_localparams.v:62` 定义普通 SEQ=8，ABF SEQ=4；
模型只实现前者。

附加文件《SLCSF微架构文档.docx》用于需求追踪；其混合了设计修订和不同配置的
阶段命名。发生冲突时使用上述 RTL。模型不包括 RAM latency=3、ECC/poison、
启动初始化扫描、Stash、跨 die、MTE、MPAM 和 ABF。

本次定向仿真实际编译并实例化了供应商的 `cmn_hns_l3_tagpipe`、
`cmn_hns_sf_tagpipe`、`cmn_hns_l3_datadp` 及其依赖，包含 31 个 RTL 模块。
没有将 RTL 公式重写成软件后充当参考实现。tag/data RAM 的返回端口由固定的
测试数据驱动，配置中的 ECC pipeline 被关闭；没有实例化整个 CMN、POCQ、
RNF/SN 或实际 SRAM。因此该参考验证流水控制、数据有效时刻、写使能、
状态结果及 replay 取消，**不声称验证了实际 SRAM 内容、完整协议或全芯片时序**。
fixture 将 runtime `ECC_DISABLE=1`、`SLC_ECC_PIPE_MODE_DIS=1`，其余配置输入
固定为 0；并未实例化配置寄存器的 reset 默认值。DRRIP 默认策略来自配置参数
及 RTL 的静态对照，由软件组件测试检查；这 14 项 RTL 流水轨迹没有覆盖完整
DRRIP 自适应训练或 SEQ 全生命周期。后续 review 增加 9 项 history/replay
轨迹和 13 项 SF SEQ/POC replay 边界轨迹；后者直接驱动 SF/POC 边界的 ACK/dealloc，
依然没有实例化 POCQ，也不将此称为完整 CMN 协议仿真。

## 周期约定与已实测结果

周期 0 是请求在 H1 满足 `request && !busy` 的准入阶段。随后寄存器推进一拍记为
周期 1。测试在下降沿之后观察稳定信号，避免 NBA 更新和驱动之间的竞争。
RAM 写使能在周期 N 有效，对应下一采样边沿完成写入，模型在 N+1 提交。
对外回调还需要遵守 HNF 明确规定的跨模块可见性边界。

| 事件 | 相对周期 | RTL 信号或位置 |
| --- | ---: | --- |
| SLC RAM 输出/比较阶段 | 4 | `l3_pipeval_hx_q[4]` |
| SLC hit、SF 查询 | 5 | `l3_hit_h5`、SF H5 |
| 汇合 replay、SF victim/SEQ 写判定 | 6 | `l3_replay_h6`、`sf_victim_val_h6` |
| SEQ valid 写入 | 7 | H6 写使能经 `sf_seqvld_q` 寄存器采样 |
| SEQ 请求寄存输出 | 8 | `sf_seq_req_h0`，物理读指针已指向该槽时 |
| POC lookup/replay 控制结果 | 7 | `l3_to_poc_tag_h9.tagrd_val` |
| 第一个 32B 数据有效 | 9 | `l3_hp_rd_data_owval_h11=01` |
| 第二个 32B 数据有效 | 10 | `l3_hp_rd_data_owval_h11=10` |
| 回流 SLC tag 写使能 | 10 | `l3_tag_wr_en_h1_q` |
| SLC SRAM 写端口有效 | 11 | `l3_tag_wr_en_h2` |
| SLC tag 提交 | 12 | 前述写端口下一采样边沿 |
| SF SRAM 写端口有效 | 12 | `sf_tag_wr_en_h3` |
| SF tag 提交 | 13 | 前述写端口下一采样边沿 |
| fill 前半行 SRAM 写使能 | 15 | `l3_qw0_lo_wr_en_h5_q`，同拍 QW1 |
| fill 后半行 SRAM 写使能 | 16 | `l3_qw2_lo_wr_en_h6_q`，同拍 QW3 |
| fill 完整行提交 | 17 | 后半行写使能下一采样边沿 |

H1→H2→H3→H3a→H4→H5→H6→H7→H8→H8a→H9 的周期依次为 0–10。
但是 `l3_to_poc_tag_h9` 在 DSLICE=0 时由 H7 直接驱动；data 输出名称 H11
也不代表该固定配置的相对周期。仅按信号后缀设置延迟会错三拍或两拍。
相应来源是 `cmn_hns_l3_tagpipe.sv:5016`、`:10511`、`:10990`，以及
`cmn_hns_l3_data_llib.sv:487`。

每笔准入请求都会造成周期 1 的 busy。普通 READ/EVICT 还在周期 9 形成 busy；
有回流更新时 busy 延续到周期 10、11。实测 `ReadUnique` 的四种 SLC/SF 命中
组合都包含 SF 分配或更新，busy 均为 `{1,9,10,11}`。被 POC replay 的请求只有
`{1,9}`，不会写 tag/data，也不会返回有效数据。无更新 `ReadOnce` 同样需要
保留普通请求的额外 busy。对应仲裁与 LAT=2 分支在
`cmn_hns_l3_tagpipe.sv:3891–4039`。

普通查询的 tag 更新经 H8 回流占用 H1 端口；fill 则以内部 FILL 类型继续第二轮
数据写入。不能让 `LookupDone` 等同于 tag/data 提交，也不能在最后一个数据
beat 或所需 tag 写入之前退休 POCQ 事务。

## 状态、替换与 SEQ 规则

SF 的状态描述上层副本，MESI 仅使用 I/S/E；SLC 的 M 表示当前持有 dirty 数据。
RNFID 是编码 ID，RNFVec 才是位图；tag 匹配及 SEQ 命中都包括 NS。

已由实际 RTL 定向仿真确认的文档歧义：

- `ReadClean` 请求者与 SF RNFID 不匹配时，SF EU→EN、SU→SN。
- SLC EN 或 MN 命中且 SF miss 的相关副本读，分配 SF EN。
- `CleanInvalid` 在 SLC dirty 和 SF 命中时，将两侧状态更新为 I，并返回两拍
  dirty 数据，不能使用 `SnpUnique` 代替该操作所需的 invalidating snoop。

前两项分别见 `cmn_hns_sf_tagpipe.sv:4515–4540` 和 `:4184–4198`。
测试 trace 的状态编码采用 RTL 的 `{unique,mesi}`：I=0、SN=1、EN=2、MN=3、
SU=5、EU=6、MU=7，不是 C++ enum 的内存表示。

SLC 优先使用最低编号的 invalid way。满 set 替换时将候选 way 的 RRPV 整体增加
`3-max(RRPV)`，从上一次 victim 之后开始选择第一个 RRPV=3 的 way，末尾回绕。
有实际 tag 更新的命中将该 way 的 RRPV 写为 0；无更新的 `ReadOnce` 命中不会
写 RRPV。实际 RTL 轨迹中 `l3_lru_wr_en_h2` 与 SLC tag 写端口同为周期 11，
无更新场景均为 0。来源是 `cmn_hns_l3_tagpipe.sv:15990–16170`、`:16698–16722`、
`:16877–16957`，不能在 lookup 回报时无条件更新 RRPV。

普通配置采用 DRRIP：SRRIP 插入值 2、BRRIP 插入值 3、source 插入值 0；默认
leader group 数为 1。1024-set 配置以 set 低 5 位的 0/1 分别选择 SRRIP/BRRIP
leader，其余 set 跟随 PSEL。PSEL 从 0 开始，在 [-512,511] 饱和；SRRIP leader
命中加 1，BRRIP leader 命中减 1，非负时 follower 使用 SRRIP。BRRIP 的 5-bit
计数器在相应 EVICT miss 时推进，每第 32 笔使用 SRRIP 插入值。
见 `cmn_hns_l3_tagpipe.sv:16455–16693` 和 `cmn_hns_cfg_params.v:103–107`。
上一次 victim 的选择掩码是共享寄存器，在 H4→H5 更新；PSEL、BRRIP 计数器
和 victim 掩码是策略寄存器，即使该尝试稍后 replay 也可能推进，不能误建模
成每个 set 独立的随机起点或等到 tag 提交才推进。

16-way SF 使用零种子的 4-bit LFSR，序列为
`0,8,4,2,9,12,6,11,5,10,13,14,15,7,3,1,0`，只在 victim 时推进；
候选由 next 值及寄存器产生。LFSR 在 H5 victim 判定时推进，后续 replay 不回滚。
见 `cmn_hns_sf_tagpipe.sv:6233–6236`、`:6328–6359`。
其他容量下的软件适配不代表与一个未仿真的 CMN 参数配置逐位等价。

SEQ 命中不等于 replay。`ReadUnique`、`CleanInvalid` 等失效/唯一性请求需要
广播；`ReadShared`、`ReadClean`、`ReadNotSharedDirty`、`ReadOnce` 在普通
SLC miss 条件下广播。命中还会更新被命中的 SEQ entry 的广播标志，防止之后的
victim 事务使用过时的精确 RNF 集合。见 `cmn_hns_sf_tagpipe.sv:5859–5881`、
`:7013–7034`。

持续访问超过 SF 容量时，还必须保证 SEQ 能进入 POCQ。HNF 的 RTL 为 SEQ
提供一个保留名额，见 `cmn_hns_localparams.v:75`、
`cmn_hns_link_ctl.sv:3684–3700`、`:4021–4023`。此前软件没有内部事务准入
保障：外部请求不断抢占刚释放的 POCQ 空位，最终 8 个 SEQ 槽全部占满，
所有前台事务因 SEQ full 重发，而内部 eviction 无处进入。固定 CMN 配置的
100000 请求测试在完成 65546 笔后复现；缩小到 4 sets × 4 ways 时，在完成
26 笔后复现相同循环等待。

当前 HNF 接口保留无 SEQ 时全部 16 个普通事务容量；出现未发送 SEQ、且
POCQ 内没有 SEQ 事务时，最后一个空位优先留给内部 SEQ，外部 REQ 在 H1
保持反压。SEQ 已在 POCQ 时不额外保留第二个空位。这实现内部 eviction 的
前进保障；完整 RTL 的静态池、动态池和 QoS dedicated 计数仍属于简化部分，
不将该接口策略表述为全部池分配时序逐拍相同。`seq_pressure` 回归使用
4×4 SF 连续驱动 1000 笔独立地址读，并检查 984 次 eviction snoop 和 1000
次 SN read；原 `tx_backpressure` 场景继续检查无 SEQ 时 16 个 entry 全占满。

压力回归还暴露了另一处循环等待：普通 `SnpCleanInvalid` 子图在 snoop
收齐后追加 `FlushSf`，内部 SEQ 原本仅在拿到 SLCSF 仲裁后才跳过这个操作。
若较老的前台 lookup 不断因 SEQ full replay，这个无实际 tag 操作的动作便
永远拿不到仲裁，已经进入 POCQ 的 SEQ 也无法退休。现在内部 SEQ 在生成
动作时直接省去 `FlushSf`：它持有被替换的 SF 快照，不应再访问替换后的 tag。
普通 CMO 仍产生真实的 SF 操作。另在退休释放空位后显式唤醒下一拍 POCQ，
确保剩余前台事务都在等待 ACK 时，待发 SEQ 仍能使用刚释放的名额。


SEQ 的 valid、sent 与 POCQ 中的内部事务是不同生命周期。H1 ACK 后一拍
寄存 sent；无 hazard 时 H2 dealloc 经 SF H3 寄存器再更新 valid，因此 ACK
后第 3 拍释放槽位。有 hazard 时保留到 POCQ wake 的 dealloc 输入下一拍。
见 `cmn_hns_poc_ctl.sv:45361–45440`、`cmn_hns_sf_tagpipe.sv:6870–6890`、
`:7044–7060`。SEQ 槽释放不表示写回事务已完成。

普通 SEQ 只允许 8 项有效槽位，但 RTL 的物理读指针仍遍历 12 槽，包括关闭
的 4 个 ABF 槽。请求经过 H0 输出寄存器后才能被 POCQ 接收；不能直接从
valid 集合挑出最老 ID。定向轨迹中复用的 slot 0 在周期 47 置 valid，指针从
1 走完其余槽，到周期 59 才再次发出该槽请求。

H5 SEQ hit 在 H6 更新保守的 broadcast 元数据，即便本次普通事务在 H6
replay 也不能回滚这项更新。一般 Fill 不是 SEQ 查询，不能把它或显式维护
操作统一当作普通 READ。NoSnp 命中 SEQ 仍可扩大后续 victim 的广播标志，
但当前 NoSnp 请求自身不发 snoop，而是返回 no-fill。元数据更新与当前请求
的 snoop 决策是两个不同输出。`ReadNoSnp`、`WriteNoSnpPtl`、
`WriteNoSnpFull` 在 SF/SEQ 命中时返回 `SlcSfLookup.noFill=true`；NoSnp
写入也不会按 WriteUnique 的规则失效现有 SF 目录。依据为 SF
`:4470–4490`、`:5859–5881`、`:6067–6072`。新增实际 RTL case 确认
WriteNoSnpFull 在 H5 命中 SEQ 后，H6 的 SEQ broadcast 元数据为 1，
当前请求的 `snp_brd/snp_dir` 都为 0，`nofill` 为 1。

休眠的 POCQ SEQ 事务在唤醒执行前，按 SEQ ID 重新取得当前目录快照，再释放
valid 槽位。更早排队的同地址请求可能已命中 SEQ、获得 dirty 数据并将目标改为
广播；继续使用初次接收时的目标集合会遗漏新 owner。`seq_broadcast_hazard`
用三笔交错事务检查这个生命周期。

普通 coherent 请求默认不侦听请求者自己。`CleanShared` 的普通 SF snoop 仅
针对 EU 且 RNFID 不匹配的副本；SF SU/EN/SN 不要求该定向 snoop，SEQ 命中广播
另行处理。见 `cmn_hns_sf_tagpipe.sv:5900–5988`。
`snoopMe=true` 对普通 `CleanShared`、`CleanInvalid`、`MakeInvalid` 生效，
由 POC 在 SF 结果之后将请求者加入目标集合；即便两侧都 miss，也会生成一笔
针对请求者的定向 snoop。这不会改变 SF 内部的 RNFID 比较和状态迁移。
见 `cmn_hns_poc_ctl.sv:31337`、`:48764`、`:41414–41432`、`:51807`。

SLC/SF set-way 历史、SEQ full、H6 写槽已有效以及 POC victim 冲突
都可能 replay。两侧 replay 必须合并后共同取消写入及数据返回，不能只取消
触发冲突的一侧。H6 槽冲突单独来自 `sf_seqwr2vld_h6`，其判定见 `cmn_hns_sf_tagpipe.sv:6946–6955`、`:8091`；
两侧合并见 `cmn_hns_l3_tagpipe.sv:14676`。

LAT=2、ECC pipeline 关闭时，两侧 history 都在 H6 汇合判断后，于周期 7
写入第一级，并沿四级寄存器保持至周期 14；周期 15 的采样边沿清空最后一级。
它们不在 tag 提交时释放，也不延长到 fill data 提交时。见 SLC
`:14348–14353`、SF `:8050–8054` 的逐级使能。新增 RTL 轨迹确认：SLC-only
`ReadOnce` 在周期 0/8 准入时第二笔 replay，0/10 时两笔都成功；同 way fill
在周期 0/12 准入时，两笔均无 history replay。原模型以 +12/+13/+17 作为
history 到期时刻，分别造成漏 replay 和多余 replay，现已修正。

`CMN_HNS_AUX_CTL_1_HNS_SF_PRECISE_SETWAY_EN_BIT_RESET_PARAM=0`
（`cmn_hns_cfg_params.v:141`）时，SF 所有普通 READ/EVICT 都要保护选中的
way，包括不会分配的 miss。SF 满 set、目标 miss 的连续 `ReadOnce`，仍会
因选择相同 replacement way 而 replay。replacement candidate 的计算与
实际分配、SEQ 写入、LFSR 推进分别处理；不分配的 miss 不推进 LFSR。
依据为 SF `:6571–6594`、`:6666–6679`、`:8073–8097`。

invalid-way 选择还包含未进入 history 的原始流水保留：SLC 在 H3→H4
采样 H5/H6 及 history，SF 在 H4→H5 采样 H6 及 history。即便前一笔将在
H6 replay，其原始 way 仍可能使后一笔选择另一个空 way。新增实际 RTL
场景确认，周期 0 的 fill 被 POC replay，周期 2 的 fill 使用 way 1，而不是
立即复用 way 0。见 SLC `:13400–13448`、SF `:6477–6529`。

软件执行顺序也不能改变 RAM 的读旧值语义。周期 0 的 invalidate 在周期
12/13 写 tag，周期 8 的请求已在此前发出 RAM 读取；后一笔必须保留旧 tag
命中并在 H6 replay。模型将本拍所有 tag 读取放在本拍提交之前，data 则按
RAM 读使能的采样边沿在 +6/+7 分别保存 32B，避免提前读取尚未写完的 fill。
实际 SRAM wrapper 依据为 `logical/models/rams/generic/cmn_hns_l3_tag_way_ram.sv`
`:122–130`、`:279–292`；新增组件测试检查这两个读写重叠边界。固定 RAM
输出的 RTL fixture 仅验证相关控制/replay 轨迹，不宣称动态 SRAM 内容验证。

这里需要修正原计划的一项解释：H5 寄存的是 full 判定，`sf_seqwrptr_q` 虽然带
`q` 后缀，却由 H6 当前 valid 向量经过组合 `por_find1_from_lsb` 选择，见
`:7149–7189`。它不是 H5 预留槽位的寄存器。普通请求隔拍准入、ABF 关闭的
范围内，没有通过合法外部请求独立触发该分支的定向轨迹；模型保留防御性
`SeqSlotBusy` 检查，不将其表述为已经完成 RTL 动态覆盖的场景。

POC victim 冲突比较的是当前流水请求地址与已经存在的 POCQ SLC victim
buffer 地址，见 `cmn_hns_poc_addr.sv:731–747`，不是本次尚未选出的 victim。
模型按请求范围为地址冲突加入 NS 隔离；供应商代码的这一 victim 分支只检查
地址，旁边的 snoop hazard 分支同时检查 NS，应区别对待。

这个比较也不能在 H1 固定成一个布尔值。POC 在 H3（相对周期 2）比较
当前 buffer，下一拍 H4 输出 `hp_replay_h4`；LAT=2 分支再延一拍后进入
SLC H5/H6。来源为 `cmn_hns_poc_addr.sv:731–770`、
`cmn_hns_l3_tagpipe.sv:5059–5069`、`:5181`、`:14401`。新增 6 项 RTL
脉冲扫描在相对周期 0–5 分别驱动 `hp_replay_h4`，只有周期 3 的脉冲在
周期 7 产生 replay。HNF 模型按 buffer 分配/释放的下一拍可见窗口记录
生命周期，SLCSF 查询相对周期 2 的快照；既不会漏掉准入后产生的冲突，
也不会把 H3 之后才产生的 victim 误判为本次 replay。

该冲突需要在 **POC H3（准入后 +2）** 比较。`poc_addr:750–770` 将结果
寄存为 +3 的 `hp_replay_h4`；LAT=2 分支再经
`l3_tagpipe:5059–5069` 的寄存器对齐到 SLC H4，并在 `:14401` 进入 H5，
最后于 H6 合并。新增实际 RTL 脉冲扫描分别只在 +0 至 +5 拉高外部 replay
输入，只有 +3 的脉冲使该事务在 +7 返回 Replay。这里的周期来自实际配置
分支和轨迹，不能按 `hp_replay_h4` 的名称将比较时刻设为准入后 +4。

原模型在 H1 固定 `victimConflict`，遗漏 H1 后产生的冲突，也会将 H3 前已
释放的 buffer 错当成冲突。现在 H6 的查询接口读取 `acceptedCycle+2` 的
POCQ buffer 历史。`PocqVictimBuffer` 记录分配和释放的有效区间；HNF 在
周期 C 的修改从 C+1 开始可见，已释放记录至少保留 7 拍，直到所有仍在流水
中的 H3 查询完成。因而 H6 时已经释放的 buffer 仍能按 H3 的实际状态触发
replay，当前 tick 内 HNF 与 SLCSF 的执行先后不会改变结果。

窗口比较按 64B cache line 与 NS，排除本 POCQ 事务自己的写回。owner 使用
`{SrcID, TxnID}`，POCQ 分配已拒绝同一对标识的并存事务；旧事务只有在写回
数据发完且 SN completion 收到后才释放 buffer，且所有写回完成前不能退休。
标识重用时，旧窗口的结束时刻早于新事务 H3，不会把旧记录误认为当前 owner
的有效 buffer。组件测试覆盖晚于 H1 的分配、早于 H3 的释放、H3 后的分配、
H6 前释放但 H3 曾有效，以及 NS、自身 owner 和同 line 不同 offset。

这里的原生 RTL fixture 直接驱动 POC/SLCSF 边界的 replay、ACK 和 dealloc
信号；它确认 SF 寄存器及 LAT=2 对齐链，不实例化完整 POCQ。POC H3 比较来源
另由上述 `poc_addr` 寄存器关系确认，软件窗口及完整协议生命周期由组件和
HNF 端口回归验证。


连续同 way 请求的定向 RTL 轨迹已
确认首笔成功、随后三笔 replay，且只有首笔产生实际写使能。

## 重现与验收证据

在仓库根目录运行：

```bash
python3 tests/gem5/chi/slcsf_rtl/run_reference.py \
  --rtl-root /nfs/home/lilongfei/tl_test/cmn/CMN700_3x2_4C_SBSX \
  --verilator /usr/local/bin/verilator --work-dir /tmp/slcsf-cmn-reference
python3 tests/gem5/chi/slcsf_rtl/check_reference.py \
  /tmp/slcsf-cmn-reference/reference.json
python3 tests/gem5/chi/slcsf_rtl/run_timing_reference.py \
  --verilator /usr/local/bin/verilator --work-dir /tmp/slcsf-timing-reference
python3 tests/gem5/chi/slcsf_rtl/run_seq_reference.py \
  --verilator /usr/local/bin/verilator --work-dir /tmp/slcsf-seq-reference
```

`run_reference.py` 只从外部读取 RTL，运行时在临时目录生成绑定和 testbench；
不会修改供应商源码树。仓库只保存原创 fixture、测量结果和源码 SHA-256，
不保存供应商 RTL。`expected.json` 保存经过检查的事件轨迹、参数及 source
digests，`check_reference.py` 逐项检查。重新生成的完整逐周期轨迹、编译日志和
仿真器版本保存在工作目录。基准采用 Verilator 5.048。

覆盖包含四种 `ReadUnique` 命中组合、fill、POC replay、四项副本读状态规则、
`CleanInvalid` dirty hit、连续同 way 冲突和两项无更新 `ReadOnce`。
追加 timing fixture 覆盖 +14 history 尾部、不分配的 SF miss、H5/H6 原始
way 保留和 fill 完成前的 history 释放。SEQ fixture 覆盖 valid/sent/request
边沿、两类 dealloc、replay 下的元数据更新、物理槽轮询和 NoSnp，另包含
6 项外部 victim replay 脉冲时刻扫描，共 13 项。
完整的 POCQ hazard 睡眠唤醒、下游反压及 RNF/SN 协议闭环仍由 gem5 组件和
集成测试验证；当前 RTL fixture 不宣称覆盖这些完整流程。

## 模型接口与本地回归

`SlcSf` 是独立 `ClockedObject`，其 `SlcSfModel` 内核也可脱离 gem5 事件队列逐拍
驱动。调用方使用 `tryAccept()`；返回 false 时保留待发 action，下拍重试，不能
丢弃请求。请求的 token 标识一次流水尝试，与可重用的 CHI TxnID 分开。请求和
victim 持有自己的 64B 数据快照；返回的 `DataBeat` 持有 32B 内容，不引用调用方
临时 buffer。

`allocate` 控制是否允许将数据分配到 SLC；`noFill` 是协议 no-fill 属性，决定
某些写请求的失效发生在查询还是 fill 阶段，二者不能互相替代。普通 lookup
不分配 SLC 数据时仍需正常维护 SF。`snoopMe` 默认 false，限定于上述普通 CMO。

结果类型的边界：

- `LookupDone`：命中、原状态、snoop 目标、memory-read 和 victim 信息已确定。
- `DataBeat`：一笔请求的前半行或后半行，victim 数据带独立标记。
- `CommitDone`：该请求实际所需的 tag/data 写入均完成；无写入请求也会产生
  完成事件，不能把它与协议响应或 `CompAck` 混为一谈。
- `Replay`：该尝试的两侧更新均取消；POCQ 保留原事务并用新的 token 重发。

HNF 从 SLCSF 取结果时要求结果周期严格小于当前周期，使跨模块结果固定在下一拍
可见，消除同一 tick 中事件注册顺序的影响。POCQ 完成还必须满足数据收齐、
必要写回完成以及协议应答等条件。

LinkLayer 也只读取早于当前 tick 入队的 RX flit，发送时只使用此前周期已
返回的 TX credit；当前 tick 中刚返回的 credit 留到下一边沿。这样已经排好
的 HNF 事件不会因 tester 在同一 tick 提前执行而提前接收数据或重复利用
credit。该修复校正了旧接口未定义的同 tick 可见性，不能计为保持旧时序的
算法性能优化；性能对照应在这些边沿修复之后建立。


原有容量默认值保持为 SLC 1024×16、SF 2048×16、1 RNF、64B line。
`configs/example/chi_hnf_slcsf.py` 显式选择本文件使用的 CMN 对照容量：
SF 4096×16、4 RNF、16 POCQ entry。
该端口环境使用 RNF ID 0–3、HNF ID 4、SN ID 0x100；RNF ID 直接作为目录编码。

构建及执行：

```bash
scons -j8 build/RISCV/gem5.opt \
  build/RISCV/mem/cache/CHI/pocq_state_graph.test.opt \
  build/RISCV/mem/cache/CHI/slcsf_model.test.opt \
  build/RISCV/mem/cache/CHI/chi_channel.test.opt
build/RISCV/mem/cache/CHI/pocq_state_graph.test.opt
build/RISCV/mem/cache/CHI/slcsf_model.test.opt
build/RISCV/mem/cache/CHI/chi_channel.test.opt
python3 tests/gem5/chi/run_slcsf.py
```

集成 runner 定义了端口级场景，包括 clean/dirty、两侧命中组合、NS 隔离、同 line
不同 offset 的 hazard、发送反压、SF eviction 和 SEQ hazard 唤醒。每个场景有
独立 watchdog，运行输出和 `summary.json` 位于 `m5out/slcsf-regression/`。

## 2026-10-21 首轮交付记录（后续 review 前）

| 验证层 | 结果 |
| --- | --- |
| `gem5.opt` 和三个 GTest 目标构建 | 通过 |
| SLCSF 组件 | 40/40 通过 |
| 原有 POCQ 状态图及修正 | 7/7 通过 |
| DAT 数据生命周期 | 3/3 通过 |
| ASan/UBSan 与容器断言（组件及 DAT） | 43/43 通过 |
| CMN RTL 参考轨迹 | 14/14 通过 |
| HNF 端口主场景 | 22/22 通过 |
| 响应延迟变化 | 0 拍配置的 3 项、17 拍配置的 1 项通过 |

反压场景先阻塞所有 TX 通道，再恢复控制通道并继续阻塞 DAT，实际检查 16 个
POCQ entry 全部占满，最终完成 24 笔读请求且数据内容一致。
`seq_broadcast_hazard` 完成 3 笔请求、8 次 snoop 和 1 次 dirty 写回，验证休眠
SEQ 在所有权变化后的目标刷新。CleanInvalid completion 同时检查 tag 已失效、
dirty 数据已到达 SN 且写回收到确认；ReadUnique 在 CompAck 前保留事务。

记录位于 `m5out/slcsf-regression/summary.json` 及其 `validation/` 子目录，后者
包含单测 XML/日志、sanitizer 日志、完整 RTL 轨迹、构建日志和带源码 SHA-256 的
`report.json`。补充时序运行位于 `m5out/slcsf-regression-zero-delay/` 和
`m5out/slcsf-regression-slow-dirty/`。ASan 运行设置 `detect_leaks=0`，因为当前
沙箱不支持 LeakSanitizer；地址和未定义行为检查已执行，不包含泄漏检查。


## 2026-10-21 RTL review 修订验证

此次修订的独立 SLCSF 组件测试为 **56/56 通过**；SEQ/victim 专项实际 RTL
测试为 **13/13 通过**，其中 7 项覆盖 SEQ 行为，6 项扫描外部 POC replay
脉冲的对齐窗口。timing/history 专项为 **9/9 通过**，保留原有 14 项流水参考
场景。以上结果属于各自验证边界，不能用组件或固定 RAM 接口 fixture 替代
整个 CMN 的动态 SRAM/POCQ 仿真。

SEQ 压力修复后的端口仿真已通过：4×4 SF 下 1000 笔连续独立地址读在
9952 周期完成，产生 984 次 eviction snoop；固定 CMN 配置下 100000 笔读
在 609495 周期完成，产生 34464 次 snoop。两者 SN read 数都严格等于
前台请求数，无重复 read。对应日志为
`m5out/chi-review/validation/seq-pressure.log` 和
`m5out/chi-review/validation/throughput100k.log`。100000 笔流量在 tester
改为同 tick 提前执行后，完成周期、snoop/read 数及逐事件摘要仍完全一致
（`365e1612afc56063`），见 `throughput100k-early.log`。

专项原始结果分别位于 `/tmp/slcsf-seq-review-tests.log`、
`/tmp/slcsf-seq-reference/reference.json`、
`/tmp/slcsf-seq-reference/trace.log` 和
`/tmp/slcsf-seq-reference/build.log`。`run_seq_reference.py` 对具体寄存边沿
逐项断言，并记录参与模块及 POC 对照源码的 SHA-256。统一 gem5 构建、HNF
端口回归和仿真性能结果由本轮交付的总验证报告另行记录；上节首轮记录保留
原来的验证版本，不充当修订后版本的验证结果。
