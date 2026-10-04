# 普通 Linux 服务器复现

本入口适用于 gem5 仓库内的 `util/chi-flowgraph`。上传的是源码；服务器应重新构建 gem5、RV64 程序和固件，并为每次运行建立新的 SHA、命令和结果记录。本机 model08/model10 的历史 SHA、严格统计失败和未完成 Linux 记录不能作为新服务器的通过证明。

截至本次移交，完整 Linux 验收仍未通过。model10 的定向取指回归通过，RTL-SE 的 22 项统计变化另有独立故障语义报告，原严格等价比较保持 FAIL；BPU 和短 FS 的统计差异仍有单独调查记录。服务器入口不会绕过这些历史结论，也不会把一个 stage 的通过称为完整 Linux 通过。

## 路径与依赖

建议使用本地 Linux 文件系统和 Ubuntu 24.04 的配套工具链。本地已用 Python 3.12、GCC/G++ 13.3、RV64 Linux GCC 13.3；Python 解包脚本使用 `tarfile.extractall(filter='data')`，需要支持此参数的 Python。gem5 保持 C++17；只有独立 CHIron refiner 使用 C++20。

需要准备：

- gem5：C/C++ 编译器、SCons、Python 开发包、m4、zlib、SQLite、Boost、protobuf、pkg-config、Git。
- RV64 Linux/BusyBox/OpenSBI：`riscv64-linux-gnu-gcc`、对应 binutils、静态交叉 libc 开发包、Make、bc、bison、flex、libssl-dev、libelf-dev、curl、xz、bzip2、cpio、rsync。
- 官方 herdtools 7.56：OCaml、Dune、Menhir、ocamlfind、Zarith。已验证组合是 OCaml 4.14；脚本核官方固定 commit 和源码归档 SHA，使用最多 `-j2`。
- 可选 kernel-build rootfs：`mksquashfs` / `unsquashfs`，以及已经安装完整本机 RISC-V 编译工具链的 RV64 Linux 用户态树。它不等同于 host 的交叉编译器。

从已经集成实现的 gem5 分支直接构建。不要运行 `bootstrap-wsl.sh`、`apply-gem5.py` 或旧补丁安装器来覆盖这个分支：它们用于较早的外部基线接入，带有特定基线保护。

```bash
# 当前目录是已检出的 gem5 仓库根。
export CHI_GEM5_DIR="$PWD"
scons build/RISCV/gem5.opt -j2
cd util/chi-flowgraph
export PROJECT="$PWD"
export CHI_LINUX_WORK="$HOME/chi-native-linux"
export CHI_LINUX_VALIDATION_ROOT="$CHI_LINUX_WORK"
export CHI_BUILD_JOBS=2
mkdir -p "$CHI_LINUX_WORK" "$PROJECT/build/linux-validation"
```

请用新的工作目录保留构建/运行证据。源码包应保留 `native/`、`scripts/`、`tests/`、`docs/`，前端及其 lockfile；`build/`、`vendor/`、二进制、PDF、`node_modules/`、`__pycache__/` 不作为源码依赖上传。官方外部源和生成产物按下述步骤重新获取。若从 Windows 打包，shell 文件必须以 LF 保存；不要把历史 SHA 清单当作服务器上重建后的 SHA。

## 构建 boot、完整 core 和 random 输入

以下是源码构建顺序，不执行 gem5。任一步失败都应停止，不要删除测试、减少矩阵或把缺失阶段改为 PASS。

```bash
set -euo pipefail
bash scripts/prepare-native-linux-sources.sh
bash scripts/build-native-linux-boot.sh
bash scripts/build-linux-litmus.sh --tools-only
TOOLS="$CHI_LINUX_VALIDATION_ROOT/herdtools7-7.56/bin"
python3 scripts/generate-linux-litmus.py --tools "$TOOLS" \
  --output build/linux-litmus-smoke --profile smoke --samples 8 --runs 1
python3 scripts/generate-linux-litmus.py --tools "$TOOLS" \
  --output build/linux-litmus-large --profile large --cycle-size 8 --samples 128 --runs 1
python3 scripts/build-linux-tests.py
bash scripts/build-native-linux-suite.sh
SUITE_INDEX=$(python3 -c 'import json; print(json.load(open("build/linux-validation/suite-build/frozen-inputs.json"))["index"])')
python3 scripts/build-linux-random-memory.py
python3 scripts/build-native-linux-random-memory.py \
  --work "$CHI_LINUX_WORK" --suite-inputs "$SUITE_INDEX" --jobs 2 \
  > build/linux-validation/random-build-result-server.json
```

