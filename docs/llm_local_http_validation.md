# LLM local_http and openai_compatible Validation

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

## openai_compatible provider 的作用

`openai_compatible` 面向远程或托管的兼容接口，例如：

- DeepSeek
- Qwen / DashScope compatible mode
- 其他兼容 `chat/completions` 的服务

它和 `local_http` 一样：

- 不会成为默认依赖
- 失败后必须 fallback 到 `mock`
- 只使用标准库 `urllib.request`

这里需要区分两个概念：

- `BASE_URL`：基础地址，例如 `https://api.deepseek.com`
- `endpoint`：完整请求地址，例如 `https://api.deepseek.com/chat/completions`

CoMemBus 现在两种都支持。也就是说，`COMEMBUS_LLM_ENDPOINT` 可以填完整 endpoint，也可以直接填这类 base URL，客户端会自动补成 chat completions 请求地址。

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

远程 OpenAI-compatible 示例：

```bash
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
  --provider openai_compatible \
  --endpoint https://api.deepseek.com \
  --model deepseek-chat
```

```bash
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py \
  --provider openai_compatible \
  --endpoint https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions \
  --model qwen-plus
```

也可以使用环境变量：

```bash
export COMEMBUS_LLM_ENDPOINT=http://127.0.0.1:11434/v1/chat/completions
export COMEMBUS_LLM_MODEL=<model_name>
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py --provider local_http
```

远程 provider 的环境变量示例：

```bash
export COMEMBUS_LLM_ENDPOINT=https://api.deepseek.com
export COMEMBUS_LLM_MODEL=deepseek-chat
export COMEMBUS_LLM_API_KEY=your-secret-key
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py --provider openai_compatible
```

```bash
export COMEMBUS_LLM_ENDPOINT=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
export COMEMBUS_LLM_MODEL=qwen-plus
export COMEMBUS_LLM_API_KEY=your-secret-key
python3 examples/incident_diagnosis_mock/run_llm_agent_demo.py --provider openai_compatible
```

## 如何判断是否真实接入

如果输出里同时满足：

- `provider=local_http`
- `used_fallback=false`

就说明本地模型服务调用成功，并且响应格式与 CoMemBus 当前的解析逻辑兼容。

对于远程 provider，如果输出里同时满足：

- `provider=openai_compatible`
- `used_fallback=false`

就说明真实远程兼容接口调用成功。

如果你填的是：

- `https://api.deepseek.com`

CoMemBus 会自动补成：

- `https://api.deepseek.com/chat/completions`

如果你填的是完整地址，例如：

- `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`

则不会重复拼接。

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

## multi-agent smoke 用法

如果希望同时验证 Planner 和 Review 的 optional LLM 接入，可以运行：

```bash
python3 examples/incident_diagnosis_mock/run_llm_multiagent_smoke.py \
  --provider mock \
  --llm-agents planner,review
```

如果本地服务已就绪，也可以改成：

```bash
python3 examples/incident_diagnosis_mock/run_llm_multiagent_smoke.py \
  --provider local_http \
  --endpoint http://127.0.0.1:11434/v1/chat/completions \
  --model <model_name> \
  --llm-agents all
```

这里的 `all` 代表：

- `planner`
- `log`
- `config`
- `review`

但当前设计仍然坚持“结构化事实保底、LLM 只做自然语言增强”：

- Planner 用 LLM 生成更自然的计划说明
- Review 用 LLM 生成最终报告
- Log / Config 即使进入 `all` 模式，也只是补充简短 explanation，不替代 deterministic facts

## remote smoke 用法

`bash scripts/run_remote_llm_smoke.sh` 用于在远程 OpenAI-compatible endpoint 已配置时做快速验证。

脚本依赖三个环境变量：

```bash
export COMEMBUS_LLM_ENDPOINT=https://your-endpoint.example.com/v1/chat/completions
export COMEMBUS_LLM_MODEL=your-model-name
export COMEMBUS_LLM_API_KEY=your-secret-key
```

这里的 `COMEMBUS_LLM_ENDPOINT` 也可以直接填 base URL，例如 `https://api.deepseek.com`。

未配置时脚本会输出：

```text
SKIP: remote LLM env not configured
```

并直接退出 `0`，这样就不会破坏默认离线实验流程。
