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

## 为什么 LLM Agent 是 Optional

v0.9 引入了一个可选的 `comembus.llm` 适配层，但它被刻意设计成 optional，而不是核心依赖。

原因有四个：

1. 当前 CoMemBus 的主目标仍然是可复现的 AI Infra MVP，而不是在线模型服务平台。
2. 比赛环境和 openEuler 复现实验必须在“没有网络、没有 API Key、没有第三方 SDK”的前提下稳定运行。
3. 外部 LLM 服务会引入额外的失败面，例如网络不可达、接口兼容性问题、延迟抖动和配额问题。
4. 现有 benchmark、mock demo、state patch、shared blackboard 和 capability discovery 都已经可以独立验证核心设计价值。

所以当前策略是：

- 默认 provider=`mock`
- `local_http` 只是附加能力
- 一旦 LLM 调用失败，必须自动 fallback 到 `mock`
- `run_all.sh` 不把 LLM demo 当作默认链路

这使得 optional LLM integration 更像一个“上层可插拔增强层”，而不是核心运行时前提。

## LLM Agent 如何复用结构化状态和共享记忆

`LLMReviewAgent` 并不直接读取原始大日志对象，也不参与 UDS / Shared Memory 传输本身。它复用的是已经压缩后的上层状态：

- `TaskState.facts`
- `StatePatch` 应用后的阶段信息
- `SharedBlackboard` 返回的记忆摘要
- 少量 evidence 行

这种接法很关键，因为它说明 LLM agent 的位置是在现有 CoMemBus 上层：

1. 低开销通信仍由 UDS + Shared Memory 完成
2. 状态传递仍由 `TaskState` + `StatePatch` 完成
3. 历史经验复用仍由 `SharedBlackboard` 完成
4. LLM 只是消费压缩后的结构化上下文，生成更自然的计划或报告

换句话说，LLM adapter 不应取代 CoMemBus 的核心机制，而应建立在它们之上。

## 为什么不把完整日志塞给 LLM

这一点和赛题目标高度一致。

完整 8MB 日志对象之所以被放进 Shared Memory，而不是直接走文本协议，就是为了避免：

- 重复复制
- 大上下文传输
- 高 token 开销
- 无法稳定复现实验

如果接了 LLM 又把原始 8MB 日志重新塞回 prompt，就会抵消 structured_mode 的很多收益。因此 `LLMReviewAgent` 当前只允许使用：

- 已提炼出的 facts
- 记忆摘要
- 少量 evidence

这让 optional LLM integration 继续保持“控制面轻量、数据面分层、上下文压缩”的设计原则。

## LLM 接入与 AI Infra 的关系

从系统边界上看，CoMemBus 到 v0.9 的分层大致如下：

- Infra 层：
  UDS、Shared Memory、ObjectRef、AdaptiveTransportPolicy
- State / Memory 层：
  `TaskState`、`StatePatch`、`SharedBlackboard`、Capability Discovery、Embedding Direct Exchange
- Experiment / Agent 层：
  mock agents、rich scenarios、benchmarks
- Optional AI Layer：
  `MockLLMClient`、`LocalHTTPChatClient`、`LLMReviewAgent`

这种分层关系意味着：

- 即使完全不接 LLM，CoMemBus 也已经是一个可运行的 AI Infra MVP
- 接入 LLM 后，系统得到的是“更自然的计划/报告能力”，不是“核心通信才终于成立”
- 默认 mock provider 保证了系统的基础可验证性

这也解释了为什么当前项目不推荐把在线 API 作为比赛默认依赖：CoMemBus 的主价值在于可控、低开销、结构化、可复现的协作基础设施。

v0.11 在此基础上增加了 `openai_compatible` remote provider。这个 provider 的目标不是改变系统结构，而是让兼容 `chat/completions` 的远程服务也能接入同一套 optional adapter 层。

它和 `local_http` 共用的设计原则仍然是：

- 仅用于 optional smoke
- API Key 只能来自环境变量
- 任意失败都必须 fallback
- 不进入默认 benchmark / `run_all.sh` 的核心离线路径

v0.11 还补了一个 multi-agent LLM smoke，用来验证“多个 agent 共享同一套 optional LLM provider 配置”这件事本身是可工作的。当前 smoke 支持：

- `--llm-agents planner,review`
- `--llm-agents all`

其中：

