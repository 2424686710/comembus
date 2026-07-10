# CoMemBus

CoMemBus 是一个面向比赛题目“多智能体低开销通信、状态传递与共享记忆机制”的可复现 MVP。v1.6 将 v1.3-v1.5 的 benchmark、可靠性、持久状态和二进制交换能力接入最终 release audit：

- 小消息通过 Unix Domain Socket 传输。
- 大对象通过 `multiprocessing.shared_memory` 共享。
- 消息中只传 `ObjectRef`，不复制 8MB 数据内容。
- 两个 mock agent 能完成发布、拉取、共享内存读取和 checksum 校验。

## MVP 已实现内容

当前仓库实现了这些基础能力：

- `comembus.protocol`：`ObjectRef`、`Message`、JSON 编解码、4 字节大端长度前缀 frame。
- `comembus.transport.uds`：AF_UNIX 客户端/服务端基础收发，多客户端线程处理，socket 文件清理。
- `comembus.object_store.shm_store`：基于 `SharedMemory` 的对象写入、读取、校验和删除。
- `comembus.object_store.lease_manager`：共享对象 lease、refcount、崩溃回收和 GC 统计。
- `comembus.reliability`：ACK/NACK、可见性超时、重复消息去重、队列背压和故障注入。
- `comembus.memory`：基于 SQLite 的 SharedBlackboard，共享记忆持久化、检索和复用。
- `comembus.capability`：`CapabilityRegistry` 和简单握手，用于 Agent 能力发现与选择。
- `comembus.collab`：text_mode 与 structured_mode 协作模式对比实验。
- `comembus.collab.embedding_state`：embedding 直接交换的 `EmbeddingState` / `EmbeddingRef`。
- `comembus.collab.embedding_codec`、`embedding_store`：float32 二进制 codec 和 Shared Memory EmbeddingRef。
- `comembus.codeact`：最小 CodeAct 沙箱，支持受限 Python 片段校验与隔离执行。
- `comembus.llm`：可选 LLM adapter 层，默认使用离线 `mock` provider。
- `comembus.state`：版本化 `TaskState`、`StatePatch`、SQLite/WAL 状态恢复和 patch rebase。
- `comembus.server`：支持 `register`、`publish`、`poll`、`ack`、`nack`、`renew_visibility`、`ping`、`shutdown` 的消息总线。
- `comembus.client`：面向 agent 的 UDS 客户端 API。
- `comembus.transport.adaptive`：按消息大小和接收者数量选择 `direct_uds` 或 `shm_ref`。
- `comembus.transport.calibrator`：用实测 direct UDS / SHM 延迟生成按 receiver 数区分的自适应阈值。
- `comembus.metrics`：线程安全的真实字节 recorder、统计函数和进程 CPU/RSS/context-switch 指标。
- `comembus.memory.ranking`、`quality`、`provenance`：检索排序、标注质量评估和记忆来源/版本/TTL。
- `examples/smoke_pubsub_shm.py`：8MB 共享内存发布/订阅 smoke demo。
- `examples/incident_diagnosis_mock/`：不依赖 LLM 的 mock 多 Agent 故障诊断 demo。
- `benchmarks/bench_transport.py`：比较 `direct_uds`、`shm_ref` 和 `adaptive` 三种传输模式。
- `benchmarks/bench_state_patch.py`：比较完整状态传递和 `StatePatch` 增量传递的字节开销。
- `benchmarks/bench_memory_reuse.py`：比较连续关联任务中的共享记忆复用收益。
- `benchmarks/bench_collaboration_modes.py`：比较纯文本协作和结构化协议协作的 token / 字节 / 步骤开销。
- `benchmarks/bench_ablation.py`：9 种公平基线与完整组件消融，默认 warmup 3 次、正式 30 轮。
- `benchmarks/bench_rigorous_transport.py`：比较 direct、SHM、固定 adaptive 和校准 adaptive。
- `benchmarks/bench_failure_recovery.py`：系统化执行 8 类 crash、重复、锁冲突、fallback 和 timeout 恢复场景。
- `benchmarks/bench_embedding_codec.py`：比较摘要、JSON 数组、float32 和 Shared Memory ref。
- `benchmarks/bench_memory_quality.py`：比较四种检索方法的 Precision/Recall/MRR/wrong reuse。
- `examples/incident_diagnosis_mock/scenarios.jsonl`：覆盖 `database_timeout`、`permission_denied`、`storage_full` 的丰富任务集。
- `examples/incident_diagnosis_mock/run_llm_agent_demo.py`：可选 LLM ReviewAgent demo，默认离线 mock。
- `examples/incident_diagnosis_mock/run_llm_multiagent_smoke.py`：可选 multi-agent LLM smoke，支持 planner/review 或 all。
- `examples/incident_diagnosis_mock/run_codeact_demo.py`：可选 CodeAct demo，返回受限沙箱执行结果。
- `examples/incident_diagnosis_mock/run_reliable_agent_demo.py`：可靠投递、对象租约、WAL 状态与 patch rebase 的端到端 Agent demo。
- `scripts/summarize_all_results.py`：汇总全部 benchmark CSV，生成 Markdown 和 JSON 报告。
- `scripts/run_all.sh`：顺序执行测试、demo、bench 和结果汇总。
- `scripts/run_ablation_bench.sh`、`scripts/run_rigorous_bench.sh`：运行 v1.3 严谨实验套件；不改变旧 `run_all.sh`。
- `scripts/run_failure_bench.sh`：运行 v1.4 failure injection 验收并生成 CSV。
- `scripts/run_embedding_bench.sh`、`run_memory_quality_bench.sh`：运行 v1.5 二进制交换和检索质量实验。
- `scripts/run_llm_demo.sh`：运行默认离线的 optional LLM demo。
- `scripts/run_remote_llm_smoke.sh`：在远程 OpenAI-compatible 环境变量已配置时运行 optional remote smoke。
- `scripts/run_llm_compare.sh`：运行 mock vs remote LLM 对比，并保存结构化 JSON 产物。
- `scripts/run_codeact_demo.sh`：运行 optional CodeAct sandbox demo。
- `scripts/run_reliable_agent_demo.sh`：运行可靠端到端 Agent demo 并检查共享内存残留。
- `scripts/run_release_validation.sh`：按固定顺序执行 openEuler 最终 release audit。
- `scripts/create_release_manifest.py`：归档结果文件 SHA-256、Git/Python/OS、测试数与 SHM 状态。
- `tests/`：基于 `unittest` 的协议、对象存储、端到端测试。

