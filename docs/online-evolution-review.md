# 在线自进化实现与验收记录

状态：工程验收通过；三题真实检查因格式失败和代理 HTTP 500 未完成，没有真实自改进或递归收益证据。

## 授权与范围

用户要求按 [完整修改方案](online-evolution-plan.md) 调用 GPT-5.6 Luna / Extra High 子 agent 实现，由主 agent 审查、测试和验收，确认后提交本地 commit。本轮顺序以用户最新要求为准，真实检查在最终实现 commit 前进行；不自动推送。

初始实现采用三个指定模型子 agent，真实检查暴露完成接口歧义后追加一名同模型、同推理级别的修复子 agent。主 agent 负责独立代码审查、必要修正、测试与最后验收。

最新核心要求：每道任务结束后用当前版本执行原七步的前两步诊断；无可执行改进机会正常跳过后五步；有机会才生成并检查候选，接任后的版本做下一题和后续改进。

不恢复已停止的旧实验，不重跑完整基线，不启动固定轨道、探针、val/test 或代码精简。三题真实开发检查使用独立运行目录，所有调用统一当前 .env。

## 初始工程状态

- 分支：codex/fix-sica-adapter。
- 初始工作树仅有未提交的在线方案文档。
- 修改前非 Docker 测试：141 passed / 9 skipped，45.82 秒；一项既有 LiteLLM DeprecationWarning。
- 现有 runtime Docker 镜像可用，架构 arm64；任务镜像仍按冻结 x86_64 身份运行。

## 当前模型代理兼容

只读预检发现 .env 模型为 step-5-preview，本地服务 127.0.0.1:8317/v1。

认证 /models 同名条目的 owned_by 为 workbuddy；认证 /models?client_version=pi 同名条目声明 context_window=1000000、max_context_window=1000000、max_tokens=64000。这些是代理声明，不证明底层权重身份。

现有框架仅识别 qoder 代理目录，错误回落 Ollama 产生身份预检失败。本轮需要最小扩展 WorkBuddy 映射，保留同名、正整数、冲突与容量一致性校验。

用户明确确认当前服务使用“订阅／免费额度，无按 token 单价”。此事实应作为独立运行的冻结计费合同记录，费用未知，不虚构零美元费率，不修改 .env。

## 主 agent 验收要求

1. vendor 与固定锁摘要不变；native 顺序、选择和完整覆盖保护没有意外改变。
2. 两步后合法 skip 保存 2/7，finish 不覆盖，不调用编码、委员会、候选检查，不递增版本。
3. 诊断失败、提前提交、缺失反馈与合法 skip 区分；阶段权限无法越过决策。
4. 同一活动版本承担任务、改进与候选父版本；演化后的版本实际执行后续任务与改进。
5. 候选检查使用新进程加载真正候选；回调通路没有死锁，模型计量完整且并发为 1。
6. 检查前后运行摘要一致，纯文档变化不算有效改进，检查后的运行修改使检查失效。
7. 接任事务恢复不重复任务、检查或接任；评分中断只恢复评分。
8. 只公开已发生任务证据；未来任务、私有评分、凭据不进入诊断归档。
9. 代理身份与订阅合同经过针对性回归。
10. 真实模型没有产生修改或候选未通过时，如实报告，不追加样本制造成功。

## 实现与主 agent 审查

新增独立 OnlineLoop、online CLI、公开反馈归档和专用报告。在线父版本/任务执行者/改进者绑定同一 H_v；七步第二步后新增合法 skip，第三步冻结模型声明的验收计划，第五步经认证 relay/broker 启动候选新进程及独立评分。机制检查将同一冻结 Python 测试分别用于父/候选的无网络容器，要求本题成绩不退化。版本接任与任务游标由不可变决策恢复。

主 agent 在子 agent 实现基础上独立审查并修正：

