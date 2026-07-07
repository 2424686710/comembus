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
- `comembus.memory`：基于 SQLite 的 SharedBlackboard，共享记忆持久化、检索和复用。
- `comembus.capability`：`CapabilityRegistry` 和简单握手，用于 Agent 能力发现与选择。
- `comembus.collab`：text_mode 与 structured_mode 协作模式对比实验。
- `comembus.collab.embedding_state`：embedding 直接交换的 `EmbeddingState` / `EmbeddingRef`。
- `comembus.llm`：可选 LLM adapter 层，默认使用离线 `mock` provider。
- `comembus.state`：版本化 `TaskState`、`StatePatch` 和内存态状态管理器。
- `comembus.server`：支持 `register`、`publish`、`poll`、`ping`、`shutdown` 的内存消息总线。
- `comembus.client`：面向 agent 的 UDS 客户端 API。
- `comembus.transport.adaptive`：按消息大小和接收者数量选择 `direct_uds` 或 `shm_ref`。
- `examples/smoke_pubsub_shm.py`：8MB 共享内存发布/订阅 smoke demo。
- `examples/incident_diagnosis_mock/`：不依赖 LLM 的 mock 多 Agent 故障诊断 demo。
- `benchmarks/bench_transport.py`：比较 `direct_uds`、`shm_ref` 和 `adaptive` 三种传输模式。
- `benchmarks/bench_state_patch.py`：比较完整状态传递和 `StatePatch` 增量传递的字节开销。
- `benchmarks/bench_memory_reuse.py`：比较连续关联任务中的共享记忆复用收益。
- `benchmarks/bench_collaboration_modes.py`：比较纯文本协作和结构化协议协作的 token / 字节 / 步骤开销。
- `examples/incident_diagnosis_mock/scenarios.jsonl`：覆盖 `database_timeout`、`permission_denied`、`storage_full` 的丰富任务集。
- `examples/incident_diagnosis_mock/run_llm_agent_demo.py`：可选 LLM ReviewAgent demo，默认离线 mock。
- `scripts/summarize_all_results.py`：汇总全部 benchmark CSV，生成 Markdown 和 JSON 报告。
- `scripts/run_all.sh`：顺序执行测试、demo、bench 和结果汇总。
- `scripts/run_llm_demo.sh`：运行默认离线的 optional LLM demo。
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

运行 state patch benchmark：

```bash
bash scripts/run_state_bench.sh
```

运行 memory reuse demo：

```bash
python3 examples/incident_diagnosis_mock/run_memory_reuse_demo.py
```

运行 memory reuse benchmark：

```bash
bash scripts/run_memory_bench.sh
```

运行 collaboration modes demo：

```bash
python3 examples/incident_diagnosis_mock/run_collaboration_modes_demo.py
```

运行 collaboration benchmark：

```bash
bash scripts/run_collaboration_bench.sh
```

生成结果图表：

```bash
python3 scripts/generate_result_figures.py
```

运行大规模 stress benchmark：

```bash
bash scripts/run_stress_bench.sh
```

一键跑完整实验并生成汇总报告：

```bash
bash scripts/run_all.sh
```

运行 optional LLM demo：

