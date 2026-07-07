# CoMemBus MVP 设计说明

## 为什么小消息走 UDS，大对象走 Shared Memory

第一阶段 MVP 关注的是一个最小但完整的闭环，而不是追求完整分布式特性。控制类消息通常很小，例如 `register`、`publish`、`poll`、`ping` 和对象引用元数据，这类消息用 Unix Domain Socket 传输有几个直接好处：

- 本机进程间通信路径短，Linux 兼容性好，openEuler 可直接使用。
- Python 标准库 `socket` 已足够，不需要额外中间件。
- 请求/响应模型简单，便于先验证协议、错误处理和资源清理。

真正的大对象则不适合反复序列化和复制。对 8MB 这类数据，如果仍然走 JSON 或 socket 负载，控制面和数据面会混在一起，复制次数也会明显增加。使用 `multiprocessing.shared_memory.SharedMemory` 可以把数据放进进程共享区域，再通过小消息只传元数据引用，从而形成：

1. 控制面：UDS 发送轻量消息。
2. 数据面：Shared Memory 保存大字节对象。
3. 引用面：`ObjectRef` 把两者连接起来。

这正是比赛题目里“低开销通信、状态传递与共享记忆机制”的最小体现。

## ObjectRef 的作用

`ObjectRef` 是当前 MVP 的核心元数据对象，至少包含：

- `object_id`：逻辑对象 ID。
- `shm_name`：共享内存段名字。
- `size`：对象字节长度。
- `checksum`：基于 `sha256` 的完整性校验值。
- `created_at`：创建时间戳。

生产者把大对象写入共享内存后，发布的不是原始内容，而是 `ObjectRef`。消费者拿到引用后：

1. 根据 `shm_name` 打开共享内存。
2. 读取 `size` 字节。
3. 用 `checksum` 做校验。
4. 校验通过后再继续使用数据。

这种方式把“消息传递”和“对象共享”分层了，也为后续支持增量状态、对象生命周期管理和多副本策略留出了接口。

## 当前 MVP 的限制

当前版本刻意保持简单，只覆盖第一阶段验收目标：

- 仅支持单机 Linux / openEuler 环境。
- 仅使用 Python 标准库。
- 服务端消息队列是内存中的 `dict[str, list[dict]]`，无持久化。
- 没有鉴权、ACL、租约、对象引用计数和回收协调。
- `poll` 是简单拉取，不支持阻塞订阅、广播确认或回放。
- 共享内存对象需要显式 `unlink`，暂未实现后台 GC。
- 当前消息协议使用 JSON + 4 字节长度前缀，适合控制面，不适合承载复杂二进制负载。

这些限制是有意保留的，目的是先把最小闭环和测试打牢。

## Benchmark 口径

为了在不重构核心代码的前提下比较两条通路，当前仓库新增了 `benchmarks/bench_transport.py`，对两种模式做对照：

- `direct_uds`：完整 payload 通过 UDS 发送给每个 receiver。
- `shm_ref`：payload 先写一次共享内存，再通过 UDS 发送 `ObjectRef` 给每个 receiver。

当前 benchmark 的实现约束如下：

- 继续复用现有 `AgentBusServer` / `AgentBusClient`。
- 多 receiver 通过 `logs_r0`、`logs_r1`、`logs_r2` 这类 topic 模拟广播。
- `direct_uds` 下，每个 receiver 都会收到一份完整 payload。
- `shm_ref` 下，共享内存每轮只写一次，每个 receiver 只拿到 `ObjectRef`。
- 每轮都必须做 `sha256` 校验。
- 发生异常时，已经创建的共享内存段必须在 `finally` 中清理。

由于 MVP 的协议层默认限制 frame 体积不超过 1MB，这个 benchmark 会只在 benchmark 进程里临时放宽该限制，以便测量 8MB 负载下 `direct_uds` 与 `shm_ref` 的差异。这样可以保持核心代码不重构，同时仍然保留 MVP 默认的控制面安全边界。