## 当前明确不包含

当前 MVP 不包含以下内容：

- LangChain、LangGraph
- FastAPI、Redis、ZeroMQ、RabbitMQ
- Web dashboard 或可视化管理界面
- 跨机器通信、分布式一致性、鉴权和复杂调度

说明：

- 默认离线流程不依赖任何远程 LLM API。
- `openai_compatible` 只作为 optional smoke provider 存在，失败时会自动 fallback 到 `mock`。
- `codeact` 只作为 optional sandbox demo 存在，不进入 `run_all.sh` 默认流程。
- `run_release_validation.sh` 只执行离线 mock LLM，并在启动时移除 API credential 环境变量。

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

运行可靠 multi-agent 集成 demo：

```bash
bash scripts/run_reliable_agent_demo.sh
```

执行最终 release audit：

```bash
bash scripts/run_release_validation.sh
```

成功后会生成 `results/release_manifest.json`。

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

运行 v1.3 完整组件消融：

```bash
bash scripts/run_ablation_bench.sh
```

运行 v1.3 transport 校准与严谨对比：

```bash
bash scripts/run_rigorous_bench.sh
```

运行 v1.4 failure injection benchmark：

```bash
bash scripts/run_failure_bench.sh
```

运行 v1.5 embedding codec 和 memory quality benchmark：

```bash
bash scripts/run_embedding_bench.sh
bash scripts/run_memory_quality_bench.sh
```

两个脚本默认固定 `random_seed=20260710`。消融对每个 mode/task 先 warmup 3 次，再记录 30 个正式轮次；transport profile 按规定使用 warmup 3 次和 20 个校准轮次，正式 transport 对比使用 30 轮。核心 benchmark 全部使用 Python 标准库和确定性 mock/replay 逻辑，不调用远程 LLM。

一键跑完整实验并生成汇总报告：

```bash
bash scripts/run_all.sh
```

