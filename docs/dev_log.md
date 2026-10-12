# 开发日志

## 2026-10-11 初始化

已确认本仓库只有 README 和未跟踪 .env；Docker 可用。旧数据为 dev30/val30/test100；不采用旧演化控制器。遵循用户授权直接实施、运行，无额外确认流程。

状态：实现中，尚未宣布测试通过或 RSI 有效。

## 运行说明

后续 CLI 和实际验证结果随代码追加；实验输出写入被 Git 忽略的 runs/。凭据只从 .env 注入模型代理，不进入归档或报告。

## 基础模块
已添加项目依赖、固定数据摘要检查、持久化实验时限、原子结果写入；SICA 关键文件逐字保留并记录摘要。尚待测试。

## 模型与工具边界
新增宿主计量代理、模型身份检查、固定权重/参数、每会话30调用/600秒限制；候选只得到短期令牌。新增只读 runtime bridge，加载指定归档 mini；原始 SICA 改进任务只替换不适用的子agent工具术语。待容器端到端测试。

## 执行与评分模块
新增隔离任务容器、隔离改进容器、归档加载证据、冻结镜像恢复与官方评分子进程。评分资产仅复制旧评分模块，新增不可变镜像身份校验；未使用旧控制器。错误原样重跑最多一次。待验证。

## SICA 适配
原始 runner 的函数体与 ArchiveAnalyzer 均保持；仅注入模型/benchmark导入和执行边界。保留原始 queue、tracker、select_base_agent、run_meta_agent_benchmark；缺失完整30题覆盖拒绝选拔，恢复不重复模型提案。实验调度在原生函数外交错两轨道。待测试。

## 实验调度
实现预检数据复用、两轨道交错、冻结冠军、同起点探针、两次val配对、条件test封存。机制判据要求同题实际模型输入改变，不能仅凭源码差异宣布成功。尚待修复/测试探针证据挂载及完整流程。

## CLI 与报告
新增四入口、实验互斥锁、真实统计与不完整覆盖判据；最终test须先满足门槛与精简回放摘要。配对bootstrap采用固定seed42、10000次；成本无定价记unknown，不用SICA协议占位0声称免费。已修复探针共同证据挂载与阶段恢复进入逻辑。待运行验证。

## 验证结果与本机网络适配
6项单元测试通过。真实Docker集成暴露internal网络无法直接访问macOS宿主；新增只允许两个固定HTTP路径的中继，候选仍无外网。修复后7项测试（含两个归档加载和只读挂载）通过，10.44秒。所有命令现在也经宿主强制60秒限时。新增mini源文件摘要校验，agent耗时与评分耗时分离。

## 运行说明（新增）
`uv run mini-swe-rsi baseline --run runs/sica-qwen-20261011 --preflight-only` 开始实际24小时计时，验证开发集前三题并保存预检。`baseline`（不带preflight-only）补齐30题；`evolve`运行交错搜索与条件门槛；`evaluate`运行验证，`evaluate --final --minimal PATH`须门槛/回放通过；`report`离线汇总。

## 文档与复现
README已包含安装、四入口、门槛、失败语义、24小时预算与输出。新增逐项上游差异和许可说明；评分依赖锁排除本机绝对源码路径，改为单独安装固定源码。7项测试再次通过，11.35秒。正式预检已启动，报告尚无有效性结论。

## 正式预检发现接口错误
固定60000字符上限被原始mini识别为可重试BadRequest，导致无新增推理的等待。修复为标准context_length_exceeded与不可重试预算错误，属于代理错误语义适配，不改变策略。保留实际调用与时钟；恢复不得覆盖未完成attempt，最多使用第二次原样基础设施尝试。增加宿主超时正常任务结果处理。

## 预算接口补充修复
实际SDK只凭错误文字识别上下文异常，单独code字段不足；改为标准maximum context length文字，并添加真实客户端回归测试。宿主SDK的wait读超时不足以作为硬时限，将增加独立计时器停止容器；首个预检等待开销保留，不重置实验时钟。补充全部160项eligibility证据文件摘要校验。