`uds_payload_bytes` 的统计口径是“该轮真正承载 benchmark 数据的 UDS frame 总字节数”，包括：

- producer 发布 payload 到 server 的 frame。
- server 在 `poll` 响应里把 payload 或 `ObjectRef` 返回给 receiver 的 frame。

`shm_bytes_written` 则表示该轮写入共享内存的数据量。对 `shm_ref` 来说应当只写一次；对 `direct_uds` 则恒为 `0`。

## Adaptive Transport 设计

在 v0.3 中，仓库新增了 `comembus.transport.adaptive.AdaptiveTransportPolicy`，用于在不重构既有核心模块的前提下，为上层调用者提供一个简单可解释的通路选择器。

当前策略只看两个输入：

- `size_bytes`
- `receivers`

默认参数是：

- `direct_threshold_bytes = 65536`
- `prefer_shm_when_receivers_gt = 1`

默认判定规则是：

- 当 `size_bytes < 65536` 且 `receivers <= 1` 时，返回 `direct_uds`
- 否则返回 `shm_ref`

这样设计的原因很直接：

- 单接收者且消息很小的时候，直接走 UDS 更简单，不需要共享内存分配和清理。
- 一旦消息达到阈值，或者接收者数量增加，重复序列化和重复传输的成本会迅速放大，转向 `shm_ref` 更稳妥。

当前 adaptive 只负责“选哪条路”，不改动现有：

- UDS 协议实现
- Shared Memory 对象存储
- server/client 基础行为

也就是说，adaptive 是一个薄决策层，不是新的传输栈。benchmark 中的 `mode=adaptive` 会先调用 policy，再复用已经存在的 `direct_uds` 或 `shm_ref` 执行路径，并用 `selected_mode` 记录实际选择结果。

## Mock Multi-Agent Incident Diagnosis Demo

v0.4 新增了一个不依赖 LLM、LangChain 或外部服务的 mock 多 Agent demo，用来验证 CoMemBus 不只是“能传对象”，而是已经能承载一个简单的多进程协作工作流。

这个 demo 的流程是：

1. 主进程启动 `AgentBusServer`，并把至少 8MB 的日志写入共享内存。
2. `PlannerAgent` 发布 incident 任务。
3. `LogAgent` 只通过 `ObjectRef` 读取共享内存日志，提取日志事实。
4. `ConfigAgent` 读取小配置内容，提取配置事实。
5. `ReviewAgent` 汇总两个事实流，生成最终 root cause 报告。

这个示例有几个设计意义：

- 证明多个独立 `multiprocessing.Process` 可以同时通过 CoMemBus 协作。
- 证明大对象依旧可以停留在 shared memory，避免走 UDS 直接复制。
- 证明控制面和数据面已经可以自然分工：
  控制任务、事实发布、最终报告走 UDS。
  大日志对象走 Shared Memory + `ObjectRef`。
- 证明 adaptive transport 可以作为上层调度参考存在，但不要求改写现有核心传输实现。

这个 demo 依然保持 MVP 范围内的克制：

- 没有真实 LLM 调用。
- 没有复杂调度器、记忆系统或外部中间件。
- 没有真正的并行黑板推理，只是用 topic 串起一个明确的 mock workflow。

但它已经足够用来说明 CoMemBus 的通信抽象，能够支撑“多个 agent 分工协作 + 大对象共享”的比赛方向。

在 v0.5.5 中，这个 demo 进一步接入了 `TaskState` 和 `StatePatch`：

1. `PlannerAgent` 不再只发布零散任务，而是先创建初始 `TaskState`。
2. `LogAgent` 读取共享内存日志后，生成基于当前版本的 `StatePatch`。
3. `ConfigAgent` 读取配置后，也生成自己的 `StatePatch`。
4. demo 主进程持有 `InMemoryStateManager`，负责按版本应用 patch。
5. `ReviewAgent` 读取最终 `TaskState`，再输出 root cause 报告。