- Planner 可以生成更自然的行动计划
- Review 可以生成更自然的结论报告
- Log / Config 即使启用 LLM，也只生成短 explanation，不改变 deterministic facts 和 patch 流程

这样设计的原因是：

- 保住现有 `TaskState`、`StatePatch` 和 SharedBlackboard 的结构化主链路
- 让 root cause correctness 继续由结构化 facts 保底
- 把“真实模型是否可接入”限制在一个很薄的 smoke 层中

这样就能同时覆盖：

- 完全离线 mock 基线
- 本地模型服务验证
- 远程兼容接口验证

v0.11.3 继续在这个 optional LLM 层上补了一点“实验产物可见性”：

- `run_llm_agent_demo.py` 可以把结果保存到 JSON
- `run_llm_multiagent_smoke.py` 也可以保存结构化 smoke 结果
- `LLMReviewAgent` 的最终报告可以可选写回 SharedBlackboard
- `scripts/run_llm_compare.sh` 可以直接比较 mock 与 remote 的报告差异

这样做的意义不是把真实 LLM 拉进默认 benchmark，而是把它作为一个“可复现实验补充层”：

- 无 API key、无网络时，主流程仍然完全可跑
- 有 remote 环境时，可以额外留下 `results/llm_mock_smoke.json` / `results/llm_remote_smoke.json`
- `used_fallback`、`total_tokens`、`report` 差异都能被保存下来，便于后续汇报或复盘

这也解释了为什么默认 benchmark 仍坚持 mock / replay：

- benchmark 关注的是稳定、可比较的系统层指标
- 真实远程模型会引入额外随机性和外部依赖
- optional LLM 更适合作为“接入验证”和“自然语言报告增强”能力，而不是 benchmark 基准本身

## 图表说明

v0.10 新增 `scripts/generate_result_figures.py`，目标是把已有 benchmark 结果转成无需第三方绘图库的 SVG 图表。

当前做法是：

- 直接读取 CSV 和 `summary_metrics.json`
- 用标准库手写 SVG XML
- 对缺失数据做 warning，而不是崩溃

这样设计的好处是：

- openEuler 环境不需要额外安装 `matplotlib`
- 结果图能直接用于技术报告、PPT 和答辩材料
- 图表产物可以跟 CSV 一样纳入可复现流程

当前默认图表包括：

- transport 平均延迟
- StatePatch vs full state 字节对比
- text_mode vs structured_mode token 对比
- text_mode vs structured_mode latency 对比
- memory reuse 指标
- capability discovery / embedding state 计数

## 大规模 Benchmark 设计

前面的 collaboration benchmark 主要验证 10 轮连续任务。v0.10 继续增加 stress benchmark，用 30 / 60 轮连续任务去观察：

- structured_mode 是否仍然稳定低于 text_mode 的 token 开销
- latency saving 是否在更长任务链上保持正收益
- memory reuse hit rate 是否能在跨 family 循环任务下继续维持
- root cause correctness 是否在大规模批次里保持稳定

因为默认 rich scenario 集合只有 12 条，所以 stress benchmark 在实现上采用“循环扩展场景”的方式：

- family、query、expected root cause 仍然保留
- task index 和 topic 会扩展到更长序列
- 共享记忆仍可按 family 延续复用

这样既不需要重构核心 benchmark 逻辑，也能把连续任务规模从 10 扩到 30 / 60。

## LLM 可选接入与 Fallback 设计

v0.10 对 `local_http` 再补了一层 `model` 参数支持：

- 显式参数 `--model`
- 环境变量 `COMEMBUS_LLM_MODEL`
- 默认值 `local-model`

这一层设计仍然坚持两个原则：

1. LLM 只能是 optional enhancement
2. 任意失败都必须 fallback 到 `mock`

因此无论失败原因是：

- endpoint 不可达
- 超时
- 响应格式不兼容
- 本地模型未启动

CoMemBus 都会自动回退，保证 demo 仍可运行。这样可以把“本地模型集成”变成一个加分项，而不是破坏复现实验的风险源。

对远程 `openai_compatible` 来说，这个原则同样成立。即使 endpoint 不可达、API key 缺失、超时或返回异常 JSON，demo 仍会回落到 mock provider 成功结束。

## 为什么 Mock Agent 不影响系统层机制验证

即使完全不接真实 LLM，本仓库仍然可以验证以下系统层问题：