## 预检纠正与错误映射测试
7项单元测试通过，1项Docker测试按标记跳过；实际客户端确认 `exceed context limit` 映射为不可重试上下文异常。排障时误停止seaborn预检容器（未到deadline），原结果保留为interrupted_host_result.json并排除正式基线，使用唯一一次基础设施重跑；实验计时不重置。新增独立容器计时器落实600秒上限。

## 原生闭环与预算回归
完整Docker测试套件10项通过（17.94秒）：包含两归档实际加载、硬时限停止无响应代码、原生meta父/改进者分别选择、固定改进者对照及恢复不重复提案。发现并修复SWE类属性遮蔽benchmark方法的问题，已用原始queue真实执行适配fixture验证。正式基线从保存结果恢复，未重新生成已完成任务。增加实现摘要快照、冻结配置恢复检查和报告失败分类/依赖/进程记录。

## 门槛与批准方案对齐（搜索开始前）
移除额外的Submitted标签要求：用户批准的机制判据是两代真实行为变化与演化改进者执行，探针判据是候选配对收益和相同实际预算。到时限后留下、随后实际执行的有效代码修改仍是候选；不能用进程标签替代行为证据。所有状态仍完整报告，宿主继续强制30调用/600秒/上下文等上限。门槛测试覆盖两次配对与实际调用超额，已通过。

## 评分与镜像准备提交修复
发现官方镜像HEAD为SWE-bench准备提交，并非数据集base_commit。错误的base->工作区补丁包含准备提交权限/文件变化，可能导致无关重建与评分超时。修复为相对不可变镜像初始HEAD提取agent变更。一次性修复脚本按Git历史重放先前补丁，不重新请求任务答案；原始补丁、原评分与所有成本保留。scikit先前因超时分类错误触发的第二次作答被排除，固定保留第一次，不择优。
同时修复官方超时标记在test_output与run_instance.log位置差异。评分基础设施失败现在只重评同一补丁，最多两次且跨恢复持久计数；不再重跑模型。3项新增回归检查通过。所有修复/排障时间计入原实验预算。

## 修复后回归与恢复
15项完整测试（含Docker）通过，17.17秒。8份既有答案按镜像HEAD重建并重评完成，无新增任务模型调用；scikit原始修改恢复为通过。公开原始补丁、修复后补丁、首次评分、误分类额外作答均保留，额外作答排除。基线继续使用同一实验根与原始计时。验证token比例新增完整计量要求，缺失usage不能被当作低token收益。

## 基线完成与行为回放
H0开发集完整30题，成功6题（20%），模型身份前后一致。此分数仅为开发集基线。
新增固定回复/原始工具输出的H0反事实回放，无模型请求、无真实shell执行；检测后续上下文输入、命令、操作顺序及终止行为差异。真实Docker测试识别第二轮上下文修改，排除未使用源码常量修改；完整16项测试通过（40.53秒）。搜索开始前将行为门槛从仅首轮提示变化扩展为首轮变化或固定轨迹回放差异；演化改进者须对应已验证行为变化的归档，而非任意源码变化。

## 搜索启动
首次递归提案18次模型调用、127063 tokens，ContextWindowExceededError，未改变行为源码。按原生SICA继续归档评测；不依据进程返回0或meta总结宣称改进。H0基线共有718次实际调用（包含前期排障/总结），5538177已计量tokens，未知usage为0。所有搜索仍只使用Qwen同一模型；固定轨道共享完整初始证据。

## 2026-10-12 父版本行为检查修正
搜索G1任务模型已完成时暂停调度，评分作答保留，时钟不重置。行为变化现在逐代与SICA实际选择的父版本比较；先自回放校准，再使用相同记录对父版本反事实回放。继承已有行为后只改未使用代码不能算第二代。所有演化代码回放在无网络、无密钥、无评分资产、只读源码的容器中运行，不在宿主导入。源码筛选包含全部文件，Python仍去除纯注释变化；最终判定由实际行为证据决定。

