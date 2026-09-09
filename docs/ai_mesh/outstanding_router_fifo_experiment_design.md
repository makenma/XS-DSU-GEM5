# Outstanding 与 Router FIFO 联合实验

## 合同与入口

原 H5/H10 实验的研究问题、硬件范围、搜索阶段与验收规则保存在
[实验要求](../../src/doc/ai_mesh/mesh_outstanding_buffer_experiment_requirements.md)。
东侧双 HBM 实验采用本节所列 profile 与拓扑注册表；每次运行的参数、
实际配置集合和结果通过交付索引追溯至对应的 `launch.json` 与 measurement。
AXI 合同见 `src/doc/ai_mesh/AXI_GARNET_CODEX_SPEC.md`；Mesh IR 见
`src/doc/ai_mesh/TORCH_EXPORT_MESH_IR_CODEX_SPEC.md`。
Dummy Core 的 Gate 定义仅采用
`src/doc/ai_mesh/DUMMY_AI_CORE_AGENT_CODEX_SPEC.md` §18，不能与前两份规格混用。

东侧双 HBM 配置采用
[east_dual_hbm_32B_profile.json](../../configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json)
和 [Topology.layouts](../../util/mesh_ir/mesh_ir/experiment/config.py)。
`data_header_sideband` 的序列化语义以
[axi_packetization.cc](../../src/mem/axi/axi_packetization.cc) 为准；32 B 是数据通路宽度，
W/R 元数据在该模式下不占数据串行周期。Garnet 的路由与消息字段保留在 flit/message
对象中；该模型没有定义完整 RTL 控制位编码、布线面积或元数据 SRAM 宏成本。

## 基线与能力审计

基线分支为 `feature/ai-mesh-mesh-ir`，HEAD 为
`773659c8a67a156a81244849ff9d915d90ba3c80`，包括用户当前未提交工作树。
原有修改保留，不以历史报告中的 SHA 替换当前实现。

| 能力 | 代码入口 | 验证范围 |
| --- | --- | --- |
| 独立读写 DMA、有限 segment/ID、SRAM commit | `src/dev/ai_mesh/axi_tensor_dma_engine.cc` | 单 descriptor 内流水；每方向 descriptor FIFO 串行 |
| 实际 AR→RLAST / AW→B outstanding | `src/mem/axi/axi_initiator_adapter.cc` | 窗口跨全部目标合计；不采用 DMA submit 计数替代 |
| 逐 beat AXI/Garnet 传输 | `src/dev/ai_mesh/axi_garnet_bridge.cc`, `src/mem/axi/axi_garnet_endpoint.cc` | 正式 profile 使用有限每周期 W admission |
| 接收 VC 与上游 credit | `src/mem/ruby/network/garnet/GarnetNetwork.cc` | 从实际接收端解析，NI 容量独立冻结 |
| workload/ABI 校验 | `util/mesh_ir/mesh_ir/builder.py`, `src/dev/ai_mesh/mesh_program_loader.cc` | 不引入实验专用 ABI 编解码 |
| mandatory Gate membership | `util/mesh_ir/mesh_ir/acceptance.py` | 由实际 manifest 派生，不复制项目计数 |

64 KiB、16-beat 的单 descriptor 有 128 个完整 burst。保留既有
descriptor 串行语义时，N=256 不属于该 workload 的可实现窗口范围；
N=128 的尾部排空代价需要单列，不作为网络饱和证据。

## 实现边界

实验配置与成本、计划及独立流量 oracle、测量与推荐放在
`util/mesh_ir/mesh_ir/experiment/`，配置层调用生产 DMA 和现有 loader。
Router 仅处理 vnet、VC、端口和容量，不引入 AXI 类型。

接收端容量与发送 credit 由同一次解析产生。异构 map 使用实际
建链结果中的 input-port ID；uniform 探针导出的端口图是后续配置依据，
不假设 router 都有五个输入端口。

内存服务使用可选的共享读写 SyntheticHbmBackend。未启用时保留
现有协议验证后端；正式测量须记录 enabled 状态、服务速率和队列。
它不是带 bank/row/refresh 的真实 HBM 模型。

## 测量与交付

### Router 统计口径

字段由 `src/dev/ai_mesh/mesh_experiment_observer.cc` 导出；状态采集入口为
`InputUnit::accountVc` 与 `SwitchAllocator::send_allowed/wakeup`。

| 字段 | 单位与含义 |
| --- | --- |
| `time_histogram[k]` | 该 VC 占用为 k flits 的累计 router cycles；快照补齐当前区间，不清零 |
| `credit_stalls` | allocator 实际检查时因缺 credit 失败的次数 |
| `no_vc_stalls` | allocator 实际检查时因无可用输出 VC 失败的次数 |
| `sa_lost` | SA-II 已提出的请求未获 grant 的次数 |

后三项不是所有 ready VC 的完整等待周期，也不包含未被 SA-I 检查的
VC 等待时间。不能单凭它们断言某 input buffer 需要加深；瓶颈判断须结合
时间直方图、full 比例、链路流量及端点背压。所有字段仅观察，不额外调用
仲裁或改变 grant。

### 交付索引

固定资源 profile、workload、有效配置、容量图和代码/构建身份均参与
结果 identity；只有独立 verifier 成功的同身份结果可续用。
理论上限与实际 FULL_TIMING 测量分开。

原始数据、搜索预算、ROI 冻结值、已测集合及未执行空间的最终索引
见 [最终报告](../../src/doc/ai_mesh/goal_mesh_outstanding_buffer_experiment.md)。报告中的推荐必须引用
可直接实例化的完整 map，不能用文字深度摘要替代配置。

断点恢复的物理配置与请求关联见
[恢复审计](../../.tmp/mesh-outstanding-experiment.JMEEG1/recovery_audit/e0_audit.json)，
执行入口与状态约束见
[恢复状态索引](../../.tmp/mesh-outstanding-experiment.JMEEG1/recovery_audit/)。
辅助工作日志不替代 case、execution、candidate history 和宿主进程身份。

每次子进程的 `run.process.json` 记录实际身份和退出状态；只有 `execution.json`
的验证结果与 measurement 绑定后才形成实验终态。源文件和二进制在正式运行前冻结，
最终报告在全部 worker 退出并完成核验后发布。
