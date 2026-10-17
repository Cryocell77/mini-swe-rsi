# 批次 C：单一在线模式、预算与任务选择验收

日期：2026-10-17（Asia/Shanghai）。

## 范围与委派

本批仅实施精简指南的 P2-1、P2-2。批次 D（重复实现与冗余清理）、E（P3）未开始。

按用户指定，使用已运行的 DeepSeek Harness Desktop，经 DSH Desktop Bridge 创建独立仓库子工作区，实际界面确认 `DeepSeek-V4.1-Flash / High`。用户明确授权添加当前仓库白名单及 `read/write/edit/glob/grep/bash/job_output` 工具；未扩大为任意仓库或开放进一步委派、插件管理等能力。

另已在 Desktop 的 Workspaces 中新建并确认 `mini-swe-rsi`，登记当前项目路径；桥接子任务仍使用独立工作区，不直接编辑主 checkout。

子工作区：`/Users/maxwell/.dsh-mcp-work/009923b2-4949-4bff-9be7-5b33a3de1dfd`，关闭会话后保留文件。现有未提交 P1 补修先同步为子工作区起点，随后独立提交为 `f6f3bd9 fix: guard online local checks against runaway output`；批次 C 的修改在该基点上审查和整合。

DSH 分轮实施预算/选择、调用方、单模式和入口检查。桥接的既有单次任务期限为 300 秒，四轮均实际结算为 `timed_out`，没有把其状态当作成功，也没有自动重放重启前请求。主 agent 读取终态结果并检查实际改动，修正审查问题后自行验收，不依赖子 agent 的通过声明。委派任务编号：

- `a13c2085-7d2f-46a1-b078-a0fde410c070`
- `40477d38-e331-4ff8-8c5f-c7a1d864e0ae`
- `a722e35f-d21c-4646-8432-2d30defc75a7`
- `7e8a92f7-cecf-467a-a77f-413273e002cf`

## 实际变化

- 删除 `Workflow.online` 参数、属性及模式分支；原 `online=True` 下的角色/步骤约束、反馈证据、明确 improve/skip、检查计划、宿主候选检查和恢复授权成为固定规则。
- 移除 bridge 的 `--online` 开关及调用方传参。保留单题 `benchmark` 与版本身份参数 `--online-version`；审计字段 `online=true` 仍表示实际运行事实。
- 容器入口在读限额、导入 agent/模型、创建日志之前校验父版本、候选、任务、轮次、版本标签和反馈路径。父改进者必须一致，版本标签绑定父 Hxxxx；benchmark 拒绝恢复授权。
- 删除 `PHASE_HOURS`、Budget/BudgetExceeded、阶段及 24 小时截止、阶段持久字段及调用链。没有额外总/阶段时限；保留单题 600 秒、角色 500/1000 迭代、每 completion 至多 5 请求、工具 600 秒和服务容量/适用费用守卫。
- 容器限额字段改为 `session_remaining_seconds`；无期限网络等待使用 None，严格 JSON 不写 Infinity。原始用量、未知消耗、迟到回复、故障暂停和恢复迭代计量保持。
- 删除 `freeze_dev_selection()`、Data 的 `dev_ids` 分支及旧前缀机制。唯一选择为 `task_limit` 和 `online_task_selection.json`，冻结原始顺序和范围，恢复拒绝变更。
- 旧 `budget.json`、`execution_scope.json`、`dev_selection.json` 运行显式拒绝；历史产物应配合对应历史 Git 版本处理。公开协议版本未额外升级，控制摘要仍严格约束恢复。

## 主 agent 审查

1. 将旧工作流固定为 `online=True`，移除其参数/属性并统一少量错误措辞后，26 个 Workflow 方法与新实现的 AST 全部一致。阶段权限、候选门槛、恢复和缓存逻辑未另行改写。
2. 对旧/新版本输入相同模拟序列，检查接受、检查拒绝、正常跳过三条路径的角色调用、步骤、计划、检查请求/结果和事件类型序列一致。比较排除时间、随机步骤编号和临时路径。此处比较的是工作流；候选是否接任由宿主独立判断，另由 Docker 集成测试覆盖。
3. `tool_output.py`、StageTools、recovery、grader、context_budget、依赖/上游/评分/tokenizer 锁及全部 vendor 文件保持原样，共核对 21 个关键文件；P1 输出资源保护保留。
4. 撤销子 agent 初版无必要的协议版本升级；补齐入口独立身份校验；修正测试哨兵到实际函数 globals，确保错误的初始化次序会失败。
5. `src/` 与 `runtime/` 的已跟踪 Python 文件从 6,115 行降为 6,078 行，净减 37 行（含空行与注释，不含 tests/vendor/scripts）。主要收益为删除模式开关、阶段时钟及重复任务选择，而非大批量删文件。

## 验证

此前临时测试环境的依赖版本元数据已损坏。子 agent 尝试的临时版本兼容 shim 未用于主 agent 验收，也未整合到项目。重新按 `uv.lock` 建立 `/tmp/mini-rsi-phase-c-verify-20261017`，核验真实版本元数据；锁文件未变。

预算/选择部分先独立验证：301 passed、10 Docker skipped、1 原有 LiteLLM 弃用警告，147.10 秒。

最终全量验收：**314 passed，0 failed，0 skipped，1 原有 LiteLLM 弃用警告，621.66 秒**。启用全部 Docker 用例，完成实际归档加载、H0→H1→H2 接任、第三题正常跳过、候选机制检查、拒绝、恢复与阶段隔离验证。模型和评分使用明确的模拟夹具，本轮不调用真实 RSI 推理服务、不重跑旧失败实验。

运行命令：

```bash
RUN_DOCKER_TESTS=1 RSI_TEST_RUNTIME_IMAGE=mini-swe-rsi-runtime:p1-review \
LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONPATH="$PWD/src" \
/tmp/mini-rsi-phase-c-verify-20261017/bin/python -m pytest -q
```

镜像：`sha256:c7fa8d89f57a3472e158adb50309434cb8bbc5aa1c53d62eed5dfb5b9c245599`。保留已测试源码/测试/锁文件的 70 个摘要，提交前再次核对，不将文档更新冒称新代码验证。

结论：批次 C 的代码精简、审查和离线/容器验收完成。没有将 DSH 的超时状态或旧真实模型故障当作通过，也没有据此宣称真实模型已完成新的接任闭环。批次 D、E 保持待实施。
