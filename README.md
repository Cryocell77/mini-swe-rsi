# mini-swe-rsi

固定模型的 Harness 自改进实验：以原始 SICA 控制器驱动官方 mini-swe-agent，候选运行与评分隔离，验证递归继承、任务收益和同起点改进能力。只有三项门槛全部通过后才进行代码精简。

本次实跑未通过递归机制门槛：已交付原生适配版与失败报告，最小实现尚未生成。结果见 [首次实验摘要](docs/results-summary.md)；本机完整产物位于 `runs/sica-qwen-20261011/`。

已修复失败状态传播、重复推理字段计量与归档证据暴露问题，独立验收33项测试通过；见[修复验收记录](docs/repair-review.md)。本次验收不代表RSI收益成立。新协议实验请使用新的运行目录，旧实验保留供离线报告使用。

## 安装

需要 Python 3.12、uv、Docker。模型服务从根目录 `.env` 读取 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LOCAL_LLM_MODEL`。凭据不进入候选或 Git。

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


当前新实验协议为 `mini-text-bash-v3`：总上下文32768 tokens，保留2048输出和明确聊天封装余量；固定Qwen tokenizer身份、文件摘要与计量算法，宿主估计与服务实际usage分开记录。旧协议运行只能离线报告，不可按新预算恢复。严格兼容仅在reasoning_content中返回的单个完整动作，保留原始和agent实际收到的回复供审计与回放。详见 [上游适配说明](docs/upstream_adaptations.md)。

最新修复验收与真实提案结果见[第二轮审查报告](docs/context-protocol-review.md)：工程检查通过，真实30调用仍无源码变化，尚未证明RSI。

改进任务另冻结 `sica-mini-improvement-v1`（`configuration.json.improvement_task`），包含任务渲染边界与原始SICA任务源码SHA；缺失或不一致的配置在预算/runtime/模型建立前拒绝恢复。任务明确实际父版本、执行改进者、未评测候选、只读启动归档与实时可写workdir，并说明现有30调用/600秒/命令60秒/输出2048/总上下文32768限制。改进者在mini中直接尽早开展元改进推理，继续保留原SICA提出、实施改进和更新日志的任务意图；benchmark提示不追加此说明。每次改进保存实际提示及宿主/容器身份摘要，真实收益仍须独立评测。

第三轮任务协议修复与实测见[任务协议验收](docs/task-protocol-review.md)：父/改进者/候选身份、元改进意图与现有预算已明确并冻结；真实30调用仍无源码变化。

七步元改进恢复使用独立任务合同 `sica-mini-improvement-v2`：显式结构调用、当前阶段完成入口、原四种委员会评审及归档分析/编码/文档角色，统一从实际选中的归档 mini 加载并共享30次真实调用预算。阶段完成仍由模型声明，允许提前提交；状态记录会如实标记未完成。只读角色限制的是工具接口，并非同容器内恶意Python的独立安全沙箱。原工具的文本传输映射、不可用web/旧测试命令及其他偏离见 [适配说明](docs/upstream_adaptations.md)。旧任务合同不能按新工作流恢复；工程fixture不代表真实RSI收益。