这样做的意义是：

- 把“事实更新”从普通消息提升为显式的状态补丁。
- 让状态版本成为 agent 交接协议的一部分。
- 证明 CoMemBus 可以承载“共享内存传大对象 + UDS 传控制消息 + StatePatch 传状态变化”的组合式 workflow。

当前这个接法仍然保持克制：

- `AgentBusServer` 本身不需要理解状态版本。
- 状态管理依然由 demo 进程内的 `InMemoryStateManager` 完成。
- patch 本身仍然通过普通 topic 消息发送，只是 payload 变成了 `StatePatch` dict。

这很适合作为下一阶段演进的过渡层：先在应用层证明版本化状态传递有效，再考虑未来是否把状态目录、冲突合并或共享黑板能力进一步系统化。

## StatePatch 状态传递机制

v0.5 新增了 `comembus.state`，用于表达“完整状态”和“增量状态变化”之间的区别。

核心对象有三个：

- `TaskState`：任务在某一时刻的完整状态快照。
- `StatePatch`：只描述这次交接发生了哪些变化。
- `InMemoryStateManager`：一个最小的版本化状态管理器，用来创建、读取、打补丁和做快照。

`TaskState` 适合表达完整上下文，例如：

- 当前目标是什么
- 当前 phase 在哪里
- 已完成步骤和待办步骤是什么
- 目前掌握了哪些 facts
- 产生了哪些 artifacts

但在多 Agent 协作里，很多时候一次交接只会发生很小的变化，例如：

- `phase` 从 `collecting` 变成 `reviewing`
- `completed_steps` 新增 1 项
- `facts` 里新加入 1 条观察

如果每次都把完整状态重发一遍，尤其是在 facts 越积越多的时候，控制面负载会越来越大。`StatePatch` 的价值就在这里：

- `set_fields`：适合覆盖单值字段，例如 `phase`
- `append_fields`：适合向步骤列表或错误列表追加内容
- `merge_dict_fields`：适合把新的 facts 或 artifacts 合并进已有状态

补丁还带有 `expected_version`。这意味着：

- 发送方知道自己是基于哪个版本做修改
- 接收方在应用前可以校验版本
- 如果版本不一致，直接抛出 `VersionConflictError`

这样做的收益有两个：

1. 节省字节数。很多场景下一个 patch 会远小于完整 `TaskState` JSON。
2. 提前暴露并发冲突。多个 agent 同时更新同一个任务时，不会悄悄覆盖彼此的结果。

当前版本只实现了内存态的最小闭环，不引入 SQLite 或分布式一致性组件，但已经足以证明“多 Agent 状态交接传 patch 比传 full state 更低开销”这一点。`benchmarks/bench_state_patch.py` 会构造 `small`、`medium`、`large` 三种状态规模，对比：

- 完整状态重发需要多少字节
- 同等语义的 `StatePatch` 需要多少字节
- 两者之间的缩减比例是多少

这为后续把 StatePatch 放进真实 agent workflow、共享黑板或更复杂的状态同步层提供了直接基础。

## SharedBlackboard 共享记忆设计

v0.6 新增了 `comembus.memory`，目标是让 CoMemBus 不仅能传消息、传大对象、传状态补丁，还能跨任务保存和复用“经验”。

这里的 SharedBlackboard 不是外部向量数据库或 LLM 记忆层，而是一个完全基于 Python 标准库实现的轻量共享记忆模块，核心由四部分组成：

- `MemoryUnit`：单条记忆的结构化元数据
- `HashEmbeddingEncoder`：轻量语义向量编码
- `SQLiteMemoryStore`：持久化存储
- `SharedBlackboard`：统一检索接口

### MemoryUnit 元数据设计

每条 `MemoryUnit` 至少记录：

- 它属于哪个任务
- 来自哪个 agent
- 属于什么任务主题
- 是 fact、evidence、summary、strategy、error 还是 artifact
- 摘要和完整内容
- 标签、置信度和额外元数据

