# 上游适配与证据边界

## 当前范围：仅在线 RSI（2026-10-16）

项目按 [P1 精简指南](online-rsi-simplification-guide.md) 移除原批量实验入口、原 SICA runner、ArchiveAnalyzer、BenchmarkTracker、轨迹回放、探针和 held-out 门槛。当前运行入口为 `online`，报告入口为 `report`；旧批量实验报告保留为历史产物，不由新入口重新生成。

保留 `vendor/sica/agent_main.py`、`vendor/sica_workflow/` 与许可证的固定原文，用于提取改进任务、七步定义、角色权限和委员会。`upstream.lock.json` 只保留当前实际使用的 vendored 文件清单及预算来源。官方 mini 和 SWE-bench 评分资产继续固定，在线角色仍从本轮活动版本加载 agent/model。

在线执行者、改进者和候选父版本一致。两步诊断后的 skip/improve、步骤 3 冻结验收计划、步骤 5 候选独立执行，以及验收后接任继续属于显式在线扩展。在线历史按时间呈现，不用不同题目覆盖的平均成绩排名。

原固定轨迹回放已移除；在线父版本与候选使用同一个冻结测试的 `mechanism_check` 保留。单题 `benchmark` 仍用于首次作答与候选复测；评分恢复、阶段写权限、公开归档、预算和真实计量边界继续执行。

控制代码摘要变化会阻止旧运行直接恢复，历史记录不改写。本轮仅完成 P1，不收敛 P2/P3 的预算、数据、上下文兼容或记录结构。

以下章节按产生时间保留，描述历史实现与实验，不应作为当前批量实验功能仍可用的说明。

## 历史适配记录

`vendor/sica/` 的 runner、ArchiveAnalyzer、BenchmarkTracker、原始 agent CLI 和 MIT 许可按固定提交逐字保留，启动时校验摘要。加载 runner 时仅替换 `base_agent.*` 导入；其余函数体保持原样。

## 必要适配

| 边界 | 实现差异 | 原因 |
|---|---|---|
| Agent | 官方 mini 替换 SICA agent，保留 benchmark/improve 入口 | 用户指定；归档源代码实际加载 |
| 模型 | 所有调用经宿主代理固定为 .env 模型，包括原始 Gemini 总结调用 | 无更强模型、统一计量与资源上限 |
| 数据 | 固定 dev30 替代上游 benchmark 注册表和18题抽样 | 用户指定旧资产 |
| 任务执行 | process_job 替换为隔离 solver + 归档 mini controller + 官方评分 | Python版本兼容、私有评分隔离；保留上游 queue/tracker/aggregation |
| 改进执行 | 原始 run_meta_agent_benchmark 的 Docker命令由边界适配器执行 | 仍由原始函数选父版本、复制代码、选改进者和生成总结 |
| 依赖镜像 | 固定依赖镜像，不自动重建候选声明的新依赖 | 同预算离线运行；仅支持现有依赖内的源代码/配置编辑，作为实验限制披露 |
| 改进任务 | 原始任务文字保留；替换不存在的 SICA 子agent/推理结构工具指令 | mini只有bash命令接口；不添加新的反思/记忆策略 |
| 调度 | 在原生函数外交错两轨道，并加入配对探针/封存评测 | 用户指定实验；不改变递归轨道的原始父选择与改进者选择 |
| 不完整结果 | 不满30题不写可供选拔的 perf；恢复不重复生成中断的提案 | 防止部分结果被误报为冠军或增加搜索预算 |

SICA 按进程返回状态生成的 meta 总结不能代表候选有效；本项目另记录 agent exit_status、源码变化、实际行为与任务测评。

行为见证逐代对照SICA实际选中的父版本。先用候选自身代码重放已记录回复/工具输出，确认轨迹可重现，再在相同记录下重放父版本，比较模型输入、命令、操作顺序和终止行为。继承已有行为后仅修改未使用代码不能计为新一代。回放均在无网络、无真实模型凭据、无评分资产且源码只读的容器内执行；原始输出缺失、自回放不一致或回放不完整时不计行为证据。

本地服务没有可核实价格。SICA `cost=0` 是接口占位；报告以 tokens/耗时计量，美元成本记 unknown。首次评分/推理预检启动24小时计时，重启不重置。

## 隔离

