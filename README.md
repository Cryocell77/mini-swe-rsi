# mini-swe-rsi

固定模型的 Harness 自改进实验：以原始 SICA 控制器驱动官方 mini-swe-agent，候选运行与评分隔离，验证递归继承、任务收益和同起点改进能力。只有三项门槛全部通过后才进行代码精简。

本次实跑未通过递归机制门槛：已交付原生适配版与失败报告，最小实现尚未生成。结果见 [首次实验摘要](docs/results-summary.md)；本机完整产物位于 `runs/sica-qwen-20261011/`。

已修复失败状态传播、重复推理字段计量与归档证据暴露问题，独立验收33项测试通过；见[修复验收记录](docs/repair-review.md)。本次验收不代表RSI收益成立。新协议实验请使用新的运行目录，旧实验保留供离线报告使用。

## 安装

需要 Python 3.12、uv、Docker。模型服务从根目录 `.env` 读取 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LOCAL_LLM_MODEL`（或 `LLM_MODEL_NAME`，两者同时存在时必须相同）。凭据不进入候选或 Git。

```bash
python3 scripts/bootstrap.py
uv sync --locked
uv venv .eval-venv
uv pip install --python .eval-venv/bin/python -r evaluator.lock.txt
uv pip install --python .eval-venv/bin/python --no-deps .upstream/swebench
docker build -t mini-swe-rsi-runtime:0.1 runtime
```

SICA/mini/SWE-bench 提交和源码摘要固定在 `upstream.lock.json`；宿主依赖固定在 `uv.lock`，容器依赖在 `runtime/requirements.lock`，评分依赖在 `evaluator.lock.txt`。实际运行还保存 `runtime-image.tar`、镜像摘要和系统包清单；可用 `docker load -i RUN/runtime-image.tar` 恢复完全相同的运行镜像。

数据默认复用 `/Users/maxwell/Documents/mini-swe-rsi-old/data/swe-bench-lite-x86-recovery-validated`。其他位置用 `--data PATH`，必须具有相同数据与清单摘要。实验另保存 `image_inventory.json`，将全部160项镜像身份绑定到运行，恢复时拒绝变化。清单包含已验证的 x86_64 镜像摘要；缺少镜像时只按不可变摘要恢复。默认不要求本机GPU，推理由 `.env` 服务提供。

## 运行

```bash
uv run mini-swe-rsi baseline --run runs/experiment --preflight-only
uv run mini-swe-rsi baseline --run runs/experiment
uv run mini-swe-rsi evolve --run runs/experiment
uv run mini-swe-rsi report --run runs/experiment
```

`baseline` 自动完成预检并补齐 dev30；预检前三题作为真实 H0 尝试复用。`evolve` 自动补齐基线，在原生选择逻辑外调度两轨道，各最多四次搜索提案；如果剩余提案数已不可能达到两代行为变化，则提前停止并保存源码摘要证明。只有机制证据成立才运行两对同起点探针与验证门槛。`report` 完全离线。

`evaluate --run runs/experiment` 执行冻结候选验证。**不会自动精简**：失败或预算不足时交付失败报告，test100 保持封存。若门槛通过，需完成工程精简与固定轨迹差分验证，将其证明保存为该运行的 `simplification_replay.json`（`passed`、`minimal_hash`）；随后：

```bash
uv run mini-swe-rsi evaluate --run runs/experiment --final --minimal /absolute/path/to/minimal/agent_code
```

最终阶段冻结四版本摘要并交错评测 test100。重启不会重置预算，也不会重复生成中断的模型提案。请勿并行启动同一实验。执行时限覆盖阶段与全局24小时；中断后的人工离线时间也消耗已启动阶段的墙钟预算。

## 验收与输出

- 递归机制：至少两代有行为见证的代码变化，且演化版本实际执行后续改进。
- 任务收益：两次 val30 配对均胜过 H0，推理 tokens <= H0 的110%。
- 改进能力：两个同起点探针对均胜过固定 H0，属于初步证据。
- 最终效果：精简版-H0 的配对95% CI下界>0；精简版-参考版下界>=−3个百分点。

`results/run_1` 保存递归轨道，`results/run_2` 保存固定改进者轨道；`probes` 保存同起点候选；`private` 保存评分和非搜索评测；`events.jsonl` 保存调用、执行版本、隔离边界与谱系。`report.json` / `report.md` 汇总实际覆盖、成功率、tokens、耗时和停止原因。缺失题目不计作完整成绩；基础设施错误最多原样重跑一次，agent错误不重试。

本地模型未提供可核实定价，因此美元成本为未知。SICA接口中的零成本是占位，不能解读为免费。

## 验证

```bash
uv run pytest -q
RUN_DOCKER_TESTS=1 uv run pytest -q
```

Docker测试使用模拟模型，不调用 `.env` 推理服务；验证不同归档实现实际加载、只读挂载和无私有数据挂载。固定轨迹回放先在隔离容器中校准候选自身轨迹，再与其实际父版本比较；它检查后续上下文变化，也排除继承已有行为后仅改未使用代码的假阳性。其他测试覆盖原始选择逻辑、持久预算、模型身份强制、上下文限制、缺失覆盖、评分原答案恢复和候选路径隔离。

必要适配和许可证说明见 [上游适配说明](docs/upstream_adaptations.md)，各项目的采用范围见 [项目对比](docs/project_comparison.md)。源码精简不会提前混入首轮适配实验。


历史上下文诊断协议为 `mini-text-bash-v3`：总上下文32768 tokens，保留2048输出和明确聊天封装余量；固定Qwen tokenizer身份、文件摘要与计量算法，宿主估计与服务实际usage分开记录。旧协议运行只能离线报告，不可按新预算恢复。严格兼容仅在reasoning_content中返回的单个完整动作，保留原始和agent实际收到的回复供审计与回放。详见 [上游适配说明](docs/upstream_adaptations.md)。

历史上下文修复验收见[第二轮审查报告](docs/context-protocol-review.md)，角色/工具兼容与30调用真实结果见[第五轮审查报告](docs/role-transport-review.md)：工程检查通过，尚未证明RSI。

历史改进任务v1另冻结 `sica-mini-improvement-v1`（`configuration.json.improvement_task`），包含任务渲染边界与原始SICA任务源码SHA；缺失或不一致的配置在预算/runtime/模型建立前拒绝恢复。任务明确实际父版本、执行改进者、未评测候选、只读启动归档与实时可写workdir，并说明现有30调用/600秒/命令60秒/输出2048/总上下文32768限制。改进者在mini中直接尽早开展元改进推理，继续保留原SICA提出、实施改进和更新日志的任务意图；benchmark提示不追加此说明。每次改进保存实际提示及宿主/容器身份摘要，真实收益仍须独立评测。

第三轮任务协议修复与实测见[任务协议验收](docs/task-protocol-review.md)：父/改进者/候选身份、元改进意图与现有预算已明确并冻结；真实30调用仍无源码变化。

历史七步元改进恢复使用独立任务合同 `sica-mini-improvement-v2`：显式结构调用、当前阶段完成入口、原四种委员会评审及归档分析/编码/文档角色，统一从实际选中的归档 mini 加载并共享30次真实调用预算。阶段完成仍由模型声明，允许提前提交；状态记录会如实标记未完成。只读角色限制的是工具接口，并非同容器内恶意Python的独立安全沙箱。原工具的文本传输映射、不可用web/旧测试命令及其他偏离见 [适配说明](docs/upstream_adaptations.md)。旧任务合同不能按新工作流恢复；工程fixture不代表真实RSI收益。

历史预算诊断任务合同为 `sica-mini-improvement-v4`，冻结全角色共享500次实际模型调用/600秒，用于已授权预算诊断；任务求解仍30调用/600秒。上下文32768、输出2048、命令60秒、并发1及模型不变，角色说明和主agent本地步数与实际会话额度一致。此设置不是原SICA每agent500迭代的完整预算复现；不兼容旧运行恢复。只做一次新提案，不重测基线/val/test，有运行源码变化才评预定开发题。

500调用诊断结果见[预算对照报告](docs/budget-500-review.md)：真实86请求后达到600秒，已进入评审和编码，但长报告提交错误及编码回复截断，未修改源码，RSI仍未成立。


## 当前预算合同：原SICA作用域（v5）

默认按每角色独立迭代：协调者/归档/求解/评审500，编码者1000；一轮最多5次provider请求（总结默认1次），全部真实请求计量。基准任务600秒/$3；自我改进无单独600秒限制，受全局24小时和阶段时间约束。工具600秒、命令声明5..1800秒取有效最小值。模型输出/context使用已认证服务的能力声明；例如deepseek-flash公布1048576 context和393216输出。无匹配本地tokenizer时由服务执行context上限，宿主审核实际usage，不再套用旧Qwen32768/2048。

DeepSeek输出截断使用其beta prefix协议续写；不支持prefix的服务显式失败。最多5次仍截断时不执行未完成动作。工具/观察/公开读取不再截断。SICA事件总线的文件视图重建未移植；300000字符仅记录为原版重建触发条件，不作为mini上下文上限。安全根路径、独立评分、源体积和容器资源隔离仍保留。DeepSeek $3守卫按[官方峰值价格](https://api-docs.deepseek.com/quick_start/pricing/)计算保守成本上界，非实际账单；无冻结价格的服务拒绝基准推理，避免虚报零成本。旧运行不能按新合同恢复。

本地Ollama兼容：通过版本、tags摘要和show参数核验模型，接受明确的同名`:latest`别名。context取实际`num_ctx`而非架构最大值；未设置独立`num_predict`时使用`context-1`推导边界，明确不作为模型声明的独立输出上限。当前Qwen服务`num_ctx=65536`，没有恢复旧2048输出裁剪。经过原生身份核验的非cloud Ollama模型可在价格未知时运行，美元成本记录为未知、$3阈值标为未复现；其他未知价格服务仍拒绝，不把本地推理记为免费。

一次运行检查入口：

```bash
uv run python scripts/smoke_once.py --output runs/budget-alignment-smoke
```

仅一次提案，运行源码变化才评测预先固定dev题django__django-14752；不重测baseline/val/test。旧模型H0公开证据只供分析。原24小时已过期时入口拒绝启动；只有用户重新授权单次测试时间后，才可显式添加 `--new-time-budget-seconds 1800`。此参数创建独立诊断时间合同，保留旧实验原始起点，不能用于把旧实验超时改写为完成。工程验收见[预算对齐记录](docs/sica-budget-alignment-review.md)。

2026-10-12用户授权新增30分钟后，代码3cc4718的单次真实检查在1768.39秒内完成：七步流程完成，模型修改了归档mini的agent源码与配置，候选加载后在预定开发题上独立评分通过。没有重测基线或运行val/test，也没有第二次提案。此结果证明一次自改源码及候选执行链路运行成功，不证明收益或递归继承。完整证据与保留问题见[单次测试报告](docs/sica-budget-alignment-smoke.md)。

Qwen 新提案单次测试见 [真实测试报告](docs/qwen-rsi-single-smoke.md)：28 调用后提前提交、源码未变、条件开发题跳过。发现超长历史反馈与当前任务混淆及服务输入完整性风险；尚无 Qwen 自改进或 RSI 收益证据。

当前元任务合同为 v6，恢复原 SICA 角色工具归属，步骤完成调用绑定宿主身份；经服务验证的 Qwen tokenizer 提供输入容量和明显截断审计（不承诺精确模板保留）。见[接口修复审查](docs/sica-tool-boundary-review.md)。旧 v5 运行保留只读，不能恢复到 v6。

v6修复后的一次Qwen实测见[结果报告](docs/sica-tool-boundary-smoke.md)：67次请求、无源码修改；命令引用错误阻碍委派，最终保守输入容量保护终止，条件开发题跳过。工程115项验收通过不等于Qwen已产生自改进。

SenseNova `token.sensenova.cn` 的认证模型目录使用 `context_length/max_output_length`，框架显式映射并冻结这些容量、量化和服务价格声明。仅完整全零费率可直接记为服务声明的零费率（不代表独立账单验证）；其他价格保持未知并拒绝基准推理，不猜币种/单位。远程GLM没有已验证tokenizer，不沿用Qwen输入完整性保证。OpenAI兼容调用形式参考[官方API说明](https://github.com/OpenSenseNova/SenseNova6.8/blob/main/API.md)，具体GLM能力以本次认证模型目录为准。

最新[GLM单次检查](docs/new-model-single-smoke.md)：工程127项通过；真实提案完成1/7后连接/API失败，无源码变化、开发题跳过，不能判定模型自改进能力不足。

本地CLIProxyAPI/Qoder的标准目录缺容量时，读取认证`/models?client_version=pi`的同名容量声明；仅作为代理声明冻结，不证明实际后端权重，不使用目录附带的通用指令或猜测价格。未知价格仍拒绝基准推理。HTTP失败记录状态码，正文不保存。扩展目录来源参考[官方客户端](https://github.com/router-for-me/pi-cliproxyapi-provider)。

用户确认本地Qoder代理为订阅／免费额度时，可在.env显式设置`RSI_BILLING_MODE=subscription`。此合同只对已核验的本地Qoder目录生效；美元费用保持未知，原SICA的$3阈值不可复现，token/时间预算仍执行。不能把订阅成本记为零美元。

按用户要求，单次诊断使用`uv run python scripts/smoke_once.py --output <新目录> --unbounded-diagnostic`，没有额外全局或阶段墙钟截止。原SICA角色500/1000轮、单题600秒和工具限制仍生效。历史有期限结果保持原样，不用新模式恢复过期实验；诊断费用与token照常记录。