这样设计的原因是：

- 便于按任务回看一段执行历史
- 便于按 agent 追踪谁产生了什么判断
- 便于把“日志证据”、“错误原因”、“复盘策略”区分开
- 便于后续演化出跨任务经验库

### 关键词 / 标签 / 轻量语义检索

检索层支持三种基本方式：

- 关键词检索：命中 `summary` / `content`
- 标签检索：命中 `tags`
- 轻量语义检索：基于 `HashEmbeddingEncoder`

`HashEmbeddingEncoder` 不是神经网络 embedding，而是：

1. 先对文本做轻量分词
2. 再用哈希把 token 投影到固定维度
3. 最后得到一个固定长度向量，并用 cosine similarity 比较相似度

它的优势是：

- 完全离线
- 只依赖标准库
- 足以对“database timeout wrong port”这类重复主题形成稳定相似度信号

虽然它不具备大型语义模型的泛化能力，但对于比赛 MVP 阶段的共享记忆检索已经足够轻量和可解释。

### 跨任务记忆复用实验设计

v0.8 在共享记忆之上继续补了四个实验层能力：Capability Discovery、Embedding Direct Exchange、Rich Task Set 和 Result Summary。

## 能力发现与协议映射

`comembus.capability` 的目标不是重写 bus，而是在现有结构化协作层之上增加一个轻量“谁能做什么”的索引：

- `CapabilityRegistry` 负责注册、查询、按动作发现、按角色发现和选择 agent
- `HandshakeRequest` / `HandshakeResponse` 提供一个最小握手格式

当前默认注册五类 mock agent 能力：

- `PlannerAgent`
- `LogAgent`
- `ConfigAgent`
- `ReviewAgent`
- `MemoryAgent`

这样一来，Planner 在 structured mode 中不需要继续硬编码“把任务发给哪个名字的 agent”，而是可以根据动作做发现：

- `analyze_log`
- `check_config`
- `summarize_result`

这一步的意义主要有三点：

1. 把“任务分配逻辑”从字符串耦合切到显式能力。
2. 让 benchmark 可以统计 `capability_count` 和 `capability_discovery_count`。
3. 为后续扩展更复杂的多 agent 编排留出协议位置。

## Embedding 直接交换设计

共享内存和 `ObjectRef` 解决的是“大字节对象如何低开销共享”的问题，但多 agent 协作里还有另一类非文本状态：轻量语义表示。

v0.8 新增的 `EmbeddingState` / `EmbeddingRef` 解决的是这个问题：

- `EmbeddingState` 保存摘要、向量、维度、来源和元数据
- `EmbeddingRef` 保存 checksum、字节数和摘要

这里的 embedding 不是外部模型，也不是向量数据库，而是继续复用现有 `HashEmbeddingEncoder`：

1. 对摘要文本分词
2. 哈希投影到固定维度
3. 归一化成轻量向量

这样做有几个现实好处：

- 只用标准库
- 不依赖在线 API
- 不依赖外部模型
- 可以证明 CoMemBus 已经支持“文本之外的状态交换”

在 structured mode 中，日志摘要会被编码成 `EmbeddingState`，随后：

- 在结构化消息中携带 `embedding_state` / `embedding_ref`
- 在最终报告里记录 non-text state 已经交付
- 在 benchmark 中统计 `embedding_state_count` 和 `embedding_state_bytes`

这让实验结果更贴近赛题中的“状态传递机制”，而不是只停留在文本压缩。

## 多任务集设计

前几个版本的样例主要围绕单一 “wrong database port” 场景。v0.8 把任务集扩成了三类任务族：

- `database_timeout`
- `permission_denied`
- `storage_full`

每个 family 至少 4 个任务，总计 12 个默认场景，保存在：

- `examples/incident_diagnosis_mock/scenarios.py`
- `examples/incident_diagnosis_mock/scenarios.jsonl`

这样设计的目的不是追求复杂推理，而是为了更真实地验证：