候选只挂载自身代码、公开开发集归档、输出目录和候选工作目录。模型凭据留在宿主；容器仅有过期即失效的会话令牌。评分只在宿主的独立 evaluator 子进程与新容器内执行；参考补丁与私有测试不进入 agent 挂载。

Docker Desktop 的 internal 网络无法直连宿主；增加固定路径中继，仅转发 `/v1/chat/completions` 和 `/tool` 到宿主计量代理。候选容器仍仅在 internal 网络内，solver 与评分容器无网络、无 Docker socket、无宿主数据挂载。

## 项目参考与许可

- SICA、mini-swe-agent、SWE-bench：MIT，保留上游许可；关键版本见 upstream.lock.json。
- FrontisAI/OpenRSI：CC BY-NC 4.0，参考执行反馈与收益拆分，未复制代码。
- OpenRSI-Foundation/OpenRSI-Index：参考Work/Judge隔离，未复制代码。
- ModularRSI：原创部分 CC BY-NC 4.0，Harbor衍生部分 Apache 2.0；参考模块边界，未复制代码。
- ScienceBuddy：参考Qwen3.5-4B候选与配对实验，未复制代码，不采用RL。
- Exo、Raven、LightRSI：参考恢复、行为证据和运行时组织，未复制代码。
- mini-rsi：仅参考公开报告；本次代码链接不可获取。
- Dream-RSI：仅参考方法；查询时完整代码未发布。

评分资产复用自 mini-swe-rsi-old 的 swe_adapter.py，增加不可变镜像身份、空补丁处理和官方评分超时分类修复。没有复用其搜索/演化控制器。

补丁相对冻结镜像的初始 HEAD 提取：官方镜像带有准备提交，不能把其相对数据集 base_commit 的差异算作 agent 修改。早期排障恢复脚本仅重建和重评已记录的答案，不重新请求任务模型；保留修复前后产物与消耗记录。评分基础设施重试只重新评判同一补丁，持久限制为最多两次，不重新生成答案。

回放支持当前DefaultAgent/TextBasedModel/LocalEnvironment执行合同；动态状态或另起入口而不能自回放校准的候选保守记为缺少证据，不据此宣布机制成立。完成任务、归档与配对评测绑定源码摘要；探针中断后继续尚未完成的dev评测，不生成第二份提案。

外层停止条件：每轨道最多四份提案。若已发生的原始源码差异数加上全部剩余提案仍少于两代，则递归门槛数学上不可达；停止继续重复评测，保留futility.json的父子完整SHA证明。按原始文件摘要计上界，避免把注释或未使用文件变化错误排除。此条件在第三份递归提案生成前记录，不改变SICA选择、评分或优化算法，也不用于制造正向结果。

## 2026-10-12 执行与反馈边界修复

原始 runner 函数体、选择、队列及统计保持不变。Docker exec 适配现在分别记录进程状态、agent 终止状态与源码变化；只有正常提交且实际修改源码才进入上游原始成功摘要。异常或无修改在边界写入确定性的事实摘要并返回失败；原生函数随后提前返回，但外层仍保存谱系并独立评测候选。预算终止留下的修改没有新增 Submitted 选拔门槛。

上一轮修复的模型消息采用 `mini-text-bash-v2`：仅支持 system/user/assistant 的字符串正文及 assistant 的单份 `reasoning_content`；删除供应商重复推理、空字段和本地元数据，拒绝原生工具协议。60000 字符按实际发出的规范化 messages 的 JSON 序列化计算；事件同时记录原始/规范化长度。协议版本单独冻结在实验配置，旧配置不能按新协议恢复；模型身份比较仍只针对实际模型配置。

改进容器的 archive 挂载改为逐文件复制的冻结公开视图，保留 `archive.agent_N.agent_code` 路径、源码 SHA 与只读挂载。SICA 选择仍读取权威原归档。视图原样保留整个通过代码验证的可编辑 agent_code（仅排除 __pycache__，拒绝 .env/.env.* 凭据文件，不限制普通配置名或新增源码扩展名），并只提供当前 dev `results.jsonl`/`perf.json`、公开任务 answer/patch/trace/execution_tree/summary/score，以及过去 meta summary；不包含完整 trajectory 配置、attempt、host_result、debug、排障结果、评分内部记录及宿主快照。轨迹的文本公开，含会话令牌的完整配置不公开。符号链接/特殊文件拒绝进入视图；源文件和挂载副本在执行前后检查 SHA。探针同样使用冻结公开证据。此视图是明确的数据暴露边界偏离，不改变源归档或归档统计。


