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

## 后续扩展方向

后续如果进入比赛完整版本，可以在这个 MVP 上继续扩展：

- 自适应通路选择：根据负载大小、访问次数和延迟预算，在 UDS、共享内存、mmap 文件之间切换。
- 状态增量传递：只传输差量块或 patch，而不是整对象。
- 共享黑板：在共享内存上层实现多 agent 可见的状态表或对象目录。
- 生命周期管理：加入引用计数、租约、心跳和回收器，减少共享内存泄漏风险。
- benchmark：系统化测量小消息延迟、大对象吞吐、复制次数、CPU 占用和 `/dev/shm` 使用情况。
