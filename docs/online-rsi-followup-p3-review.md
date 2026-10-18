# 后续精简两个 P3 项验收

实施：2026-10-18；验收：2026-10-19。基点：`ac08a70`。执行依据：[后续精简执行计划](online-rsi-followup-simplification-plan.md)。本轮处理用户追加授权的两个 P3 项：生成包附带组件及角色提交格式。

## 分工与最终实现

三个子 agent 均使用 **GPT-5.6 Luna / High**，分别实现新 H0 包裁剪、角色结构化传输、相关测试迁移；主 agent 独立审查、修正、测试、验收并提交本地 commit。子 agent 不提交、不推送，不启动完整 Docker 验收或真实模型实验。

1. **生成包裁剪**：完整复制固定 mini 包并应用原计费删除补丁后，按新增 `mini-online-slim-v1` 清单移除 `run/**`、`config/benchmarks/**` 和包内 `__main__.py`，共 23 个文件。清单绑定上游提交与补丁后摘要，并与从原锁／计费清单推导的允许删除映射精确比较；全部目标验证后才删除。整个过程继续在临时 seed 中完成，失败不发布 H0，既有归档直接返回，不重新裁剪 evolved candidates。
2. **角色传输**：结构化命令统一为 `sica-workflow` 后直接跟一个原始 JSON 对象，删除 shell quoted JSON 解析和 `shlex` 依赖，删除受限角色 echo/printf 提交分支。所有角色以 `submit/report` 结束，由既有 `_structured_submission` 输出 mini 完成标记；其完整报告和已带标记报告的内容处理保持。角色说明、格式错误提示及测试／模拟模型一起迁移。标准 mini benchmark 的 echo 提交与编辑角色允许步骤中的普通 shell 保留。

在线协议与 improvement 合同升级为 **v7**；资源政策 v2、检查 revision 2 和实际限制不变。删除清单纳入控制摘要。旧 v2～v6 运行保留，使用对应 Git 版本读取，不迁移、不重新报告。

## 支持范围与独立审查

- 在线根归档入口继续通过不可变 bridge 加载真实 mini `DefaultAgent`、`LitellmTextbasedModel` 和配置。生成包不再提供 `python -m minisweagent`、独立 mini CLI、批量 benchmark 或轨迹查看工具；宿主 `benchmark/improve` 不受影响。
- 其余 51 个包文件全部保留，与基点应用计费补丁后的内容逐字一致，包括所有 agent/model/environment/config/utils 模块、其他配置与模型注册映射。候选离线测试仍使用固定依赖范围内的这些 API，不扩大到依赖安装或隐藏模型入口。
- 保留文件没有对被删除 runner 的静态导入或动态注册依赖。`get_config_path()` 仍包含可选的 benchmark 目录查找：目录缺失时 `.exists()` 正常跳过；这是保留的配置 API 搜索规则，不是继续提供被删除的自带配置。实测 `mini_textbased.yaml` 可加载，`swebench` 自带配置不可用。
- 主 agent 合并了初版 seed 的重复验证辅助函数，移除未使用的可选参数与重复读锁；同一个精确映射同时验证删除集合和 post-billing 摘要。补充清单版本、上游提交、目标输入摘要错误时不得发布的回归。
- 修正初版提交拒绝测试的假阳性：除了包装命令和带副作用尾缀，还使用真正的裸 echo、含实际换行的裸 printf、纯 shell quoted JSON。与基点执行对照确认 **6 个原先接受的用例全部变为拒绝**，并验证输出不能触发 mini `Submitted`。
- 补齐用于恢复和版本加载测试的短路径模型替身迁移：该替身仅用于 improve，固定返回 raw JSON `submit/report`，不增加模式开关或按提示猜测用途。完整闭环替身中的 benchmark 分支继续使用标准 mini echo 提交；不放宽角色传输、调用限额、恢复状态或源码断言。
- 原始 JSON 内的换行、单双引号、Unicode、反引号和 shell-looking 文本保持数据语义；已有报告不被 shell 解释。历史模型响应 fixture 不修改。所有角色在裁剪后仍有明确 raw JSON 说明，原 SICA 提示核心、角色权限与七步定义保留。
- AST 对照确认工作流只改 `execute/instructions/role_templates`；`_structured_submission`、状态恢复、委员会、检查计划、角色权限和七步调度未变。Driver 只改 H0 元数据，初始化发布流程主体未变；其余宿主改动限于合同版本及控制清单。预算、计量、评分、隔离、输入完整性和故障处理不改。

## 验证与减量

主 agent 集成定向回归 **149 项通过**；加强生成包真实进程验证后，seed／提交专测 **42 项通过**。真实进程明确校验导入来自新 seed，加载配置、构造真实 TextbasedModel／DefaultAgent／LocalEnvironment，再用保留的 DeterministicModel 完成一次两步离线 agent 运行；显式设置有限步数，最终断言 `Submitted` 与本地命令观察结果，不连接推理服务。

两个短路径模型替身用例定向 Docker 复验 **2 项通过**。最终冻结代码全量验收为 **411 passed、0 failed、0 skipped，135.16 秒**；仅有既存 LiteLLM 无当前事件循环的弃用警告。Docker 使用既有缓存镜像 `mini-swe-rsi-runtime:p1-review`（`sha256:c7fa8d89f57a3472e158adb50309434cb8bbc5aa1c53d62eed5dfb5b9c245599`），模型与评分为确定性替身；覆盖实际归档加载、两代接任、七步与 skip、独立候选／机制检查、评分与授权恢复、角色终止、公开过滤及隔离。不启动额外真实模型实验。

减量使用两个独立口径：

- **生成 Harness 包**：从 74 个文件降至 51 个文件；Python/YAML 从 **6,633 行降至 4,240 行，减少 2,393 行（约 36.1%）**，另移除 10 行 runner README 文本。保留文件内容完全不变。固定原始上游没有删除这些文件。
- **自有生产 Python**：从 **6,060 行变为 6,097 行，净增加 37 行**，包含空行与注释，不含生成包、测试、固定上游、bootstrap 或文档；主要新增可审计 seed 裁剪与失败保护，同时删除旧传输解析。不能将生成包减量冒称为仓库源码净删行。

固定 mini 原始 74 个文件、SICA 原文、既有计费删除补丁及清单、tokenizer 与依赖锁保持字节一致；数据、`.env`、历史运行及已有候选未修改。源码／测试／资产快照在最终测试、暂存和提交间核对一致，既有 `.DS_Store` 不纳入提交；消息遵循 `refactor: ...` 格式，不推送。