## 第二轮文本协议与上下文预算

新实验使用 `mini-text-bash-v3`，身份与模型身份分开冻结。宿主对规范化后实际发送的 content / reasoning_content 逐字段使用固定 Qwen tokenizer 编码，总预算 32768 tokens，保留 2048 输出与 512 + 每消息32 tokens聊天封装余量。tokenizer仓库revision、文件SHA256、tokenizers库版本和计量算法记录于 `tokenizer.lock.json` / `configuration.json`。这只是宿主输入估计，不复刻服务聊天模板或SICA文件视图重建；实际服务prompt_tokens仍独立记录，事件列出与估计的差值。字符长度仅用于审计。缺失/摘要不匹配的tokenizer不退回通用tokenizer；旧协议或不一致配置在修改预算、创建runtime及请求模型前拒绝恢复。

空正文回复仅在stop结束、没有原生工具/拒绝、reasoning_content（或服务原始reasoning别名）中恰好一个完整官方 `mswea_bash_command` 块时复制该块到正文。允许块外思考文字但只复制动作块，普通思考本身、多个动作和截断回复不转换，非空正文不覆盖。复制后从推理字段移除动作以免重复，冲突的推理别名拒绝转换。事件保留原始response、实际返回agent_response和适配原因；离线回放优先采用实际返回版本，旧事件仍使用其原始response。此规则只修复已经观察到的协议错位，不向模型添加求解策略。

服务配置身份单独保存为 `service_identity.json`：只读 `/api/show` 的显式 `num_ctx` 必须至少32768且模型family为qwen35；冻结参数原文、聊天模板SHA256及架构context_length；verbose元数据的完整tokenizer.ggml字段（含词表、merges和token types）按规范JSON摘要与tokenizer.lock.json逐次比对，审计只保存摘要。结束时再次检查服务身份，防止窗口、模板或tokenizer改变。架构最大窗口不等于服务实际配置窗口。服务实际prompt_tokens加预留输出若超过32768，该次已发生调用仍完整计量和保留原始回复，但不将动作交给agent；回放不会返回带error的拒绝事件。

## 第三轮改进任务协议

模型文本协议仍为 `mini-text-bash-v3`，tokenizer及服务身份合同不变。独立 `sica-mini-improvement-v1` 任务合同由 `core.improvement_contract()` 冻结版本、runtime/bridge.py和原始agent_main.py摘要；配置缺失或不匹配在Budget、runtime和模型创建前拒绝恢复。只在improve入口修正原任务的身份语句与不可用工具术语：尽早元改进推理改为mini内直接开展推理，保留原始提出、实施、最小改动与日志更新意图。不恢复SICA元推理工具的七步结构、委员会或子agent，也不添加人工解题策略或逐步反思。benchmark任务文件原样传给mini。

原生proposal继续调用原有select_base_agent，确定性预选父值传至执行边界；runner内仍按原函数确定父及最优改进者。固定轨道只将执行改进者改为H0，父可不同；探针共同父为H0，执行改进者分别为H*和H0。current_iter编号保持上游语义，candidate单独编号。宿主在执行前核验改进者编号对应实际归档模块，父minisweagent源码对应候选启动源码（原生changelog仍按上游从最近版本继承）。只读公开归档是启动快照，候选在其中的副本并无已评测收益，只有workdir实时可写。提示展示既有30调用、600秒、命令60秒、输出2048和总32768token合同，无预算扩张。

每次改进记录host_improvement_task.json、improvement_task.json及improvement_prompt.txt；宿主独立渲染提示摘要并对照容器记录和实际文本SHA，记录parent/proposer/candidate与renderer/upstream摘要。agent_launch事件保留同一身份及真实挂载信息；这些执行证据不替代独立性能评分。

## Seven-step workflow transport (2026-10-12)

`sica-mini-improvement-v2` restores the pinned SICA seven-step definition with explicit invocation, current completion tools, sequential advancement and failure exit. `vendor/sica_workflow` contains eight byte-exact upstream Python sources plus the original MIT license, indexed by path and SHA256 in `upstream.lock.json`; bootstrap reproduces these files. The adapter extracts active literal steps, system/role descriptions and active `construct_core_prompt` assignments rather than importing SICA's old provider stack. The original ArchiveAnalyzer supplies the archive explorer's initial overview and overview/best/worst/compare operations. Optional `previous_agent_runs`/`requirements` fields are conveyed within the caller's self-contained task rather than separate transport fields; the original unused coder reasoning_structure_prompt is not rendered.

