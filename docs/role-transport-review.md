# 第五轮：角色任务与工具契约诊断、修复审查

2026-10-12。用户授权诊断、规划、GPT-6.1 Sol / low子agent修复、主agent独立对照原SICA审查验收、本地commit并测试。本轮使用research[F]-iteration流程，先更新implementation.md再实施；不重测基线、val/test，不更换模型或扩大资源合同。

## 诊断依据及问题分类

第四轮真实运行30调用、87.65秒，协调者5、归档角色25；工作流已调用、第一步未完成、源码未变。15条动作接口错误，归档角色两次尝试修复历史`/testbed/sklearn/pipeline.py`；最大实际prompt13295，远低于32768预算，没有真实服务端点错误。

| 问题 | 证据与判断 | 本轮修改方向 |
|---|---|---|
| 角色任务冲突 | bridge加载mini默认instance后直接实例化所有角色。实际wire的user消息附带“Please solve this issue”、复现、编辑步骤及任意bash示例；与归档分析/协调者职责冲突，是代码适配问题 | improve专用角色模板；原SICA SYSTEM_PROMPT位于system，core作为user任务 |
| 工具说明与能力冲突 | 默认observation建议head/sed/重定向，但结构接口只允许单一JSON参数。真实轨迹反复拼接管道 | 角色能力一致的观察/错误说明；结构read提供行范围；继续拒绝复合命令 |
| 子角色元任务身份不足 | 子任务只有模型转述，缺少宿主parent/proposer/candidate和当前阶段；历史trace含任务命令，模型将其当作当前修复对象 | 显式任务身份和证据来源，保留原委派全文与公开失败证据 |
| 预算失败记录失真 | 子角色APIError实际包裹LimitsExceeded，metrics没有记录请求前的预算拒绝 | 会话状态与受控拒绝记录，保留原异常并准确分类，禁止泛化APIError |
| 第一阶段不收敛 | 30调用全部用完，未调用编码角色 | 此为真实行为结果，模板冲突修复后再测；不凭一次失败断言模型固有上限或必须换模型 |

原BaseAgent.construct_system_prompt以self.SYSTEM_PROMPT构建system，运行时分别发送system/core；原ArchiveExplorer工具集合没有ExecuteCommand。默认继承LOGDIR/WORKDIR/OPEN_FILES，不要求全部调用者对话。因此本轮不开放归档角色任意shell，也不以恢复为名注入完整父对话。

独立只读诊断见runs/role-transport-repair-20261012/audit_role_transport.md及JSON。修改前封存上一轮4034文件清单，原实验5668文件继续核验。工程审查、测试及提交后真实结果将在完成后追加。

## 实施、独立审查与上游对照

子agent实施improve专用模板、原system/core分层、行范围/真实路径来源、子任务身份、共享会话状态与受控失败分类。宿主从实际proposer归档配置独立计算五种角色模板摘要并对照容器；v3合同另外冻结relay。保留原归档agent/model类、文本解析器和system定制；不扩大预算，不自动修复动作或完成阶段。

主agent审查补齐了：Docker中继的/status白名单与摘要、子角色cost_limit=0、状态查询失败保留原异常、首次请求前预算、精简阶段身份、短操作错误的有效示例、工具过期的类型记录、旧上下文拒绝不能污染后续服务错误。测试期发生过源码变动导致的身份不一致，已保留失败日志及逐字段diff，随后冻结代码复测；未把该失败改称模型问题。

固定SICA仍为ed8275dca4d3c5dbf77229964351fe9b424797dc。主agent重新远端核验14份vendor文件逐字相同，14原runner函数AST/code object一致，mini固定提交的74文件不变。独立状态检查确认七步字段、当前完成限制、成功/失败状态及四份原评审拼接不变。另独立从原源文件解析五种SYSTEM_PROMPT及15段活动core文本，对照最终模板；原文保留，放置层级已修正。不是完整SICA运行时逐位复现，所有必要映射见upstream_adaptations.md。

明确的继承边界：improve覆盖归档的instance/observation/format_error三处模板，因此候选在这些模板上的改动只影响benchmark；归档system定制与agent/model源代码继续参与后续改进执行，Docker递归/固定对照对此有见证。文件视图上下文重建仍未移植；角色只读仍是工具接口能力，不是独立OS沙箱。任务身份和证据标签用于澄清执行对象，不能保证模型一定遵循；没有新增强制修改或晋升条件。

## 工程验收

子agent初轮离线33 passed/2 skipped；一个移动源码批次29 passed/1 failed，固定后受影响Docker批次39 passed/142.91秒。最后两处反馈与记录修改后，精确受影响批次24 passed/64.23秒。各次产物和源码范围单列，未混称同一树。

主agent在最终冻结树执行完整Docker套件 **86 passed/341.20秒**，仅已有LiteLLM弃用提示一项，见parent-final.xml及parent-final.txt；验证后12个冻结文件摘要一致。独立检查实际wire：21请求成功fixture包含五种角色、全部七步；6请求fixture如实incomplete并记录LimitsExceeded；首次和每次反馈的剩余调用数与真实全局计数精确一致，system原文正确、user没有通用issue编辑流程。结果在parent_wire_check.json；初次审计重复计入pytest的current符号链接，排除别名后两份独立fixture核验通过，生产代码没有因此修改。

原实验5668文件、上一轮4034文件均与清单一致，diff检查通过。未发现阻断验收问题，工程修复接受；模拟端点结果不证明真实模型会编辑或RSI收益。代码提交后仅运行一次新目录真实提案，结果另行追加。

## 提交后的真实结果与原工具名兼容修正

代码6b8e215的首个真实提案耗时78.39秒、30次同一Qwen调用、401794总token（372665缓存），没有上下文超限或端点错误。实际角色system、改进任务user及共享预算反馈均正确。1次输出格式错误，27次read/list；最后两次使用原工具名`meta_improvement_reasoning_structure`被适配层拒绝。七步未启动，没有委派或源码修改，按本地30步上限准确记录LimitsExceeded。此结果不能归为纯模型问题：原工具描述保留名称，但注册入口只接受invoke，是可复现的代码兼容缺口。首个运行838文件封存，未运行单题、基线、val/test。

先追加修复方案，再由同一GPT-6.1 Sol / low子agent添加两个原TOOL_NAME显式别名；原结构名映射invoke，原委员会名`meta_improvement_review_committee`映射committee。原请求名另存requested_op；不猜测其他名称、不自动推进、不扩大权限。子agent相关18项测试通过，主agent独立复测工作流13项全部通过（17.19秒）。原源AST/摘要核对两个名字，原七步、四评审与五种system/15段core再次通过；Docker实际wire经原名启动，21调用完成七步，6调用版本仍准确失败。最新别名修正由上述相关测试覆盖，86项完整套件对应前一个提交，不混称同一代码版本。

主agent再次核验远端14文件、14控制函数、mini74文件及原实验5668、第四轮4034、首个第五轮838文件未变。工程修复验收后单独提交，再运行一次新目录真实提案；这是修复已复现接口缺口后的测试，不是同代码重复采样。结果另行追加。