父版本修正后完整16项测试通过（48.19秒，含Docker），从同一目录恢复搜索。额外固定Python基础镜像摘要，导出实际运行的runtime镜像、摘要和系统包清单，避免以后apt版本变化无法重现本次环境；未替换正在运行的实验镜像。

递归G1完整dev30为9/30，与H0的6/30不同，但两者原始代码SHA完全相同（409edf8c...）。固定G1同样只浏览无编辑、上下文额度耗尽。报告将相同源码标注为重复运行，不作为Harness收益。追加按任务/提案/探针/总结分类的实际调用成本，以及未完成归档覆盖和预算停止分类；不使用部分覆盖选冠军。

## 恢复完整性补充
检查发现探针在已写谱系后中断，会跳过尚未完成的dev测评；现恢复时继续dev，不重新提案。完成任务、整个归档和配对评测均绑定代码摘要，源码改变拒绝复用旧结果；H0不能通过再次baseline重写身份。7项相关测试通过，新增3项恢复回归测试。当前实际归档源码未改变，所有既有任务均有身份记录，运行中的搜索行为不受此次恢复检查补充影响。后续启动保存不含凭据的完整宿主实现快照；候选仍不能访问该快照。

## 门槛不可达时提前停止
首轮两轨道和固定G2共三份提案均无原始源码变化。按用户“最多四次提案”与“两代行为变化”约束，增加外层数学不可达停止：已发生原始父子文件差异数+剩余原生提案数<2时停止。以全部文件SHA为保守上界，不以AST/评分判断；不改变SICA机制，不跳过可能成功的递归链。此规则在原生G2/G3生成前声明。等待当前作答结束后原样恢复，计时不重置。

20项完整测试通过（48.81秒，含Docker）。搜索在固定G2的已完成作答评分边界恢复，未重新请求该作答；所有阶段与全局时钟保留。数学不可达检查另验证了恢复时重访已完成代数的情况，按当代前缀计数，不因后续谱系存在而误报。进度统计改为已评分任务，原始SICA tracker的pending行单列为started。

## 镜像清单冻结
旧eligibility证据含通过状态但不直接包含镜像ID，因此另固定全部160项镜像身份到image_inventory.json，恢复时拒绝元数据变化；给本次既有运行补齐时逐个核对以前评分job使用的镜像，未改变既有配置。实际核对Docker中全部160个镜像的ID、amd64架构与仓库摘要：以不可变image ID判定内容身份，另记录本地仓库别名与旧清单差异，不把同一镜像共享多个实例别名误判为内容改变。本地已有60个开发/验证镜像，ID和架构一致，且至少一个仓库摘要匹配；100个测试镜像目前缺失，仅冻结旧清单与摘要，没有下载或运行。共享镜像的一处仓库别名差异单列，详情见image_validation.json。若进入最终阶段，恢复缺失镜像须消耗原预算。新增2项元数据篡改/历史不一致回归检查，12项相关测试通过。

完整22项测试通过（48.53秒，含Docker）。另补充Broker.finish等待已发出的请求计量结束再保存会话用量；新增并发结算回归测试3项通过。运行中的旧进程计量将与事件总账逐题核对；新启动使用同步结算。报告成本始终按全部真实调用事件求和，包括被排除的基础设施作答。

## Harness差异交付
每份提案另保存harness.diff，与任务解题patch.diff区分；恢复时仅补齐差异文件，不重新提案。已生成的真实候选将按完整归档比较补齐空差异，保留无修改证据。原始选择/统计函数和候选策略未改变。

## 实验结束与验收
2026-10-12 03:18，递归前三份提案原始源码完全相同，最后一次最多产生一代，门槛不可达，正常停止（退出0），并非预算耗尽。完成150个同dev30任务尝试的独立评分；H0 6/30，递归副本9/30和6/30，固定副本8/30和7/30。全部mini代码SHA相同。五份提案均ContextWindowExceededError，无源码改动。未运行探针、val或test，未精简。
末尾模型身份一致，150个完成任务与总账对账零差异；3493次真实调用、26372570 tokens，缺失usage0。完整23项测试通过（47.48秒，含Docker）；末尾报告阶段时长按相邻阶段入口计，不将历史完成阶段的墙钟截止时间误称为超时。实际源差异为空并已补齐diff及摘要。失败分析和可提交的小体积结果摘要已保存。

