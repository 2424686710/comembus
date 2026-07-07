# CoMemBus

CoMemBus 是一个面向比赛题目“多智能体低开销通信、状态传递与共享记忆机制”的第一阶段 MVP。当前目标不是实现完整多智能体平台，而是先验证一条最小闭环：

- 小消息通过 Unix Domain Socket 传输。
- 大对象通过 `multiprocessing.shared_memory` 共享。
- 消息中只传 `ObjectRef`，不复制 8MB 数据内容。
- 两个 mock agent 能完成发布、拉取、共享内存读取和 checksum 校验。

## MVP 已实现内容

当前仓库实现了这些基础能力：

- `comembus.protocol`：`ObjectRef`、`Message`、JSON 编解码、4 字节大端长度前缀 frame。
- `comembus.transport.uds`：AF_UNIX 客户端/服务端基础收发，多客户端线程处理，socket 文件清理。
- `comembus.object_store.shm_store`：基于 `SharedMemory` 的对象写入、读取、校验和删除。
- `comembus.server`：支持 `register`、`publish`、`poll`、`ping`、`shutdown` 的内存消息总线。
- `comembus.client`：面向 agent 的 UDS 客户端 API。
- `comembus.transport.adaptive`：按消息大小和接收者数量选择 `direct_uds` 或 `shm_ref`。
- `examples/smoke_pubsub_shm.py`：8MB 共享内存发布/订阅 smoke demo。
- `examples/incident_diagnosis_mock/`：不依赖 LLM 的 mock 多 Agent 故障诊断 demo。
- `benchmarks/bench_transport.py`：比较 `direct_uds`、`shm_ref` 和 `adaptive` 三种传输模式。
- `tests/`：基于 `unittest` 的协议、对象存储、端到端测试。

## 当前明确不包含

当前 MVP 不包含以下内容：

- LangChain、LangGraph 或任何在线 LLM API
- FastAPI、Redis、ZeroMQ、RabbitMQ
- Web dashboard 或可视化管理界面
- 跨机器通信、持久化存储、鉴权和复杂调度

## 本地运行

先检查环境：

```bash
bash scripts/check_env.sh
```

运行测试：

```bash
bash scripts/run_tests.sh
```

运行 8MB shared-memory demo：

```bash
bash scripts/run_demo.sh
```

运行 mock multi-agent incident diagnosis demo：

```bash
bash scripts/run_agent_demo.sh
```

运行 transport benchmark：

```bash
bash scripts/run_bench.sh
```

默认会生成：

```text
results/transport_bench.csv
```

CSV 字段包括：

- `mode`
- `selected_mode`
- `size_bytes`
- `receivers`
- `round`
- `latency_ms`
- `uds_payload_bytes`
- `shm_bytes_written`
- `checksum_ok`

其中：

- `direct_uds` 会把完整 payload 通过现有 UDS JSON frame 发布给每个 receiver。
- `shm_ref` 会把 bytes 写入共享内存一次，再把 `ObjectRef` 发布给每个 receiver。
- `adaptive` 会用 `AdaptiveTransportPolicy` 自动选择 `direct_uds` 或 `shm_ref`。
- 多 receiver 使用 `logs_r0`、`logs_r1`、`logs_r2` 这类 topic 模拟广播。
- benchmark 会对每轮数据执行 `sha256` 校验，并在异常时清理共享内存。

默认 adaptive 策略是：

- `size_bytes < 65536` 且 `receivers <= 1` 时，选择 `direct_uds`
- 其他情况选择 `shm_ref`

`selected_mode` 用来记录该轮实际落到哪条传输路径：

- `mode=direct_uds` 时，`selected_mode=direct_uds`
- `mode=shm_ref` 时，`selected_mode=shm_ref`
- `mode=adaptive` 时，`selected_mode` 可能是 `direct_uds` 或 `shm_ref`

说明：

- 当前 MVP 协议默认把 frame 限制在 1MB 以内，适合控制面消息。
- benchmark 为了比较 `direct_uds`、`shm_ref` 和 `adaptive` 在 8MB 数据上的差异，会只在 benchmark 进程内临时放宽这个上限，不修改核心运行时代码。

## Mock Multi-Agent Demo

`examples/incident_diagnosis_mock/` 展示了 CoMemBus 在没有任何 LLM 框架的前提下，如何支撑多个独立 agent 进程协作：

- `PlannerAgent` 负责发布 incident 诊断任务。
- `LogAgent` 通过 `ObjectRef` 读取至少 8MB 的共享内存日志并提取日志事实。
- `ConfigAgent` 处理小配置文本并发布配置事实。
- `ReviewAgent` 汇总两个事实流并生成最终 root cause 报告。

这个示例的重点不是“智能推理”，而是证明 CoMemBus 已经可以承载一个小型多 agent 工作流：

- 各 agent 是独立 `multiprocessing.Process`
- 所有控制消息都通过 `AgentBusClient` / `AgentBusServer`
- 大日志对象不会通过 UDS 全量复制
- 最终结果由 review agent 汇总发布

## 在 openEuler Docker 中运行

构建镜像：

```bash
docker build -f Dockerfile.openeuler -t comembus:openeuler .
```

运行测试：

```bash
docker run --rm --shm-size=256m comembus:openeuler
```

如果想进容器手动执行 demo，可以这样运行：

```bash
docker run --rm -it --shm-size=256m comembus:openeuler bash
```

进入容器后再执行：

```bash
bash scripts/check_env.sh
bash scripts/run_tests.sh
bash scripts/run_demo.sh
bash scripts/run_bench.sh
bash scripts/run_agent_demo.sh
```

## 设计说明

设计思路和后续扩展方向见 [docs/mvp_design.md](docs/mvp_design.md)。
