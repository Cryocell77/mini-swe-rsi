# SICA 工具归属、完成接口与输入核验（2026-10-13）

用户授权修复、主 agent 对照原 SICA 审查、验收提交后进行一次 Qwen 条件单题。固定 SICA ed8275dca4d3c5dbf77229964351fe9b424797dc，mini 04d809ceab9df28f9adaed044884180159172930；不改上游、模型参数、父版本选择或晋升逻辑。元任务合同升级 v6，旧运行不能按新合同恢复。

## 原源对应与实现

| 项目 | 原源 | 本次实现 |
|---|---|---|
| 主协调者工具 | main_orchestrator.py AVAILABLE_TOOLS 没有 Best/Worst/Compare | 从原 AST 提取角色权限，在真实运行环境传入 role 后强制检查；主角色不再直接 archive/search |
| 归档角色工具 | archive_explorer.py 独有 Best/Worst/Compare | archive 只对归档角色开放；主角色仍可按原允许的文件查看或委派取得分析报告，不声称严格隔绝所有历史原文 |
| 其他角色 | coder/problem_solver 有委员会，reviewer 无归档/委员会，problem_solver 可调用 coder | 恢复对应支持的操作及委派边界，拒绝 JSON 自报角色提升权限；提示列出实际权限及原角色描述 |
| 归档数量 | archive_tools.py Best/Worst 的 count default=3、lt=10 | 默认3、整数且<10；不更改原 ArchiveAnalyzer 返回全文的行为 |
| 步骤完成 | sequential.py 创建动态 step_<4位hex>_complete，参数仅 success，invocation 由闭包保存 | 模型调用该步骤名并填 success；宿主绑定 invocation，错误/过期名仍拒绝。旧 complete+tool 显式兼容，invocation 可省略，若提供则必须匹配；不把裸 complete 或结构总名自动当步骤完成 |
| 任务完成验真 | 原 Complete 信任 all_complete 声明 | 不新增强制七步/强制源码编辑规则，继续单独审核源码变化与运行结果 |

原 ReasoningAgent、原事件总线和文件视图重建仍未适配；Calculator/文件编辑等通过 mini shell 的既有映射保留，不能声称整个 SICA runtime 原样复刻。原30万文件事件阈值不是总输入保护，不能将此次输入检查称为原机制直接移植。

## 输入完整性范围

对经原生 Ollama 身份核验且 tokenizer 摘要与冻结 Qwen 资产匹配的模型，启用 verified-qwen-content-v1。发送前计数 content 文本，另加保守 framing 裕量512+32×消息数，至少留1 token生成空间；仍使用当前上下文65536和输出推导上界65535，不恢复旧32768/2048，也不自动裁剪、摘要或重试。Qwen以外没有已验证tokenizer的模型仍按服务能力/usage记录，明确审计能力不同。

服务 tokenizer、模板、参数和算法纳入身份合同；Ollama参数按键值行排序比较，值内空格保留，解决 verbose 与非 verbose 返回顺序差异，不放过实际值变化。每个请求记录完整 wire 消息摘要。若服务 prompt 计数比已发送文本显著减少（超过同一 framing 裕量），记 InputIntegrityError，不返回动作；调用/usage仍完整计量，不能把这类失败记为未调用或零成本。

这不是精确 chat template 重建。当前服务模板元数据为 `{{ .Prompt }}`，无法据此证明每个字节被模型消费；事件明确 exact_preservation_proven=false，只能报告“已验证文本计数，未检测到大量输入缺失”或失败。对误差阈值以内的遗漏仍无严格保证。

## 审查及预检

主 agent 独立阅读本轮 diff，对照原角色 AVAILABLE_TOOLS/AVAILABLE_AGENTS、归档字段及动态完成闭包。锁定的 SICA 5个控制源/9个工作流源/7个预算源、mini74文件共95项匹配；前次 Qwen843文件及 DeepSeek920文件封存清单复核不变。

无推理预检核验当前 qwen35-4b-agent:latest、原权重digest及65536上下文；两次身份核验一致。离线重放前次最后输入：content文本86717 tokens，超过容量，发送前 ContextLimitExceeded、0模型请求。上次88160包含辅助 reasoning_content，本轮按 Ollama content 字段审计，差异已明确而非改变数据或模型。

首轮完整Docker验收113 passed/173.69秒；之后真实只读metadata预检发现参数行顺序导致误拒绝，修复并新增回归，最终验收记录追加于下文。新增模拟测试覆盖权限、默认数、参数范围、闭包接口、旧超长请求拒绝、静默输入损失计量及禁止动作执行。真实Docker模拟端点覆盖主角色 archive 越权被拒、归档角色合法调用、简化接口完成七步、编码与归档加载。工程阶段无真实模型推理。

## 单次真实检查合同

验收并本地commit后，仅在新目录运行一次Qwen H0起点提案，沿用1800秒诊断保护信封；运行源码变化才评django__django-14752，不重测baseline/val/test、不自动重采样。历史dev30仅供分析，不是新基线；旧24小时起点与已有结果保留。本次输入审计和权限修复属于公开协议变化，不能和旧运行称为完全相同Harness，只保持模型、数据、预算及采样范围一致。无可信价格，费用记未知，不声称复现$3守卫。

最终验收115 passed/173.66秒，含Docker模拟端点，失败0。accepted_source_freeze的src/runtime/tests摘要与最终提交前源码一致；工程阶段无真实推理。测试日志与JUnit在runs/sica-tool-boundary-repair-20261013/accepted-engineering.*，仅本机保存。本次修复通过主agent原源对照审查，接受在已披露mini接口及输入审计能力边界内的实现，不声称完整原SICA运行时等价。