## 2026-10-12 adapter repair（实施待主 agent 验收）

- `runtime/bridge.py`：先保留 result/answer/trajectory/trace，再对非 Submitted 终止返回非零进程状态。
- `src/mini_swe_rsi/sica.py`：分别记录 process_status、exit_status、execution_succeeded、change_status；异常/无修改生成确定性事实摘要，正常已修改仍调用原始摘要生成器；未增加候选评测/选拔 Submitted 门槛。
- `src/mini_swe_rsi/broker.py`：固定文本 bash 协议字段规范化，保留单份 reasoning_content，拒绝隐藏原生工具协议；计量和发送及事件共用规范化消息，追加原始/规范化长度审计。
- `src/mini_swe_rsi/experiment.py`：协议单独冻结于配置、旧协议恢复提前拒绝；探针证据使用公开冻结视图，避免全目录复制。
- `src/mini_swe_rsi/archive_view.py`：逐文件白名单公开归档视图，保留模块路径、有效 dev 数据与公开轨迹/历史 meta summary，排除诊断副本、完整 trajectory 配置、评分/宿主内部资产；拒绝 symlink/special 文件并冻结内容摘要。
- `src/mini_swe_rsi/driver.py`：原归档选中源码的 SHA 对照只读视图，执行前后核验源码不变；native/probe 共用挂载修复。
- `tests/test_sica_inheritance.py`：原生选择/恢复覆盖正常修改、无修改、legacy 进程0但agent失败、预算失败后源码修改仍可独立评测。
- `tests/test_broker.py`：重复推理元数据去重、实际 wire/计量一致、真实超限和原生工具绕过拒绝。
- `tests/test_archive_view.py`：公开白名单、原始源码摘要、冻结视图篡改、symlink 拒绝。
- `tests/test_archived_runtime.py`：实际 Docker 内检查排障/宿主/credential trajectory 不可见、源码只读且归档模块实际加载。
- `docs/upstream_adaptations.md`：声明执行摘要、文本协议、公开视图偏离与限制。

修改前快照保存在 `runs/adapter-repair-20261012/before/`。首次单元验证 `uv run pytest -q`：26 passed、3 skipped（Docker）、1 个已有 LiteLLM deprecation warning。后续完整 Docker 验证另记。未运行真实模型实验、未修改旧运行或固定上游、未提交。

完整 Docker 首次运行 28 passed / 1 failed：新增消息规范化审计事件被旧回放当成模型/工具操作，导致错误行为差异。`src/mini_swe_rsi/replay.py` 修复为仅回放真实 model_call/tool_call，并对照实际文本协议字段（包含 reasoning_content）；没有合成新回复或工具输出。随后重跑受影响测试及完整套件。

第二次完整 Docker 套件：29 passed（48.18 秒），仅已有 LiteLLM deprecation warning。根据主 agent 审查，公开视图的 agent_code 改为完整逐字复制（只排除已有 __pycache__ 语义，拒绝 .env/.env.*），不限制普通 config.json、隐藏源码文件或新增源码扩展名；新增普通配置/文件和公共证据 symlink 回归。`sica.py` 的 execution_succeeded 仅控制执行反馈，不控制候选评测/选拔；raw_source_changed 单独用于事实摘要，保留旧 source_changed 机制字段语义。新增真实 Docker HTTP 上下文错误测试，验证 bridge 非零退出且保留 result/trajectory/trace，目标不变。

针对最终视图/状态变更运行 `RUN_DOCKER_TESTS=1 uv run pytest -q tests/test_archive_view.py tests/test_sica_inheritance.py tests/test_archived_runtime.py`：10 passed（15.03 秒，包含真实 bridge 异常退出）。`tests/test_resume_identity.py` 另增加旧协议配置在创建 runtime/变更 budget 前拒绝恢复的回归。最终完整套件由主 agent 独立再验收。