- 同一 family 的后续任务能否命中 SharedBlackboard
- 不同 family 的记忆是否能保持基本隔离
- structured_mode 是否能在 richer task set 下继续保留低 token / 低文本开销的优势

场景文件同时也让 benchmark 输入数据标准化，后续如果比赛需要扩充任务族，只要继续追加 JSONL 即可。

## 一键实验汇总设计

随着 benchmark 数量增多，只看单个 CSV 已经不方便。v0.8 新增：

- `scripts/summarize_all_results.py`
- `scripts/run_all.sh`

汇总脚本会读取存在的：

- `results/transport_bench.csv`
- `results/state_patch_bench.csv`
- `results/memory_reuse_bench.csv`
- `results/collaboration_bench.csv`

并输出：

- `results/summary_report.md`
- `results/summary_metrics.json`

这样做的意义是：

- Markdown 适合直接放进实验记录或比赛材料
- JSON 适合后续自动抽取、生成表格或做额外分析
- 缺少部分 CSV 时也不会崩溃，便于逐步实验

## 与评分细则对应关系

就比赛题目“多智能体低开销通信、状态传递与共享记忆机制”而言，当前 CoMemBus 到 v0.8 已经形成一条比较清晰的映射：

- 低开销通信：
  UDS 负责小消息，Shared Memory 负责大对象，transport benchmark 对比 `direct_uds`、`shm_ref`、`adaptive`
- 状态传递：
  `TaskState` + `StatePatch` 负责版本化交接，state benchmark 对比 full state 与 patch
- 共享记忆：
  `SharedBlackboard` 负责记忆写入、检索与复用，memory benchmark 统计命中率和节省步骤
- 非文本协作协议：
  `structured_mode` 结合 `ObjectRef`、`StatePatch`、`MemoryRef`、`EmbeddingState`，与 text_mode 做直接对照
- 多任务连续评测：
  `scenarios.jsonl` 覆盖三个 family，不再局限于单一数据库端口错误样例

这仍然不是完整比赛系统，但已经足以支撑一个可运行、可测试、可对比、可汇总的阶段性实验平台。

为了证明这套黑板不只是“能存”，仓库新增了两个验证入口：

- `examples/incident_diagnosis_mock/run_memory_reuse_demo.py`
- `benchmarks/bench_memory_reuse.py`

memory reuse demo 使用两阶段任务：

- Task 1 写入 database timeout / wrong port 的 evidence、summary、strategy
- Task 2 在完整分析前先检索相关记忆
- 如果命中，就复用历史 strategy，并减少重复步骤

memory reuse benchmark 则连续执行多个相关任务，记录：

- 是否命中历史记忆
- 复用了哪条 memory
- 相比 baseline 节省了多少 structured steps
- 查询延迟和总延迟

这让“共享记忆是否真的带来收益”变成了可以量化的问题。

### 与赛题要求的对应关系

SharedBlackboard 对应赛题里的“共享记忆机制”这一层能力：

- UDS 负责低开销控制消息通信
- Shared Memory 负责大对象共享
- StatePatch 负责任务状态增量传递
- SharedBlackboard 负责跨步骤、跨任务的中间结果沉淀与复用

也就是说，CoMemBus 现在已经初步覆盖了：

- 通信
- 共享对象
- 状态传递
- 共享记忆存储与检索

后续如果继续扩展，可以把 SharedBlackboard 与：

- 更复杂的多 agent workflow
- 自动策略推荐
- 任务模板复用
- 黑板式协作调度

进一步结合起来。

## 纯文本协作模式设计

`text_mode` 是一个明确的基线模式，用来模拟传统多 agent 系统里最常见的问题：每次交接都把完整上下文重新用自然语言或冗长 JSON 描述一遍。

在这个模式里，agent 之间传递的信息通常包含：

- task goal
- current full context
- 日志摘要或日志片段
- 之前已经发现的 facts
- 下一步指令
- 期望输出格式

