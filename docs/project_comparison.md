# 项目选型与参考边界

这是一份实现选型记录。搜索算法首轮只采用 SICA，不把多个项目拼接成一个未经验证的新控制器。星数随时间变化，不用于替代机制与代码证据。

| 项目与一手来源 | 与本实验有关的机制 | 本项目采用范围 |
|---|---|---|
| [SICA](https://github.com/MaximeRobeyns/self_improving_coding_agent) | 归档统计、父版本选择、最佳归档 agent 执行改进、候选复制与后续评测 | 固定提交的 runner、ArchiveAnalyzer、BenchmarkTracker 逐字保留；只适配 agent/model/data/执行边界 |
| [Frontis OpenRSI](https://github.com/FrontisAI/OpenRSI) | 执行反馈与谱系；系统收益与模型收益需要分开；主要演化 MLE 程序 | 参考实验记录和收益分离，不移植训练或程序搜索算法 |
| [OpenRSI-Harness](https://github.com/OpenRSI-Foundation/OpenRSI-Index/tree/main/RSI-Harness) | 工作环境与独立评分环境；提交和评判基础设施 | 采用隔离原则，不把提交评分设施等同于递归改进 agent |
| [ModularRSI](https://github.com/IQuestLab/ModularRSI) | Harness 模块边界、候选验证；Harbor 集成 | 参考允许修改范围与候选验证；首轮不移植其固定编辑器组合 |
| [ScienceBuddy](https://github.com/Gen-Verse/ScienceBuddy) | 小模型候选与父子比较，外层训练与内层改进组成嵌套流程 | 采用配对比较思路；本实验不训练，也不引入独立更强改进模型 |
| [Exo](https://github.com/exoharness/exo)、[Raven](https://github.com/EverMind-AI/Raven) | 行为执行、回退、审计；较大的运行时和平台 | 参考行为证据与审计，不采用其整个平台 |
| [LightRSI](https://github.com/zjunlp/LightRSI) | 上下文与运行时组织 | 只作为门槛通过后的工程精简参考，不提前加到 H0 |
| [mini-rsi](https://www.mini-rsi.com/)、[Dream-RSI](https://github.com/zhengkid/Dream-RSI) | 改进能力测量、历史回放 | 仅方法参考；调研时前者代码链接不可获取，后者完整代码待发布 |

选择 SICA 的关键原因是可在固定模型下实际启动归档中的改进 agent，并保留其原生选择机制。首轮实验仍须证明两代行为变化和演化改进者执行；框架支持递归不能替代本次运行证据。

官方 mini-swe-agent 的固定提交和 SICA 固定提交见根目录 upstream.lock.json。所有必要差异与许可记录见 upstream_adaptations.md。