## 主 agent 独立验收补充（待运行）

新增 `tests/test_sica_runtime.py`：真实 Docker + 官方 mini + 原始 SICA meta 函数，模拟模型给出实际写入命令，验证只修改 RW 候选、原父源码保持不变、下一轮实际从已修改归档加载并显示继承的系统配置、执行摘要路径与谱系正确。选拔分数为明确标记的测试 fixture，不构成实际 RSI 收益证据。原始 SICA 五个文件已从固定 GitHub 提交再次核验，逐字等于 vendor 与本地上游副本。

## 主 agent 独立验收完成

新增端到端继承测试单独通过（7.93秒）。最终完整 `RUN_DOCKER_TESTS=1 uv run pytest -q --junitxml=runs/adapter-repair-20261012/acceptance.xml`：33 passed（59.83秒），仅已有LiteLLM弃用提示。SICA五个原始文件与固定GitHub提交逐字一致；14个原生函数AST不变，在相同导入边界下实际加载code object一致；mini的74个固定文件摘要未变。CLI帮助正常。

同一 `.env` Qwen服务执行1次协议兼容请求、175 tokens，返回OK，模型身份与原实验一致。旧五份失败提案离线规范化后四份下一次输入低于原60000字符上限，一份仍超限，未扩大合同。旧运行5668个文件的路径/大小/mtime清单未变。以上只验收工程修复，没有重启搜索、val/test或宣称RSI收益。

`docs/repair-review.md`记录独立审查发现、上游对照、测试与限制；README和诊断文档已同步。此前项目实现尚未入库，本次提交包含完整适配实现及修复，排除凭据/数据/运行产物/依赖环境。

## 运行说明（修复版）

保持原四入口。修复版使用 `mini-text-bash-v2` 配置，新实验需另建运行目录；旧协议运行只可离线report，不允许按新协议恢复。完整验收：`RUN_DOCKER_TESTS=1 uv run pytest -q`，测试模型均为fixture；单次真实接口检查另存修复验收目录，不计作RSI实验。
# 2026-10-12 修复后框架检查（不重测基线）

按用户最新要求复查五项诊断，针对性回归 15 passed。新目录 `runs/framework-smoke-20261012-124050` 复用旧 H0 当前有效公开 dev30 证据，运行一次原生 SICA / 同一 Qwen 提案，并用既有正确开发题补丁验证独立评分器。前四项工程问题通过检查；第五项仍存在：18 次调用与 18 条只读命令，规范化下一次输入 60,994 字符超过 60,000 合同，未修改源码。失败正确传播为进程 1 和事实摘要，无错误成功反馈。评分器 completed/resolved 均为 true。未重测基线、未运行 val/test；旧 5,668 文件清单不变。详见 `docs/framework-smoke.md`。


## 2026-10-12 数据集五题诊断完成

用户授权直接做小规模数据集测试。预先固定 dev 清单前五题，使用同一 Qwen、官方 H0 和修复后的生产执行器，单独目录 runs/dataset-diagnostic-20261012-130957。独立评分 1/5（Seaborn）；五题均写文件，三题改业务源码。四题上下文终止、一题调用上限。共117次模型调用、106条工具命令、953634 total tokens（857554 cached）、耗时727.0秒；没有基础设施错误或未计量调用。发现11次正文为空且命令仅在reasoning_content的格式失败，Django占8次；此兼容性问题不能归为已修复的重复字段计量问题。Flask的mode方案与评分text接口不匹配；Xarray生成补丁参数未传递导致NameError及17个回归。未改变生产代码、提示词或预算，未重跑完整基线/演化/val/test，旧5668文件清单不变。详见docs/dataset-diagnostic.md；样本结果不证明RSI有效。

## 2026-10-12 第二轮边界修复（子agent实施中，待独立验收）

