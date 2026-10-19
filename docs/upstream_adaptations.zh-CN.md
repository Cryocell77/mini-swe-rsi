# 上游与适配

[English](upstream_adaptations.md) | 简体中文

## 代码来源

| 组件 | 仓库 | 固定提交 | 用途 |
|---|---|---|---|
| SICA | `MaximeRobeyns/self_improving_coding_agent` | `ed8275dca4d3c5dbf77229964351fe9b424797dc` | 改进任务、七步定义、角色、委员会与预算来源 |
| mini-swe-agent | `SWE-agent/mini-swe-agent` | `04d809ceab9df28f9adaed044884180159172930` | 初始 Harness 的 agent、model、environment、config 和 utils |
| SWE-bench | `SWE-bench/SWE-bench` | `726c5461e2ef52d83cf1ea2107870a8bb3328d57` | 官方评分资产 |

来源版本与集成源码摘要保存在 [upstream.lock.json](../upstream.lock.json)。所需源码已经纳入 `src/mini_swe_rsi/`，安装和测试无需运行下载脚本。

- `harness/template/minisweagent/`：预先精简的 mini 运行源码；`harness/LICENSE.mini` 保留其许可证。
- `workflow/task/` 和 `workflow/sources/`：SICA 任务、角色与七步定义原文，附 MIT 许可证。运行目录不再包含未使用的上游 agent、provider 实现或预算参考副本。
- `evaluation/swebench/`：SWE-bench 实现；包内导入改为 `mini_swe_rsi.evaluation.swebench`，数据集名称、镜像名称与评分配方保持原样。许可证位于 `evaluation/LICENSE.swebench`。

## H0000 的构建

[driver.py](../src/mini_swe_rsi/driver.py) 直接复制 `harness/template/minisweagent/` 中预先精简的 mini-swe-agent 2.4.6 源码来创建 H0000。源码已移除金额计费、独立 mini CLI、批量 runner 和附带 benchmark 配置。文件摘要保存在 [upstream.lock.json](../upstream.lock.json) 的 `mini.seed_files` 中。

初始化只校验摘要、复制源码并添加归档根入口及元数据，不在运行时打补丁或删除文件，也不重写已有候选。根入口通过项目桥接器执行任务和改进。保留的 mini API 可供候选离线测试使用，独立 `python -m minisweagent` CLI 不再保留。

## 与上游的主要差异

本项目用宿主在线循环替代 SICA 批量 runner。每题完成后诊断、改进和检查，通过后发布下一版本；七步定义与角色说明取自保留的 SICA 原文，通过文本操作协议执行。

所有角色调用 `.env` 中指定的同一个模型。候选使用现有依赖镜像，可以编辑源码、配置和测试，不能安装新依赖。数据可来自 Hugging Face 或本地 SWE-bench 记录，CLI 按所选 split 的顺序执行；任务和镜像身份在每个运行中冻结。

[grader.py](../src/mini_swe_rsi/evaluation/grader.py) 复用本地旧项目的评分适配代码，增加镜像身份校验、空补丁处理、超时分类和容器隔离。它调用 SWE-bench 官方评分资产；补丁相对任务镜像的初始 HEAD 提取，排除镜像准备提交带来的差异。

首次作答、候选复测和机制对照分别保存。执行规则与产物布局见 [RSI 运行机制](architecture.zh-CN.md)。