运行 optional LLM demo：

```bash
bash scripts/run_llm_demo.sh
```

运行 optional multi-agent LLM smoke：

```bash
python3 examples/incident_diagnosis_mock/run_llm_multiagent_smoke.py --provider mock
```

运行 optional remote LLM smoke：

```bash
bash scripts/run_remote_llm_smoke.sh
```

运行 optional LLM compare：

```bash
bash scripts/run_llm_compare.sh
```

运行 optional CodeAct demo：

```bash
bash scripts/run_codeact_demo.sh
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

## v1.3 Rigorous Benchmark and Ablation Suite

v1.3 是新增的独立严谨实验层，保留旧 benchmark、`run_all.sh` 和已有 CSV 格式。新增字段只出现在新结果中；`send_frame`、`recv_frame`、`SharedMemoryObjectStore` 的 recorder 参数均为可选参数，不启用时保持原行为。

消融的 9 个模式为：

- `text_full_context`
- `text_summary`
- `json_full_state`
- `structured_no_shm`
- `structured_no_patch`
- `structured_no_memory`
- `structured_no_embedding`
- `structured_no_capability`
- `structured_full`

所有模式读取同一份 `examples/incident_diagnosis_mock/scenarios.jsonl`，使用同一份日志和配置事实、相同的 5 个 Agent，以及固定的 Planner → Log → Config → Memory → Review → Planner 交接路径。`text_summary` 使用固定的字符串解析摘要函数，不调用模型；`json_full_state` 和 `structured_no_patch` 都传完整 `TaskState`；其余 `structured_no_*` 每次只移除一个命名组件。

v1.3 的字节口径是：

- `wire_bytes` 等于 recorder 观察到的发送端 frame 字节，包含每个 frame 的 4 字节长度头；不是根据 payload 大小推算。
- `received_bytes` 是接收端观察值，用于交叉校验，不会再叠加到 `wire_bytes`。
- `shm_bytes_written` / `shm_bytes_read` 单独记录共享内存读写，绝不混入 UDS wire bytes。
- `message_count` 是成功发送的真实 frame 数。

统计结果包括每轮 latency、CPU time、`ru_maxrss`、系统支持时的 voluntary/involuntary context switch，以及分组后的 mean、p50、p95、p99、样本标准差、95% 置信区间和 min/max。吞吐量按实际交付 payload bytes 与实测 wall-clock latency 计算。

特别说明：`estimated_tokens` 采用确定性的 `ceil(text_chars / 4)` 字符估算，`token_metric_type=character_estimate_4_chars_per_token`。它不是 tokenizer 或模型 API 返回的真实 token 数，不能作为真实模型计费 token 使用。

transport 校准覆盖 `1KB,4KB,16KB,64KB,256KB,1MB,8MB` 和 `receivers=1,2,4,8`。`AdaptiveTransportCalibrator` 会实跑 `direct_uds` 与 `shm_ref` 并写出 `results/transport_profile.json`；`AdaptiveTransportPolicy.from_profile(...)` 读取每个 receiver 数的 crossover threshold。profile 不存在或无效时，仍回退到原来的固定 64KB 策略。

新增产物为：

```text
results/ablation_bench.csv
results/rigorous_transport.csv
results/transport_profile.json
results/rigorous_summary.md
results/rigorous_metrics.json
results/figures/ablation_latency.svg
results/figures/ablation_tokens.svg
results/figures/latency_percentiles.svg
results/figures/transport_crossover.svg
```

完整实验口径见 [docs/benchmark_methodology.md](docs/benchmark_methodology.md)。

## v1.4 Reliable Delivery, Object Lifecycle and State Recovery

v1.4 在既有协议和 client/server API 上增量扩展可靠性。旧 `publish(topic, payload)` 和 `poll(topic)` 仍可直接使用：旧 `poll` 在返回 payload 时自动 ACK，保持原来的消费即移除行为。需要显式可靠投递时使用：

```python
result = producer.publish("jobs", payload, message_id="stable-business-id")
delivery = consumer.poll_reliable(
    "jobs",
    consumer_agent="worker-1",
    visibility_timeout=30.0,
)
consumer.renew_visibility(delivery["message_id"], visibility_timeout=60.0)
consumer.ack(delivery["message_id"], result={"status": "done"})
```

也可以调用 `nack(message_id)` 立即重新入队。显式 poll 后消息进入 invisible 集合；deadline 前未 ACK 会自动重新可见，下一次投递的 `delivery_attempt` 增加。ACK 后 `DedupStore` 保存业务结果，相同 `message_id` 再次 publish 时直接返回 `duplicate_suppressed=true` 和原结果，不会再次入队。`AgentBusServer(max_queue_size=N)` 启用背压，容量耗尽时客户端收到明确的 `QueueFullError`。

协议 `Message` 兼容性新增：

- `message_id`
- `delivery_attempt`
- `created_at`
- `visibility_deadline`

旧 frame 中没有这些字段时，解码器会补默认值；现有 `type/topic/payload` 语义不变。

共享内存生命周期由 `ObjectLeaseManager` 管理。注册记录包含 owner、consumer 集合、refcount、deadline、state 和访问时间。重复 acquire/release 是幂等的；正常释放到 refcount 0 后仍会等待 lease 到期；消费者崩溃未 release 时，GC 在 lease 到期后把遗留 holder 归零并 unlink。`get_stats()` 报告 `leaked_object_count` 和 `reclaimed_object_count`。

持久状态使用 `SQLiteStateManager`：

- 强制文件数据库进入 `PRAGMA journal_mode=WAL`。
- `states` 保存最新 snapshot，`patches` 保存每个成功 patch。
- snapshot 更新和 patch 日志写入在同一个 `BEGIN IMMEDIATE` 事务中。
- SQLite locked/busy 使用有界退避重试；耗尽后抛出 `SQLiteBusyError`。
- `recover(task_id)` 支持进程重启恢复；`compact(task_id)` 固化最新 snapshot 并删除已合并 patch。
- `PatchRebaser` 允许 list append 和不同 facts key 合并；同一标量字段或同一 facts key 的并发修改抛出 `PatchConflictError`。

failure benchmark 覆盖：consumer crash 重投、重复消息、ObjectRef holder crash、并发 patch、coordinator commit 后 crash、SQLite lock、LLM endpoint fallback、CodeAct timeout。结果写入：

```text
results/failure_injection.csv
```

所有预期故障都必须被明确观察并完成恢复；意外异常会写入 `error`、将 `success` 设为 false，并使脚本返回非零状态。完整设计见 [docs/reliability_design.md](docs/reliability_design.md)。

## v1.5 Binary Embedding Exchange and Memory Retrieval Quality

原有 `HashEmbeddingEncoder`、JSON `EmbeddingState` 和 JSON `EmbeddingRef` 保留。v1.5 另外提供 `EmbeddingBinaryCodec`，使用 little-endian IEEE-754 float32：

```python
binary = EmbeddingBinaryCodec.encode_float32(vector)
restored = EmbeddingBinaryCodec.decode_float32(binary, dim=len(vector))
```

`SharedEmbeddingStore.put_vector()` 把这些二进制 bytes 写入共享内存，并返回新的 shared `EmbeddingRef`，其中包含完整 `ObjectRef`、`dim`、`dtype=float32` 和 SHA-256 checksum。接收端使用 `open_memoryview()` 直接验证和解码 shared buffer，不先复制为整段 Python `bytes`。旧 JSON 通路仍可作为兼容回退。

Embedding benchmark 比较：

- `summary_text`
- `embedding_json`
- `embedding_float32`
- `embedding_ref`

维度为 32/64/128/384/768，每组默认 warmup 3 次并记录 30 轮。当前实测平均 UDS wire bytes 为：短摘要 181.6、JSON 5893.0、float32 1620.4、shared ref 389.8。这个结果说明 binary 对完整向量显著小于 JSON，但短摘要仍可能比固定大小的 ref 元数据更小；benchmark 不预设 binary 对短文本一定占优。

`MemoryUnit` 向后兼容新增：

- `content_hash` 和同内容去重
- `version`、`valid_from`、`expires_at`
- `parent_memory_ids`、`superseded_by`
- `provenance`

旧 SQLite memory 表会自动增加新列并回填 content hash。搜索默认排除尚未生效、已过期或已被 supersede 的记录；直接按 ID 获取仍可用于审计历史。

Memory quality benchmark 使用 5 个 family、40 条 corpus memory 和 30 条 query。每个 family 至少有 2 个关键词相似但根因不同的 hard negative，同时包含过期策略和相互矛盾/被修正的记忆。query 只传可观察线索标签，不传标准答案 family tag。输出 Precision@1/3、Recall@1/3、MRR、`wrong_reuse_rate`、stale rejection、query latency 和 task success，而不是把任意命中都当作成功。

当前扩展数据集中 keyword 的 MRR 为 0.8335、wrong reuse 为 0.2333；hash embedding 的 MRR 为 0.5543、wrong reuse 为 0.5667；tag/hybrid 的 MRR 为 1.0、wrong reuse 为 0；所有方法 stale rejection 都为 1.0。详细语料和指标口径见 [docs/memory_quality.md](docs/memory_quality.md)。

## v1.6 Final Integration and Release Audit

可靠 Agent demo 使用真实 UDS `AgentBusClient` / `AgentBusServer`，而不是直接调用队列内部实现。LogAgent 第一次 `poll_reliable` 后取得 ObjectRef 并模拟崩溃，不发送 ACK；visibility timeout 后同一 message 以 attempt 2 重投。retry agent 执行业务一次并 ACK，随后相同 message ID 的 publish 由 `DedupStore` 返回已处理结果。

同一初始 TaskState 同时交给 LogAgent 和 ConfigAgent。Config patch 先通过 `SQLiteStateManager` 的 WAL 事务提交；Log patch 因旧版本被拒绝，再由通用 `PatchRebaser` 验证无字段冲突后重放。Coordinator 关闭并重新打开 SQLite manager，通过 `recover(task_id)` 恢复版本 3。崩溃 LogAgent 遗留的共享内存 holder 在 lease 到期后由 `ObjectLeaseManager.collect_expired()` 回收。

CodeAct worker 保留 AST 白名单和 wall-clock timeout，并在子进程执行前设置 `RLIMIT_CPU`、`RLIMIT_AS`、`RLIMIT_FSIZE`、`RLIMIT_NOFILE`、`RLIMIT_NPROC`。stdout 最多保留 4096 字符并显式报告截断；worker 使用单向 Pipe 回传，不需要在 `RLIMIT_NPROC=0` 后创建 Queue feeder thread。

最终验证入口按顺序运行环境检查、所有测试、旧主流程、可靠 demo、v1.3-v1.5 benchmark、failure injection、离线 LLM demo、CodeAct demo和 `/dev/shm` 检查：

```bash
bash scripts/run_release_validation.sh
```

任一核心命令失败会立即停止。脚本保留本轮新生成且被 Git 忽略的 CSV/JSON 证据，但会恢复既有 tracked SQLite/SVG/Markdown 结果基线，因此从干净 commit 启动时结束后仍保持工作树干净。最后生成的 `results/release_manifest.json` 包含当前 Git commit、Python/OS 版本、动态发现的测试总数、全部结果文件 SHA-256、生成时间和共享内存残留数；manifest 不读取或保存 API Key。

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

当前支持三个 provider：

- `mock`
- `local_http`
- `openai_compatible`

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

## OpenAI-Compatible Remote LLM

v0.11 新增了 `openai_compatible` provider，用于接入远程兼容 `chat/completions` 的服务，例如：

- DeepSeek
- Qwen / DashScope compatible mode
- 其他 OpenAI-compatible API

推荐通过环境变量配置：

```bash
export COMEMBUS_LLM_ENDPOINT=https://your-endpoint.example.com
export COMEMBUS_LLM_MODEL=your-model-name
export COMEMBUS_LLM_API_KEY=your-secret-key
```

这里有两个概念：

- `BASE_URL`：基础地址，例如 `https://api.deepseek.com`
- `endpoint`：完整请求地址，例如 `https://api.deepseek.com/chat/completions`