这些内容在多个 agent handoff 中会反复出现，因此即使语义上没有新增多少信息，消息体也会迅速膨胀。v0.7 的 `TextCollaborationRunner` 正是用来稳定复现这种现象的。

## 结构化协议协作模式设计

`structured_mode` 则把协作内容压缩成更明确的协议单元，而不是传整段上下文叙述。

这里复用了仓库里已经实现的能力：

- `AgentCapability`：描述 agent 能力
- `StructuredMessage`：传递 `action_type`、`params`、`result`
- `ObjectRef`：传递大日志对象引用
- `StatePatch`：传递任务状态变化
- `SharedBlackboard`：传递可复用历史记忆的 `memory_ref`

结构化模式里的几个关键设计点是：

- 大日志通过 Shared Memory 写入一次，然后只传 `ObjectRef`
- 状态交接通过 `StatePatch` 完成，而不是反复发送完整状态
- 历史经验通过 `memory_id` 引用，而不是整段历史文本

这样做可以把真正“会反复增长的东西”从消息体中拆出去，让协议层只保留必要的控制信息和摘要。

## 指标设计

为了比较两种模式，v0.7 引入了这些核心指标：

- `message_count`
- `text_chars`
- `approx_tokens`
- `protocol_bytes`
- `object_ref_count`
- `state_patch_count`
- `memory_ref_count`
- `non_text_state_bytes`
- `shared_object_bytes`
- `total_latency_ms`
- `memory_hit_rate`

这些指标对应不同层面的开销：

- `message_count`：交接次数
- `text_chars` / `approx_tokens`：文本上下文负担
- `protocol_bytes`：真正通过协议发送的字节量
- `object_ref_count`：是否在复用共享内存而不是直接传大对象
- `state_patch_count`：是否在用增量状态，而不是 full state
- `memory_ref_count`：是否在复用历史记忆，而不是重传历史解释
- `total_latency_ms`：整体协作代价
- `memory_hit_rate`：共享记忆在关联任务中能否真正命中

## 为什么结构化模式能降低重复上下文和 token 开销

结构化模式更省的核心原因不是“消息更短”这么简单，而是因为它把不同性质的信息分流到了不同机制：

- 大对象走 Shared Memory
- 状态变化走 StatePatch
- 历史经验走 SharedBlackboard / MemoryRef
- 控制消息只保留 action、params、result 和少量摘要

这样一来，重复上下文不再需要在每个 handoff 中重新拼接成一大段文本。对于关联任务来说，随着历史经验逐渐积累，structured mode 还能进一步通过 `memory_hit` 减少步骤数。

## 与赛题评分项的对应关系

这组实验可以直接映射到赛题中的两个关键方向：

- “通信效率 25 分”
  通过 `text_chars`、`approx_tokens`、`protocol_bytes`、`ObjectRef` / `StatePatch` / `MemoryRef` 次数来说明结构化协议如何降低通信负担。
- “实验验证 15 分”
  通过 `bench_collaboration_modes.py` 的可复现 CSV 输出、`token_saving_ratio`、`latency_saving_ratio`、`memory_hit_rate` 和 `total_saved_steps` 来提供可量化证据。

因此，v0.7 不是只新增一个 demo，而是补上了“纯文本协作基线 vs 结构化协议协作”这一组能够直接拿来解释实验效果的数据。

## 后续扩展方向

后续如果进入比赛完整版本，可以在这个 MVP 上继续扩展：

- 自适应通路选择：根据负载大小、访问次数和延迟预算，在 UDS、共享内存、mmap 文件之间切换。
- 状态增量传递：只传输差量块或 patch，而不是整对象。
- 共享黑板：在共享内存上层实现多 agent 可见的状态表或对象目录。
- 生命周期管理：加入引用计数、租约、心跳和回收器，减少共享内存泄漏风险。
- benchmark：系统化测量小消息延迟、大对象吞吐、复制次数、CPU 占用和 `/dev/shm` 使用情况。
