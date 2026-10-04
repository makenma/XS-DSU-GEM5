# Linux rootfs 与 guest 内核构建

当前状态：已准备文件系统、入口与结果检查器，并完成承载它的 Linux/OpenSBI 固件构建及字节、地址布局核验；尚未在 gem5 中验证这份大 rootfs 的挂载或 guest 内核构建。

本方案继续使用四核 KMH、4 个 `ChiNativeL2`、4HN/2SN 和 3×2 NoC。根文件系统是内建 initramfs 中的一份 LZ4 SquashFS 文件，Linux 通过 loop 挂载只读层，再叠加 tmpfs 可写层。文件解压、页缓存、编译器和编译输出均由模拟 CPU 执行，RAM 请求走已有 CHI 路径。没有增加磁盘或 DMA 模型。

现有 `native_l2_fs.py` 只连接 HiFive PCI 配置空间，没有在 DT 中公布 PCI/VirtIO 磁盘。固定 gem5 的 `VirtIOBlock` 在 `src/dev/virtio/block.cc:49` 使用 `system->physProxy`，队列在 `src/dev/virtio/base.cc:88,156,168` 通过 `readBlob/writeBlob` 访问内存；因此不能把它直接接到 SN 后端当作已经验证的 coherent DMA。

## 准备输入与产物

在 WSL 中运行：

```sh
bash scripts/prepare-native-linux-rootfs.sh
```

脚本默认读取已准备的 Ubuntu noble riscv64、Linux 6.6.52 源码、BusyBox 和 `guest-control`，只准备 guest 内核构建；不依赖 litmus、pthread 或 torture 模块。显式设置 `CHI_ROOTFS_MODE=all` 才加入这些测试。脚本不下载软件，不运行 QEMU/chroot/guest，不编译内核，也不启动 gem5。默认输出 `/root/chi-flowgraph-work/linux-validation/native-rootfs`。再次准备时通过 `CHI_LINUX_ROOTFS_OUT` 选择新目录，避免覆盖已有证据。

脚本从发行版树排除 home/root 内容、运行目录、缓存/日志、机器身份、SSH 私钥、apt 认证信息和网络配置，并锁定密码字段；没有复制 WSL 主机的 `/usr` 或用户目录。记录 GCC、make、as、ld、sh 的 RISC-V ELF 类型和 SHA256，以及源码树、模块、测试程序和入口脚本的摘要。

最终 kernel-only 产物位于 `/root/chi-flowgraph-work/linux-validation/native-rootfs-kernel-only-final`。SquashFS 实际大小为 600,838,144 字节，SHA256 为 `1f31f9b3ca4ebd99eb71a5ee675cab0eb077999b085f5bb68e2ca85300cb0282`。下一步构建使用该目录唯一的 `initramfs.list`；目录还包含 `rootfs-manifest.json`、`artifacts.sha256`、`kernel-rootfs.config` 和打包日志。已读取实际 SquashFS 内的入口、构建脚本、控制程序和 manifest，逐项核验摘要，报告为 [rootfs-kernel-only-preparation.json](../build/linux-validation/rootfs-kernel-only-preparation.json)。这仅证明准备产物完整，不是 guest 运行通过。

首份 `/root/chi-flowgraph-work/linux-validation/native-rootfs` 仍含全套测试，大小 602,930,154 字节、SHA256 `fb1048327eacbe233292d74db8437b253719ec9cdcd40bcb892dab3d722c0579`；中间 `native-rootfs-kernel-only` 也原样保留。这两份均不作为最终运行镜像。最终 `initramfs.list` 直接引用 BusyBox、`guest-control` 和项目 `rootfs-init.sh`，这些源文件在 Image 构建前应保持与核验报告记录的摘要一致。

## 内核与内存布局

根挂载需要以下选项为 built-in：`BLK_DEV_LOOP`、`SQUASHFS`、`SQUASHFS_LZ4`、`OVERLAY_FS`、`TMPFS`、`DEVTMPFS`、`PROC_FS`、`SYSFS`。完整配置片段由脚本输出。外层 initramfs 使用 `INITRAMFS_COMPRESSION_NONE`，避免再次解压已经压缩的镜像。BusyBox 需要 `sh mount mkdir mknod losetup switch_root sleep touch`，以及 `FEATURE_MOUNT_LOOP`、`FEATURE_MOUNT_FLAGS`。