- native metadata 的 online=False 差异会破坏严格模板身份比对，改为仅在线模式添加字段。
- 候选检查不能放在七步结束后，改为进入改进前登记、第五步回调执行，之后只检查和接任已测试快照。
- 运行摘要曾有两种算法，统一 core.execution_hash；公开证据从实际完成的 attempt 提取，私有宿主结果不挂载给角色。
- 删除验收标准的事后兼容补填；第三步必须明确声明 criterion 和必要机制计划，后续不能替换。
- 在线归档分析使用按时间排列的证据，拒绝跨不同题集平均分排名。
- 机制通过不能掩盖本题成绩退化；机制测试超时属于检查故障，不可冒充父机制失败。
- candidate job 的结果检查/执行登记移入同一锁，避免快速回调下并发重复执行；绑定 session、角色与完整身份。
- 正常跳过、拒绝与恢复分别记录；拒绝消耗独立候选编号，已有执行证据可仅恢复评分。
- 修正在线 CLI 的 finally 分支，确保生成在线报告；成本报告从所有实际 model_call 事件汇总，包含候选和失败开销。
- 诊断步骤只接受本步骤成功且非空的 archive_explorer 提交，宿主绑定对应角色身份；失败角色、评审角色、错误步骤和匿名旧事件均不能作为两步完成证据。同一步允许再次委派补查。
- 结构化操作直接解析 raw JSON，完整报告无须第二层 shell 引号；保留旧 quoted JSON 和 printf 兼容，但拒绝尾随 shell 内容，不自动修补错误 JSON 或执行命令。
- 已有模型工作流状态时，在任何删除候选、复制源码或创建会话之前拒绝重开；中断恢复不能清理旧候选现场，已有不可变接任决策的恢复仍优先。

固定 vendor/sica 的 5 份源码和 vendor/sica_workflow 的 9 份文件全部与上游锁摘要匹配。原 SICA 七步指令、角色和委员会原文不变；逐题调度、两步后跳过、候选检查及接任属于明确在线扩展。

## 工程验收

- 主 agent 独立运行完整 Docker + 非 Docker 套件：174 passed，168.86 秒，无跳过；一项既有 LiteLLM DeprecationWarning。
- 最后修正 CLI 报告分支后，追加该分支定向回归：1 passed。
- 独立真实 Docker 全链：2 passed，42.86 秒。模拟模型与模拟官方评分，实际使用 OnlineLoop、Driver、relay/broker、归档 agent、新任务容器及候选/机制进程，覆盖 H0→H1→H2、后续改进者加载、第三轮两步正常跳过、真实请求计量与并发1。
- 新增主 agent 审查测试覆盖缺失/非法验收计划、私有证据引用、第五步替换机制计划、跨题排名拒绝以及 CLI 报告选择。
- git diff --check 通过。

完成接口修复后，主 agent 再次独立运行完整 Docker + 非 Docker 套件：**197 passed，171.48 秒，无跳过**，仅一项既有 LiteLLM DeprecationWarning。该轮包含结构化报告提交的完整文本保留、mini 实际完成信号消费、提前结束不得推进父七步，以及 completed/skip 状态下非零、缺失、布尔进程返回值不得推进在线任务的新回归。

诊断尚未产生 improve 决策就结束时，明确记 diagnosis_failed，不登记候选、不消耗 P 编号；已有 improve 决策但后五步未完成仍计候选拒绝。进程异常始终保持 interrupted，不能被工作流 JSON 的 completed/skip 声明覆盖。

测试产物：runs/online-engineering-20261014-final/results.xml 与 source_manifest.json。CLI 定向修正发生在完整套件启动之后，其验证单独列出，不将其冒称为当时已经收集到的完整套件测试。

修复后的最终全套产物：runs/online-engineering-20261014-repair/results.xml 与 source_manifest.json。

进一步修复 raw JSON 和诊断角色证据后，完整 Docker + 非 Docker 回归：211 passed，174.37 秒，无跳过，仅一项既有 LiteLLM DeprecationWarning。产物：runs/online-engineering-20261014-acceptance/results.xml 与 source_manifest.json。真实 Docker 在线链路使用 raw JSON 完成提交，且两步各有对应成功的 archive_explorer 证据，覆盖 H0→H1→H2 与第三题正常跳过。

现场保留边界修复后的最终验收：**213 passed，174.35 秒，无跳过**，仅同一既有警告。产物：runs/online-engineering-20261014-release/results.xml 与 source_manifest.json。最终 src/runtime/vendor/tests 摘要逐项与该测试快照一致，14 份固定 SICA 源码/许可再核验通过。工程验收通过后提交本地实现；运行目录中的 implementation_acceptance.json 关联最终 commit、工程测试和两个真实运行源码快照，明确最后工程修复未再作真实模型重验。

## 三题真实在线检查

运行目录：runs/online-step5-three-20261014。

命令：mini-swe-rsi online --run runs/online-step5-three-20261014 --task-limit 3 --billing-mode subscription。

