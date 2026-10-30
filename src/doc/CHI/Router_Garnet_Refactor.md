# CHI router 移植、简化与验证

## 来源与边界

移植来源是 `/nfs/home/majunhong/project/chiron/XS-DSU-GEM5/src/mem/cache/CHI/ChiRouterRefModel.*`。
移植前 `.cc` SHA-256 为
`d3bb94340d453dc24f9ac6a3e3df5656fab8c755166c61991fc423bff67f082d`，
`.hh` 为 `cee58fa175a2334b7844c09c5613f0d70d7798a0fefb0e4bf2a14e8f912115a1`。
原件保存在本次工作目录外的 `/tmp/chi-router-original/`，并未修改来源仓库。

参考本仓库 Garnet 的 `Router.cc`、`InputUnit.cc`、`OutputUnit.cc`、
`SwitchAllocator.cc`，采用输入缓冲、路由计算、输出请求收集、仲裁和输出提交的分层。
保留 CHI 的四通道、4 个 P × 4 个 D 接口、8 个方向接口、单双 lane、
共享 FIFO/按输出分组队列、XY 路由、显式 route table 以及既有阶段顺序。
这是 CHI flit 模型，没有引入 Garnet 的 Ruby 协议、VC 分配或 packet/body/tail 语义。
本文的阶段基线来自原 C++ reference model，不表示已完成 CMN router RTL 逐拍对齐。

## 等价结构与算法改造

`ChiRouterUnits.hh` 提供独立于 SimObject 的四个单元：

- `ChiRouterRoutingUnit` 保存静态路由和兼容路径的事务别名。源 ID 的静态路由已等于
  实际入口时，无需分配每事务记录；复用同一 key 时擦除旧别名。需要别名的请求仍保持
  原 `(source ID, transaction ID)` 查找优先级。**兼容别名路径尚无事务退休通知，长期
  使用不同 transaction ID 仍可能增长**；普通使用正确 node ID 的流量没有该增长。
- `ChiRouterInputUnit` 按配置只分配一组共享 FIFO 或一组按输出分组队列，维护 O(1)
  payload/control 计数。原模型同时构造的两套队列以及未使用的 `outQ/outQPdev`
  已删除。方向入口控制流水保存目标、route field 和序号，不再复制完整 DAT。
- `ChiAgeArbiter` 用固定优先序保存 least-recently-granted 的全序，获胜者移至最后。
  原 age matrix 的 O(N²) 选择改为 O(N)。该策略与 Garnet 的 round-robin 不同；
  本次保留原仲裁赢家。输出请求先按输入收集一次，再按输出选择赢家，避免重复检查
  每个输入队首。
- `ChiRouterOutputUnit` 保存每个 D endpoint 的 FIFO，并以同一个总计数限制整个
  P 出口容量。选择规则仍为原来的 D round-robin；每次查看对应 D 的队首，不再
  对共享 deque 做查找、中间删除，以及 credit 不足时插回队首。

第一轮共享 FIFO 模式，router 自有的 deque 对象从 2,848 个减到 384 个（不包含端口队列）；
按输出分组模式从 2,848 个减到 2,496 个。第二轮去掉 16 个单槽 merge deque，
为按 D 的 PTX 增加 48 个 deque，最终分别为 416 和 2,528 个。PTX 仍共享原
`p_tx_depth` 容量，未将容量按 D 倍增。所有 buffer 容量及 credit 的功能修正见下节，
不将这些修正混作“原错误行为的等价优化”。

## 修正的继承问题

1. 原 `updateCreditFromIf()` 每周期根据 peer 有任意 credit 就补 shadow credit，
   没有对应归还事件。现在发送以真实 peer buffer credit 为准，仅成功入队消耗一次。
2. 方向入口将 flit 移入内部队列时过早归还 wire credit，使阻塞时内部队列无界。
   现在 RX/TX 两种方向均保留所有权，到实际输出提交才归还；local ingress 仍按原
   有限 input credit 控制进入 P 输入缓冲。