现有内核配置已支持所需文件系统及 Zicbom。构建大 rootfs Image 前仍须合并最终目录中的配置片段，确认 `INITRAMFS_COMPRESSION_NONE=y`，把 `INITRAMFS_SOURCE` 指向最终 `initramfs.list`，再构建 Image/OpenSBI。只有可选 all 模式需要先构建 torture modules，且最终运行内核与镜像内模块必须保持相同版本、配置/ABI和构建产物。

已使用 [build-native-linux-rootfs.sh](../scripts/build-native-linux-rootfs.sh) 复用完成的 kernel objects，以 `-j2` 构建 Image 和独立 `opensbi-rootfs` 固件。脚本在修改配置前校验并保存 core suite 的 Image、配置和 initramfs 清单至 `artifacts/core-suite-before-rootfs/77cc3b4a2659fa1c27342aa5ff41f88f974afd6aacafefb37e69e37f7cf2fbcc`，不会覆盖已有 `opensbi-suite` 固件；再次调用发现已有构建记录时会停止，保留证据。

最终固件为 `/root/chi-flowgraph-work/linux-validation/opensbi-rootfs/platform/generic/firmware/fw_payload.elf`，625,055,736 字节，SHA256 `e53cb2394430e92f2b9763effff88f246b19d90cfa2306b50077c3f9eb63922f`；Image 为 623,902,720 字节，SHA256 `254f9b18aa7be53f9a9a45f6a06d6de282c4b60daa051563dc7ddfa1baa01a68`。核验报告 [rootfs-build/build-result.json](../build/linux-validation/rootfs-build/build-result.json) 确认 raw newc CPIO 内四个源文件、Image 内完整 CPIO 和固件内完整 Image 的哈希一致，三个 `PT_LOAD` 不相交且避开 DTB，core suite 固件哈希未变。这里是主机交叉编译启动固件，和后续 guest 内本机 `make -j4 Image` 的验收分别记录。

现有 FS 配置的运行参数为：

```text
--kernel FINAL_FW_PAYLOAD.elf --mem-size 4GiB --numa-nodes 2
--dtb-addr 0x17fe00000 --max-ticks 1000000000000000
```

RAM 为 `[0x80000000, 0x180000000)`，两个逻辑 NUMA 节点各 2 GiB；DTB 保留最后 2 MiB。固件入口仍为 `0x80000000`，内核为 `0x80200000`。initramfs 内建于 Image，没有单独装载的 initrd 地址。FS 配置检查 ELF 的每个 `PT_LOAD` 都位于 RAM 内且不覆盖 DTB。

tmpfs 上限分别为可写根 512 MiB、`/tmp` 512 MiB、`/build` 1536 MiB；这些是上限，不是启动时立即分配的容量。原 SquashFS 通过 bind mount 保留原 RAM 页，随后移动 `/run` 挂载树并 `switch_root`，不额外复制整个镜像。

## guest 工作与验收

`rootfs-entry.sh` 核对 `riscv64`、四核 online、两个 NUMA 节点和足够内存，默认只在全新 `/build/kernel` 中执行本机 `make ARCH=riscv -j4 Image`。已有 core 测试由独立 `suite-init.sh` 与阶段检查器执行，保留各自真实执行证据，不在这个启动中默认重复。这个 rootfs 启动不传 `--suite-pipeline`；最终调用 `guest-control stage kernel-build`，先 `fflush`/`tcdrain` 排空串口，再触发 `m5_exit`，随后由专用内核构建检查器核验。

只有在准备时设置 `CHI_ROOTFS_MODE=all`，并在 guest 命令行显式加入 `chi.rootfs_mode=all`，才依次执行：

1. 官方 Linux litmus 及其原始 wrapper。
2. RCU 与四种 lock torture。
3. pthread suite，128 次迭代。
4. 在全新 `/build/kernel` 中执行本机 `make ARCH=riscv -j4 Image`。

最后一项使用 `tinyconfig + SMP/NR_CPUS=4`，明确标为 `tinyconfig-smp4`。这是实际内核 Image 编译，不代表完成 defconfig 全量构建，也不代表已经启动新编译的 Image。没有使用 `CROSS_COMPILE`，编译器和 make 是 rootfs 内的 RISC-V 程序。

