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
- `comembus.server`：支持 `register`、`publish`、`poll`、`ping`、`shutdown` 的内存消息总线。
- `comembus.client`：面向 agent 的 UDS 客户端 API。
- `examples/smoke_pubsub_shm.py`：8MB 共享内存发布/订阅 smoke demo。
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
```

## 设计说明

设计思路和后续扩展方向见 [docs/mvp_design.md](docs/mvp_design.md)。