Text bash actions consisting solely of `sica-workflow '<JSON>'` replace original function tools. `invoke`, `state` and current `complete` belong to the outer orchestrator. Original OpenFile/ViewDirectory/RipGrep map to structured read/list/literal-search; file-view open/close context reconstruction is not ported. Archive analysis uses original formatted analyzer methods. Editing and offline checks use mini bash in coding/general roles. Original role templates and step instructions retain legacy tool names; explicit transport instructions explain these mappings. Original web tools and reasoning-agent delegation are unavailable. Original `python -m workdir.agent` test recipes cannot access a hidden model endpoint: offline checks or explicit role tasks use the declared shared transport. Host-only independent benchmark grading remains separate.

All roles instantiate DefaultAgent and LitellmTextbasedModel from the actually selected archived code, use the same broker token/session and selected model, and share the existing 30 actual calls / 600 seconds / 60-second commands / 2048 output / 32768 total context contract. The improve entrypoint sets official mini's existing `MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT=1` transport setting so broker budget rejection returns immediately rather than retrying rejected requests; benchmark keeps its existing setting. Child local task limits are disabled to avoid granting a new independent allowance; the broker remains authoritative. Original committee specialisations pragmatist, taste_maker, utility_optimizer and testing_manager run serially (concurrency one) and return the original concatenation of four reviews. There is no synthesizer or code-enforced re-review. Serial scheduling and uniform model replace upstream parallel scheduling and heterogeneous models.

The main orchestrator and reviewers/archive explorer expose restricted tool interfaces: normal shell actions are rejected; structured public reads and exact final-output submissions are handled without a shell. Coding/general delegates may edit and run local checks, but cannot invoke/complete the parent's structure. This is **read-only tool capability, not per-role OS/security isolation**: all roles execute selected archived Python in the same candidate-mounted controller container. It does not sandbox hostile evolved Python from candidate writes or the shared broker token. The private grader/host-data boundary remains separately enforced by container mounts and host execution.

Completion success is the model's declaration, as upstream; no source-change, committee-success, testing-success or promotion gate was added. Early final submission remains possible. Workflow state and events distinguish never invoked, active, completed, failed and incomplete; budget/termination never fabricate step completion. Existing interrupted state cannot be silently resumed as a new conversation. Host recovery records incomplete when a controller is killed. Definitions, transport, renderer and upstream task identities freeze at configuration creation and are checked before each launch; container and host identity evidence include all workflow sources. Benchmark prompts and execution do not initialize or expose this workflow.

## 第五轮：角色任务与文本工具合同（2026-10-12）

独立任务协议升级为`sica-mini-improvement-v3`，模型/服务/tokenizer及共享30次/600秒合同不变。仅improve入口将固定上游角色SYSTEM_PROMPT及AGENT_DESCRIPTION放入实际system消息，原活动core与完整委派任务留在user；不传入完整父对话。AGENT_DESCRIPTION在原实现主要是工具元数据，此处放system保留角色说明。原步骤与角色原文仍以固定源摘要核验。

实际选中归档agent/model类、官方文本动作解析器与归档system格式/行为定制仍被使用；只读角色仅移除默认system中建议`&&/||`的精确短语。原归档通用instance_template整体由元任务core模板替代，避免“修复issue/复现/任意shell/编辑示例”成为每个角色的任务。improve的原观察/错误模板也整体覆盖为有效结构read示例及共享状态，故这三处归档模板定制在improve入口有明确覆盖；benchmark全部保留原模板。原始所选模板与各角色派生模板分别保存摘要，宿主和容器独立比较。编辑角色保留原system的复合bash格式建议，未新增自动动作修复或策略。

只读read使用1基含端点行范围（默认1..200，最多500行），仍限制候选与公开归档真实解析路径；返回实际路径、证据性质、行数及截断信息。历史任务对白/`/testbed`路径明确标为既往证据。当前parent/proposer/candidate、可写workdir、公开archive及精简活动步骤身份传给所有角色；不会把父步骤完整指令变成子角色的新任务。

