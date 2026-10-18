# 仅保留当前完整 RSI 循环：后续精简执行文档

日期：2026-10-19。审计基点：`88c710a`（`refactor: trim online harness and simplify role transport`）。

本文件合并最近两轮“只保留最新版本完整 RSI 循环”的代码审计，包括函数调用检查、实际产物比对及测试结果。文档最初仅用于分析；用户随后授权实施全部事项，使用 GPT-5.6 Luna / High 子 agent，由主 agent 审查、测试、验收并提交。13 项已全部验收，详情见[验收记录](online-rsi-current-only-simplification-review.md)。 这里的 P1/P2 是新一轮优先级，与[上一份执行计划](online-rsi-followup-simplification-plan.md)中已经完成的 P1/P2/P3 分开计数。

“最新版本”指当前项目的在线实现，不指升级上游依赖。审计基点的在线协议与 improvement 合同均为 v7；资源政策 v2、检查政策 revision 2、候选检查 revision 1、恢复格式 v1 各有独立含义，版本数字不需要一致。当前 mini-swe-agent 仍固定为 2.4.6。

## 1. 审计依据与结论边界

| 依据 | 已观察到的结果 | 能支持的结论及限制 |
|---|---|---|
| 自有生产代码的调用方、写入方与读取方检查 | 找到重复输出、宽松字段兼容、重复身份包装与摘要算法 | 可以定位精简范围；不能仅凭函数名称或单次未命中判断死代码 |
| 全量测试，启用真实 Docker | 411 passed，0 failed，0 skipped；pytest 记录耗时 133.785 秒 | 当前基点回归通过；这是变更前基线，不是未来修改的验收结果 |
| 5 项定向测试的宿主调用跟踪 | 5 passed，6.88 秒；观察到 245 条调用边、121 个文件与函数名组合 | 证实任务、检查、接任及恢复链路；不覆盖所有分支、容器子进程或全部函数 |
| 已完成运行的文件比对 | 6 对 `trace.txt` / `execution_tree.txt` 相同；4 对 `check_plan.json` / `check_plan_host_record.json` 相同 | 配合生产代码的同内容写入及无独立消费者，支持删除重复副本；不是删除其他计划或轨迹文件的依据 |
| 隔离的格式探测 | 宿主结果可用其他摘要字段匹配；候选已完成记录缺 metadata 可被注册器载入 | 说明内部契约仍宽松；后续还有私有记录和宿主核验，不能据此声称可以绕过整个接任流程 |

全量测试使用真实容器、确定性模型服务和评分替身，没有调用真实模型推理。测试镜像为 `mini-swe-rsi-runtime:p1-review`，镜像 ID 为 `sha256:c7fa8d89f57a3472e158adb50309434cb8bbc5aa1c53d62eed5dfb5b9c245599`。测试出现一条既有 LiteLLM event loop 弃用警告，没有测试失败。

本机证据为 `/tmp/mini-rsi-current-only-audit-pytest.xml` 和 `/tmp/mini-rsi-current-callgraph.json`；它们是临时审计产物，不纳入源码提交。实现时须重新生成验收证据。

定向跟踪使用：

- `test_online_acceptance.py`：两代真实源码变化并使用新父版本、两步诊断 skip、已有决策恢复不重复任务／检查／接任。
- `test_workflow.py`：委员会只读与角色权限。
- `test_online_state.py`：检查回调提前注册并绑定活动父版本。

实际观察到的主链为 `run_once → execute_task / run_round → _improve_with_driver → _check_candidate → _acceptance → _commit_round`；恢复链调用 `_recover → _apply_existing_decision`。以下计划以保留这些行为为前提。

## 2. 必须保留的完整循环

1. 当前活动 H_v 执行当前任务；宿主评分并生成绑定源码与 attempt 的反馈。
2. 前两步诊断可以终止为 skip；skip 不启动第三步，也不产生接任。
3. 第三步声明接受标准及可选机制测试计划；第四步修改候选源码。
4. 第五步冻结候选与计划，启动独立进程检查候选；机制标准仍要求父版本失败、候选通过，并保留现有评分约束。
5. 第六／七步只更新说明与变更日志；宿主核验后原子接任为 H_(v+1)。下一任务及改进进程实际使用新版本。
6. 拒绝、暂停、中断、评分恢复、显式授权的当前版本恢复、不可变决策重放，以及 token／调用／耗时累计保持有效。

