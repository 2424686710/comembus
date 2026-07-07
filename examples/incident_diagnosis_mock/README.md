# Mock Incident Diagnosis Demo

这个示例不依赖 LLM、LangChain 或任何在线服务，只使用 CoMemBus 当前已经具备的能力来模拟一个多 Agent 协作诊断流程。

## 参与 Agent

- `PlannerAgent`：创建初始 `TaskState` 并发布给主流程。
- `LogAgent`：通过 `ObjectRef` 读取至少 8MB 的共享内存日志，并生成 `StatePatch`。
- `ConfigAgent`：读取小配置文本，并生成 `StatePatch`。
- `ReviewAgent`：读取最终 `TaskState`，输出最终 root cause 报告。

## 为什么这个 demo 有意义

这个示例证明了 CoMemBus 不只是能做单 producer / single consumer 的共享内存传递，还能支撑多个独立 agent 进程围绕同一个 incident 协作：

- 控制消息依旧走 UDS。
- 大日志对象依旧只通过 `ObjectRef` 传递，不走 UDS 全量复制。
- 多个 agent 通过 topic 串起一个简化的工作流。
- 状态交接通过 `TaskState` + `StatePatch` 完成，而不是只传零散结果。
- 最终结果由单独的 review agent 生成，说明 bus 能支撑分工式协作。

## 运行方式

```bash
python3 examples/incident_diagnosis_mock/run_demo.py
```

成功时会输出：

```text
OK: mock multi-agent incident diagnosis completed
```