`build-native-linux-suite.sh` 会构建匹配内核 ABI 的 CBO/uncached 模块并冻结所有输入。完整 core 包含 6 个 smoke、1936 个 large 命名测试（1908 个唯一 semantic Hash，128 samples/instance）、RCU 与四类 lock torture、128 次 pthread/原子/真实迁移测试、CBO 两阶段和 uncached 测试。生成器与模型结果、完整清单和实际 ELF 均会核验。

random 使用独立 kernel/OpenSBI 输出，原 core 固件和冻结 bundle 不变。参数保持 128 epochs、seed=1、每 writer 64 pages、1 MiB pressure、每 epoch 64 pressure samples、120 guest seconds 超时；不能用 memcpy 代替真实跨 NUMA 页面迁移。它的 build-result JSON 包含新固件和输入 index 的身份。

## 启动与检查

新入口是 `scripts/run-native-linux-server.py`。所有 stage 都固定四个 KMH CPU、四个 ChiNativeL2、4HN/2SN、3×2 NoC、1 GHz、两个 NUMA 节点；L2=512 sets × 8 ways、32 MSHR，HN=64 entries、8192 SLC lines。没有减少核心数、改时钟或减少 guest 测试次数的参数。

| stage | 必需输入 | 固定最大 tick |
|---|---|---:|
| boot | boot 固件；默认从 work-dir 查找 | 100000000000 |
| core | `--suite-inputs`，完整 suite 固件 | 100000000000000 |
| random | `--random-build-result` | 130000000000000 |
| kernel | `--rootfs-manifest`，含完整编译环境的固件 | 10000000000000000 |

`--gem5`、`--work-dir`、`--output` 必须显式提供；输出目录必须不存在。`--gem5-source` 可显式提供，也可读取 `CHI_GEM5_DIR` 或按仓库内 `util/chi-flowgraph` 位置推导。默认固件分别是 work-dir 下的 `opensbi-build`、`opensbi-suite`、`opensbi-rootfs`；`--firmware` 可以显式选择实际 ELF。random 固件必须与 build-result 的 SHA 相符。

先只准备命令并核对文件，不启动模拟：

```bash
python3 scripts/run-native-linux-server.py --stage core \
  --gem5 "$CHI_GEM5_DIR/build/RISCV/gem5.opt" --gem5-source "$CHI_GEM5_DIR" \
  --work-dir "$CHI_LINUX_WORK" --suite-inputs "$SUITE_INDEX" \
  --output "$CHI_LINUX_WORK/runs/server-core-preflight-01" --dry-run
```

dry-run 仅生成 `PREPARED` / `guestExecuted=false`，不产生 PID、PASS 或 Linux 完成记录。正式执行必须用另一个全新目录。建议顺序执行；core 未通过时停止，不启动后续组。

boot 是可选的独立启动探测，沿用历史 100e9 tick 上限；本机某些配置到 `/init` 已超过此上限，达到上限必须记为未完成。完整 core 自己包含启动阶段，使用原 100e12 tick 上限，不要求先拿到独立 boot PASS。

```bash
# 可选的独立 boot 探测。
python3 scripts/run-native-linux-server.py --stage boot \
  --gem5 "$CHI_GEM5_DIR/build/RISCV/gem5.opt" --work-dir "$CHI_LINUX_WORK" \
  --output "$CHI_LINUX_WORK/runs/server-boot-01"

# 完整工作负载顺序执行；core 未通过时停止。
set -euo pipefail
python3 scripts/run-native-linux-server.py --stage core \
  --gem5 "$CHI_GEM5_DIR/build/RISCV/gem5.opt" --work-dir "$CHI_LINUX_WORK" \
  --suite-inputs "$SUITE_INDEX" --output "$CHI_LINUX_WORK/runs/server-core-01"
python3 scripts/run-native-linux-server.py --stage random \
  --gem5 "$CHI_GEM5_DIR/build/RISCV/gem5.opt" --work-dir "$CHI_LINUX_WORK" \
  --random-build-result build/linux-validation/random-build-result-server.json \
  --output "$CHI_LINUX_WORK/runs/server-random-01"
```

入口在前台等待真实子进程结束，保存 `execution-plan.json`、`host-pid.txt`、`host-exit-code.txt`、`host-completion.json`、完整 run/serial 与 `server-stage-result.json`。实际工作目录固定为项目目录；执行前后重验 binary、firmware、配置及冻结输入的 SHA。非零退出、断言、缺结果、超时和检查器失败不能获得 `stagePassed=true`。

