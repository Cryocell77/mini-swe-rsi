# RSI 运行机制

## 一轮如何执行

活动版本 `H_v` 先完成当前题，宿主独立评分，再将执行轨迹和公开反馈交给同一个版本分析。改进者从 `H_v` 复制出候选，按 SICA 七步执行：

| 步骤 | 内容 |
|---|---|
| 1、2：诊断 | 归档分析角色分别提交报告，引用本题公开证据 |
| 3：设计 | 评审改进方案，确定验收判据与测试计划 |
| 4：实现 | 修改候选源码、配置及测试，运行本地检查 |
| 5：检查 | 宿主保存候选快照，重新执行当前题并独立评分，运行声明的机制测试 |
| 6、7：记录 | 更新 `description.txt` 和 `agent_change_log.md` |

步骤 1、2 结束后，协调者选择 `skip` 或 `improve`。`skip` 结束本轮；`improve` 需要列出改进机会，继续步骤 3～7。只有步骤 4 允许修改运行代码。

宿主在七步结束后核对检查结果和源码摘要，决定是否发布 `H_(v+1)`。接受后，下一题及后续改进加载新版本；拒绝则继续使用 `H_v`。

## 候选检查

步骤 3 选择以下一种判据：

- `current_task_failure_fixed`：修复当前题的失败。
- `mechanism_parent_failed_candidate_passed`：当前题得分不退化，同一机制测试在父版本失败、候选通过。

机制判据还需声明测试计划：

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

测试路径在步骤 3 确定，测试代码在步骤 4 编写。宿主将同一份测试覆盖到父版本与候选中，分别在只读 `/source` 下执行。测试可通过 `RSI_SOURCE_ROOT` 获取源码根目录，临时文件写入 `/tmp`。

检查后的运行源码不得再修改。宿主比较受测候选和最终候选的执行摘要，防止接任未经测试的代码。

机制测试应从父子共有的行为入口验证缺陷。如果父版本只是因为缺少新增函数而导入失败，现有退出码判据仍可能通过，但这不能说明行为得到改善。测试还需要检查预期结果，而非只检查新增字段或符号是否存在。

## 数据要求

用 `prepare` 从 Hugging Face 或本地 JSON／JSONL 创建数据目录。`manifest.json` 记录数据来源、原始文件摘要、各 split 的任务顺序和默认 split；`raw.json` 保存 SWE-bench 任务记录。任务数和 split 名称没有固定限制。

```text
data/
├── manifest.json
└── raw.json
```

`online --split NAME` 选择 split，`--task-limit N` 选择前 N 题。任务顺序和选择范围在运行目录的 `online_task_selection.json` 中冻结；同一运行不能更换 split、重排或扩充任务。原始数据或清单改变后，恢复也会拒绝继续。

运行只准备选中任务的官方 linux/amd64 镜像，优先使用本地缓存，否则拉取镜像。解析得到的镜像 ID 与 registry digest 写入 `image_inventory.json`；恢复沿用该记录，不重新解析 latest 标签。

现有的 `images/` 镜像元数据和 `preflight/` 记录也可保留：清单声明了预检证据时，加载器会核对摘要。它们不再是所有新数据集的必需文件，也不再要求固定 30/30/100 划分。

未指定 `--data` 时，仍使用本地资产位置 `/Users/maxwell/Documents/mini-swe-rsi-old/data/swe-bench-lite-x86-recovery-validated`。其默认 split 是 dev。新准备的数据使用所指定 split 作为默认值。

## 隔离与权限

角色可以读取已发生任务的公开反馈、诊断、检查摘要和已接任源码。公开归档在启动时复制为只读视图，实时候选位于 `/home/agent/workdir`。

源码编辑权限仅在步骤 4 开放；步骤 6、7 只能写描述和变更日志。候选不接触模型密钥、未来任务、参考补丁或私有评分资产。

模型调用经宿主 Broker 转发。容器使用受限会话令牌，候选检查绑定任务、轮次、父版本及会话。任务工具、机制测试和官方评分在隔离容器内执行，模型容器通过中继访问宿主，不挂载 Docker socket。

## 资源限制