新增受认证、无模型调用的`/status`，初始提示与工具反馈显示同一共享会话剩余额度。模型/工具预算拒绝记录在到达端点/执行工具之前，真实模型计数不增加。原始运行异常另存，只有受控状态的终止原因用于分类；generic APIError不会按字符串判断，下一真实请求清除历史终止原因。状态读取失败保留原异常类别并记录状态不可用。没有角色配额、强制返回、自动步骤推进、自动编辑或新的候选晋升条件。操作系统层面的角色沙箱限制仍与第四轮说明一致。

第五轮提交后补充：固定原源`meta_improvement.py`的TOOL_NAME是`meta_improvement_reasoning_structure`，`committee_design.py`的是`meta_improvement_review_committee`。原描述保留这些名称而适配仅注册invoke/committee，造成真实最后两次调用被错误拒绝。这是已复现的适配兼容缺口，不全部归为模型失败。

现在AST读取并严格核对这两个固定原名，分别注册为invoke/committee的显式别名；说明仅展示当前角色有权限的映射。requested_op原样记入事件，规范化后的操作沿用同一权限检查和原dispatch：编辑角色可用委员会别名、不能调用结构别名；归档与评审两者均不可用。没有`review_committee`等猜测映射，没有shell动作改写，`step:1`不会自动声明完成或跳转，未知/未来完成入口继续拒绝。模板/工作流摘要变化仍由现有v3冻结合同识别。

最终审查补充运行时边界：适配层采用单活动结构，活动中再次invoke拒绝；固定原sequential.run可创建多个invocation状态。七步定义、成功/失败推进与四评审拼接对齐，不代表完整动态注册/多活动结构运行时逐位复现。本轮保留单活动合同，不以重复invoke新建结构掩盖真实未推进。另固定原BaseAgent/MainOrchestrator的MAX_ITERATIONS=500，而本实验全角色共享30实际调用；30并非上游默认资源规模。

2026-10-12用户授权新预算对照：元改进任务合同v4明确limits为全角色共享500实际模型调用、600秒；解题仍30/600，其余参数不变。生产提案/探针从合同建立会话，宿主以真实max_calls渲染元任务与角色说明/摘要并传给容器，本地主agent步数使用同额度；子角色仍没有独立新预算。提示仅替换额度数字，不加入新策略。此为扩大预算诊断，仍不是原SICA每agent500迭代（可能含continuation）的完整运行时/资源复现；旧实验不恢复或改写，原24小时截止继续生效。

## 2026-10-12 v5预算作用域更新

此前30/500共享调用、2048输出、32768 context、60秒命令及600秒improve是历史诊断合同。当前改为每角色500/coder1000独立迭代、每迭代最多5 provider请求、总结1请求、benchmark600秒/$3、improve无独立时间/费用阈值；模型输出和context取认证元数据，DeepSeek通过beta prefix续写。原文件视图重建未移植；公开读取和工具输出取消人为截断，安全隔离仍保留。费用为官方峰值保守上界而非账单，未知价格拒绝benchmark。完整来源、差异、验收和单次测试状态见[sica-budget-alignment-review.md](sica-budget-alignment-review.md)。

Qwen切回后的Ollama适配：认证原生version/tags/show，冻结模型digest、参数和模板摘要，context取明确num_ctx且不得超过架构容量；模型未设置独立num_predict时用context-1推导单次上下文内输出边界，明确不冒称服务声明的独立输出cap。对已核验的非cloud原生Ollama允许用户授权的本地测试，$3费用守卫不可复现、实际费用未知；metrics记录cost_accounting_available=false，逐题cost_estimate为null，不记为免费。DeepSeek和其他未定价远端的原守卫不变。只增加传输/身份兼容，不修改SICA控制器、原mini源码、模型配置或解题策略。

## 2026-10-14 独立在线模式与完成接口

新增 `mini-swe-rsi-online-v2`，与 native SICA 归档选择/循环分开。在线活动版本同时做当前任务与本轮改进；原步骤和角色原文不变，第二步后的显式 improve/skip、第三步冻结检查计划、第五步实际候选检查和宿主接任均属于在线扩展，不称为原版 SICA 已有机制。公开归档按时间呈现，不对不同题目覆盖的版本直接平均排名。完整协议见 [online-evolution-plan.md](online-evolution-plan.md)，验收记录见 [online-evolution-review.md](online-evolution-review.md)。