完整源码摘要与执行源码摘要用途不同：第六／七步修改根目录文档时，可以改变完整摘要而不改变执行摘要。**不能合并这两种摘要，也不能删除宿主与容器各自的验证。**

保留阶段写入权限、候选冻结、私有评分与未来任务隔离、服务凭据隔离、归档过滤、控制清单、资源限制和工具输出保护。保留 `trace.txt`、私有完整 `trajectory.json`、`process.log`、答案与补丁，以及实际使用的公开反馈。删除计费遗留占位不等于删除 token／缓存／耗时审计。

## 3. 合并后的优先级清单

P1 包括证据明确的冗余删除，以及当前格式必须统一的内部记录；P2 涉及对外字段、跨模块接口或算法重构，适合在 P1 稳定后处理。优先级不代表可以跳过验收。

| 编号 | 优先级 | 可精简部分 | 实施结果 | 状态 |
|---|---|---|---|---|
| P1-01 | P1 | 旧 SICA stdout 汇总行 | 删除 `0\|0\|0\|duration` 及专用计时 | 已验收 |
| P1-02 | P1 | 重复文字轨迹 | 删除 `execution_tree.txt` 的生产与公开白名单项 | 已验收 |
| P1-03 | P1 | 重复宿主计划副本 | 删除 `check_plan_host_record.json` | 已验收 |
| P1-04 | P1 | 未使用导入与赋值 | 删除已确认无用途的局部代码 | 已验收 |
| P1-05 | P1 | 宿主结果摘要别名匹配 | 内部完成记录统一要求完整源码 `code_hash` | 已验收 |
| P1-06 | P1 | 任务摘要身份兜底 | 已存在摘要仅接受完整当前身份 | 已验收 |
| P1-07 | P1 | 候选持久记录身份兜底 | 按状态校验完整身份，移除旧布局默认值 | 已验收 |
| P2-01 | P2 | report 内自带完成标记 | 仅接收报告正文，由宿主添加标记 | 已验收 |
| P2-02 | P2 | 双协议键 | 统一为 `protocol`，删除 `online_protocol` 别名 | 已验收 |
| P2-03 | P2 | 固定为真的报告字段 | 删除 `test_sealed` 输出及 CLI 展示 | 已验收 |
| P2-04 | P2 | 父版本结果重复包装 | 保留一次来源核验，简化下游所需身份接口 | 已验收 |
| P2-05 | P2 | 重复迭代参数与过时提示 | 单一活动父版本输入，内部派生原提示变量 | 已验收 |
| P2-06 | P2 | 宿主／容器摘要算法重复 | 抽取纯实现，两端仍独立调用验证 | 已验收 |

## 4. P1 实施细则

### P1-01：删除旧 stdout 汇总

位置：[runtime/bridge.py](../runtime/bridge.py)，`main()` 执行段的 `start = time.time()`、汇总 `print` 与专用 `time` 导入。

上游 SICA 曾输出并解析 `total_tokens|cached_tokens|total_cost|duration`。当前 bridge 的前三项固定为零；当前 Driver 读取 JSON 结果、进程状态与宿主 metrics，生产代码没有解析此行的消费者，真实进程日志中仍能看到该占位。

删除汇总行及仅为此服务的计时、导入。保留执行状态、退出码、结果 JSON、进程日志和真实消耗统计。验收正常提交与失败退出均保持原语义，日志不再产生旧四列汇总；不得删除其他模块的计时。

### P1-02：删除重复 `execution_tree.txt`

位置：[runtime/bridge.py](../runtime/bridge.py)、[driver.py](../src/mini_swe_rsi/driver.py) 的超时轨迹保存、[archive_view.py](../src/mini_swe_rsi/archive_view.py) 的公开名单及相关测试。

