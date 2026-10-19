# mini-swe-rsi

基于 [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) 和 [SICA](https://github.com/MaximeRobeyns/self_improving_coding_agent) 的在线 RSI 框架：让 agent 在解题后修改自己的 Harness，再通过独立检查决定是否采用新版本。整个过程使用同一个模型。

每轮先完成一道 SWE-bench 任务，再分析执行轨迹。诊断支持改进时，agent 设计方案、修改代码、提交候选；宿主重测候选，通过后由新版本继续下一题。没有可执行改进时，直接进入下一题。

## 安装

需要 Python 3.12、[uv](https://github.com/astral-sh/uv) 和 Docker。ARM 主机还需支持运行 linux/amd64 容器。

在仓库根目录执行：

```bash
uv sync --locked
uv venv .eval-venv
uv pip install --python .eval-venv/bin/python -r evaluator.lock.txt
uv pip install --python .eval-venv/bin/python --no-deps .
docker build -t mini-swe-rsi-runtime:0.1 runtime
```

上游源码已集成在仓库内，安装时无需下载源码。`.eval-venv` 用于独立评分。来源版本及依赖见 [upstream.lock.json](upstream.lock.json)、[uv.lock](uv.lock)、[容器依赖](runtime/requirements.lock) 和 [评分依赖](evaluator.lock.txt)。

## 快速开始

将 [.env.example](.env.example) 复制为 `.env`，填写服务地址、密钥和模型名。

准备项目所需的数据目录后，先运行一题：

```bash
uv run mini-swe-rsi online --run runs/online --data /path/to/data --task-limit 1
```

`--data` 需要包含已批准的数据清单、原始数据、镜像元数据和预检记录。当前仅支持项目固定的数据资产；格式与默认位置见[数据要求](docs/architecture.md#数据要求)。

`--task-limit N` 选择开发集原顺序的前 N 题，省略时运行全部开发题。每次新实验使用独立的 `--run` 目录。

## 查看结果与继续运行

生成报告不需要模型服务：

```bash
uv run mini-swe-rsi report --run runs/online
```

报告写入 `online_report.md` 和 `online_report.json`，包含任务得分、候选接受／拒绝／跳过情况、模型调用和 token 用量。源码版本位于 `versions/`，宿主决定位于 `decisions/`。

继续已有运行时使用相同的参数：

```bash
uv run mini-swe-rsi online --run runs/online --data /path/to/data --task-limit 1
```

已完成的任务不会重跑。原目录的题目范围、源码、模型和服务配置必须保持一致；已有模型工作流中断后需[显式恢复授权](docs/architecture.md#中断恢复)。

## 测试

```bash
uv run pytest -q
RUN_DOCKER_TESTS=1 uv run pytest -q
```

第二条命令包含 Docker 集成测试，模型服务和评分使用测试替身。要验证自己的模型服务，请运行上面的单题命令。

## 文档

- [RSI 运行机制](docs/architecture.md)：七步流程、候选检查、数据要求、资源限制和中断恢复。
- [上游与适配](docs/upstream_adaptations.md)：代码来源、许可证及与上游的差异。

当前在线协议和改进协议均为 v8。旧版本运行目录需使用对应代码版本处理。候选接任表示通过了本轮判据，跨题收益需要另行评估。
