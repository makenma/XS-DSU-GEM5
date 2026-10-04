# CHI FlowGraph

本地 CHI 事务回放工具：**固定版本 gem5 的真实组件 / 运行采集 → C++20 `chi-refine` → 浏览器三维回放**。界面为中文，协议字段和 opcode 保留英文。

## 在服务器继续验证

在 gem5 新分支中，本项目位于 `util/chi-flowgraph/`。实际 gem5 源码修改已合入
`src/`，按 [服务器运行说明](docs/native-linux-server.md) 直接编译和运行，不要再对这个
分支执行旧的 `bootstrap-wsl.sh` 或补丁安装器。服务器会记录新的构建和运行身份。

交付时完整 Linux 验收尚未完成；定向通过项和保留的统计差异见
[验证状态](validation/README.md)。原始本机路径的历史记录不能代替服务器实跑。

## 在另一台电脑修改网站

线上网站：<https://chi-flowgraph.makenma.workers.dev/>。

克隆本仓库，或在 GitHub 的 **Code → Download ZIP** 下载源码并解压。源码包包含网页、65 个示例轨迹、测试和生成脚本；修改和查看已有示例不需要安装 gem5。

使用 Node.js 24 和 pnpm 11，在项目目录运行：

```bash
git clone https://github.com/makenma/chi-flowgraph.git
cd chi-flowgraph
pnpm install --frozen-lockfile
pnpm dev
```

下载 ZIP 时省略前两条命令，在解压后的目录运行安装和启动命令。浏览器打开 <http://127.0.0.1:5173/>；修改 `src` 后页面会自动更新。`src/components` 包含界面组件，`src/trace` 包含轨迹和回放逻辑，`src/styles.css` 控制样式，`public/demos` 保存演示数据。依赖版本由 `pnpm-lock.yaml` 固定。

修改后运行 `pnpm test` 和 `pnpm build`。在 Windows PowerShell 中执行 `./scripts/package-public-site.ps1`，将生成的 `build/publish/chi-flowgraph-public.zip` 上传到 Cloudflare 中现有 **chi-flowgraph → New deployment**，完成后检查线上效果。Git 提交和推送用于同步源码，线上网站需另外部署构建产物。

仓库不包含 `node_modules`、`dist`、`build`、第三方源码检出目录和本机凭据。大型原生运行轨迹 `examples/native-l2/run.chi.jsonl` 按下文命令重新生成，已有网页示例可直接使用。

## 打开网页

项目已经带有组件测试和实际 gem5 运行生成的示例，查看示例不需要启动 gem5。

若要给别人一个公网网址，运行 `pnpm build`，再运行 `./scripts/package-public-site.ps1`。将生成的 `build/publish/chi-flowgraph-public.zip` 上传到 Cloudflare Pages 的 **Workers & Pages → Create application → Drag and drop your files**，发布后分享 `https://<项目名>.pages.dev/`。发布包保留全部 65 个演示示例，排除仅供性能测试的 100k 事件轨迹。更新网页时重新构建、打包并创建新部署。

```powershell
pnpm install --frozen-lockfile
pnpm dev
```

Windows 浏览器打开 <http://127.0.0.1:5173/>。选择示例或导入 `.chi.jsonl`；导入和索引在 Worker 中执行，数据留在本机。可按地址、节点、opcode、事务身份筛选，点击消息暂停，点击字段来源跳转，按完整整数 tick 定位。

点击 mesh 中正在飞行的 flit，会在点击位置暂停统一时钟，以白色双环标出选中的消息，并自动打开右侧字段页；点击播放可继续并行传输。拖动仍用于旋转视角。交互回归可运行 `pnpm test:flit-pick`（需先启动开发服务器）。

页面分别显示原始事件和协议展开步骤。补出的消息使用原事件 tick 锚点和 `substep` 排序；播放速度是界面步速，不代表模拟器周期。缺失的初始缓存状态和数据保持未知。

默认会话是四核 IRIW，实际运行于 **4 RNF、4 HNF、2 SNF 的 3×2 mesh**。四线程代码窗区分当前执行、事务来源和历史写入者；点击代码行可选择动态执行实例。RN/HN 节点状态展示两级 SAM 的地址、参数、XOR 中间结果和目标。

