# Serving checkpoint

Serving 支持在已排空的 program boundary 保存完整运行状态，并在新进程中恢复。适用模式为 FULL_TIMING、FAST_EVENT 和 WINDOWED_TIMING；恢复要求相同二进制、配置和输入内容，不支持保存正在传输的请求、DMA 或网络消息。

在既有 [runner](../../configs/example/ai_mesh/run_gate6_serving.py) 命令后追加：

```sh
--checkpoint-dir /absolute/path/checkpoint --checkpoint-at-tick 1000100000
```

触发 tick 到达后使用 [program boundary](../../src/dev/ai_mesh/serving_program_boundary.cc) 排空；实际保存 tick 记录在 `checkpoint.json`。保存完成后原进程继续执行。

新进程使用相同运行参数，替换上述选项：

```sh
--restore-checkpoint /absolute/path/checkpoint
```

恢复进程可使用新的输出目录，也可同时指定另一组保存选项。目标 checkpoint 目录必须不存在。完整目录由 [CheckpointBundle](../../util/mesh_ir/mesh_ir/serving_checkpoint.py) 原子发布并校验，包括原生状态、Python 运行状态与已生成的 MoE 诊断工件；不要只复制 `m5.cpt`。

## 实现与验收入口

- 原生存储与 CRC：[checkpoint archive](../../src/sim/checkpoint_archive.hh)；字段定义：[checkpoint fields](../../src/base/checkpoint_fields.hh)。构建依赖 Boost.Serialization 与 Boost.PFR，检查入口为 [src/SConscript](../../src/SConscript)。
- Host / 协议：[driver](../../src/dev/ai_mesh/agent_driver_checkpoint.cc)、[frontend](../../src/dev/ai_mesh/serving_frontend_checkpoint.cc)、[recorder](../../src/dev/ai_mesh/gate3_recorder_checkpoint.cc)。
- 执行与物理状态：[core](../../src/dev/ai_mesh/mesh_core_checkpoint.cc)、[dispatcher](../../src/dev/ai_mesh/mesh_dispatcher_checkpoint.cc)、[DMA](../../src/dev/ai_mesh/mesh_dma_checkpoint.cc)、[内存](../../src/dev/ai_mesh/mesh_memory_checkpoint.cc)、[AXI](../../src/mem/axi/axi_endpoint_checkpoint.cc)。
- 网络：[Garnet](../../src/mem/ruby/network/garnet/GarnetCheckpoint.cc)、[Simple Switch](../../src/mem/ruby/network/simple/SwitchCheckpoint.cc)、[MessageBuffer](../../src/mem/ruby/network/MessageBufferCheckpoint.cc)。
- 窗口与 Python 状态交接：[ServingTimeline](../../util/mesh_ir/mesh_ir/serving_timeline.py)、[runner](../../configs/example/ai_mesh/run_gate6_serving.py)。
- [真实新进程恢复测试](../../util/mesh_ir/tests/integration/test_gate8_checkpoint.py) 对账业务事实、终态 tick、payload、DMA 时序、KV/cache、桥流量、窗口和 MoE 工件，并验证错配与损坏拒绝。
- 测试命令见 [统一测试索引](mesh_ir_test_commands.md#gate-8-serving-checkpoint)。通用 gem5 `stats.txt` 中的平均值统计不属于逐字节一致验收对象。