CoMemBus 现在两种都支持。如果 `COMEMBUS_LLM_ENDPOINT` 里只给了 base URL，客户端会自动补成 chat completions endpoint。

然后运行：

```bash
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
  --provider openai_compatible \
  --endpoint "$COMEMBUS_LLM_ENDPOINT" \
  --model "$COMEMBUS_LLM_MODEL"
```

也可以直接跑：

```bash
bash scripts/run_remote_llm_smoke.sh
```

如果希望把 mock 和 remote 的输出都保存成可复现实验产物，并直接对比它们的报告内容，可以运行：

```bash
bash scripts/run_llm_compare.sh
```

这个脚本至少会生成：

- `results/llm_mock_smoke.json`

如果远程环境变量已配置，还会额外生成：

- `results/llm_remote_smoke.json`

这些 JSON 可以直接查看：

- `root_cause`
- `report`
- `used_fallback`
- `total_tokens`
- `root_cause_correct`

DeepSeek 示例：

```bash
export COMEMBUS_LLM_ENDPOINT=https://api.deepseek.com
export COMEMBUS_LLM_MODEL=deepseek-chat
export COMEMBUS_LLM_API_KEY=your-deepseek-key
bash scripts/run_remote_llm_smoke.sh
```

Qwen / DashScope 示例：

