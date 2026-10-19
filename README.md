# mini-swe-rsi

固定模型的在线 Harness 自进化框架。活动版本完成一道任务后，根据公开反馈执行 SICA 诊断；有可执行改进时生成候选，经独立检查与宿主验收后接任，继续执行后续任务。

- 本页：安装、配置、运行、恢复与测试。
- [RSI 机制说明](docs/architecture.md)：七步流程、检查计划、隔离与产物。
- [上游来源与适配](docs/upstream_adaptations.md)：固定依赖、保留内容与运行边界。

## 安装

需要 Python 3.12、uv 和 Docker。以下命令在仓库根目录执行；在 ARM 主机上，Docker 还需能够运行固定的 linux/amd64 任务镜像。

```bash
python3 scripts/bootstrap.py
uv sync --locked
uv venv .eval-venv
uv pip install --python .eval-venv/bin/python -r evaluator.lock.txt
uv pip install --python .eval-venv/bin/python --no-deps .upstream/swebench
docker build -t mini-swe-rsi-runtime:0.1 runtime
```

bootstrap 按 [upstream.lock.json](upstream.lock.json) 下载并校验固定上游。宿主、运行容器和独立评分环境的依赖分别由 [uv.lock](uv.lock)、[runtime/requirements.lock](runtime/requirements.lock) 和 [evaluator.lock.txt](evaluator.lock.txt) 固定。

数据默认位于 `/Users/maxwell/Documents/mini-swe-rsi-old/data/swe-bench-lite-x86-recovery-validated`。其他位置通过 `--data PATH` 指定，但必须具有相同的已批准清单、原始数据和镜像库存；不能直接换成任意 SWE-bench 数据集。在线按固定开发集的原顺序执行，缺失任务镜像时按不可变摘要恢复。

## 模型配置

在仓库根目录创建未纳入 Git 的 `.env`：

```dotenv
OPENAI_BASE_URL=https://your-service.example/v1
OPENAI_API_KEY=your-api-key
LOCAL_LLM_MODEL=your-model-id
```

`LLM_MODEL_NAME` 可替代 `LOCAL_LLM_MODEL`；两者同时设置时必须相同。所有角色使用同一模型，凭据仅由宿主读取。

启动时从认证服务元数据冻结模型身份、上下文和输出容量；不提供隐式容量默认值。支持的元数据适配包括 DeepSeek、Ollama、SenseNova 以及本地 Qoder / WorkBuddy 代理。代理声明的模型名不代表底层权重已被核验。无匹配本地 tokenizer 时由服务执行上下文上限，宿主审核真实 usage，不把字符数当作 token。

运行记录 token、调用次数和耗时，不计算美元费用，不设置费用阈值，也不要求服务提供价格。

## 运行与报告

```bash
uv run mini-swe-rsi online --run runs/online --data /absolute/path/to/data --task-limit 3
uv run mini-swe-rsi report --run runs/online
```

`--task-limit N` 冻结开发集原顺序的前 N 题；省略时执行全部开发题。首次验证可用 `--task-limit 1`。运行真实调用配置的模型服务及独立评分器；每个运行目录只允许一个活动进程。

`report` 完全离线，生成 `online_report.json` 和 `online_report.md`，列出首次任务、接任／拒绝／跳过、调用及 token 用量。首次作答与候选复测分开记录。用量不完整时报告会明确标识，不将未知调用视作零消耗。

## 恢复与常见问题

再次运行同一目录即可沿已持久化的边界恢复：

```bash
uv run mini-swe-rsi online --run runs/online
```

题目序列、源码、模型、服务和控制代码身份必须与冻结配置一致；不可通过扩大 `--task-limit` 给原目录追加题目。已完成的任务和决定不会重复执行。评分基础设施故障只补评已保存的同一补丁。

已有模型工作流不自动重开。需要显式继续未完成工作流时，使用 `--resume-checkpoint PATH`，其中 PATH 必须是该运行 `private/` 下、由宿主核对并授权的恢复 JSON。授权绑定原工作流完整状态、源码摘要、身份、控制清单和累计用量；具体校验见 [recovery.py](src/mini_swe_rsi/recovery.py)。这会创建新的模型上下文，不是原对话无缝续跑，也不是可直接填入任意状态的检查点参数。

| 现象 | 处理方式 |
|---|---|
| 缺失模型容量或认证失败 | 核对 `.env` 和服务认证元数据；模型名存在本身不足以启动 |
| 服务中断或调用失败 | 检查运行目录的 `failure.json`、`events.jsonl` 和离线报告；按持久化状态决定是否需要显式恢复授权 |
| 数据或镜像身份不一致 | 使用批准的数据资产和固定镜像，不能修改清单来绕过校验 |
| 恢复提示协议、源码或配置不一致 | 使用对应代码版本处理原运行，或使用新目录；不要重写旧运行身份 |
| 正常跳过改进 | 这是两步诊断后的合法结果，表示本轮未形成可执行改进，不表示七步均已执行 |

## 测试

```bash
uv run pytest -q
RUN_DOCKER_TESTS=1 uv run pytest -q
```

Docker 测试使用确定性模型服务和评分替身，不调用 `.env` 中的真实推理服务。完整集成验收需要 Docker 可用且相关测试没有跳过；真实模型运行需另行执行 `online`，并以实际完成的阶段、评分和接任证据判断结果。

当前仅支持在线协议 `mini-swe-rsi-online-v8` 与改进协议 `sica-mini-improvement-v8`。旧协议目录不能用当前代码恢复或重新生成报告。单题通过、机制检查通过或候选接任都不能单独证明持续自改进收益。