新增 mini-text-bash-v3：32768总token预算，2048输出预留及512+每消息32的聊天封装余量；使用独立冻结的Qwen tokenizer身份、版本与SHA256。规范化消息与wire共用，逐字段编码content/reasoning_content，记录宿主估计、服务usage和两者差值，不把字符数或宿主估计冒充服务实际prompt_tokens。旧协议/预算不一致及tokenizer校验失败在Budget/Runtime创建前拒绝。模型身份仍独立冻结。

严格兼容空正文且推理全文只有一个完整官方动作块的stop回复；原生工具、refusal、普通思考、多动作、截断或非空正文保持原样。保留原始response、agent_response、适配原因，回放使用实际返回版本。新增边界/拒绝/恢复单元覆盖，Docker回放fixture改为仅reasoning_content动作以验证真实mini执行与回放。未改变SICA/mini固定源码、策略、已有运行；未请求真实模型、未提交。

主agent检查真实诊断回复后指出动作前含普通思考，边界调整为只提取推理中唯一完整官方块，允许块外文字但不交给shell执行；复制后推理中移除该动作，冲突reasoning/reasoning_content别名拒绝转换。只读重放五题诊断的全部11个真实空正文回复均通过适配验收；动作相关及缺tokenizer拒绝共15项通过。

补充独立服务配置身份：只读/api/show校验qwen35与显式num_ctx，冻结参数/模板SHA/窗口，避免将架构最大长度当当前服务窗口。服务actual prompt超总预算的回复保留usage但拒绝下发动作；带error事件不回放为成功。服务配置四项fixture测试通过，动作及服务边界等19项测试通过；全量仍待tokenizer artifact落地后运行。

第二轮实施完成：补充verbose服务tokenizer元数据完整摘要核对（词表、merges、token types与特殊token参数），仅保存摘要；结束时再次校验服务身份。Qwen tokenizer许可随包交付。完整 `RUN_DOCKER_TESTS=1 uv run pytest -q`：58 passed（98.06秒），仅已有LiteLLM弃用提示；包含真实Docker、动作适配回放、冻结恢复与服务变更拒绝。未请求真实模型、未改旧运行、未提交；独立上游审查和真实预检由主agent完成。

## 第二轮主agent独立工程验收

固定SICA五文件重新远端核验、14函数AST/加载code object一致；mini74文件不变，旧运行5668文件清单不变。匹配tokenizer固定落盘，135条历史请求实测prompt均低于宿主估计（最大差值−561）。完整Docker套件58 passed/98.06秒，CLI和diff检查通过。审查补齐服务tokenizer指纹与结束时服务身份核验，保留原始/交付回复与真实用量。工程验收通过，真实提案另用新目录执行，未重测基线。详见docs/context-protocol-review.md。

## 第二轮提交后真实验收

代码commit9ed0d95。主agent边界套件29 passed/11.35秒。真实SICA提案30调用30命令，9次reasoning唯一动作适配后执行；最大输入70340字符，12次超过旧上限，最大实际prompt18941，所有请求符合32768合同。350180tokens/328322cached，101.82秒含评分检查；评分器resolved=true。没有上下文/格式/服务错误；全部命令只读，源码未变，LimitsExceeded进程1。模型与服务身份未变，旧5668文件清单未变。没有重复基线或运行val/test/精简。说明工程修复确实生效，但模型在30次调用内仍未进入编辑，未证明RSI。

## 四项问题只读复查与归因补充

本轮未新增真实模型调用或修改生产代码。针对前四工程项复跑18项Docker回归全部通过/46.47秒。逐轮审计v3提案确认：930条历史消息字段规范、失败摘要正确、339文件公开视图无禁止资产、workdir可写。额外发现agent_1未编辑候选被当作已改进历史版本，最后11命令8次diff；原SICA本身创建候选后挂载整归档，此可见性并非新复制bug，但适配任务未明确候选身份。bridge将尽早调用元改进推理结构替换为检查归档，语义不等价；30步合同未进入实际任务提示。此前只强调模型反复读取的归因不完整，现补充任务协议与预算因素，因果仍待对照验证。详见docs/four-issues-source-edit-analysis.md。