guest 输出完整 make 日志、开始/结束记录、四核前后 `/proc/stat`、Image/vmlinux/config/log 的 SHA256、新 Image 大小、对象文件数以及 ELF64/RISC-V 检查。运行后使用：

```sh
python3 scripts/check-linux-kernel-build.py SERIAL_FILE --output kernel-build-result.json
```

检查器要求唯一且有序的记录、全新目录、真实零退出码、正工作量、编译/链接/Image 生成日志、有效摘要和四个 CPU 的工作计数都增长。摘要由 guest 内 `sha256sum` 计算并保存在串口；没有把 Image/vmlinux 导出给主机，因此报告明确不声称主机重新计算过产物摘要。已通过一份有效合成样例及 23 个反例变体；这些只是检查器测试，不是 guest 构建已通过的证据。

全局 `CHI_LINUX_ROOTFS_END status=completed` 仅表示各命令返回成功；litmus、torture、pthread 和内核构建仍须分别通过主机检查器。这里不据此宣称所有 CHI opcode、全部 RVWMO 交错或缓存终态已被证明。

## 独立 QEMU 参考产物

为后续真实 gem5 guest 内核构建增加产物对比，已用 [build-linux-kernel-reference.sh](../scripts/build-linux-kernel-reference.sh) 完成一次独立参考构建。它从最终 SquashFS 解出新目录 `/root/chi-flowgraph-work/linux-validation/qemu-kernel-reference/root`，使用其中的 RISC-V GCC 13.2.0-23ubuntu4、binutils 2.42 和相同源码；不挂载主机目录、不运行 m5ops，也不产生 gem5 验收标记。参考 runner 从冻结 guest 脚本提取相同的环境、tinyconfig 修改和 make 命令；为限制资源占用，只把并行度从 `-j4` 改为 `-j2`，以 nice 10 运行。

完整的 [reference-result.json](../build/linux-validation/kernel-reference/reference-result.json) 保存来源、环境、配置与产物路径；同目录还保存原始 guest 脚本、参考脚本、配置日志、make 日志、工具链版本/cc1 摘要和 GCC specs。源码树摘要与镜像 manifest 一致，最终 `.version=1`，541 个对象文件；空 `BUILD_SALT`、`RANDSTRUCT_NONE`、无调试信息和无内建 initramfs 均已核验。主机重新计算的以下摘要与参考环境内的 `sha256sum` 一致：

| 参考产物 | 字节数 | SHA256 |
|---|---:|---|
| Image | 1,178,112 | `3f25b62e6d34c66bc1faa54797c2cea686620fcd05bcc3621931333576a92c2f` |
| vmlinux | 1,705,712 | `ddd61fe2bb4594234327661aaa1be753a1d48c9226cb006f5de3fc6684e314e6` |
| .config | 28,416 | `30e1183990b42df1e4bfed7e5d3c0dd1ef57f50789810ce2b1b36e5a3e680e64` |

首次准备预检曾因校验器规范化符号链接中的 `./` 而停止，已修正为原始链接文字哈希并复用解包目录；当时尚未创建构建目录或运行编译。这不构成第二次参考构建。本次只完成一次 make，没有用 QEMU 代替任何 gem5 测试。

取得真实 gem5 串口结果后运行：

```sh
python3 scripts/compare-linux-kernel-build.py \
  --serial ACTUAL_GEM5_SERIAL \
  --reference build/linux-validation/kernel-reference/reference-result.json \
  --output build/linux-validation/kernel-reference/gem5-comparison.json
```

比较器首先调用原有 `check-linux-kernel-build.py.check`，要求真实四核 guest 构建通过；然后比较配置 SHA、Image 大小/SHA 和 vmlinux SHA。缺少结果返回 `INCOMPLETE`（退出码 2），guest 验收失败返回 `GUEST_FAILED`，产物差异返回 `MISMATCH` 并逐项列出；差异需要诊断输入、可复现性和执行结果，不能直接归因于 CHI。匹配返回 `MATCH`（退出码 0）。不比较 make 日志 SHA、耗时或 CPU 计数；匹配也不表示主机重新计算过 gem5 产物本身的哈希。当前 [comparison-pending.json](../build/linux-validation/kernel-reference/comparison-pending.json) 明确为 `INCOMPLETE`，其中所给待收集串口路径不存在，没有真实 guest 结果。