当前文件与 `trace.txt` 写入相同正文，并没有独立树结构；在线反馈使用 `trace.txt`。删除两处重复生产及对应归档声明，调整测试和当前说明。

验收正常、失败、期限中断路径仍可读取 `trace.txt`；已有私有完整轨迹不受影响。不能因为某个失败路径没有 `trajectory.json` 就同时删掉补救文字轨迹。历史运行中的重复文件无需清除。

### P1-03：删除重复 `check_plan_host_record.json`

位置：[online.py](../src/mini_swe_rsi/online.py)，最终检查计划写入段。

该副本与同处写入的 `check_plan.json` 相同，没有独立生产读取方。删除副本写入与相关说明，保留宿主审计用的 `check_plan.json`。

**保留 `check_plan_declared.json` 和 `check_plan_frozen.json`**：前者是第三步声明，后者是第五步扩展宿主身份后的不可变检查输入。验收两种接受标准、第五步冻结后计划不可变及恢复一致性；禁止合并或覆盖这两个文件。

### P1-04：删除已确认无用途的局部代码

位置：[stage_tools.py](../src/mini_swe_rsi/stage_tools.py) 的 `StageTools.command()` 局部 `import subprocess`。

该方法只构造命令，没有消费此导入，可直接删除。另可核对 [driver.py](../src/mini_swe_rsi/driver.py) 中 `relative = None`：仅在确认每条实际读取路径都先赋值后删除初始占位。

复用阶段权限、挂载及控制器回归即可，无需新增仅断言“某行不存在”的测试。其他模块实际使用的 `subprocess`、计时、回调参数不得随同清理。

### P1-05：统一当前宿主完成记录的源码身份

位置：[core.py](../src/mini_swe_rsi/core.py) 的 `result_matches_source()`；Driver 的任务／重试／候选检查；Online 的反馈、父结果与候选检查。

当前函数把 `code_hash/full_source_hash/execution_hash` 与预期摘要集合取交集。真实 `task_execution.json` 和 `host_result.json` 写入方已经使用完整源码 `code_hash`；但部分测试替身用执行摘要写入同名字段。

实施顺序：

1. 列出所有调用与真实写入结构，先将测试替身改为生产契约；尤其核对 `test_online_acceptance.py` 的 FakeDriver。
2. 内部完成记录只凭必需的完整源码 `code_hash` 判断来源，删除其他摘要别名救援和多摘要集合接口；若存在旧别名或冲突，按明确的新契约拒绝，不能让冲突记录命中任意一项后通过。
3. 一次选定 matching attempt，再使用该 attempt 的评分、执行及激活证据；保留损坏记录的明确报错和合法第二次尝试选择。

验收缺少 `code_hash`、错误源码、冲突字段、损坏首次记录不被第二次掩盖、合法重试选择，以及文档更新后完整／执行摘要仍各司其职。该项只收敛内部完成记录，不删除反馈和候选身份中真实使用的两种摘要。

### P1-06：已存在任务摘要必须具有当前身份

位置：[online.py](../src/mini_swe_rsi/online.py) 的 `_feedback()`。

当前 `execution_summary.json` 写入方已有 `task_id/round_id/version/full_source_hash/execution_hash/attempt` 等字段；读取方仍允许身份缺失、摘要缺失和旧 `code_hash` 兜底。

收敛为存在的摘要必须是合法普通 JSON 对象，并具有完整且一致的当前身份，核对任务、轮次、版本、双摘要及所选 attempt。去除缺字段视为匹配或旧摘要名替换的路径。

**整个摘要尚未创建是合法崩溃窗口**：可从已验证宿主完成记录生成；已有摘要损坏或身份不符应拒绝，不能先覆盖再当作恢复成功。保留原子写入和不可变决策约束。验收正常生成、当前格式恢复、缺字段／错 attempt／错版本／坏文件拒绝及合法中断窗口。

### P1-07：收敛候选任务持久身份

位置：[broker.py](../src/mini_swe_rsi/broker.py) 的 `register_candidate_job()` / `candidate_check()`；[online.py](../src/mini_swe_rsi/online.py) 的 `_check_candidate()` 及私有完成记录验证。

