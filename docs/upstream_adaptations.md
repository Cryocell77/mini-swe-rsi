# 上游适配与证据边界

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
