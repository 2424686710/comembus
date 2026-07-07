# Mock Incident Diagnosis Demo

这个示例不依赖 LLM、LangChain 或任何在线服务，只使用 CoMemBus 当前已经具备的能力来模拟一个多 Agent 协作诊断流程。

## 参与 Agent

- `PlannerAgent`：发布诊断任务，并给日志任务和配置任务打上选择的传输模式说明。
- `LogAgent`：通过 `ObjectRef` 读取至少 8MB 的共享内存日志，提取日志事实。
- `ConfigAgent`：读取小配置文本，提取配置事实。
- `ReviewAgent`：汇总 `log_facts` 和 `config_facts`，输出最终 root cause 报告。

## 为什么这个 demo 有意义

这个示例证明了 CoMemBus 不只是能做单 producer / single consumer 的共享内存传递，还能支撑多个独立 agent 进程围绕同一个 incident 协作：

- 控制消息依旧走 UDS。
- 大日志对象依旧只通过 `ObjectRef` 传递，不走 UDS 全量复制。
- 多个 agent 通过 topic 串起一个简化的工作流。
- 最终结果由单独的 review agent 生成，说明 bus 能支撑分工式协作。

## 运行方式

```bash
python3 examples/incident_diagnosis_mock/run_demo.py
```

成功时会输出：

```text
OK: mock multi-agent incident diagnosis completed
```