生产注册已经提供完整身份，生产调用 `_check_candidate()` 也提供 identity；持久读取仍有缺 metadata、缺 job_id、可选 identity 和缺 parent_hash 的兜底。

实施时统一调用方与状态记录：

- 要求宿主注册身份完整，绑定 job、任务、轮次、候选、父版本、阶段、检查 revision、角色与会话；模型请求只回显现有允许公开的身份字段，不能暴露或要求它提供私有会话摘要。
- 删除生产调用不需要的可选 identity 默认构造；已有持久记录要求 job_id 与 metadata 完整且匹配，completed 记录不能因 metadata 缺失而重新发起检查。
- 按 `registered/running/completed/failed/interrupted` 的语义分别检查字段：completed 要求结果，failed 要求错误；不能要求 running 具备完成结果。保留中断识别、幂等性、并发状态和明确错误。
- 私有完成记录必须绑定已验证父版本完整摘要；不接受缺 parent_hash。不得以公开结果替代私有宿主证据。

验收覆盖每种状态的合法记录、缺失／冲突身份、会话或角色不符、已完成重复请求不重复执行、中断不伪装为普通拒绝，以及当前授权恢复。

**不能直接删掉 `_check_candidate()` 补充 `record` 的分支**：真实 Driver 的公开返回值没有完整 record，当前逻辑需要从同一已验证 attempt 的私有宿主结果补齐。若重构此接口，必须一并统一生产返回与隔离边界，并保留评分和来源一致性验证。

## 5. P2 实施细则

### P2-01：角色提交只接收报告正文

位置：[runtime/workflow.py](../runtime/workflow.py) 的 `_structured_submission()`；[test_completion_transport.py](../tests/test_completion_transport.py) 的 prefixed legacy report 回归。

删除“report 已带 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 前缀时沿用原文”的兼容。当前结构化输入继续为原始 JSON `{"op":"submit","report":"完整非空正文"}`，宿主统一添加 mini 完成标记。带完成前缀或只有标记的 report 明确拒绝。

保留非空、类型与额外字段验证，以及报告内部引号、换行、Unicode、尾部空格、类似 shell 的普通文本数据语义。保留 benchmark 的标准 mini 提交路径；旧 shell 引号 JSON 和受限角色 echo/printf 已在前轮移除，无需重复实施。

### P2-02：协议只保留一个字段

位置：[online_contract.py](../src/mini_swe_rsi/online_contract.py)、Online 配置写入／恢复、报告读取及测试生产者。

统一当前契约键为 `protocol`，同步所有写入和读取，删除 `online_protocol` 别名及双键写入。新契约拒绝旧别名布局、缺键和冲突身份，不提供旧版本迁移。

报告仍须支持**当前协议的部分故障产物**，例如仅有 state 或 configuration 的中断目录；“文件不完整”不自动等于旧格式。保留实际 check policy 验证及失败报告语义。

### P2-03：删除固定 `test_sealed` 输出

位置：[report.py](../src/mini_swe_rsi/report.py)、[cli.py](../src/mini_swe_rsi/cli.py) 及报告测试。

该字段恒为 True，当前没有实际 seal 操作或在线门禁消费它。删除报告键和 CLI 展示，避免输出一个没有验证过程的结论。保留真实 usage、事件完整性、失败状态和公开过滤。

拒绝旧 `test_seal` 产物的测试或标记检查仍属于拒绝旧格式的边界，不随此项删除。

### P2-04：简化父版本结果传递

位置：[online.py](../src/mini_swe_rsi/online.py) 的 `_load_parent_record()`、`_improve_with_driver()` 回调；[driver.py](../src/mini_swe_rsi/driver.py) 的 `candidate_check()`。

当前父结果经源码验证后扩展三种摘要，回调又覆盖摘要；Driver 实际只从该参数读取父 `code_hash`。在 P1-05 稳定后，删除嵌套 record 的旧布局兜底与重复包装，选择显式 `parent_hash` 或一个最小且已验证的内部结果对象。

**不能删除 `_load_parent_record()` 的验证职责**。保留父任务已完成、正确 attempt、源码绑定、父反馈及评分约束、私有完成记录的父身份。接口变更同步真实 Driver 和测试替身。