3. merge 到 TX 之间的 `pPipeQ` 原本可无限积压，现在是一个有限流水槽。
   下游阻塞逐级传回 candidate/input/wire，不能在软件队列中无限吸收流量。
4. 空闲判定遗漏 local credit pipeline，队列清空后可能停止推进尚未归还的 credit。
   现在未完成的 credit 寄存器也保持流水运行。负 occupancy、credit 下溢/上溢均直接
   报错，不再 clamp 掩盖计数错误。
5. single-lane 中 primary/EXT 方向入口共享 logical input；原聚合 control/data 计数
   可能把 src4 的控制容量用于晚一拍到来的 src0 payload，造成队首错配、重复 grant
   或死锁。现在每条物理入口保存已准入的 control token，下一拍只推进相同序号和
   目标的 payload。控制和 payload 阶段均按 `(cycle, physical source)` 顺序执行；
   payload commit 不停顿，并断言新 token 不覆盖旧 token。未来若增加 payload
   阶段停顿，必须继续保持此合并顺序。
6. `local_ports` 目标有连接但无 credit 时，原模型可能改投同 P/D 的 `device_ports`。
   现在接口由连接拓扑固定，不能因目标反压而改变接收者。
7. 同 tick 的事件先后可能改变 flit/credit 的可见周期。端口记录 flit 的接受 tick，
   router 只读取更早 tick 的 flit；严格 peer credit 查询排除当前 tick 刚归还的数目。
   router 使用独立时钟事件，端口回调只安排下一 edge。端口的默认 dequeue、
   enqueue 和 `hasTxCredit()` API 保留；本轮 HNF/LinkLayer 也显式采用严格
   flit/credit 查询，并用 normal/early 两种事件顺序检查逐拍一致性。

以上修正会改变原错误拥塞场景的行为与周期；保时序要求适用于结构和算法优化，
不能用原先丢包、无界吸收或错误 credit 的轨迹作为正确时序基准。

## 阶段与可见周期

以 router 实际采样输入为 cycle 0：

| 路径/动作 | 周期 |
|---|---:|
| local 请求采样 | 0 |
| local P/D 注入仲裁 | 1 |
| local 来源输出仲裁 | 2 |
| local → 方向出口提交 | 3 |
| internal 控制采样 | 0 |
| internal payload 与输出仲裁 | 1 |
| internal → 方向出口提交 | 2 |

local 目标出口在输出提交后经过 candidate/merge/TX，实际发送再晚一拍。
无竞争时，从 upstream port 接受到 downstream endpoint 可见：local 来源方向目标
为 5 拍，internal 来源方向目标为 4 拍；local 目标分别为 6、5 拍。
local input credit 从输出 grant 到可再使用仍保留 single-lane 2 拍、dual-lane 3 拍。

## 验证与复现

```sh
scons build/RISCV/gem5.opt build/RISCV/mem/cache/CHI/chi_router_units.test.opt -j32
build/RISCV/mem/cache/CHI/chi_router_units.test.opt
python3 tests/gem5/chi/run_router.py --check-event-order --out m5out/chi-review/router
g++ -O3 -DNDEBUG -std=c++17 -Isrc src/mem/cache/CHI/ChiRouterUnits.bench.cc -o /tmp/chi-router-units-bench
/tmp/chi-router-units-bench 10000000
```

组件测试穷举 2/4/8 路所有优先序及请求掩码，检查原 age matrix 与新全序仲裁的赢家
和 grant 后关系；8 路包含 40,320 × 256 种组合。共享 FIFO、按输出分组队列分别与
独立队列参考随机比较 50,000 步，包含控制先于 payload 的行为。路由测试覆盖普通
100,000 个事务无 alias 增长、显式静态映射、每事务别名和 transaction ID 复用。

