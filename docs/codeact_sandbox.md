# Minimal CodeAct Sandbox

## 定位

v0.12 的 CodeAct sandbox 是一个可选加分项，不属于 CoMemBus 默认实验主链路：

- 不进入 `scripts/run_all.sh`
- 不影响现有 mock / benchmark / openEuler 离线复现实验
- 只提供单独入口 `bash scripts/run_codeact_demo.sh`

它的目标不是实现完整 code interpreter，而是证明：

- agent 可以生成一小段受限 Python 代码
- 代码可以在轻量沙箱里执行
- 结果可以按结构化 dict 回到 CoMemBus 上层流程

## 安全策略

当前 sandbox 只允许一个很小的 Python 子集。

允许的核心能力：

- 数字、字符串、列表、字典、元组
- 变量赋值
- `if`
- `for`
- 少量白名单函数调用：
  - `len`
  - `sum`
  - `min`
  - `max`
  - `sorted`
  - `str`
  - `int`
  - `float`
  - `range`
- 预置 `math` 命名空间中的少量函数和常量

明确禁止：

- `import` / `from ... import ...`
- `open`
- `eval`
- `exec`
- `compile`
- `__import__`
- `with`
- `try`
- `class`
- `lambda`
- `global` / `nonlocal`
- `while`
- 双下划线属性访问
- 文件读写、网络访问、系统命令

## 隔离执行方式

`run_code_sandbox()` 使用 `multiprocessing.Process` 执行代码，并通过 `multiprocessing.Queue` 回传结果。

当前约束：

- 默认 `timeout=2` 秒
- 超时后 `terminate()` 子进程
- `stdout` 和错误文本限制为 4096 字符
- 用户代码必须定义 `result`

这意味着 CodeAct 能力只是一个“轻量、安全、可验证”的受限执行层，而不是任意 Python 执行环境。

## 与赛题鼓励项的关系

这个模块对应的是“多智能体协作中的工具调用 / 代码动作”方向，但实现上刻意保持克制：

- 不接第三方工具框架
- 不接外部 shell
- 不访问文件系统
- 不访问网络
- 不改变现有 bus / shared memory / state / blackboard 主链路

因此它更像一个最小验证：

1. 上层 agent 产生代码动作
2. 代码在受限沙箱中运行
3. 结果按结构化协议返回
4. 结果还能写入 SharedBlackboard 形成可复用 strategy memory

## 运行方式

```bash
bash scripts/run_codeact_demo.sh
```

成功时会输出：

```text
OK: codeact demo completed
```
