# 上游来源与适配

本页说明当前在线 RSI 的来源、固定方式及实际适配范围。使用方式见 [README](../README.md)，执行规则见 [RSI 机制说明](architecture.md)。精确版本、文件与归档摘要以 [upstream.lock.json](../upstream.lock.json) 为准。

## 固定来源

| 组件 | 来源仓库 | 固定提交 | 当前用途 |
|---|---|---|---|
| SICA | `MaximeRobeyns/self_improving_coding_agent` | `ed8275dca4d3c5dbf77229964351fe9b424797dc` | 改进任务原文、七步定义、角色说明、委员会及预算来源 |
| mini-swe-agent | `SWE-agent/mini-swe-agent` | `04d809ceab9df28f9adaed044884180159172930` | 生成初始 Harness；实际在线 agent、model、environment、config 与 utils API |
| SWE-bench | `SWE-bench/SWE-bench` | `726c5461e2ef52d83cf1ea2107870a8bb3328d57` | 独立官方评分资产 |

[scripts/bootstrap.py](../scripts/bootstrap.py) 按锁定归档下载、校验并同步保留的文件。`vendor/sica/` 和 `vendor/sica_workflow/` 保留实际使用的上游源码与 MIT 许可证；mini 与 SWE-bench 的固定原文下载到 `.upstream/`。生成的 Harness 包含 mini 许可证，不能因精简文档而删除许可证或固定来源清单。

## 初始 Harness

[driver.py](../src/mini_swe_rsi/driver.py) 从固定 mini-swe-agent 2.4.6 原文构建 H0000，再通过 [harness_seed.py](../src/mini_swe_rsi/harness_seed.py) 应用可复现适配：

1. [mini-no-billing.patch](../src/mini_swe_rsi/assets/mini-no-billing.patch) 及其 [摘要清单](../src/mini_swe_rsi/assets/mini-no-billing.json) 删除价格计算、金额累计、费用配置、相关模板变量与轨迹字段。
2. [mini-online-slim.json](../src/mini_swe_rsi/assets/mini-online-slim.json) 定义要移除的独立 mini CLI、批量 runner 和附带 benchmark 配置。
3. 增加归档根入口、许可证、描述及变更日志，通过固定桥接器提供在线任务与改进执行。

保留的 mini API 可供候选离线测试使用。生成包不再提供独立 `python -m minisweagent` CLI；已有候选不会被重新裁剪。上游原始快照与生成后的可演化 Harness 分开保存。

## 在线适配边界

| 边界 | 当前实现 |
|---|---|
| 调度 | 宿主顺序执行固定开发题；不调用原 SICA 批量 runner、冠军选拔或轨迹回放流程 |
| 改进流程 | 从固定 SICA 原文提取七步、角色与委员会，通过当前文本操作协议运行；两步诊断后的 skip/improve、检查计划及版本接任由在线宿主管理 |
| 模型 | 所有角色统一调用 `.env` 指定的模型，经宿主服务冻结身份与容量、完整记录真实用量 |
| 环境 | 固定依赖镜像；候选在已有依赖内编辑源码、配置和测试，不自动安装候选声明的新依赖 |
| 数据 | 使用已批准的数据清单及镜像库存；CLI 按开发题原顺序冻结前缀 |
| 评分 | 宿主独立调用固定 SWE-bench 评分器；候选不接触参考补丁或私有测试 |
| 证据 | 首次作答、候选重测和机制对照分开保存，身份与源码摘要绑定到实际执行记录 |

评分包装器 [grader.py](../src/mini_swe_rsi/grader.py) 复用本地旧项目的评分适配资产，并实现冻结镜像身份、空补丁处理、官方超时分类与评分隔离；不复用其搜索或演化控制器。补丁从任务镜像的初始 HEAD 提取，避免把镜像准备差异计入候选答案。

这里运行的是经过明确适配的在线框架，不是未修改的 SICA 原批量实验。框架接任说明当前宿主判据通过；它不自动构成跨任务泛化、效率提升或长期 RSI 收益的证明。