```bash
export COMEMBUS_LLM_ENDPOINT=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
export COMEMBUS_LLM_MODEL=qwen-plus
export COMEMBUS_LLM_API_KEY=your-dashscope-key
bash scripts/run_remote_llm_smoke.sh
```

像 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` 这种已经是完整 endpoint 的地址，CoMemBus 不会重复拼接。

判断真实接入成功的关键信号是：

- `provider=openai_compatible`
- `used_fallback=false`

如果输出里出现：

- `provider=mock`
- `used_fallback=true`

就说明请求失败后已经自动回退到 mock。

注意：

- API Key 只能来自环境变量，不要写进代码，也不要提交到仓库
- 真实 API 只作为 optional smoke test
- 默认 benchmark 仍使用 mock / replay / deterministic 逻辑，保证可复现

默认 benchmark 不使用远程 LLM 的原因是：

- openEuler 离线复现不能依赖外网
- 真实 API 的延迟、可用性和配额都不稳定
- benchmark 当前要验证的是可控的 AI Infra 路径，而不是在线模型波动

因此真实 LLM 目前主要用于验证：

- OpenAI-compatible provider 是否可插拔接入
- `LLMReviewAgent` 是否能生成自然语言报告
- 真实 `usage.total_tokens` 和 report 差异是否已经被保留下来

`bash scripts/run_remote_llm_smoke.sh` 的行为是：

- 若 `COMEMBUS_LLM_ENDPOINT`、`COMEMBUS_LLM_MODEL`、`COMEMBUS_LLM_API_KEY` 任意一个未配置，则输出 `SKIP: remote LLM env not configured` 并退出 `0`
- 若环境已配置，则依次运行单 Agent LLM demo 和 multi-agent LLM smoke
- 若任一调用发生 fallback，则脚本输出 warning，但仍保留 smoke 结果，便于快速排查远程兼容性

## Minimal CodeAct Sandbox

v0.12 新增了一个可选的最小 CodeAct sandbox，用来证明 CoMemBus 也能承载“结构化 facts -> 受限代码动作 -> 结构化结果返回”这一类工具调用。

当前设计原则是：

- 默认不进入 `run_all.sh`
- 不依赖第三方库
- 不允许访问文件、网络或系统命令
- 不允许 `import os`、`open`、`eval`、`exec`、`compile`、`__import__`
- 使用 `multiprocessing.Process` 做轻量隔离
- 默认超时 2 秒
- 子进程设置 CPU、地址空间、文件大小、文件描述符和子进程数量 rlimit
- stdout 限制为 4096 字符并返回 `stdout_truncated`

运行方式：

```bash
bash scripts/run_codeact_demo.sh
```

成功时会输出：

```text
OK: codeact demo completed
```

更完整的限制说明见：

- [docs/codeact_sandbox.md](docs/codeact_sandbox.md)

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
bash scripts/run_llm_compare.sh
python3 scripts/generate_result_figures.py
bash scripts/run_remote_llm_smoke.sh
```

## 设计说明

设计思路和后续扩展方向见 [docs/mvp_design.md](docs/mvp_design.md)。

原生 openEuler 虚拟机验证步骤见：

- [docs/openeuler_vm_validation.md](docs/openeuler_vm_validation.md)
