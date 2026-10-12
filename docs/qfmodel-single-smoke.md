# qfmodel代理单次自改进检查（2026-10-13）

用户再次更换.env并要求单题测试。沿用已确认范围：H0一次新提案，有运行源码变化才测django__django-14752；不重测baseline/val/test，不追加提案。明示1800秒单独诊断保护，非原SICA上限，不重置旧24小时实验。

## 服务兼容与验收

新配置为本地CLIProxyAPI8.0.20，标准认证/models列出qfmodel、owned_by=qoder，没有容量；原生Ollama/llama/LMStudio接口均404，0推理。按[官方客户端](https://github.com/router-for-me/pi-cliproxyapi-provider)所用的/models?client_version=pi发现同名slug，目录声明context_window/max_context_window=180000、max_tokens=8192。本机配置只读，未改代理路由、账户、.env或参数。

broker仅在loopback/Qoder且标准容量缺失时读取该认证目录，要求唯一同名条目、正整数、标准字段无冲突、最大上下文有效；冻结白名单容量，不使用目录中的通用Codex指令，模型仍按mini/SICA当前提示执行。同一个qfmodel别名用于所有角色，temperature请求0，其余服务默认。代理未公布真实后端和权重摘要；容量是代理声明，不是独立证明底层模型配置或固定权重。没有已验证tokenizer，只审核服务usage，不承诺Qwen的输入计数或精确保留。

价格未公布，保持未知；改进可运行，若候选产生运行源码变化，单题尝试仍须通过现有benchmark价格合同检查。不得绕过守卫或把被阻止评测写成解题失败/通过。补记HTTP状态数字，错误正文不保留，避免上轮只记录HTTPError无法区分状态的诊断缺口。

主agent对照本轮diff和原SICA预算/provider边界：角色、七步、模型策略、父版本/改进者选择、输出预算作用域均未更改；8192来自新代理声明，不是恢复旧2048人工上限。SICA runtime未移植部分沿用已披露范围，不宣称原样完整移植。95项固定SICA/mini源码摘要及旧Qwen843/DeepSeek920/修复Qwen841/GLM850封存文件全部不变。

定向38项验收通过（20.34秒），连续两次真实元数据核验一致，工程阶段0真实推理。完整验收与真实结果追加于下文；产物在runs/model-recheck-20261013-021701。

完整含Docker模拟端点验收135 passed/231.40秒，失败0（1条依赖弃用警告）。主agent接受代理声明映射和安全HTTP状态日志；src/runtime/tests冻结一致。工程阶段0真实推理，提交后仅一次提案。

## 原提案实际结果（保留原1800秒记录）

代码6e14568执行一次H0提案，144实际请求、8,363,934已计量tokens（prompt8,279,192/completion84,742/cached7,702,072），1次TimeExceeded请求无usage。已计量143响应均stop；另第54请求归档角色prompt181651+completion50超过代理声明180000，由宿主在已计量后拒绝返回动作，记ContextLimitExceeded，不能写成服务主动报告输入溢出。最大completion9321也高于请求的8192，代理声明/usage语义与实际请求约束并非独立核实，必须保留这一限制。

实际启动归档2次、评审4次、软件开发1次，主43轮/归档47+14轮/评审1+11+4+11轮/开发13轮；完成5/7步。到1800秒宿主结束进程137、TimeExceeded，单次runner继而尝试dev时BudgetExceeded（1800.68秒），dev未启动、0dev请求。不是原500/1000轮额度耗尽。原提案不能标记完整完成。

候选真实变化仅minisweagent/config/mini_textbased.yaml，原始hash1b53c0fe10dcdaf5f7320b599ebbad58d8bd457df3a39d8913e8f2f455742235，行为hashde6d58dbd2c3dd66325125bcbaecbdfc79d723ec4dd8231f60d15aad7d6734b1。模型加入定位后小窗口查看、锚定编辑等提示，将观察输出分支改为4000字符的head/tail；不是Python核心实现修改。主agent离线验证布局、YAML/Jinja和bridge后的新分支确实可用，未改候选。源码变化证明提出了可运行的Harness配置改动，不证明更强或递归改进能力。

## 用户后续确认与取消额外总时限

用户要求继续，并确认qfmodel为订阅／免费额度，没有按token单价。显式.env订阅合同仅用于已核验本地Qoder代理，费用仍未知，不将原metrics中0.0视为实际零美元成本。随后用户指出不应有预算上限：取消额外1800/1200秒诊断总时限，保留原SICA的原生限制。新独立诊断使用unbounded_diagnostic；历史原24小时和已结束1800秒记录不改写，不重新生成提案。

原SICA依据：runner.py第367行TIMEOUT_SECONDS=10*60并于474行传给benchmark；base_agent.py第68行MAX_ITERATIONS=500、coder.py第104行1000；improve入口agent.exec(self_improvement_prompt)未传timeout。自改进没有原生30分钟总时限。原CLI benchmark默认300但runner覆盖600，本项目按runner保持600。工具调用另有600秒安全超时。取消外加总时限不等于原SICA完全无任何限制。

订阅版独立定向41项、完整138项通过（308.17秒）；随后新增无总时限实现，定向36项通过（15.43秒）。新完整验收与已有候选单题结果完成后追加。单题使用相同模型和源码，独立目录记录，服务身份只允许多出用户声明计费合同，不重跑baseline/val/test/提案，不修改候选或手工补齐7步。

最终无额外总时限及订阅合同验收：140 passed/362.99秒，失败0（1条依赖弃用警告）。真实Docker模拟端点完成无总deadline七步及子角色执行，同时有限deadline/500/1000迭代测试仍通过；源冻结一致。主agent审查接受，提交后仅补测已有冻结候选，不重启提案。


2026-10-13 已有冻结候选单题最终结果：`django__django-14752` 独立 SWE-bench 评分 resolved=true、score=1.0；运行 exit_status=TimeExceeded、601.14秒，未正常提交，评分使用宿主提取的工作树补丁。共36次模型请求（1次未计量超时），计量325,042 tokens，其中缓存293,134；美元成本未知，订阅不可按token折算。任务补丁修改 autocomplete.py 并增加测试。不可将通过评分写成正常完成。

候选仍为1b53c0fe10dc，实际加载归档代码及新的 Locate/anchored 提示词；前后模型身份一致。没有新提案、baseline、val或test。原提案只修改 mini_textbased.yaml，且此前被额外30分钟限制造成七步仅完成5步；本次候选评测通过不补齐该缺陷、不构成晋升或递归能力证据。新的独立诊断不设总/阶段时间上限，单题仍按原SICA为600秒。

验收复核：src/runtime/scripts/tests冻结一致；95个固定上游文件与3,454个历史封存文件未变；测试容器已清理。审计与封存位于 runs/model-recheck-20261013-021701/candidate-single/independent_audit.json、sealed_inventory.json，原提案也已封存。本次工程验收140项通过；无需再次完整测试。结论：已有候选能生成通过该题的补丁，但完成协议/耗时仍未通过，Harness收益与RSI收益均未建立。