### P2-05：统一活动版本输入并修正提示

位置：[online.py](../src/mini_swe_rsi/online.py) 的控制器参数；[driver.py](../src/mini_swe_rsi/driver.py) 的参数读取与提示渲染；[runtime/bridge.py](../runtime/bridge.py) 的 parser、验证及提示适配；恢复身份相关模块。

当前 `--parent-iter/--best-iter/--current-iter` 均由同一活动 H_v 生成。选定单一父版本输入，并与已有 `--online-version` 身份校验一致；原始 SICA 模板仍需要的 best/current 值在内部派生。

同步宿主、容器、启动身份、恢复授权、控制摘要和测试；保留负值／版本不匹配拒绝及实际启动源码路径核验。修正“improver may differ from parent”等过时适配文字，说明当前活动版本负责改进从自身复制的候选。

原始固定 SICA 模板与 vendor 快照不修改。不得只删参数声明而遗漏恢复身份或原模板插值。

### P2-06：共享摘要算法

位置：[core.py](../src/mini_swe_rsi/core.py) 的摘要函数与 [runtime/workflow.py](../runtime/workflow.py) 的候选摘要函数。

将完整树／执行树摘要提取为无服务依赖的纯模块，宿主与容器分别调用，并把新模块纳入控制清单。先固定有效目录的摘要基线，再重构。

当前异常语义有差别：宿主缺目录可能得到空树摘要，运行时可能返回 None。明确统一接口及调用方错误边界，不用默认空摘要掩盖无效输入。

验收所有有效树摘要保持一致；仅根目录说明文件被执行摘要排除，包内同名资源仍影响摘要；根入口文件保留，`__pycache__` 排除，符号链接／特殊文件与无效路径继续被相应边界拒绝。共享算法不能代替任何一端的独立核验。

## 6. 建议实施批次与依赖

以下 A～E 是**本文件的新批次**，与历史同名字母批次无关；每批完成主 agent 审查及验收后再进入下一批。

| 新批次 | 内容 | 依赖与版本策略 |
|---|---|---|
| A：直接冗余删除 | P1-01～P1-04 | 先完成。保留功能契约，按既有控制清单处理源码身份变化，不机械升级所有协议 |
| B：当前内部记录收敛 | P1-05～P1-07 | 先规范生产者与测试替身，再收敛读取；作为一个一致的持久契约变更验收 |
| C：提交与报告契约 | P2-01～P2-03 | B 完成后推进；同时迁移提示、配置／报告与测试 |
| D：父版本接口精简 | P2-04～P2-05 | 依赖 B；跨宿主、容器和恢复身份一次改完 |
| E：共享摘要实现 | P2-06 | 在前几批稳定后进行；用摘要等价性和真实容器回归控制风险 |

首次不兼容持久契约变化时，在线协议从当前 v7 升级到 v8；若后续批次再次改变已经发布的持久格式，应再递增，不能让同一版本代表两种契约。若尚未发布且合并实施，可统一形成一个新契约。发生角色任务或恢复格式变更时，另行判断其独立版本是否需要递增；不得为追求数字相等修改无关 revision。

新代码拒绝旧版本恢复和重新报告；历史运行使用对应 Git 版本读取。**当前新版本自身的合法崩溃恢复必须继续支持**，不应把它当作旧版本适配删除。

## 7. 验收与提交要求

### 定向验证

| 变更类别 | 优先复用的测试 | 必须核对的行为 |
|---|---|---|
| 输出及冗余文件 | `test_archived_runtime.py`、`test_archive_view.py`、`test_online_docker.py` | 正常／失败日志完整，归档证据可读，重复产物不再生成 |
| 宿主结果及摘要 | `test_online_acceptance.py`、`test_online_result_custody.py`、`test_grading_recovery.py` | 当前完整身份、同 attempt 取证、错误和缺失字段拒绝、评分恢复不重跑模型 |
| 候选记录及恢复 | `test_broker.py`、`test_online_state.py`、`test_online_authorized_resume.py`、`test_resume_identity.py` | 分状态字段、幂等检查、会话／角色绑定、授权恢复及消耗累计 |
| 提交与契约 | `test_completion_transport.py`、`test_role_transport.py`、`test_online_contract.py`、`test_online_review.py`、`test_cli_options.py` | 原始 JSON 正文、旧格式拒绝、当前部分故障报告、标准 mini 提交 |
| 版本接口与摘要 | `test_improvement_task.py`、`test_contracts.py`、`test_local_checks.py`、`test_stage_write_policy.py`、`test_online_docker.py` | 渲染身份、实际执行来源、两种摘要语义、阶段冻结及双端验证 |

