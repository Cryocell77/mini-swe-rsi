# SICA预算作用域对齐验收（2026-10-12）

本轮实施用户要求的预算对齐与新模型配置兼容。旧500共享调用诊断不是原SICA预算复现；当前合同为`mini-text-bash-v4`/`sica-mini-improvement-v5`，旧运行不能续接。真实模型测试尚未启动：原实验24小时截止已过，未擅自重置。

## 固定上游核对

SICA固定`ed8275dca4d3c5dbf77229964351fe9b424797dc`；mini固定`04d809ceab9df28f9adaed044884180159172930`。重新下载并校验两个固定提交归档SHA，核对SICA保留14文件与mini74文件全部一致，无vendor源码或选择/归档/统计/循环函数修改。另将7份预算来源SHA写入`upstream.lock.json.sica.budget_files`，bootstrap和回归测试校验这些来源。

`.upstream/sica`是解压目录，不能把对其执行git得到的外层仓库HEAD当作SICA固定提交。本轮用归档摘要和文件内容核验。

## 预算对应关系

| 项目 | 原SICA来源与作用域 | 当前实现 |
|---|---|---|
| 角色迭代 | `base_agent/src/agents/base_agent.py`500，main500，coder1000 | 每次角色调用独立计数；宿主和mini同时约束，取消生产共享30/500请求上限 |
| 角色续写 | BaseAgent.execute显式`max_continuations=5`；provider循环最多5次总请求 | 同一mini迭代最多5次实际请求，所有usage汇总；DeepSeek beta assistant prefix续写 |
| 总结请求 | `llm/api.py`默认None，runner未传续写参数 | 默认1请求，不套用provider内部4次默认值 |
| 输出 | provider未显式指定时使用`model.max_output_tokens` | 取当前服务认证元数据；DeepSeek为393216，不复用旧2048或旧DeepSeek V3的8192 |
| 上下文 | 原provider/model支持范围；300000字符是文件视图重建触发器 | 取服务context_window；DeepSeek为1048576，由服务执行自身tokenizer上限、宿主审核实际usage；不伪造本地token估计 |
| benchmark时间 | runner任务600秒，外层收尾余量10秒 | 任务宿主期限与mini均600秒，全局/阶段可更早截止 |
| improve时间 | agent.exec的timeout/cost_threshold均None，runner无600秒包裹 | 取消improve整体600秒，保留用户全局24小时/阶段期限 |
| 工具时间 | BaseAgent工具安全600秒；命令duration必填5..1800秒 | 有效min(声明时间、600、剩余墙钟)；mini没有该必填字段时默认600，明确属于命令接口适配 |
| 工具/观察/读取输出 | ExecuteCommand完整stdout/stderr，无本轮旧截断 | 取消1MiB/10000/30000/500行/搜索与列表截断；保留合法行范围和安全路径 |
| benchmark费用 | runner传$3，improve不传费用阈值 | $3守卫按官方峰值价格保守上界计量，费用单列；不当作实际账单 |

新模型的能力来自实际认证`/models`；[官方能力与价格文档](https://api-docs.deepseek.com/quick_start/pricing/)、[prefix协议](https://api-docs.deepseek.com/guides/chat_prefix_completion/)用于复核。`.env`支持`LLM_MODEL_NAME`和原`LOCAL_LLM_MODEL`，冲突时拒绝。所有角色与总结统一同模型；保留temperature请求0，思考/effort使用服务默认，未声称该采样参数与原SICA的0.666一致。官方服务在思考模式下忽略temperature，参见[思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)。服务未公布权重摘要，记录`weights_verifiable=false`，不能用模型别名证明权重字节恒定。

费用按冻结峰值每百万token：缓存输入$0.006、普通输入$0.3、输出$1.2。该上界可能比非高峰实际费用更早触发$3，属于明确披露的保守计费差异。无冻结价格的服务拒绝benchmark推理，不把未知费用写成免费。模型调用超时而无usage记为未计量；晚到usage单独审计，不返回动作。provider持续发送保活数据也不能延长宿主总请求期限。

## 保留的实现差异

预算对齐不等于原Agent运行时逐字移植。mini没有SICA事件总线和文件视图重建，300000字符仅记录原触发条件且`fileview_regeneration_implemented=false`，不伪称已实现。保留独立评分、只读公开归档、容器资源/源体积和路径隔离；评分器既有300秒测试限制、离线回放安全时限不是SICA Agent预算，沿用已验证资产。

最多5请求仍截断、provider不支持prefix时显式失败，不执行未完成动作；原SICA可能返回部分文本，此处采用更严格的动作边界。总结是文本，单请求截断可保留部分摘要。只删除mini原观察模板的已知输出预算分支，保留候选自行修改的其余观察模板，避免宿主覆盖自改进。

原七步仍由模型声明阶段完成，当前文本报告提交接口仍需要合法引用；本轮没有用人工策略强制编辑、强制推进或自动反复采样。因此工程验收不能证明新模型必然修改源码。

## 验收与单次测试

最终冻结版本的验收结果在本机`runs/sica-budget-alignment-20261012/accepted-engineering-tests.log`和JUnit文件；最终105项全部通过（101.56秒，含真实Docker模拟端点），失败0；冻结源码、14/74上游文件和旧运行清单复核无变化。覆盖角色独立500/1000、provider最多5请求、总结1请求、393216输出传播、usage/缓存/成本、真实Docker七步及归档继承、超时/恢复拒绝、评分隔离和观察模板保真。Docker端点为模拟服务，没有真实模型收费。此前初批90项离线通过、随后98项含Docker通过；之后新增边界回归并再次冻结验收，不用旧通过数冒充最终结果。

`smoke_once.py`已准备：只调用一次生产提案；若修改运行源码才评预定dev题`django__django-14752`，不重跑baseline/val/test，失败不追加提案。旧Qwen的H0公开dev30仅供分析，不能标作新模型基线。

原起点`1791726529.765753`，截止为2026-10-12 21:48香港时间。过期入口在创建新运行、模型身份请求或容器前拒绝；测试验证原起点不变。若用户另行授权单次30分钟诊断，可显式使用`--new-time-budget-seconds 1800`，新目录记录独立时间合同并保留原起点来源；不会改写旧实验或声称24小时内完成。当前不对新模型解题、自改进或RSI收益作结论。