```bash
bash scripts/run_llm_demo.sh
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

## Result Figures

v0.10 新增了一个纯标准库 SVG 出图脚本：

```bash
python3 scripts/generate_result_figures.py
```

默认读取：

- `results/transport_bench.csv`
- `results/state_patch_bench.csv`
- `results/memory_reuse_bench.csv`
- `results/collaboration_bench.csv`
- `results/summary_metrics.json`

并输出到：

```text
results/figures/
```

当前至少会生成：

- `transport_latency.svg`
- `state_patch_bytes.svg`
- `collaboration_tokens.svg`
- `collaboration_latency.svg`
- `memory_reuse.svg`
- `capability_embedding_counts.svg`

这样可以直接把 benchmark 结果放进技术报告、PPT 或比赛材料里，而且不需要 `matplotlib`、`numpy`、`pandas`。

## Stress Benchmark

v0.10 还新增了：

```bash
bash scripts/run_stress_bench.sh
```

它会在 `results/stress/` 下运行三组更大规模对比：

- `--tasks 30 --text-context-bytes 16384`
- `--tasks 30 --text-context-bytes 65536`
- `--tasks 60 --text-context-bytes 65536`

并输出每组的：

- 行数
- `text_mode total_tokens`
- `structured_mode total_tokens`
- `token_saving_ratio`
- `latency_saving_ratio`
- `structured_mode memory_hit_rate`
- `root_cause_correct` 是否全部为 `true`

这里的 richer task set 不再局限于 12 条原始样例；当轮数超过默认场景数时，benchmark 会基于当前 family 结构做循环扩展，继续验证连续任务稳定性。

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

## StatePatch

`comembus.state` 新增了一个轻量级版本化状态层，目标是在多 Agent 交接任务上下文时，只发送变化部分，而不是每次都重发完整状态。

核心对象包括：

- `TaskState`：完整任务状态快照，带 `version`。
- `StatePatch`：基于 `expected_version` 的增量更新。
- `InMemoryStateManager`：用于 demo 和 benchmark 的内存态状态管理器。

默认思路是：

- 初始时可以创建一个完整 `TaskState`
- 某个 agent 完成一步后，只发送 `StatePatch`
- 接收方按版本校验并应用补丁
- 成功后状态版本自动 `+1`

这样做的意义是：

- 多 agent 交接时减少重复状态传输
- facts 很多时，补丁通常远小于完整状态
- 版本号可以尽早发现并发更新冲突

`bash scripts/run_state_bench.sh` 会生成：

```text
results/state_patch_bench.csv
```

用于比较 `full_state` 与 `patch` 在 `small`、`medium`、`large` 三种状态规模下的字节开销。

## SharedBlackboard

`comembus.memory` 提供一个可持久化、可检索、可复用的 SharedBlackboard，用于保存多 Agent 执行过程中的中间结果、摘要、证据链、错误原因和经验片段。

核心对象包括：

- `MemoryUnit`：一条黑板记忆，包含来源 agent、任务主题、类型、摘要、内容、标签、置信度和元数据
- `SQLiteMemoryStore`：把记忆和 embedding 持久化到 SQLite
- `HashEmbeddingEncoder`：基于分词和哈希的固定维度轻量语义向量
- `SharedBlackboard`：统一封装写入、关键词检索、标签检索、语义检索和综合检索

当前语义向量不是外部模型 embedding，而是纯标准库实现的 hash embedding。这让模块可以：

- 不依赖在线 API
- 不依赖外部模型或向量数据库
- 在 openEuler / Python 标准库环境中直接运行

这部分能力对赛题里的“共享记忆存储、检索、复用”很关键，因为它让不同 agent 不仅能共享对象和状态，也能复用历史经验片段。

`python3 examples/incident_diagnosis_mock/run_memory_reuse_demo.py` 会演示跨任务记忆复用：

- Task 1 写入 evidence、summary、strategy
- Task 2 在完整分析前先检索历史记忆
- 若命中相关记忆，则复用历史 strategy 并跳过部分分析步骤

`bash scripts/run_memory_bench.sh` 会生成：

```text
results/memory_reuse_bench.csv
```

并输出 `memory_hit_count` 和 `memory_hit_rate`。

## Text Mode vs Structured Mode

v0.7 新增了一个可复现实验，用来比较：

- `text_mode`：纯文本协作基线
- `structured_mode`：结构化协议协作模式

`text_mode` 模拟传统多 agent 系统里反复传递完整自然语言上下文的方式。它会在每次交接里重复发送：

- task goal
- full context
- log summary
- previous facts
- next instruction
- expected output format

因此它的 `text_chars`、`approx_tokens` 和 `protocol_bytes` 会明显偏高。

`structured_mode` 则复用当前已有能力：

- `action_type`
- `params`
- `result`
- `AgentCapability`
- `ObjectRef`
- `StatePatch`
- `MemoryRef`（直接用 `memory_id`）

其中：

- 大日志不走 UDS 文本，而是写一次 Shared Memory，再通过 `ObjectRef` 传递
- 任务状态不重传完整上下文，而是通过 `StatePatch` 交接
- 历史经验不重传完整文本，而是通过 `SharedBlackboard` 搜索后传 `memory_ref`

`python3 examples/incident_diagnosis_mock/run_collaboration_modes_demo.py` 会输出：

- `text_mode approx_tokens`
- `structured_mode approx_tokens`
- `text_mode text_chars`
- `structured_mode text_chars`
- `token_saving_ratio`
- `root_cause_correct`

`bash scripts/run_collaboration_bench.sh` 会生成：

```text
results/collaboration_bench.csv
```

并统计输出：

- `text_mode total_tokens`
- `structured_mode total_tokens`
- `token_saving_ratio`
- `text_mode total_latency_ms`
- `structured_mode total_latency_ms`
- `latency_saving_ratio`
- `structured_mode memory_hit_rate`
- `structured_mode total_saved_steps`

这组实验对应赛题里“纯文本协作模式”和“结构化协议协作模式”的直接对照，也有助于解释为什么结构化协议能更好地压缩重复上下文和 token 开销。

v0.8 在这组实验上继续做了三项增量扩展：

- Capability Discovery：`structured_mode` 会先注册 Planner / Log / Config / Review / Memory 五类能力，再通过 `select_agent(action_type)` 选择目标 agent，而不是写死分发对象。
- Embedding Direct Exchange：日志摘要会生成 `EmbeddingState` 和 `EmbeddingRef`，以非文本状态形式在结构化消息里传递，并统计 `embedding_state_count`、`embedding_state_bytes`。
- Rich Scenario Task Set：benchmark 默认可读取 `examples/incident_diagnosis_mock/scenarios.jsonl`，覆盖 `database_timeout`、`permission_denied`、`storage_full` 三个任务族。

`results/collaboration_bench.csv` 现有字段保持不变，并兼容性新增：

- `scenario_family`
- `capability_count`
- `capability_discovery_count`
- `embedding_state_count`
- `embedding_state_bytes`

这意味着 benchmark 不再只围绕单一“wrong database port”样例，而是可以比较多任务族场景下：

- 共享记忆是否命中
- 能力发现是否发生
- embedding 非文本状态是否被传递
- structured_mode 是否仍然明显低于 text_mode 的 token 开销

## Optional LLM Agent

v0.9 新增了一个完全可选的 `comembus.llm` 接入层。它的目标不是让 CoMemBus 依赖外部模型，而是在不破坏当前 openEuler 离线复现实验的前提下，提供一个“如果你本地已经有模型服务，就可以接上去”的薄适配层。

当前支持两个 provider：

- `mock`
- `local_http`

默认一定是 `mock`，这意味着：

- 没有 API Key 也能跑
- 没有网络也能跑
- `run_all.sh` 不需要改
- openEuler 默认复现实验不受影响

`mock` provider 会根据结构化 facts 中的关键词，稳定返回确定性 root cause 和 report，例如：

- `database timeout` + `wrong database port`
- `permission denied`
- `storage full`

如果你本地已经启动了一个 OpenAI-compatible chat completions 接口，可以额外使用：

```bash
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
  --provider local_http \
  --endpoint http://127.0.0.1:8000/v1/chat/completions \
  --model your-local-model
