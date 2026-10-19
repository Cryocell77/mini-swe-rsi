# 上游与适配

## 代码来源

| 组件 | 仓库 | 固定提交 | 用途 |
|---|---|---|---|
| SICA | `MaximeRobeyns/self_improving_coding_agent` | `ed8275dca4d3c5dbf77229964351fe9b424797dc` | 改进任务、七步定义、角色、委员会与预算来源 |
| mini-swe-agent | `SWE-agent/mini-swe-agent` | `04d809ceab9df28f9adaed044884180159172930` | 初始 Harness 的 agent、model、environment、config 和 utils |
| SWE-bench | `SWE-bench/SWE-bench` | `726c5461e2ef52d83cf1ea2107870a8bb3328d57` | 官方评分资产 |

归档与文件摘要保存在 [upstream.lock.json](../upstream.lock.json)。[bootstrap.py](../scripts/bootstrap.py) 下载上游归档，校验后将使用的 SICA 文件同步至 `vendor/sica/` 和 `vendor/sica_workflow/`；mini 与 SWE-bench 源码位于 `.upstream/`。

SICA 的 MIT 许可证保留在 vendor 目录中，生成的 Harness 附带 mini 许可证。其余上游许可保留在下载的源码内。

## H0000 的构建

[driver.py](../src/mini_swe_rsi/driver.py) 从 mini-swe-agent 2.4.6 创建 H0000。[harness_seed.py](../src/mini_swe_rsi/harness_seed.py) 在副本上应用两项处理：

1. 用 [mini-no-billing.patch](../src/mini_swe_rsi/assets/mini-no-billing.patch) 删除价格计算、金额累计及费用相关配置、模板变量和轨迹字段；修改前后的摘要记录在[补丁清单](../src/mini_swe_rsi/assets/mini-no-billing.json)。
2. 按 [mini-online-slim.json](../src/mini_swe_rsi/assets/mini-online-slim.json) 移除独立 mini CLI、批量 runner 和附带 benchmark 配置。

归档根入口通过项目桥接器执行任务和改进。保留的 mini API 可供候选离线测试使用，独立 `python -m minisweagent` CLI 已移除。这些处理只用于创建 H0000，不重写已有候选。

## 与上游的主要差异

本项目用宿主在线循环替代 SICA 批量 runner。每题完成后诊断、改进和检查，通过后发布下一版本；七步定义与角色说明取自保留的 SICA 原文，通过文本操作协议执行。

所有角色调用 `.env` 中指定的同一个模型。候选使用现有依赖镜像，可以编辑源码、配置和测试，不能安装新依赖。数据来自固定清单，CLI 按开发题顺序选取前 N 题。

[grader.py](../src/mini_swe_rsi/grader.py) 复用本地旧项目的评分适配代码，增加镜像身份校验、空补丁处理、超时分类和容器隔离。它调用 SWE-bench 官方评分资产；补丁相对任务镜像的初始 HEAD 提取，排除镜像准备提交带来的差异。

首次作答、候选复测和机制对照分别保存。执行规则与产物布局见 [RSI 运行机制](architecture.md)。