选择“CPU 协议测试 · 19 类 REQ”可查看全部 **19/19** 类真实 CPU 测试接口事务；左侧覆盖列表可直接跳到对应请求及调用代码。另有四核重试/victim 压力测试和三份 IRIW 地址轮换会话。原有九个示例继续保留。模型、接口 ABI、固定工具链及复现命令见 [扩展说明](docs/extended-litmus.md)。

## 原生 CHI L2

最新实现已按固定昆明湖 v2 / CoupledL2 的触发条件覆盖 **12 种实际 REQ**，包含完整行写 MakeUnique、三类 CPU CBO、WriteCleanFull、WriteEvictOrEvict 双分支和 Normal non-cacheable 读写。四核 CPU 基准及压力测试通过；新增维护数据检查一直追踪到实际内存端口。运行 `bash scripts/run-native-rtl.sh` 可复现；[实现、12 类请求统计和验证边界](docs/native-l2-rtl-alignment.md) 记录本次结果，可本地导入[新回放文件](build/native-rtl-run2/session.chi.jsonl)。以下旧会话保留作为此前普通访存验证记录。

新增 `ChiNativeL2` 自己维护 cache tag/data、MSHR、snoop、victim 和 retry 状态，路径为 **KMH CPU → classic L1 → ChiNativeL2 → CHI NoC → HN → ChiNativeMemory → SimpleMemory**。普通 CPU load/store/fence 已驱动四核数据自检、脏数据转移、权限升级、容量替换和重试压力测试通过；L2 在运行时直接发送原生 CHI。网页默认的旧 IRIW 等 CPU 示例仍使用 classic L2 + Cache2ChiBridge，原生会话需本地导入 [`examples/native-l2/run.chi.jsonl`](examples/native-l2/run.chi.jsonl)。

已有包含 `ChiNativeL2` 的最新版 gem5 构建时，从 WSL 项目目录运行：

```bash
bash scripts/run-native-l2.sh
python3 scripts/check-native-wire.py --input build/native-l2-run/raw.jsonl --output build/native-l2-run/native-wire-audit.json
```

随后在 Windows 转为回放文件；`h-native-256` 保留实际 flit 字段、ID、数据及 tick，不补消息或修正协议：

```powershell
New-Item -ItemType Directory -Force examples/native-l2 | Out-Null
.\build\chi-refine.exe --input build/native-l2-run/raw.jsonl --output examples/native-l2/run.chi.jsonl --profile h-native-256
```

这是 64B line / 256-bit DAT 的有限 Issue H profile。实际 baseline 与压力轨迹的独立原始协议 gate 均通过，CHIron 字段映射合计 41,670 条通过；CHIron transaction joint 尚未支持。详细实现选择、覆盖范围、压力测试及导出开关一致性证据见 [原生 L2 设计](docs/native-l2-design.md) 和 [验证记录](docs/native-l2-validation.md)。

原生 L2 的四核 IRIW 验证使用普通 CPU 指令，无 fence 和 reader `fence r,r` 两个版本各运行 128 轮：`bash scripts/run-native-litmus.sh iriw`、`bash scripts/run-native-litmus.sh iriw-fenced`。逐轮读值、禁止结果判定、此次发现并修复的 snoop 等待环，以及运行报告见 [原生 L2 litmus 验证](docs/native-l2-litmus.md)。

## 环境与固定来源

| 部分 | 配置 |
|---|---|
| gem5 | `makenma/XS-DSU-GEM5`，`65f3241a6cd486dffddaca251536f5c691af9a1f`，原 C++17 |
| CHIron | `RISMicroDevices/CHIron`，`55cd1e1d92142960af2b57c686fc9f35ce7286be` |
| 展开工具 | C++20，GCC 13+，nlohmann/json 3.11.3 |
| 前端 | React、TypeScript、Vite、Three.js；版本由 lockfile 固定 |
| 协议依据 | 项目内 `IHI0050H_amba_chi_architecture_spec.pdf`，B2.4 / B2.5 / B2.7 / B2.9 / B2.10 |

计划推荐 WSL2 + Ubuntu 22.04；当前机器已有的 WSL `Ubuntu` 实际为 **24.04.2、GCC 13.3**，本次验证使用该环境。gem5 构建放在 Linux 文件系统 `~/chi-flowgraph-work/gem5`，网页从 Windows 项目目录启动。没有修改 gem5 的全局 C++ 标准。

本机 Ubuntu 已迁移到 `D:\wsl\Ubuntu\ext4.vhdx`。Linux 路径保持不变，源码、工具链和构建产物随虚拟磁盘存储在 D 盘。

