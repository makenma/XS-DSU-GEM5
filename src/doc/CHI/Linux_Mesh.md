# 双核 Linux CHI mesh

`configs/example/chi_mesh_linux.py` 将两个 TimingSimpleCPU、两个 HNF 和一个
SNF 接入四个自定义 CHI router。CPU 的取指、数据和页表遍历都经过 RNF 和
mesh；ELF/DTB 只在初始化时通过 SNF functional port 装载。平台 MMIO 走独立总线。

```text
 CPU0/RNF0             CPU1/RNF1
     |                     |
 router(0,0) -------- router(1,0)
     |                     |
 router(0,1) -------- router(1,1)
     |                 |       |
    HNF0              HNF1     SNF
```

路由采用单 lane，保留逐拍流水、有限缓冲、credit 和仲裁。RNF wire ID 为
0/128，HNF 为 16/144，SNF 为 148。两个 HNF 按物理地址的 64B 行交织；
SLCSF 使用 `home_count=2, home_index=0/1` 去掉 home 选择位后计算 set/tag，
victim 重建时恢复该位。每个 HNF 的 SLC 为 1024×16、SF 为 4096×16，
POCQ 16 项，RAM latency 2。RNF 每核 512 行、4 路；内存 128MiB。

CPU 为 1GHz RISC-V TimingSimpleCPU，RNF 是有限容量、单事务的功能性 CPU
适配器，详见 [RNF_Bridge.md](RNF_Bridge.md)。它使用 ReadUnique 取得行，
因此读共享的流量高于具备 ReadShared 的完整 CPU 缓存系统；不能据此宣称
XiangShan O3 或某款 CPU L1 的周期精度。

## 固件和登录镜像

`util/chi_prepare_linux.py` 从服务器现有的 OpenSBI/Linux 6.10.7 ELF 生成
登录镜像，保留内核和 firmware 指令，只替换原位置的 gzip initramfs 及长度。
脚本检查来源 SHA-256、空间边界、newc 内容和 BusyBox 动态库依赖，并生成
manifest 和镜像内版权说明。原始文件不被修改。

当前输出为 `m5out/linux-chi-mesh/assets/fw_payload.elf`，SHA-256：
`4897883ab3e7566ad5d85d65c874c99e6ac31887ee3bca23d3e08746e8a4257d`。
镜像使用 BusyBox init/getty/login；串口用户名 `root`，密码为空。未启用网络服务。

配置生成匹配两个 hart 的 DTB，并装载到 firmware 预留的内嵌 DTB 地址。
UART 兼容字符串包括 `ns16550a`，RTC 为 10MHz。Linux 参数包含
`initcall_blacklist=check_unaligned_access_all_cpus`，与所提供固件的启动约束一致。

```sh
build/RISCV/gem5.opt --listener-mode=off -d m5out/chi-linux-repeat \
  configs/example/chi_mesh_linux.py \
  --kernel m5out/linux-chi-mesh/assets/fw_payload.elf
```

配置检测真实串口输出 `gem5-chi login:` 后 drain 全部 CPU/CHI 模块，再写
`cpt.login`。这不是软件打印一个标志后伪造的登录点。状态为 gem5 native
checkpoint，包含物理内存、暖 RNF/SLC/SF 和 router 仲裁历史；不是 GCPT，
也不会为了保存而清空缓存。恢复时必须使用相同拓扑及容量配置。

## CPU 和 native checkpoint 支持

这个平台需要补齐原仓库的 RISC-V SimpleCPU 取指与状态保存支持。解码器原先
假定 `fetchPC` 总是指令地址，而 SimpleCPU 按 4B 对齐取指；现在正确处理
偏移 2B 的压缩指令，以及从两个取指字拼接跨界的 32 位指令。O3 直接从实际
PC 提供完整指令字的路径保持原有含义。

TLB 的新格式保存 L1、共享 L2 的五类 page-cache bank，以及前后向预取表。
每项包含翻译级别、ASID/VMID、两阶段 PTE 和使用状态，并保留物理槽位、
真实 trie key/prefix、空闲链表顺序及替换/预取策略状态。恢复时重建指针，
不通过重新查询或清空 TLB 代替恢复。旧实现只保存 L1，还会对没有 L1 空闲
链表的共享 L2 调用 `front()`；缺失 L2 状态的旧 native 格式现在明确拒绝。

PMP 单独保存全部原始地址和配置，先恢复整张表，再重建包括 TOR 前项地址
依赖的范围及有效规则数。仅恢复 ISA CSR 数组不能恢复权限表，也无法找回
旧 CSR 掩码已丢失的原始地址位，因此没有完整 PMP 状态的旧 native checkpoint
必须重新生成。此检查仅在 native 恢复入口执行；GCPT 的内存镜像加 restorer
启动路径仍走 `initState()`。

CPU 的 LR/SC 预约按 context 保存，并与 RNF 的行数据及预约一起恢复。旧 ISA
快照缺少预约字段时使用 INVALID，使首次 SC 安全失败；重新执行 LR/SC 后可
成功。SimpleCPU 另外保存指令和微操作累计数，保证 `instret` 不在恢复后回退。
既有通用/浮点/向量寄存器、PC、CSR 和中断状态继续使用原有保存接口。

