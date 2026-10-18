# mini-swe-rsi

固定模型的在线 Harness 自进化框架：活动版本完成一道任务后，根据公开反馈执行 SICA 两步诊断；存在可执行改进时完成后五步，候选经独立检查与宿主验收后接任，继续做题和自改进。

当前仅提供 `online` 与 `report`。P1 已移除原批量基线、双轨道、探针、held-out 门槛和固定轨迹回放。历史实验结果保留为已有产物；在线候选通过某一题的检查不代表已经证明普遍 RSI 收益。

## 安装

需要 Python 3.12、uv 和 Docker。模型配置从根目录 `.env` 读取 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LOCAL_LLM_MODEL`（或 `LLM_MODEL_NAME`；两者同时存在时必须相同）。凭据留在宿主，不进入候选或 Git。

```bash
python3 scripts/bootstrap.py
uv sync --locked
uv venv .eval-venv
uv pip install --python .eval-venv/bin/python -r evaluator.lock.txt
uv pip install --python .eval-venv/bin/python --no-deps .upstream/swebench
docker build -t mini-swe-rsi-runtime:0.1 runtime
```

上游提交、来源与文件摘要固定在 `upstream.lock.json`；宿主依赖为 `uv.lock`，容器依赖为 `runtime/requirements.lock`，独立评分依赖为 `evaluator.lock.txt`。保留 SICA 七步、角色与任务提示原文及许可证，实际执行使用归档的官方 mini-swe-agent。

数据默认复用 `/Users/maxwell/Documents/mini-swe-rsi-old/data/swe-bench-lite-x86-recovery-validated`。其他位置通过 `--data PATH` 指定，数据和清单摘要必须一致。当前仍保留原固定数据集与完整镜像库存校验；在线只按冻结的开发题顺序执行，缺少任务镜像时按不可变摘要恢复。

## 运行与报告

```bash
uv run mini-swe-rsi online --run runs/online --data /absolute/path/to/data --task-limit 20
uv run mini-swe-rsi report --run runs/online
```

`--task-limit N` 冻结开发题原顺序的前 N 题；省略时使用全部开发题。三题检查使用 `--task-limit 3`。每个运行目录独占运行锁，请勿并行启动同一目录。

本地 Qoder / WorkBuddy 代理由用户确认使用订阅或免费额度、无按 token 单价时，可显式冻结订阅合同：

```bash
uv run mini-swe-rsi online --run runs/online --task-limit 3 --billing-mode subscription
```

也可在专用 `.env` 设置 `RSI_BILLING_MODE=subscription`。该合同仅适用于已核验的本地代理目录；美元成本保持未知，token 和时间照常计量，不能把订阅成本记为零美元。

报告完全离线，生成 `online_report.json` 与 `online_report.md`，分别列出首次任务、候选检查、接任/拒绝/跳过和已发生模型消耗。原批量运行目录不能作为在线报告输入。

## 在线闭环与验收

- 本轮任务执行者、改进者、候选父版本始终是同一个活动版本 `H_v`。
- 步骤 1、2 各需要成功且非空的归档分析角色报告。随后协调者显式选择 `skip` 或 `improve`，缺失诊断不能作为正常跳过。
- 正常 `skip` 以两步终态推进题目，不创建接任版本；`improve` 才进入步骤 3～7。
- 步骤 3 声明并冻结验收计划，步骤 4 实现。只有步骤 4 可以修改 Harness 源码、配置与测试，步骤 6、7 只更新描述与历史。
- 步骤 5 由宿主独立加载候选、重新执行当前题并独立评分。机制判据额外将同一个冻结测试用于父版本与候选。当前题不退化且满足声明判据、执行摘要匹配，才可原子接任。
- 接任后 `H_(v+1)` 实际执行下一题及后续改进；拒绝或正常跳过时继续使用 `H_v`。

状态和不可变证据保存在 `online_state.json`、`versions/`、`tasks/`、`rounds/`、`proposals/`、`decisions/` 与 `events.jsonl`。首次作答结果不会被候选复测覆盖。公开归档仅暴露已发生任务的反馈、诊断、检查摘要和已接任源码；私有评分、凭据、未来任务不进入角色上下文。

在线不设额外全局或阶段墙钟截止。保留 SICA 每角色独立迭代：协调者/归档/求解/评审 500，编码者 1000；每轮最多 5 次 provider 请求。单题 600 秒；工具有效上限 600 秒，命令声明范围 5～1800 秒。全部实际请求完整计量。

## 恢复与版本身份

```bash
uv run mini-swe-rsi online --run runs/online --resume
```

运行会校验冻结的题目、源码、模型、服务、计费和控制代码身份。已完成任务及不可变决策可在持久化边界恢复；评分基础设施故障只补评原补丁，不再次生成任务答案。

已有模型工作流不能自动作为新会话重新开启。需要明确的宿主私有恢复授权时使用 `--resume-checkpoint PATH`；授权覆盖未完成的前五步和适用的未完成检查，累计调用与角色迭代继续计量。这是新模型上下文，不宣称原对话无缝续跑。

P1 改变控制代码摘要，旧在线运行不能直接恢复到精简后的实现。历史运行保留对应 Git 版本和产物；使用新运行目录，不放宽摘要验证或重写历史记录。

## 模型与服务

启动时从认证服务元数据冻结模型身份、上下文和输出容量；所有角色使用同一模型。代理仅声明模型名或容量时，不冒称已验证其底层权重。

- DeepSeek 使用认证目录及已冻结的价格合同；截断回复通过其 beta prefix 协议续写。费用记录为保守上界，不作为实际账单。
- Ollama 核验原生 version/tags/show、模型摘要、实际 `num_ctx` 与模板。Qwen tokenizer 匹配时提供文本计量和明显输入缺失审计，不承诺精确复刻服务聊天封装。
- SenseNova 映射其目录容量；仅完整、明确的全零费率可记为服务声明的零费率。
- 本地 Qoder / WorkBuddy 在标准目录缺容量时使用认证扩展目录。未知费用保持未知，仅按已支持的合同运行，不猜测价格。

无匹配本地 tokenizer 时由服务执行 context 上限，宿主审核真实 usage，不把字符数当作 token。非支持 prefix 的服务截断回复显式失败，不执行未完成动作。服务故障暂停、未知用量和迟到回复继续保留审计。

在线改进阶段的 shell 输出超过 300,000 字节时完整落盘，返回明确索引和预览；超过 8 MiB 时终止工具沙箱并保存带摘要的不完整前缀。日志只读挂载供按需读取。这是本地工具资源保护，不是 token 估计或实验总时限；benchmark 输出处理保持原样。离线 agent 循环测试必须有明确退出及有限步数。详见 [第 5 步修复验收](docs/step5-loop-fix-validation.md)。

## 验证与精简范围

```bash
uv run pytest -q
RUN_DOCKER_TESTS=1 uv run pytest -q
```

Docker 测试使用本地模拟模型，不调用 `.env` 推理服务，检查版本实际加载、在线继承、接任/拒绝/跳过、恢复、候选机制测试及隔离边界。

已完成 P1 与 P2（批次 C、D）：工作流固定在线规则，移除阶段/24 小时预算，仅用 `task_limit` 和 `online_task_selection.json` 冻结任务序列；事件完整落盘，结果读取、计量和公开过滤共享实现，独立身份与接任检查保留。旧运行的 `budget.json`、`execution_scope.json`、`dev_selection.json` 会被明确拒绝，历史产物应配合对应 Git 版本读取，不自动迁移或改写。批次 E 尚未实施。实施优先级见 [在线 RSI 精简指南](docs/online-rsi-simplification-guide.md)，验收见 [P1 精简验收](docs/online-rsi-p1-review.md)、[批次 C 验收](docs/online-rsi-phase-c-review.md)与[批次 D 验收](docs/online-rsi-phase-d-review.md)，历史差异见 [上游适配说明](docs/upstream_adaptations.md)。