新增测试应针对真实行为或被删除兼容路径的拒绝，不为删除一行导入添加镜像实现的测试。先跑定向回归，再跑启用 Docker 的全量测试；每批新增／删除测试后记录实际数量，不固定要求恰好 411 项。

可复用的全量命令如下（在项目根目录运行，已安装开发依赖且本机具备测试镜像）：

```sh
RUN_DOCKER_TESTS=1 RSI_TEST_RUNTIME_IMAGE=mini-swe-rsi-runtime:p1-review LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run pytest -q --junitxml=/tmp/mini-rsi-current-only-acceptance.xml
```

镜像变量是本机审计镜像覆盖，其他环境应指定经核验的可用镜像。Docker 无法运行或集成测试跳过时，必须记录验收尚未完成，不能仅凭单元测试宣称完整循环通过。测试不得依赖 `.env`、真实凭据或真实模型服务；真实模型最短实验与本计划的确定性验收分开记录。

### 主 agent 审查与未来提交

1. 对每项确认生产者、消费者和删除理由；复核测试替身与真实 Driver 契约一致。未观察到调用只能作为线索。
2. 复核两代接任后下一任务确实用新 H；skip 不进入第三步；独立候选及机制检查、拒绝、暂停和恢复路径通过。
3. 核对私有结果未暴露、第五步源码不可写、第六／七步只能写文档、已冻结证据及已发布决策不能覆盖。
4. 对照基点确认原始上游、计费删除补丁、mini 裁剪清单、依赖锁、数据、凭据、历史运行、已有候选与 `.DS_Store` 不在修改范围；如新控制模块需要冻结，只更新对应控制清单和契约。
5. 保存验收结果、变更范围和源码／生成包各自减量。允许必要的校验增加代码，不以总行数下降代替正确性。
6. 后续实施如委派，子 agent 只负责所分配变更与定向测试，不提交、不推送；主 agent 审查集成、全量测试并验收后提交。模型按实施时用户指定配置，不因本次文档请求自动启动实施。
7. 提交采用现有 Conventional Commit 格式，例如 `refactor: remove redundant online runtime artifacts`；正文说明最终行为与验证。每个提交保持契约自洽，不提交半迁移状态；不包含临时运行产物，只提交本批文件。

## 8. 不应作为本轮清理对象的代码

- 当前候选公开结果缺 `record` 时的私有宿主补齐、父结果来源验证、双摘要、独立检查与接任证据。
- mini 剩余 model/environment/config/utils API 与包资源：当前生成 H0 已由 74 文件裁剪为 51 文件；没有证据支持按单次调用记录继续删除完整模块。
- provider 真实响应格式、模型配置、可选传输政策及故障暂停分支；“historical” 注释不等于功能无用。
- HTTP 框架回调等隐式调用方法、约定中的 callback 参数、当前可选缓存数据；静态未引用不等于死代码。
- token／缓存／耗时统计和资源／输出保护；这里只删除 stdout 中固定零值的旧计费占位。
- 图像资产、输入完整性检查及历史命名的数据路径；它们仍有当前生产用途。
- 拒绝旧版本的测试和旧产物标记检查；拒绝旧格式与支持旧格式是不同职责。
- `.upstream`、固定 vendor 源码、旧执行计划和验收记录；它们是来源与历史证据，不是待删除的当前兼容实现。

A～E 全部实施并验收；最终全量 478 passed、0 skipped。详见[验收记录](online-rsi-current-only-simplification-review.md)。本文件保留审计基点与原始执行边界；没有待实施事项。
