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

## 原生验证命令

在 VM 中执行：

```bash
bash scripts/run_all.sh
bash scripts/run_llm_demo.sh
```

这里的设计含义是：

- `run_all.sh` 验证默认离线、无外部模型依赖的完整主流程
- `run_llm_demo.sh` 验证 optional LLM adapter 的默认 mock provider 也能在 VM 中离线运行

## 需要保存的验证证据

建议至少保存以下材料：

1. `/etc/openEuler-release` 输出
2. `bash scripts/run_all.sh` 成功日志
3. `results/summary_report.md`
4. `/dev/shm` 无 `comembus_` 残留的检查结果

例如：

```bash
find /dev/shm -maxdepth 1 -name 'comembus_*' -print
```

没有输出即可作为“共享内存对象已清理”的证据。

## Docker 与 VM 的关系

Docker 适合做用户态兼容验证和快速 CI 风格测试，但它不等同于原生系统验证。

对于比赛材料或技术报告，建议明确区分：

- Docker：用户态兼容验证
- openEuler VM：原生系统验证

这样更能证明 CoMemBus 在目标 OS 环境中的可运行性与可复现性。