新环境在 WSL 中安装依赖，Ubuntu 22.04 需先确保配套 GCC/G++ 13 可用：

```bash
sudo apt-get update
sudo apt-get install build-essential git python3-dev python3-pip python3-venv \
  scons m4 zlib1g-dev libsqlite3-dev libboost-all-dev libprotobuf-dev \
  protobuf-compiler pkg-config curl unzip
bash /mnt/d/proj/chi_flowgraph/scripts/bootstrap-wsl.sh
```

`bootstrap-wsl.sh` 仅创建缺失的源码目录并核对固定 commit。`apply-gem5.py` 核对目标文件与基线 / 上次生成版本，遇到独立修改时拒绝覆盖。可用 `CHI_WORK_DIR` 指定 Linux 工作目录。

## 生成真实组件示例

```bash
# WSL
bash /mnt/d/proj/chi_flowgraph/scripts/collect-components.sh
```

此命令编译并运行原 HNF 回归，以及新增的 `FlowGraph.test`。测试直接使用真实 `HnfCoherencyController` 和 `HnfSLCSF`，RN / SN 为确定性的测试端点，不依赖 guest 程序。每个场景分别开、关导出器运行并比较 tick、请求结果、数据和缓存终态。

六个组件场景：miss→hit、dirty owner 供数、ReadUnique 失效、写回及 dirty victim 下游写入、RetryAck/P-Credit/重发、同地址等待。原始轨迹保存在 `examples/coarse`。

```powershell
# Windows，g++ 13+ 在 PATH 中；也可用 CXX 指定编译器
pnpm native:build
pnpm demo
```

`pnpm demo` 重建九个原有示例并保留已验收的 `litmus-*` 会话：六个组件场景、实际事件循环的 miss→hit 和 RN 重试，以及一个有序读变体。有序读通过 **显式** `--ordered-read` 配置，从组件 miss/hit 观测补出 `Order=01` 和 ReadReceipt；普通输入不自动改为有序读取。

## 实际 gem5 运行采集

**实际事件循环采集已通过。** 完整 RISCV 可执行文件在 D 盘 WSL 中构建成功；同一地址两次读取得到 miss→hit，RN 完成两笔请求，SN/HN 返回六拍数据，结束于 tick `800000`。导出器开关前后的模拟 tick、RN latency CSV 和系统统计一致，开启采集的 SN→HN→RN 数据逐字节一致。缓存终态及非零数据的开关一致性由组件测试另行验证。

第二个实际场景将 HN 在途名额限制为 1，让两笔请求以 1ns 间隔到达；第二笔经过 RetryAck / PCrdGrant 后重发并完成，开关导出器结果同样一致。原 RN 按 TxnID 查找信用的问题已修复，信用先后顺序、错误节点/类型和一份信用不能重复使用均有 RN 组件回归。

```bash
# WSL：第一次完整构建较慢
bash /mnt/d/proj/chi_flowgraph/scripts/collect-runtime.sh
# 已有构建产物，只重新运行采集和检查
bash /mnt/d/proj/chi_flowgraph/scripts/run-runtime.sh
```

系统路径为 `PyTrafficGen → Cache2ChiBridge → router → HomeNodeFull → Chi2ClassicMemBridge → SimpleMemory`。router 参与模拟但从导出的拓扑隐藏。固定版本的 NULL 构建受上游 CPU 依赖限制，因此使用 RISCV 可执行文件运行无 CPU 的流量系统。

运行脚本先检查 WSL 虚拟磁盘所在的 Windows 盘，默认要求至少 20 GiB 可用空间（可通过 `CHI_MIN_BUILD_FREE_GIB` 调整）。WSL 内 `df` 显示的虚拟容量不能代替宿主盘余量。gem5 构建和临时文件保存在 Linux 工作目录中；本机迁移后，它们都存储在 D 盘的虚拟磁盘内。

脚本对两个场景分别运行导出器关闭 / 开启的配置，比较模拟 tick、RN latency CSV 和系统统计，并检查实际 HNF miss→hit、重试次数和 SN/HNF 数据一致性。报告写入 `build/qa/runtime-report.json` 与 `runtime-retry-report.json`。详细源码接点和边界见 [实现状态](docs/implementation.md)。

其他 gem5 配置通过以下环境变量启用：

