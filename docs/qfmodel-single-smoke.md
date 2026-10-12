# qfmodel代理单次自改进检查（2026-10-13）

用户再次更换.env并要求单题测试。沿用已确认范围：H0一次新提案，有运行源码变化才测django__django-14752；不重测baseline/val/test，不追加提案。明示1800秒单独诊断保护，非原SICA上限，不重置旧24小时实验。

## 服务兼容与验收

新配置为本地CLIProxyAPI8.0.20，标准认证/models列出qfmodel、owned_by=qoder，没有容量；原生Ollama/llama/LMStudio接口均404，0推理。按[官方客户端](https://github.com/router-for-me/pi-cliproxyapi-provider)所用的/models?client_version=pi发现同名slug，目录声明context_window/max_context_window=180000、max_tokens=8192。本机配置只读，未改代理路由、账户、.env或参数。

broker仅在loopback/Qoder且标准容量缺失时读取该认证目录，要求唯一同名条目、正整数、标准字段无冲突、最大上下文有效；冻结白名单容量，不使用目录中的通用Codex指令，模型仍按mini/SICA当前提示执行。同一个qfmodel别名用于所有角色，temperature请求0，其余服务默认。代理未公布真实后端和权重摘要；容量是代理声明，不是独立证明底层模型配置或固定权重。没有已验证tokenizer，只审核服务usage，不承诺Qwen的输入计数或精确保留。

价格未公布，保持未知；改进可运行，若候选产生运行源码变化，单题尝试仍须通过现有benchmark价格合同检查。不得绕过守卫或把被阻止评测写成解题失败/通过。补记HTTP状态数字，错误正文不保留，避免上轮只记录HTTPError无法区分状态的诊断缺口。

主agent对照本轮diff和原SICA预算/provider边界：角色、七步、模型策略、父版本/改进者选择、输出预算作用域均未更改；8192来自新代理声明，不是恢复旧2048人工上限。SICA runtime未移植部分沿用已披露范围，不宣称原样完整移植。95项固定SICA/mini源码摘要及旧Qwen843/DeepSeek920/修复Qwen841/GLM850封存文件全部不变。

定向38项验收通过（20.34秒），连续两次真实元数据核验一致，工程阶段0真实推理。完整验收与真实结果追加于下文；产物在runs/model-recheck-20261013-021701。

完整含Docker模拟端点验收135 passed/231.40秒，失败0（1条依赖弃用警告）。主agent接受代理声明映射和安全HTTP状态日志；src/runtime/tests冻结一致。工程阶段0真实推理，提交后仅一次提案。