## 第三轮任务协议修复启动

用户授权修复最新诊断的提示意图、候选身份与预算可见性。计划已写入implementation.md；GPT-6.1 Sol / low子agent实施，主agent独立审查/上游对照/验收/commit/真实测试。保持原30调用及v3模型协议，不改SICA选择或mini源码；新任务协议独立冻结，旧实验封存。当前已有四项复查文档随本轮保留。

## 第三轮任务协议实施（子agent，待主agent独立验收）

在SICA→mini边界恢复“尽早直接开展元改进推理”意图；去除把元改进推理改成额外浏览归档的非等价替换，保留原任务提出/实施改进和更新日志要求，不新增策略、反思步骤或预算。任务区分父版本、执行改进者、未评测候选、RO启动归档与RW实时workdir；固定轨道父值与H0改进者分开，探针父恒H0，current_iter沿用上游编号。预算仅在improve显示，benchmark仍读取原任务文件。

新增core.improvement_contract()，独立冻结sica-mini-improvement-v1及bridge/上游任务源码SHA；Experiment在context/Budget/runtime/model前拒绝缺失或不同任务合同。宿主绑定实际归档proposer路径及parent→candidate启动minisweagent源码，独立渲染prompt身份并核对容器实际任务文本SHA及renderer/upstreamSHA。记录host_improvement_task.json、improvement_task.json、improvement_prompt.txt与verified/unverified_failure；成功执行缺失身份证据拒绝，启动/超时失败保留失败状态。

新增原始任务意图、父≠改进者、候选未评测/冻结快照、probe共同H0起点、旧合同提前拒绝及错误挂载身份回归。真实Docker继承fixture增加第三轮固定H0改进者/父H2，核验wire提示、独立身份和真实加载模块、父源码不变及候选更新；均使用模拟模型，不请求真实服务。针对性38项单元通过；首次Docker针对性10项通过/37.40秒。完整首次65通过/1失败，失败为遗留测试调用旧双参数渲染签名，已更新；首轮产物保留在新runs/task-protocol-repair-20261012目录，最终完整套件结果另追加。未修改vendor/.upstream、tokenizer/service合同、搜索/评分/晋升规则、旧runs；未实际请求模型、commit或push。

第三轮最终完整 `RUN_DOCKER_TESTS=1 uv run pytest -q --junitxml=runs/task-protocol-repair-20261012/implementation.xml`：66 passed/98.50秒，仅已有LiteLLM弃用提示。stdout保存implementation-tests.txt；首轮失败记录未覆盖。完整套件包含第三轮fixed H0改进者/parent H2真实Docker继承、wire任务身份及宿主/容器SHA核验；修复通过工程fixture不代表真实模型会改进或RSI收益。主agent继续独立审查/上游核对及授权真实提案。

主agent独立审查补充：发现同进程初始化后若renderer改变，Driver此前重新读取当前contract但未对照已冻结配置。现每次improve在runpy加载renderer/创建容器/模型前，对照已有configuration.json.improvement_task；缺失或不一致拒绝，无配置的独立fixture/API继续采用当前contract。新增初始化后renderer变化及缺合同两项启动前拒绝fixture。仅重跑受影响 `RUN_DOCKER_TESTS=1 uv run pytest -q tests/test_improvement_task.py tests/test_sica_runtime.py`：11 passed/17.43秒，产物renderer-freeze.xml与renderer-freeze-tests.txt。此前66项完整验收属于该小修前，未混称为小修后的全套结果；未真实模型调用或commit。

## 第三轮主agent验收

独立提示原文差异审查和五个固定SICA文件远端核验通过，14原函数AST/code object及mini74文件不变，旧5668封存文件不变。主agent14项独立测试通过/16.98秒；审查追加每次启动前检查冻结taskcontract，子agent修复后11项affected测试通过/17.43秒，代码复审通过。完整66项测试为补充前最终完整套件，结果分开记录。CLI及diff检查通过，工程修复验收；将提交后用新目录同Qwen实测。
