# 第四轮：恢复 SICA 七步工作流的独立审查

2026-10-12。按用户授权，由 GPT-6.1 Sol / low 子agent实现，主agent对照固定上游、审查、验收后提交并实测。恢复方案预先写入 implementation.md。本轮不重测基线，不运行 val/test，不进入精简。

## 恢复内容与原代码对照

固定 SICA `ed8275dca4d3c5dbf77229964351fe9b424797dc`、mini `04d809ceab9df28f9adaed044884180159172930`。新增八份原始 Python 文件和 MIT 许可至 vendor/sica_workflow；原有五份 SICA vendor 文件不变。主agent另从固定提交读取上游，14份文件逐字一致，原 runner 的14个函数 AST/code object 不变，mini 的74个文件摘要不变。独立审计产物为 runs/sica-workflow-repair-20261012/independent_upstream_audit.json。

| 原机制 | 本轮实施及独立核验 |
|---|---|
| 七步元改进 | AST提取原活动 Step 字段；显式调用、仅当前完成工具有效、成功顺序推进、失败退出；独立对照全部七步字段与状态转换 |
| 主协调者与执行角色 | 恢复原主协调者、archive_explorer、software_developer、general_problem_solver、reviewer 的system/description及活动core prompt；子角色加载实际选中归档的 mini agent/model |
| 四人委员会 | 原四份specialisation原文，返回四份评审拼接，无新增合成调用或强制重新评审门槛 |
| 归档分析 | 原 ArchiveAnalyzer 生成角色初始概览及overview/best/worst/compare反馈 |
| 提交和步骤完成 | 与原机制一致，步骤成功由模型声明；允许早提交，未完成如实记录；不新增源码修改、委员会通过或测试通过的晋升门槛 |
| 预算与继承 | 所有角色使用同一会话令牌、同一模型、共享30次真实调用和600秒；Docker验证演化与固定改进者实际归档路径 |
| 身份及中断 | v2合同冻结全部定义/transport/renderer摘要；宿主与容器对照；状态原子写入，中断未完成，拒绝覆盖已有中断会话 |

主agent审查要求补齐角色准备失败的具名错误、原初始归档概览、子角色能力说明、官方mini拒绝预算后的重试退出、状态原子写入与宿主启动前拒绝已有状态。最终冻结文件摘要另存 implementation-final-freeze.json。独立语义结果在 parent_semantic_check.json。

## 明确的适配差异

这是原 SICA 定义和核心流程在 mini 上的移植，不能称为完整原运行时逐位复现。原函数工具通过结构化文本动作转接；文件视图的打开/关闭与上下文重建未移植，搜索是字面搜索；原web和reasoning-agent工具不可用。原测试命令映射为离线检查或共享预算的角色任务，独立数据集评分仍由宿主执行。可选历史运行/requirements进入显式角色任务，而非独立字段。

原并行、异构模型委员会改为串行、统一.env模型；这符合用户固定模型和资源合同。只在improve入口设置官方现有重试参数为1，避免宿主拒绝预算后继续无效退避，benchmark设置不变。原步骤中的“成功”仍是声明，不能当作真实测试成功。

主协调者、归档分析者和评审的只读限制是**工具接口能力**，不是每个角色的操作系统沙箱。所有角色在同一个候选挂载容器内执行归档Python，不能防止恶意演化源码绕过接口写入候选或使用共享令牌。私有评分与宿主数据隔离另外由容器挂载和评分进程保证。

## 工程验证

子agent首次完整套件76 passed/170.38秒；其后受影响Docker套件29 passed/78秒，最后小修离线26 passed/2 skipped。前述结果分别对应各次源码，不混称为最终冻结树验证。首次Docker失败暴露观察缺少exception_info字段，已修复；低预算fixture暴露官方重试退避，已修复。失败日志全部保留。

主agent独立针对性测试14 passed/1 skipped；独立原文与状态语义检查通过。最终冻结树完整Docker套件 **80 passed/240.72秒**，仅已有LiteLLM弃用提示一项，产物parent-final.txt及parent-final.xml；验证后10个冻结文件摘要仍一致。模拟端点七步成功运行21次实际请求、七个角色（含四评审）；低预算运行如实未完成。另核验跨角色30次上限与超时后不再请求端点。这些是工程证据，不能作为真实RSI收益。

旧实验5668个封存文件的路径、大小和mtime与原清单一致。主agent未发现阻断验收的实现问题，工程验收通过。真实Qwen提案在代码提交后独立运行，其结果另行追加。
