# LLM local_http Validation

## 为什么默认使用 mock provider

CoMemBus 到 v0.10 仍然把低开销通信、状态传递和共享记忆作为核心目标，因此 optional LLM integration 不能成为默认运行依赖。

默认使用 `mock` provider 的原因是：

- 不需要网络
- 不需要 API Key
- 不需要第三方 SDK
- 不影响 `run_all.sh` 的 openEuler 离线复现实验

## local_http provider 的作用

`local_http` 的定位是“如果本地已经有模型服务，就可以额外接进来”，而不是“没有本地模型就无法运行”。

当前它使用 `urllib.request` 调用 OpenAI-compatible chat completions 风格接口。

## 兼容本地模型服务

只要本地服务提供 OpenAI-compatible endpoint，通常都可以尝试接入，例如：

- Ollama 暴露的兼容接口
- vLLM 暴露的兼容接口

## 示例命令

```bash
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
  --provider local_http \
  --endpoint http://127.0.0.1:11434/v1/chat/completions \
  --model <model_name>
```

也可以使用环境变量：

```bash
export COMEMBUS_LLM_ENDPOINT=http://127.0.0.1:11434/v1/chat/completions
export COMEMBUS_LLM_MODEL=<model_name>
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py --provider local_http
```

## 如何判断是否真实接入

如果输出里同时满足：

- `provider=local_http`
- `used_fallback=false`

就说明本地模型服务调用成功，并且响应格式与 CoMemBus 当前的解析逻辑兼容。

## 如果 used_fallback=true

如果输出里出现：

- `provider=mock`
- `used_fallback=true`

则说明 CoMemBus 已经自动回退到离线 mock provider。常见原因包括：

- 本地模型服务不可达
- endpoint 地址不对
- 模型服务响应超时
- 响应格式不兼容 OpenAI-style `choices[0].message.content`

这种 fallback 设计是有意保留的，用来保证 optional LLM integration 不会破坏默认可复现流程。