逐拍恢复证据来自实际 guest 继续执行：双 hart 缓存/原子测试、LR→checkpoint→SC
测试，以及启用 Sv39、PMP 和 `instret` 的测试，均要求原跑在 checkpoint 后
继续执行与重新启动进程恢复的最终绝对 tick 相同。这个检查覆盖相应定向程序，
不将 Linux 交互登录成功等同于所有工作负载均已证明逐拍等价。

## 验证入口

- `tests/gem5/chi/run_mesh.py`：两个真实 hart 使用压缩指令，强制有限 RNF
  淘汰和 dirty writeback，互读全部数据，AMO/LRSC，再保存和恢复暖状态。
  要求原跑与恢复的终止 tick 相同。
- `tests/gem5/chi/run_mesh_lrsc_checkpoint.py`：在 LR 和 SC 之间保存；正常
  恢复必须保留预约，缺少旧 ISA 预约字段的副本必须安全地让首次 SC 失败，
  重新 LR/SC 后成功。
- `tests/gem5/chi/run_mesh_mmu_checkpoint.py`：S 模式下保持暖 Sv39 翻译，
  checkpoint 前后对只读 PMP 页的写入均须触发 access fault，并验证可写数据
  和 `instret` 单调，原跑与恢复的结束 tick 相同。
- `tests/gem5/chi/restore_linux_mesh.py`：从登录点恢复，通过 localhost 串口
  实际登录，读取两个在线 CPU 和内核版本，并检查源 checkpoint 的文件哈希不变。
- `run_router.py` 和 `run_slcsf.py` 保留原有逐拍、反压和同 tick 事件顺序
  回归。路由宿主性能测量见 [Router_Performance_Round2.md](Router_Performance_Round2.md)。

三组 guest 定向测试通过，结束 tick 如下。每组恢复的开始 tick 等于其原始
checkpoint tick，没有复跑 checkpoint 之前的指令。

| 测试 | 原跑结束 tick | 恢复结束 tick |
| --- | ---: | ---: |
| 双核数据/淘汰/AMO/LRSC | 1422156000 | 1422156000 |
| LR 和 SC 之间保存 | 27347000 | 27347000 |
| Sv39/PMP/instret | 33579000 | 33579000 |

报告分别位于 `m5out/linux-chi-mesh/{mesh-final,lrsc-final,mmu-final}/report.json`。
另有 92/92 单元测试（SLCSF 61、POCQ 9、channel 5、router 9、decoder 5、
TLB 3），日志位于 `m5out/linux-chi-mesh/validation/*-unit-final.log`。
新 TLB 的 ASan/UBSan 三项测试通过；本轮未启用泄漏检测。

## Linux 最终实测结果

最终二进制 SHA-256 为
`b81e2129fd7d7a7067497a1717a98bd283dead7e7e293b847590f8c0b2624ff4`。
Linux 6.10.7 成功启动两个 CPU，进入真实 `gem5-chi login:` 提示后保存：

`m5out/linux-chi-mesh/boot-final/cpt.login`

保存 tick 为 **2228000050000**，宿主耗时 1129.78 秒。完整冷启动串口输出与
checkpoint 修复前逐字节相同，保存 tick 也相同。RNF0 保留 225 条有效行
（121 条脏行），RNF1 保留 499 条有效行（180 条脏行）；两个 HNF 的 SLC/SF
独立保存。drain 后全网 local 注入/交付总数以及 internal 发送/接收总数均平衡。
记录见 `boot-final/{result.json,checkpoint-audit.json,system.platform.terminal}`。

从该 checkpoint 恢复后，已经通过 localhost 串口实际登录 root 并执行命令：

```text
CHI_RESTORE_ONLINE=0-1
CHI_RESTORE_KERNEL=6.10.7-g0dc336bf6c07
CHI_RESTORE_DONE
```

恢复从 tick 2228000050000 继续，验证结束于 2355000050000，宿主耗时 70.66 秒。
源 checkpoint 的四个文件 SHA-256 全部保持不变；没有重启内核或修改保存的
guest 寄存器、缓存及内存。报告为
`m5out/linux-chi-mesh/restore-final-local/report.json`，串口证据在同目录的
`system.platform.terminal`。

```sh
python3 tests/gem5/chi/restore_linux_mesh.py \
  --kernel m5out/linux-chi-mesh/assets/fw_payload.elf \
  --checkpoint m5out/linux-chi-mesh/boot-final/cpt.login \
  --out m5out/chi-linux-restore-repeat
```

恢复验收脚本需要绑定 localhost 串口。首次在受限沙箱中运行因禁止创建 socket
而在 tick 0 终止，记录保留在 `restore-final/`；允许本地 socket 后完整验收通过，
记录在 `restore-final-local/`。早先的 `boot/cpt.login` 缺少完整 TLB/PMP 状态，
仅保留为诊断记录，应使用上面的 `boot-final/cpt.login`。

总验收索引为 `m5out/linux-chi-mesh/validation-summary.json`。