真实端口测试包含 `timing/alias/congestion/idle/throughput`，每项覆盖单双 lane 与
两种队列模式；congestion/throughput 另有第二个实际 router 的方向链路。检查完整
DAT 内容及借用缓冲的所有权、重复/错误接收者、最终所有 wire credit 回收和长反压
时有限存储上界。`--check-event-order` 将 endpoint 与 router 的同 tick 顺序反转，
比较全部接受/交付事件的周期、顺序摘要。每次测试保存 `trace.csv`、`run.log` 和
`result.json`，矩阵结果见对应目录 `summary.json`。

第一轮最终结果：组件测试 8/8、ASan+UBSan 组件测试 8/8、实际端口矩阵 56/56 全部通过；
28 组正常/反转事件顺序的周期及完整事件摘要一致，
`m5out/chi-review/router/summary.json` 的 `event_order_mismatches` 为空。
前期旧模型暴露的 primary/EXT 控制错配和反压时改投另一 endpoint 均已有定向回归。

独立仲裁 kernel 的初测为 5 次 × 10,000,000 步，所有运行结果摘要一致：原 matrix
中位 0.638 秒，全序实现中位 0.205 秒，约 3.12 倍。该数字只描述仲裁算法，不能当作
完整 gem5 或 CPU workload 的加速比；完整仿真性能由主任务的独立测量报告给出。
旧测试器直接探测 enqueue 的容量，新测试器按下一 edge 可见的 peer credit 注入；
二者的接受轨迹不同，因此没有将两个测试器产生的整机运行时间作为等价性能比较。

## 第二轮保时序性能改造

基线使用第一轮全部功能修复完成后的 binary，保存为 `/tmp/chi-review-round1.opt`；
对应源码保存在 `m5out/chi-review-round2/baseline-source/src/mem/cache/CHI`。
第二轮沿用相同 tester、流量、端口反压和记分逻辑，不复用前期不同测试器的速度数据。

- 在初始化完成绑定后缓存已连接入口，并保留原先按 source、接口和 RX/TX 的
  采样顺序；采样直接构造队列中的 payload，省去一次完整 `RoutedFlit` 拷贝。
- VOQ 在入队/出队时维护 payload/control 非空位图，仲裁通过位图交集获取请求。
  共享 FIFO 仍按队首判断，允许 internal control 先于 payload；不能套用 VOQ 规则。
  输出请求与未提交 grant 也使用位图，按原输出编号升序处理。
- 仲裁、输出提交和 credit 检查引用已有 payload，只在存储所有权转移时复制。
  单槽 merge 用 `optional` 表示；PTX 的每 D FIFO 消除线性查找和中间 erase。
  原共享 deque 的失败重插仅改变不同 D 之间的排列，不改变任一 D 的投影序列，
  因而分队列保持每拍的 RR 赢家和 shared-capacity 反压。
- 每通道 payload 计数在端口采样时增加，在成功 egress 时减少；control、grant
  和各内部队列中的对象始终归属同一个在途 payload。该计数用于 O(1) 活跃检查，
  通道所有 payload 与 credit 尾均清空时才跳过空操作。credit 尾包括单 lane 下
  未消费但尚未移出的 H2。**这些计数和位图是宿主执行的缓存元数据，不增加流水级。**
- 仍对 credit 阻塞的 flit 每拍推进；真正空闲时检查端口中的当前 tick 到达项，
  即使它们尚不能由严格可见性采样，也保留下一拍。Consumer 唤醒和严格
  flit/credit 可见周期沿用基线。

第二轮独立单元与 ASan+UBSan 均 9/9 通过。新增按 D 输出缓冲与原 shared deque
的 150,000 周期随机对照，覆盖 1/4/16 总容量、不同 endpoint credit、失败重插和
RR 选择；仲裁 mask API 也执行相同全部优先序/请求掩码穷举。
完整仿真的等价检查还应逐项比较所有 router/relay 统计（包括 stall cycles），
并确认每个接受与交付事件的周期摘要相同；测时独立交替运行 baseline/candidate。