```

或设置环境变量：

```bash
export COMEMBUS_LLM_ENDPOINT=http://127.0.0.1:8000/v1/chat/completions
export COMEMBUS_LLM_MODEL=your-local-model
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py --provider local_http
```

当前 `local_http` provider 支持：

- 显式 `--model`
- 环境变量 `COMEMBUS_LLM_MODEL`
- 默认值 `local-model`

运行 demo 时会额外输出：

- `provider=...`
- `model=...`
- `used_fallback=true|false`

其中：

- `provider=local_http` 且 `used_fallback=false` 表示真实本地模型调用成功
- `provider=mock` 且 `used_fallback=true` 表示本地 HTTP 调用失败后已经回退到 mock

但这里有两个设计原则：

- 本地 HTTP provider 失败、超时、响应异常时必须自动 fallback 到 `mock`
- 不推荐把在线 API 当作比赛默认依赖

原因很简单：CoMemBus 当前强调的是“低开销通信、状态传递、共享记忆、可复现实验”，不是把外部网络和线上服务当作默认前提。

`LLMReviewAgent` 也不会把 8MB 原始日志直接塞进 prompt，而是只复用：

- `TaskState.facts`
- 少量 evidence
- `SharedBlackboard` 返回的 memory summaries

这样可以保持 structured_mode 的上下文压缩优势。

更完整的 local HTTP 验证步骤见：

- [docs/llm_local_http_validation.md](docs/llm_local_http_validation.md)

## Mock Multi-Agent Demo

`examples/incident_diagnosis_mock/` 展示了 CoMemBus 在没有任何 LLM 框架的前提下，如何支撑多个独立 agent 进程协作：

- `PlannerAgent` 负责创建并发布初始 `TaskState`。
- `LogAgent` 通过 `ObjectRef` 读取至少 8MB 的共享内存日志，并生成 `StatePatch`。
- `ConfigAgent` 处理小配置文本，并生成 `StatePatch`。
- `ReviewAgent` 读取最终 `TaskState` 并生成 root cause 报告。

这个示例的重点不是“智能推理”，而是证明 CoMemBus 已经可以承载一个小型多 agent 工作流：

- 各 agent 是独立 `multiprocessing.Process`
- 所有控制消息都通过 `AgentBusClient` / `AgentBusServer`
- 大日志对象不会通过 UDS 全量复制
- 状态交接不再只靠零散消息，而是通过 `TaskState` + `StatePatch`
- 主流程里的 `InMemoryStateManager` 会做版本校验并应用 patch

运行这个 demo 时，日志里会展示：

- 初始状态版本
- 每个 patch 的 `expected_version`
- 每次 apply 后的新状态版本
- 最终汇总后的 `facts`

## Capability Discovery 与 Embedding Direct Exchange

当前 structured collaboration runner 会在每轮启动时构建一个 `CapabilityRegistry`，默认注册：

- `PlannerAgent`
- `LogAgent`
- `ConfigAgent`
- `ReviewAgent`
- `MemoryAgent`

Planner 不再通过硬编码字符串选择下游，而是按动作发现：

- `analyze_log`
- `check_config`
- `summarize_result`

随后，`LogAgent` 在输出结构化日志摘要时还会生成轻量 hash embedding：

- `EmbeddingState`：包含摘要、向量、维度、metadata
- `EmbeddingRef`：包含 checksum、dim、vector_bytes、summary

这样可以证明 CoMemBus 不只是在传文本、对象引用和状态补丁，也可以传递“轻量语义状态”。

## Result Summary

运行：

```bash
python3 scripts/summarize_all_results.py
```

或直接：

```bash
bash scripts/run_all.sh
```

会读取存在的 benchmark CSV，并生成：

```text
results/summary_report.md
results/summary_metrics.json
```

其中：

- `summary_report.md` 适合直接贴到实验记录或比赛材料里
- `summary_metrics.json` 适合后续脚本复用、做自动报告或表格抽取

如果某个 CSV 还不存在，脚本会跳过并在报告里写 warning，不会直接崩溃。

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
bash scripts/run_state_bench.sh
bash scripts/run_memory_bench.sh
bash scripts/run_collaboration_bench.sh
python3 scripts/summarize_all_results.py
bash scripts/run_llm_demo.sh
python3 scripts/generate_result_figures.py
```

## 设计说明

设计思路和后续扩展方向见 [docs/mvp_design.md](docs/mvp_design.md)。

原生 openEuler 虚拟机验证步骤见：

- [docs/openeuler_vm_validation.md](docs/openeuler_vm_validation.md)