```bash
CHI_FLOWGRAPH_CONFIG=/path/capture-config.json \
CHI_FLOWGRAPH_OUT=/path/coarse.jsonl \
  /path/gem5/build/RISCV/gem5.opt your-config.py
```

配置包含 `manifest` 和可选的 `filter`。`manifest.endpointPorts` 必须精确列出端点 Port 名称到 NodeID 的映射；拓扑、时钟、SAM 应来自实际配置。过滤示例：

```json
{"filter":{"startTick":"100000","endTick":"900000","address":"0x4040","nodes":["HN0"]}}
```

所有过滤条件取交集。窗口过滤自动将 `complete` 设为 false，并清除未重新采样的初态。未完成事务只报告证据不足，不推断死锁。

## 离线展开接口

```powershell
.\build\chi-refine.exe --input examples/coarse/miss-hit.coarse.jsonl --output session.chi.jsonl --profile h-core-256
```

Linux 可独立编译，构建目录仍放在 Linux 文件系统：

```bash
cd /mnt/d/proj/chi_flowgraph
mkdir -p ~/chi-flowgraph-work/refiner
g++ -std=c++20 -O2 -Wall -Wextra -isystem vendor/json -isystem vendor/chiron \
  native/refine.cpp -o ~/chi-flowgraph-work/refiner/chi-refine
~/chi-flowgraph-work/refiner/chi-refine --input examples/coarse/miss-hit.coarse.jsonl \
  --output session.chi.jsonl --profile h-core-256
```

当前配置：64B cache line、256-bit DAT、52-bit PA、11-bit NodeID、12-bit 协议 ID；整行 DataID 为 0、2。超出配置的地址保留原始字符串和不支持说明。输入格式见 [schema 1.0](docs/format.md)。

`chi-refine` 保留原始记录，分别生成协议消息、ID 分配/释放和 ValidationResult。格式或 I/O 错误返回 2；协议失败写入 `status=fail`，仍输出可检查的 session，进程返回 0。

## 回归与性能复现

```powershell
pnpm check
pnpm test:native
python scripts/audit-sessions.py
pnpm benchmark:generate
# 保持 pnpm dev 运行；环境需有 Playwright 和 Chrome
pnpm test:browser
```

本机 Codex 的 Playwright 在其捆绑运行库中，运行脚本可用 `CHI_NODE_MODULES` 指向包含 `playwright` 的 `node_modules`；其他环境可安装 Playwright 到独立测试环境，脚本亦支持 `CHI_BROWSER` 指定浏览器 channel。

性能数据使用单独标为 `performance_fixture` 的合成文件，不冒充 gem5 轨迹。报告和截图在 `build/qa`。浏览器验收使用 100,000 事件、200 可见 flit、Chrome headless 1600×1000；结果见 `browser-report.json`。这是当前机器和浏览器配置的结果，不是所有桌面设备的性能保证。

已经对照提供的 Issue H PDF 检查字段、握手、ID 生命周期、重试、数据分拍和 BE，并修正发现的问题。独立示例核查见 `build/qa/protocol-audit.json`；修正说明、页码和验证边界见 [实现状态](docs/implementation.md)。

CHIron 执行 E.b 字段映射和 RNFJoint / SNFJoint 事务检查；本项目另行执行有限 Issue H 规则。可用 `--rotate-retry-id` 验证重发换协议 ID：固定 CHIron 版本的此项覆盖会标为 unsupported，本项目的身份和信用规则仍执行。OWO 有序写、CopyBack 不传数据的 Comp-only 变体保留原始视图。通过这些检查不表示实现了完整 Issue H，DMT/DCT/DWT、Atomic、DVM、RNI/HNI 不在首版范围内。

## 教学目录与并行回放

共有 50 个分级教学用例：20 个真实单 REQ 冷 SLC 用例、4 种真实 HNF 命中组合、8 个真实复杂场景、3 个 CopyBack/Snoop 推演及 15 个特殊协议场景推演。特殊场景按消息越序、重试信用、snoop 竞争、DCT 转发、数据合并取消与高级缓存机制分组，提供 C 示意、关键时刻、能力前提与手册页码。所有推演均明确标注，不计真实 CPU 覆盖。默认按物理事件时刻并行步进，点击代码直接定位请求，时序和事件列表并排联动。准备条件、证据区分与重现命令见 [教学说明](docs/teaching.md)。
