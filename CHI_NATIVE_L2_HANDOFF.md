# CHI native L2 server handoff

本分支基于 `65f3241a6cd486dffddaca251536f5c691af9a1f`，已直接合入原生 CHI L2、
端点轨迹、CBO、取指修复及 Linux 验证工具。gem5 保持 C++17。

**从这里开始：[服务器构建与运行说明](util/chi-flowgraph/docs/native-linux-server.md)。**

- gem5 实际实现位于 `src/`；工具、前端和测试位于 `util/chi-flowgraph/`。
- 直接编译本分支，不要再次执行历史 `apply-*` 补丁安装器。
- 默认验证拓扑为 4 KMH CPU、3×2 CHI mesh、4 HN-F、2 SN-F。
- [交付验证状态](util/chi-flowgraph/validation/README.md)保留实际通过与失败记录。
- 完整 Linux 长测尚未完成，服务器新编译/运行需要生成自己的证据。
- 镜像、二进制、完整运行日志、大型生成 trace 和协议手册 PDF 不在 Git 中。