- UDS 与 Shared Memory 的分层通信是否成立
- `ObjectRef` 是否能稳定传递大对象引用
- `TaskState` / `StatePatch` 是否能压缩状态交接
- `SharedBlackboard` 是否能跨任务复用历史记忆
- `CapabilityRegistry` 和 `EmbeddingState` 是否能支撑结构化协作

也就是说，mock agent 和 mock provider 不是“偷懒替代品”，而是故意保留的可复现基线。它们让我们能先把 AI Infra 机制验证清楚，再决定是否叠加真实模型推理层。

## 多 Agent LLM Smoke 的意义

v0.11 新增了 multi-agent LLM smoke test。它的重点不是让 LLM 接管核心事实判断，而是验证：

- PlannerAgent 可以用 LLM 生成更自然的计划说明
- ReviewAgent 可以用 LLM 生成更自然的报告
- `all` 模式下，LogAgent / ConfigAgent 也只把 LLM 用于简短 explanation，而不是替代 deterministic facts

这样设计的好处是：

- `root_cause_correct` 仍由结构化 facts 保底
- LLM 调用失败不会破坏系统级验证
- 可以把“多 Agent + optional real model”这一层单独做 smoke，而不干扰默认 benchmark 和离线流程

## Minimal CodeAct Sandbox

v0.12 新增了一个最小 CodeAct sandbox，但它被明确放在 optional 加分项位置，而不是主执行链路里。

它的目标很克制：

- 让 agent 能产生一小段 Python 代码
- 让代码在受限环境中执行
- 让执行结果以结构化 dict 返回
- 让结果还能沉淀到 SharedBlackboard 中

这个模块当前不进入 `run_all.sh`，原因很直接：

- 比赛主链路要继续保持可复现和低风险
- 代码执行天然比普通结构化消息更敏感
- CodeAct 在当前阶段更适合作为“能力扩展验证”，而不是默认基础设施前提

### 为什么要单独做 AST 校验

如果只做子进程隔离，而不做语法层限制，仍然会留下很多不必要的风险面。

所以当前设计先经过 `ASTCodeValidator`，只允许一个很小的 Python 子集：

- 基本字面量和容器
- 变量赋值
- `if`
- `for`
- 少量白名单函数
- 少量预置 `math` 能力

明确禁止：

- `import`
- `open`
- `eval`
- `exec`
- `compile`
- `__import__`
- `with`
- `try`
- `class`
- `lambda`
- `while`
- 双下划线属性访问

这样做的意义是：先把“能做什么”压缩到一个很小、很容易解释和测试的范围，再谈执行隔离。

### 为什么还要用 multiprocessing 隔离

AST 校验解决的是“语义子集”问题，`multiprocessing.Process` 解决的是“运行时失控”问题。

当前 sandbox 的执行约束是：

- 代码在单独子进程里运行
- 默认超时 2 秒
- 超时后直接 `terminate()`
- `stdout` 和错误文本都限制在 4096 字符以内
- 用户代码必须显式写出 `result`

这让 CodeAct 更像一个“轻量工具动作层”，而不是 unrestricted Python interpreter。

### 与赛题鼓励项的对应关系

如果把 CoMemBus 的层次拆开来看：

- UDS + Shared Memory 解决低开销通信
- `TaskState` + `StatePatch` 解决状态传递
- SharedBlackboard 解决共享记忆
- Minimal CodeAct Sandbox 则补上一个“受限代码动作”的实验入口

它的价值不在于功能有多强，而在于它证明了：

1. 结构化 facts 可以驱动受限代码执行
2. 代码执行结果可以重新进入结构化协作链路
3. 整个过程仍然可以保持离线、标准库、openEuler 兼容

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

## v1.3 严谨 Benchmark 与组件消融设计

v1.3 在原 MVP 上增加独立实验层，不替换已有 demo、旧 benchmark 或 `run_all.sh`。兼容边界如下：

- UDS 的 `send_frame` / `recv_frame` 只增加可选 `MetricsRecorder`；默认 `None` 时保留旧路径。
- `SharedMemoryObjectStore` 只增加可选 recorder；原有无参数构造仍有效。
- 旧 CSV 字段和旧汇总脚本不改；v1.3 写入单独的结果文件。
- `AdaptiveTransportPolicy()` 仍是固定 64KB fallback；只有显式 `from_profile()` 才使用校准阈值。

### 真实字节而不是估算字节