core 和 kernel 调用原 `check-native-linux-validation.py` 的单阶段检查，random 调用原独立检查器。它们重读真实 serial、逐项结果、拓扑及最终 census。boot 只验证四核/两 NUMA 正常启动，不能替代工作负载。任何单阶段报告都固定 `completeLinuxAcceptance=false`；完整完成还需要三组实际通过、原始证据和总验收。旧 `check-native-linux-complete.py` 的外层 Codex session receipt 不能在服务器伪造，服务器入口保存的是实际 `python-subprocess-wait` receipt。

## kernel-build rootfs 的现存缺口

源码当前没有从零创建 Ubuntu RV64 rootfs 的完整便携脚本。`prepare-native-linux-rootfs.sh` 只打包已有用户态树；历史 `build-native-linux-rootfs.sh` 还依赖一次性生成的 `rootfs-kernel-only-preparation.json` 和原 core 快照记录。排除 `build/` 后，不应宣称直接运行这两步便能完整重建本机大固件。

服务器应先由管理员用可信的 RV64 发行版镜像或 debootstrap/mmdebstrap 准备独立用户态树，安装本机 riscv64 的 gcc、binutils、make、libc 开发文件、bc、flex、bison、libssl-dev、libelf-dev；这是准备环境，不是 gem5 guest 测试。检查 `usr/bin/gcc` 等为 ELF64/RISC-V，记录发行版、包版本和镜像摘要。不要复制 x86 host 的 `/usr`。若使用 QEMU 完成发行版安装或做参考编译，其结果也不能计入 gem5 Linux 验收。

已有该树后可打包：

```bash
export CHI_LINUX_BASE_ROOTFS=/absolute/path/to/prepared-riscv64-rootfs
export CHI_LINUX_ROOTFS_OUT="$CHI_LINUX_WORK/native-rootfs-kernel-only-final"
export CHI_ROOTFS_MODE=kernel-build
bash scripts/prepare-native-linux-rootfs.sh
```

仍需人工完成并记录以下构建操作：保存 core 的 Image/config/initramfs；核 `artifacts.sha256`、SquashFS 内实际入口和 manifest；在独立 Linux 输出目录合并 `kernel-rootfs.config`，设置 `INITRAMFS_COMPRESSION_NONE=y` 与实际 `initramfs.list`；构建 Image；用独立 OpenSBI 输出嵌入这个 Image；核实际 CPIO→Image→firmware 字节、ELF PT_LOAD 和 DTB 不重叠。现有 `check-native-linux-rootfs-build.py` 是这些核验的实现参考，但其输入目录/历史快照格式仍固定，不能直接拿一个伪造 preparation JSON 绕过检查。修齐便携 rootfs 构建链前，kernel stage 需要外部提供经这些步骤核验的固件与实际 manifest。

运行已核验的 kernel-build 固件时：

```bash
python3 scripts/run-native-linux-server.py --stage kernel \
  --gem5 "$CHI_GEM5_DIR/build/RISCV/gem5.opt" --work-dir "$CHI_LINUX_WORK" \
  --firmware /absolute/path/to/verified-fw_payload.elf \
  --rootfs-manifest "$CHI_LINUX_ROOTFS_OUT/rootfs-manifest.json" \
  --output "$CHI_LINUX_WORK/runs/server-kernel-01"
```

该 stage 保持 4 GiB RAM、DTB=`0x17fe00000`、两 NUMA，以及 guest 内真实 `make -j4 Image` 的 `tinyconfig-smp4` 测试。manifest 是来源记录，不能替代固件嵌入字节核验或真实 guest 编译日志。

## 哪些旧入口不能直接迁移

- `native_linux_run08.py`、`native_linux_run_v2.py` 及 08/10 prepare/continue/complete：绑定本机历史固件、bundle、binary SHA 和部分 `/root/...` 路径，保留作历史证据，不是服务器启动器。
- `check-native-fetch-fault-regression.py`：仅验证本机固定 model09→model10 的 RTL-SE 故障 episode，绑定 `/mnt/d/...` 和冻结 ELF/SHA；不能套用新服务器模型或 BPU 工作负载。
- `bootstrap-wsl.sh` / installer：不要重放到已经包含改动的新 gem5 分支。
- Windows `.ps1`、浏览器和本机导出/打包入口：与 headless Linux CPU 验证无关。

新服务器入口和负例测试只证明准备/验收逻辑可执行，不表示已经在服务器运行 guest。可先运行纯测试：

```bash
python3 -m unittest discover -s tests/native -p test_native_linux_server.py -v
python3 -m unittest discover -s tests/native -p test_native_fetch_fault_regression.py -v
```