| 项目 | 限制 |
|---|---|
| 协调者、归档、求解及评审角色 | 每次角色调用最多 500 次迭代 |
| 编码角色 | 每次角色调用最多 1000 次迭代 |
| 一轮 completion | 最多 5 次 provider 请求 |
| 单题作答 | 600 秒 |
| 改进流程 | 无额外全局或阶段墙钟期限 |
| 工具命令 | 声明范围 5～1800 秒，实际最多 600 秒 |
| 模型上下文与输出 | 从认证服务元数据读取容量 |

改进 shell 输出超过 300,000 字节时保存到文件，返回索引和预览；超过 8 MiB 时终止沙箱，仅保存标有“不完整”的前缀及摘要。工具超时同样会清理沙箱。

离线 agent 循环测试需要显式退出，并设置有限步数。`LimitsExceeded` 表示额度耗尽，不能作为成功提交。

## 角色操作

结构化操作使用 `sica-workflow` 加原始 JSON：

```text
sica-workflow {"op":"read","path":"/home/agent/workdir/README.md","start_line":1,"end_line":40}
sica-workflow {"op":"submit","report":"完整报告正文"}
```

JSON 对象不加外层 shell 引号，报告正文不带完成标记。其他操作及 shell 权限由当前角色的工具说明给出。

## 文件布局

| 路径 | 内容 |
|---|---|
| `configuration.json`、`online_task_selection.json` | 配置身份与 CLI 选定的题目序列 |
| `online_state.json` | 活动版本、游标和运行状态 |
| `versions/Hxxxx/` | 已发布源码及版本身份 |
| `tasks/` | 首次作答的 `attempt_N` 记录和公开反馈 |
| `rounds/Rxxxx/` | 诊断、角色报告、工作流状态及检查计划 |
| `proposals/Pxxxx/` | 候选源码、复测结果和机制对照 |
| `candidate_checks/`、`decisions/` | 检查状态和宿主决定 |
| `events.jsonl` | 模型调用、工具调用和状态事件 |
| `private/` | 评分资产和恢复授权等宿主私有记录 |
| `online_report.json`、`online_report.md` | 汇总报告 |

首次作答保留在 `tasks/`，候选重测保留在 `proposals/`，两者分开统计。文本轨迹为 `trace.txt`，完整 `trajectory.json` 保持私有。`.env`、私有轨迹和会话令牌不应提交到 Git。

## 中断恢复

重新运行相同目录会检查题目、源码、模型、服务及控制代码是否匹配。已提交的决定不重做；评分基础设施失败时补评原补丁，不重新生成答案。

未完成的模型工作流需要 `--resume-checkpoint PATH`。PATH 必须是本运行 `private/` 下的宿主授权文件，绑定原工作流状态、候选源码摘要、运行身份、控制清单及累计用量。恢复会创建新模型上下文，并继承调用和角色迭代计数。具体支持的阶段与字段见 [recovery.py](../src/mini_swe_rsi/recovery.py)。

当前协议为 `mini-swe-rsi-online-v8` 和 `sica-mini-improvement-v8`，检查政策 revision 为 2。协议字段为 `protocol`，机制计划字段为 `test_path`、`args`、`timeout_seconds`。宿主任务记录使用完整源码 `code_hash`，执行摘要另用于比对实际受测代码。

## 代码结构

`src/` 下只提供 `mini_swe_rsi` 包：

```text
src/mini_swe_rsi/
├── harness/       # 初始 Harness 模板、生成逻辑与许可证
├── workflow/      # 七步状态机、桥接器、角色定义与来源记录
├── evaluation/    # 隔离评分入口与项目内 SWE-bench 实现
└── ...            # 在线调度、模型服务、恢复与报告
```

`harness/template/minisweagent/` 是生成候选的源码模板，不作为宿主顶层包加载。每代候选仍有独立的 `minisweagent` 包及源码身份。评分实现使用 `mini_swe_rsi.evaluation.swebench` 导入，不依赖外部同名安装包。根目录 `runtime/` 只保留容器 Dockerfile 和依赖锁。

### 实现入口

- [online.py](../src/mini_swe_rsi/online.py)：在线调度、检查与接任。
- [driver.py](../src/mini_swe_rsi/driver.py)：容器执行与候选重测。
- [broker.py](../src/mini_swe_rsi/broker.py)：模型服务、用量及会话。
- [workflow.py](../src/mini_swe_rsi/workflow/workflow.py)：七步状态机与角色操作。
