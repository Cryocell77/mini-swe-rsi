# RSI 机制说明

使用入口见 [README](../README.md)，固定上游与适配见 [上游说明](upstream_adaptations.md)。

## 在线循环

活动版本 `H_v` 同时作为本轮任务执行者、改进者和候选父版本。宿主先运行当前题并独立评分，只将已发生任务的公开反馈交给改进者。

| 阶段 | 执行与约束 |
|---|---|
| 1、2：诊断 | 各取得成功且非空的归档分析角色报告，引用本轮公开证据 |
| 诊断决策 | 协调者显式选择 skip 或 improve；skip 需无可执行改进，improve 需声明实际改进机会 |
| 3：设计 | 评审方案，声明并冻结验收判据及可选机制测试计划 |
| 4：实现 | 编码与本地测试；只有本步允许修改 Harness 源码、配置和测试 |
| 5：检查 | 宿主冻结候选，重新运行当前题并独立评分；存在机制计划时，父子运行同一份冻结测试 |
| 6、7：记录 | 只更新 `description.txt` 和 `agent_change_log.md`，不能修补检查后的运行源码 |
| 宿主决策 | 核对身份、实际执行摘要及验收结果；接受则原子发布下一版本，否则保留原活动版本 |

正常 skip 在两步诊断后结束本轮，不创建接任版本。只有完成步骤 3～7 并通过宿主验收，候选才可接任。接任后下一题及后续改进实际加载新版本源码。

## 检查计划与证据

第 3 步必须选择 `current_task_failure_fixed` 或 `mechanism_parent_failed_candidate_passed`。后者还需声明机制计划，例如：

```json
{
  "criterion": "mechanism_parent_failed_candidate_passed",
  "mechanism_plan": {
    "test_path": "tests/mechanism_check.py",
    "args": [],
    "timeout_seconds": 60
  }
}
```

路径在第 3 步声明，测试在第 4 步实现。宿主将同一测试覆盖到父版本与冻结候选中，各自在只读 `/source` 下执行；源码根由 `RSI_SOURCE_ROOT=/source` 提供，临时文件应写入 `/tmp`。

接任要求当前题得分不退化、满足声明的判据，且最终执行源码摘要与实际检查的候选一致。模型自行报告通过不能替代宿主记录。

有意义的机制测试应通过共同的行为入口验证具体缺陷与修复，父子都能运行到对应断言。仅因父版本不能导入候选新增符号而失败，虽然可能满足当前退出码判据，也不足以证明行为改善。真实收益还需要额外的跨题或持续运行证据。

## 权限、隔离与资源

- 公开归档只包含已发生任务的反馈、诊断、检查摘要和已接任源码；启动时生成只读视图。候选在归档中的副本不是实时工作目录。
- 角色按阶段获得读写权限；源码只能在第 4 步的候选工作区中修改。凭据、未来任务、参考补丁和私有评分资产不进入角色上下文。
- 模型请求经宿主 Broker 固定身份并记录用量。候选端只持有受限会话令牌，候选检查还绑定任务、轮次、父版本及会话。
- 任务工具、机制测试和官方评分使用隔离容器；模型容器通过中继访问宿主服务，不获得 Docker socket 或任意宿主挂载。
- 每角色独立迭代上限：协调者、归档、求解和评审各 500，编码者 1000；每轮最多 5 次 provider 请求。
- 单题作答期限 600 秒；改进无额外全局或阶段墙钟期限。命令声明范围为 5～1800 秒，工具有效上限为 600 秒。
- 改进 shell 输出超过 300,000 字节时落盘并返回索引／预览；超过 8 MiB 时终止工具沙箱，只保留注明不完整的前缀及摘要。工具超时同样清理沙箱。
- 离线循环测试须有显式退出及有限步数；达到 LimitsExceeded 不是成功提交。

## 结构化角色操作

角色使用 `sica-workflow` 后直接跟原始 JSON，例如：

```text
sica-workflow {"op":"read","path":"/home/agent/workdir/README.md","start_line":1,"end_line":40}
sica-workflow {"op":"submit","report":"完整报告正文"}
```

结构化 JSON 不再外包 shell 引号。报告不得自行带完成标记前缀。标准 mini benchmark 提交及编辑角色获准的 shell 操作由各自执行入口处理；权限与可用操作以当前角色提供的工具说明为准。

## 运行产物

| 路径 | 内容 |
|---|---|
| `configuration.json`、`online_task_selection.json` | CLI 冻结的控制／模型／数据身份和开发题序列 |
| `online_state.json` | 当前活动版本、任务与轮次游标、运行状态 |
| `versions/Hxxxx/` | 已发布 Harness 源码及版本身份 |
| `tasks/` | 首次任务的 `attempt_N` 记录和公开反馈；不被候选复测覆盖 |
| `rounds/Rxxxx/` | 诊断、角色报告、工作流状态、声明及冻结的检查计划 |
| `proposals/Pxxxx/` | 候选源码、独立作答／评分与机制对照证据 |
| `candidate_checks/`、`decisions/` | 候选检查状态及不可变宿主决定 |
| `events.jsonl` | 真实调用、工具与状态事件，按发生顺序完整落盘 |
| `private/` | 宿主评分资产及授权等私有记录 |
| `online_report.json`、`online_report.md` | 离线汇总报告 |

`trace.txt` 提供文本轨迹；完整 `trajectory.json` 保持私有。不要将 `.env`、私有轨迹或会话令牌发布给候选或提交到 Git。

## 当前格式与恢复边界

在线和改进合同均为 v8，检查政策 revision 为 2。协议字段使用 `protocol`；机制计划使用 `test_path`、`args`、`timeout_seconds`。任务结果只从 `attempt_N` 读取，宿主完成记录使用完整源码 `code_hash`；执行摘要用于单独核对受测运行代码。

恢复同时核对源码、模型、服务和控制清单。已完成的不可变决定不重做；评分基础设施失败补评原答案，不重新生成答案。已有模型工作流需宿主私有授权才能继续，累计调用与角色迭代继承。恢复仅接受受支持的未完成状态；未知结果的候选检查不自动当作缓存成功或重放。

实现入口：[online.py](../src/mini_swe_rsi/online.py)、[driver.py](../src/mini_swe_rsi/driver.py)、[broker.py](../src/mini_swe_rsi/broker.py)、[workflow.py](../runtime/workflow.py)、[recovery.py](../src/mini_swe_rsi/recovery.py)。