原 SICA 的叶角色自动获得 ReturnResult，主协调者另有 SubmitAnswer。此前将结束工具映射为裸 printf，而适配提示开头又要求整条命令使用 sica-workflow JSON，真实模型因此错误包装提交命令。现在增加受控 `sica-workflow {"op":"submit","report":"完整非空文本"}` 结束适配，宿主添加 mini 完成信号。raw JSON 直接解析，不需要第二层 shell 引号；保留旧 quoted JSON 和裸 printf 兼容，尾随命令仍拒绝。这个入口只结束当前角色，不改变原 AST 派生的角色工具权限，也不推进、补全或绕过父七步。在线诊断另要求每步成功且非空的 archive_explorer 报告，并由宿主绑定角色与步骤身份；native 无此新增门槛。提前结束仍会留下 incomplete/diagnosis_failed；候选接任仍须完整在线检查证据。固定 vendor 和锁文件保持不变。

## 2026-10-17 在线本地检查资源保护

用户要求修复真实第 5 步测试桩无限循环及超大观察问题后继续单题实验。本地循环测试增加明确退出和有限步数的提示约束；在线改进 shell 输出分块落盘，300,000 字节以上返回显式索引和预览，8 MiB 以上销毁工具沙箱、返回失败和标明不完整的前缀摘要。这是新增的工具资源保护，覆盖此前“工具输出无上限直接内联”的在线行为，不称为上游 SICA 原生事件总线或文件视图重建。benchmark 输出及评分、七步与角色定义保持原样，不新增全局/阶段期限。完整边界与回归证据见 [step5-loop-fix-validation.md](step5-loop-fix-validation.md)。

## 2026-10-17 批次 C：固定在线模式

移除 Workflow 的模式参数与分支及 bridge 的 `--online` 开关，原在线诊断、写权限、候选检查和恢复授权无条件执行。保留审计元数据的 `online=true` 事实字段。容器入口在模型初始化前验证完整在线身份，benchmark 仍供首次作答和独立复测使用。

删除旧 Budget、阶段/24 小时时钟和开发题前缀机制；Broker、Driver 仅执行各自真实任务/请求/工具期限，容器字段改为 `session_remaining_seconds`，无期限为 null/None。唯一任务冻结来自 `task_limit` 与 `online_task_selection.json`。带旧预算或前缀选择产物的运行明确拒绝，不自动迁移。协议版本保持不变，控制源码摘要继续阻止跨实现恢复。保留原生限额、同模型、独立评分、真实用量与未知消耗。验收见 [online-rsi-phase-c-review.md](online-rsi-phase-c-review.md)。

## 2026-10-18 批次 D：重复实现与冗余清理

移除未消费的 behavioral 摘要和 Broker 内存事件列表，事件审计继续完整落盘；正常与错误回复共享有效用量和费用累计，迟到回复保持独立规则。任务结果排序、正规对象读取及摘要匹配统一，但反馈、评分、候选启动、私有证据和接任门槛仍在各边界执行。

报告与公开归档共享递归过滤，不扩大各自白名单；新增 helper 纳入控制摘要。宿主和容器分别复用本次加载的 workflow，保留独立身份核验及活动归档配置加载。测试接口按生产证据合同统一，删除类身份绕过。固定上游原文、锁、角色权限、七步和原生限额保持，不引入批次 E 的兼容性清理。实施与验收见 [online-rsi-phase-d-review.md](online-rsi-phase-d-review.md)。

## 2026-10-18 批次 E：可选兼容路径收敛

ContextBudget 只接收显式 provider 能力，删除历史隐式容量分支；认证服务容量、实际 usage、Qwen tokenizer 核验与内容审计继续执行原有 provider 语义。移除未启用的共同任务验收路径，候选仍须当前题不退化并满足声明的修复／机制标准。在线合同改为 `mini-swe-rsi-online-v3`、检查政策 revision 2，拒绝旧 v2 及共同任务字段，不迁移或重标历史产物；七步 improvement 协议仍为 v6。

删除无效 `--resume` 与 bridge `--timeout/--cost-threshold`，禁用参数缩写；持久化边界恢复、明确工作流授权以及真实期限和费用合同保留。完整数据／镜像校验、身份字段、上游原文和锁文件保持。取舍与验收见 [online-rsi-phase-e-review.md](online-rsi-phase-e-review.md)。
