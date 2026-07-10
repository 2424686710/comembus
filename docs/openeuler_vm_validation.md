# openEuler VM Validation

这份文档对应 CoMemBus 在 openEuler 24.03-LTS-SP3 虚拟机里的原生系统验证，不依赖 Docker 作为唯一验证方式。

## VM 配置建议

- 4 CPU
- 8GB RAM
- 40GB disk
- NAT 网络

## openEuler 24.03-LTS-SP3 安装

1. 在虚拟机管理器中创建新虚拟机并挂载 openEuler 24.03-LTS-SP3 ISO。
2. 按默认引导完成安装，建议单磁盘自动分区。
3. 首次启动后确认系统版本：

```bash
cat /etc/openEuler-release
```

## 安装依赖

```bash
sudo dnf install -y git python3 python3-pip findutils procps-ng which
```

## 获取仓库

如果你通过 GitLink 或镜像托管分发 CoMemBus，可以直接 clone：

```bash
git clone <your-gitlink-or-git-url> CoMemBus
cd CoMemBus
```

## 原生最终验证命令

在 VM 中执行：

```bash
bash scripts/run_release_validation.sh
```

这里的设计含义是：

- 先验证 OS/Python/SQLite/SharedMemory 环境，再执行完整 unittest
- 顺序执行旧 `run_all.sh`、可靠 Agent demo、ablation、rigorous、failure、embedding 和 memory quality benchmark
- LLM 步骤固定使用离线 mock provider；release 脚本会移除 API credential 环境变量
- CodeAct 步骤验证 AST、timeout 和 Linux rlimit 后的正常执行
- 最后检查 `/dev/shm` 并生成 `results/release_manifest.json`

脚本使用 `set -euo pipefail`，任一核心命令失败都会立即停止，不能通过后续汇总掩盖失败。
验证开始时要求 Git 工作树干净；执行中产生的 tracked 结果变化会在 manifest 生成前恢复，因此审计成功后 `git status --porcelain` 仍为空。新生成的 benchmark CSV/JSON 和 release manifest 由 `.gitignore` 排除，可作为本机审计证据保留。

## 需要保存的验证证据

建议至少保存以下材料：

1. `/etc/openEuler-release` 输出
2. `bash scripts/run_all.sh` 成功日志
3. `results/summary_report.md`
4. `results/rigorous_summary.md`
5. `results/release_manifest.json`
6. `/dev/shm` 无 `comembus_` 残留的检查结果

例如：

```bash
find /dev/shm -maxdepth 1 -name 'comembus_*' -print
```

没有输出即可作为“共享内存对象已清理”的证据。

`release_manifest.json` 记录当前 Git commit、Python 版本、OS release、动态发现的测试总数、每个结果文件的 SHA-256、UTC 生成时间和 `shm_residue_count`。它不读取或保存 API Key。

## Docker 与 VM 的关系

Docker 适合做用户态兼容验证和快速 CI 风格测试，但它不等同于原生系统验证。

对于比赛材料或技术报告，建议明确区分：

- Docker：用户态兼容验证
- openEuler VM：原生系统验证

这样更能证明 CoMemBus 在目标 OS 环境中的可运行性与可复现性。