`MetricsRecorder` 在 `sendall` 成功后记录 `len(encoded_frame)`，因此包括 JSON body 和 4 字节长度头。接收端在实际读完 header/body 后记录相同 frame 长度。报告中的 `wire_bytes` 只取 `sent_bytes`，避免把同一个 frame 在发送端和接收端重复计数。

共享内存采用另一组计数器：创建并复制 payload 后增加 `shm_bytes_written`，每次从 shared memory buffer 复制数据后增加 `shm_bytes_read`。这两个字段不属于 UDS wire bytes。

### 公平流程

9 个消融模式共享同一场景、同一日志 bytes、同一配置文本、5 个 Agent 和 5 次 frame 交接。流程固定为 Planner → Log → Config → Memory → Review → Planner。最终 root cause 从相同配置事实中确定性解析，所有模式都必须验证 `root_cause_correct=true`。

`text_full_context` 每次携带完整、逐步累积的文本上下文，不再通过人为固定延迟把基线做弱。`text_summary` 使用固定摘要函数。`json_full_state` 传完整 JSON 状态。结构化消融则以 `structured_full` 为控制组，每次只移除 SHM/ObjectRef、StatePatch、MemoryRef、EmbeddingState/Ref 或 Capability Discovery 中的一项。

### 校准策略

`AdaptiveTransportCalibrator` 在每个 size/receiver 组合上分别实测 direct UDS 和 SHM ref。对每个 receiver 数，最小的“SHM 平均延迟不高于 direct”尺寸作为 crossover；若测试范围内没有 crossover，则阈值设为最大测试尺寸加 1 byte。profile 保存测试矩阵、seed、warmup、rounds、延迟统计和阈值，policy 不使用估算 latency。

### 统计与进程指标

统计模块只用标准库，percentile 使用有序样本上的线性插值，standard deviation 使用样本标准差，95% CI 使用 `mean ± 1.96 * s / sqrt(n)`。CPU time 来自 `time.process_time()`，峰值 RSS 和 context switch 来自 `resource.getrusage(RUSAGE_SELF)`。Linux/openEuler 的 `ru_maxrss` 单位按 KiB 报告。

`estimated_tokens` 只是 `ceil(text_chars / 4)`，不是模型 tokenizer 或模型响应中的真实 token。核心严谨 benchmark 不访问远程 LLM。

## v1.4 可靠投递、对象生命周期与状态恢复

v1.4 不创建第二套协议，而是在原 `Message` 和 AgentBus 命令上增加可选元数据与命令。旧 `poll` 仍是自动 ACK；只有 `poll_reliable` 才把消息保留在 invisible 集合并要求显式 ACK/NACK。这样旧 demo 无需修改，同时新 worker 可以获得 at-least-once delivery。

可靠队列在一个锁保护的状态机中维护 available、invisible、known ID 和 processed ID：

1. publish 检查 DedupStore 和 in-flight ID。
2. poll 把 available 消息转为 invisible，并设置 deadline。
3. ACK 原子移除 invisible 记录并写入处理结果。
4. NACK 或 visibility timeout 把同一 envelope 重新入队，保留 message ID 并增加下次 attempt。
5. 容量统计同时包含 available 和 invisible，避免慢消费者绕过背压。

共享对象采用 lease + holder set。`ref_count` 始终等于不同 consumer holder 数。lease 未到期时，即使 refcount 已为 0 也不删除；lease 到期时，崩溃 consumer 的 holder 被判为 leaked 并回收，随后在 refcount 0 条件下 unlink。所有非预期 unlink 错误继续向调用者抛出。

SQLite 状态管理把 patch 审计日志和最新 snapshot 放入同一 WAL 事务，避免“patch 已写但 snapshot 未更新”或相反的半提交。进程重启只读取已提交 snapshot，并检查 patch log 不能领先于 snapshot。compact 在事务中固化最新 snapshot 后删除已覆盖 patch。

Patch rebase 采用字段级保守规则：整字段 `set_fields` 只要 base→latest 已变化就冲突；list append 可组合；facts/artifacts merge 只有触及同一且已变化的 key 才冲突。无法确认安全时拒绝，而不是静默覆盖。

failure injection 将预期故障与意外异常区分：预期异常必须真的发生并通过后置状态证明恢复；意外异常写入 CSV error 并令 benchmark 非零退出。详细状态机、表结构和异常规则见 `docs/reliability_design.md`。
