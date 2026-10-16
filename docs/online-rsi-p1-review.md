# 在线 RSI P1 精简验收记录

日期：2026-10-16

## 范围与执行

按用户确认的 [精简指南](online-rsi-simplification-guide.md) 实施 P1：仅保留在线运行与在线报告，移除原批量实验及其专属支持代码。三个实现子 agent 均为 GPT-5.6 Luna / High，分别负责入口与专属模块、运行时边界、测试迁移；主 agent 集成审查、修正问题、执行验收并负责最终提交。

本轮不实施 P2/P3，不启动真实模型实验，不恢复或改写历史运行。现有 `.venv` 和默认 runtime 镜像标签保持；验证使用独立临时环境与 `mini-swe-rsi-runtime:p1-review`。

## 已完成的修改

- 删除 `experiment.py`、`sica.py`、旧 `replay.py` 和两份历史 smoke/repair 脚本。
- CLI 仅支持 `online/report`，删除原 `baseline/evolve/evaluate` 及其参数。报告不再导入原实验或统计门槛，并拒绝把原运行目录标记成在线。
- 删除原归档视图、最好/最差题排名、版本成绩比较及原归档模块加载；保留在线按时间呈现的公开归档。
- 删除 Broker 的独立总结调用和总结专用预算分支；所有保留角色仍执行原迭代、请求、工具及单题限制。
- 删除旧基线失败题集迁移函数；P2 的任务选择与全局预算结构暂保留。
- 移除 vendored runner、ArchiveAnalyzer、BenchmarkTracker，调整来源清单。bootstrap 校验并准备保留的任务提示、许可证、mini 和工作流来源。
- 移除 `numpy/pandas/tabulate/jsonlines` 四项直接依赖，重新生成宿主锁和容器依赖锁；独立评分锁不变。
- 迁移混合测试，删除原流程专属测试；保留在线机制检查、评分恢复、版本继承、阶段写权限及公开隔离测试。
- 更新 README、用户范围记录和适配说明，保留历史材料的原始内容并明确其历史性质。

自有生产 Python 从 7,479 行降至 6,055 行，净减少 1,424 行（19.04%，含空行和注释，不含 tests、vendor、依赖及文档）。原上游运行组件另减少 2,064 行。行数仅说明范围，不作为正确性依据。

## 主 agent 审查与修正

1. 保留显式父/改进者参数并在不一致时拒绝，不把错误身份静默归一化。在线版本号必须与父版本一致；使用归档时必须加载该活动版本的真实源码路径。
2. 归档概览只在归档探索者的工具说明中出现；删除原排名能力后不继续向其他角色宣称拥有该权限。
3. 在线成功后的报告错误正常传播；原启动/运行错误、KeyboardInterrupt 和 SystemExit 不被收尾报告错误覆盖。中断不伪装为正常失败或跳过。
4. 报告检查配置/状态对象、协议字段和原运行标记；历史在线报告读取不要求重新认证模型或启动容器。
5. 修正迁移测试中不可达断言、重复目录创建和不一致的提示身份，恢复固定来源缺失/新增/改写三类检查；增加原命令拒绝、身份和报告异常回归。

精简前基准提交：`bc26c15`。测试镜像：`sha256:c7fa8d89f57a3472e158adb50309434cb8bbc5aa1c53d62eed5dfb5b9c245599`；默认 `mini-swe-rsi-runtime:0.1` 标签仍指向原镜像。

## 不变性检查

- 87 个保留资产摘要一致，包括固定 SICA 任务/角色原文及许可证、官方 mini 源码、tokenizer 合同和独立评分锁。
- `online.py`、`recovery.py`、`stage_tools.py`、`context_budget.py`、bridge、relay、Dockerfile、评分锁及 tokenizer 锁共 9 个文件与精简前逐字一致。
- Driver 的 `score/task/finish_task/task_retry/_mechanism_plan/_run_mechanism_process/mechanism_check/candidate_check` 共 8 个方法的 AST 与精简前一致。
- Workflow 状态推进、诊断、候选验收和恢复方法保持；改动限于归档上下文与归档操作分支。固定角色/七步原文、权限派生和模板派生算法保持，传输说明明确更新为在线时间顺序概览，不宣称提示逐字相同。
- 预算合同仅删除无消费者的 `summary_max_requests`，其余角色、completion、工具、单题和模型容量限制一致。
- 控制摘要仍严格冻结；旧在线运行不可直接恢复到精简后的控制实现，不放宽检查或改写历史身份。

## 验证结果

最终全套测试：**301 passed，0 failed，0 skipped，121.19 秒**。包含真实 Docker 在线版本加载、H0 → H1 → H2 接任、两步 skip、恢复及隔离机制检查。存在 1 条 LiteLLM 事件循环弃用提示，未导致失败。

已完成的独立检查：

- 在新临时虚拟环境中从新锁文件离线安装成功，确认四个删除依赖均未安装，在线入口/报告及实际使用的 mini 模块仍可加载。
- 新容器从新 requirements.lock 构建成功；无网络启动时必要库可导入，四个删除依赖均不存在。
- 在临时目录执行干净 bootstrap，固定 SICA、mini、SWE-bench 归档摘要和保留文件核验通过，不重新创建已删除 vendored 组件。
- 宿主锁检查与容器依赖导出一致性检查通过。
- 生产源码可解析，无已删除模块的导入；在线 CLI/报告不间接加载原实验模块。
- 最终测试覆盖的 75 个文件摘要与验收冻结清单一致；87 个保留资产再次核验一致。新增/更新的当前文档链接及 `git diff --check` 通过。

复现命令（正常安装环境）：

```bash
uv sync --locked
uv run pytest -q
docker build -t mini-swe-rsi-runtime:p1-review runtime
RUN_DOCKER_TESTS=1 RSI_TEST_RUNTIME_IMAGE=mini-swe-rsi-runtime:p1-review uv run pytest -q -ra
```

`RSI_TEST_RUNTIME_IMAGE` 只通过测试 fixture 替换测试进程中的镜像查找，不修改生产配置或默认镜像标签。Docker 测试使用本地模拟模型，任务评分使用确定性替身；机制测试实际运行隔离父/候选进程。

## 结论与限制

主 agent 审查、最终全套测试和源码一致性检查通过，接受本次 P1 实现并提交本地 commit，不自动推送。工程验收验证在线行为、隔离与恢复链路，不证明真实模型的自改进收益或跨题泛化。P2/P3 仍按指南后续独立实施；历史运行与真实模型实验不属于本次验证范围。