冻结开发集顺序前三题：astropy__astropy-14995、django__django-13033、mwaskom__seaborn-3010。不重跑整批基线，不调用 val/test。配置认证为 step-5-preview / workbuddy，代理声明 context=1000000、output=64000，订阅合同已冻结，权重无法独立核实。

首次推理前保存 startup_provenance.json 与 private/startup_source，包含实际未提交实现的摘要与安全源码快照，不含 .env。最终实现 commit 在测试完成后创建；不把测试前基准 commit 冒充实际实现 commit。

首次运行在第一题后的诊断步骤 2 出现完成接口卡点，已封存并由主 agent 中断（进程退出 130），不是预算截止。第一题 astropy__astropy-14995 的官方评分为 0，任务推理 144458 tokens、182.048 秒；失败状态 RepeatedFormatError，补丁为空。步骤 1 正式提交了诊断报告，步骤 2 已形成报告但把 printf 提交命令包装为 sica-workflow 的参数，JSON 解析拒绝后继续反复读取文件。随后尝试的结构化 submit 操作也没有注册。

根因涉及适配提示：开头要求命令整体为 sica-workflow JSON，后文又要求直接 printf 提交。原 SICA 自动注册的 return_result 与 orchestrator 的 submit_answer 具有明确完成语义；当前文本映射给模型造成了接口歧义，不能将整个问题简单归因于模型能力。封存产物保留全部实际请求、任务证据与 private/transport_stall_checkpoint；截至中断记录 139 次已返回模型调用、4745559 tokens，美元费用未知，未产生候选或新版本。

接口修复后使用相同三题、相同 H0、同模型重验。第一题已完成的任务执行/官方评分只复用一次，逐项验证模型、服务、数据、H0 与 benchmark 控制代码未变；诊断从修复后的接口重新开始。为避免改写冻结控制合同，新建修复运行目录，明确记录来源与变更，不冒称原进程无缝恢复。原中断诊断开销纳入总成本，原证据不删除，不自动追加题目获取成功。

修复运行目录：runs/online-step5-three-20261014-transport-repair；启动脚本 run_repaired.py 明确验证了第一题作答的复用条件。其 private/startup_source 与 startup_provenance.json 保留了这次真实执行的精确实现，后续修复没有改写运行合同或结果。

修复运行实际又发生 139 次请求，其中两次未返回用量；两次均为 HTTP 500，分别出现在 reviewer 和 main，等待时间约 3132 秒、2467 秒。主协调者最后返回 BadRequestError，控制进程非正常退出，在线状态保持 interrupted，任务游标仍为 0。planned 三题中仅第一题完成作答；django、seaborn 未执行。

该轮在诊断阶段有 archive_explorer 的三次动作均未符合官方 mswea_bash_command 格式：包括自造 XML 标签与只有叙述没有动作，最终 RepeatedFormatError。协调者随后自行读取源码并声明完成前两步、形成第三步机制检查计划，但没有进入编码委派。委员会也存在格式失败、重复检查和提交引号错误；一名 reviewer 最后仅提交了 51 字符的格式隔离测试文本。这不能算完整评审。

这一证据暴露了原在线验证只数 role_end、没有要求成功的 archive_explorer 和对应步骤身份的缺口；最后修复已同时在工作流推进点与宿主接任点收紧验证。完成接口也进一步支持 raw JSON，避免让完整报告经过 shell 解析。这两项最后修复只做工程与 Docker 回归，**没有追加第三轮真实提案**；不将修复前烟测冒称为最终实现的真实通过结果。

两次运行合计保留 278 次请求、7976015 个已报告 tokens，另有 2 次用量未知，计量不完整；美元成本未知。第一题只作答并评分一次。candidate_work 与 H0 源码摘要相同，没有注册实际提案、没有 candidate_check、没有 H1，也没有第二/第三题或递归继承证据。代理只证明公开模型标识，不能据此判断底层权重的编码或改进能力。

验收结论：工程闭环由真实 Docker、模拟模型/评分的回归覆盖；当前服务的三题真实闭环尚未验证成功，真实 Harness 收益与改进能力均无证据。观察到的失效同时涉及已修复的适配接口/诊断门槛问题、模型动作格式不遵循和代理服务错误，不能全部归因于代码，也不能认定更换模型必然解决。工程 fixtures 不能证明真实模型产生了自改进，也不能证明长期 RSI 或泛化收益。
