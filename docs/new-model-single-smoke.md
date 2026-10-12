# 新模型单次自改进检查（2026-10-13）

用户授权使用新.env尝试；新模型为SenseNova认证接口的glm-5.2。仅一次H0提案，有运行源码修改才测开发题django__django-14752；不重跑基线/val/test，不追加失败提案。沿用1800秒独立诊断保护，不改写旧24小时实验。

## 必要兼容及审查

原预检0推理：服务/models返回context_length/max_output_length，与现有context_window/max_output_tokens名称不同，误回落Ollama查询404。broker新增仅token.sensenova.cn的显式映射，验证正整数、相互约束及标准字段冲突；冻结原能力、支持功能/采样参数、量化和价格声明。没有修改.env、模型策略、七步、角色权限、归档选择或预算作用域。

认证目录声明context1048576、output131072、quantization=fp8，未公布权重摘要；模型名不能证明实际权重字节。所有角色统一同模型、temperature请求0、思考使用服务默认。远程服务没有已验证tokenizer，无Qwen的本地发送前计数/明显输入缺失保证；由服务限制及实际usage审核，不能声称输入精确保留。

价格目录prompt/completion/input_cache_read/image/request均显式0；只接受完整全零合同避免猜币种/单位，额外收费字段或非零/缺失均保持未知并拒绝benchmark推理。报告零费率是服务声明，不代表独立账单核验。OpenAI兼容形式参考[官方API说明](https://github.com/OpenSenseNova/SenseNova6.8/blob/main/API.md)，此文对应另一模型，GLM容量仅据本次认证目录，不外推文中其他模型能力。

主agent已审查diff：仅服务兼容，固定SICA5控制/9工作流/7预算及mini74共95项源码摘要相同。原SICA已有多provider模型调用抽象，本次是mini宿主broker的新服务适配，不能称原SICA整套runtime原样运行。此前v6工具边界和已披露差异不变；本次同时改变模型、服务和容量，不能据单次结果作纯模型因果结论。

定向37项验收通过，连续两次真实metadata核验一致、0推理。工程和真实结果于完成后追加；本机产物目录runs/new-model-single-20261013。

完整含Docker模拟端点验收127 passed/119.46秒，失败0（1条依赖弃用警告）。src/runtime/tests冻结摘要一致。接受本次兼容，提交后仅一次真实提案，不追加模型采样。

## 提交后真实结果

代码38c2db1；生产scripts/smoke_once.py在全新runs/new-model-single-20261013/real-smoke执行一次。638.96秒（10分39秒），在1800秒上限前自然失败退出；不是时间预算或500/1000角色额度耗尽。进程退出1，单次检查runner正常保留失败结果退出0；smoke_result.completed仅代表检查流程完成，不代表自改进成功。

| 指标 | 结果 |
|---|---:|
| 实际模型请求 | 41 |
| 有usage正常响应 | 38（全部finish_reason=stop） |
| 未取得usage请求 | 3 |
| 已计量prompt / completion | 2,528,628 / 12,809 |
| 已计量总tokens（含缓存） | 2,541,437 |
| cached tokens | 2,319,872 |
| 最大单次prompt / completion | 191,226 / 3,803 |
| 主协调者迭代 | 17 |
| 三次归档角色迭代 | 18 / 1 / 5 |
| 七步完成 | 1/7 |
| 源码变化 | 无 |
| 预定开发题 | 跳过：无运行源码变化 |

首次归档角色成功提交报告，主协调者使用绑定完成接口完成第一步。随后主模型再次委派归档角色，第二个归档角色的首请求在166.08秒后RemoteDisconnected；模型又委派第三个归档角色，其第5请求HTTPError，主协调者下一请求也HTTPError并最终BadRequestError退出。该三次委派均来自同一个提案内模型动作，没有追加提案或宿主失败重采样。实际未启动software_developer/general_problem_solver/reviewer，未进入源码编辑阶段；父候选原始及行为摘要完全相同。

结构操作错误另有一次Invalid escape和一次No closing quotation；其后模型成功委派并完成第一步，故这两项是可恢复的文本工具格式错误，不是最终退出原因。模型请求没有length截断，宿主无context/time/iteration拒绝；远程无可信tokenizer，本轮没有输入精确保留证明。

**判断：本轮被连接/API失败打断，未检验到源码自改进，不能据此认定GLM能力不足或RSI有效。** broker现有通用错误处理只保留HTTPError类名（兼容层映射为BadRequestError），未保存这两次HTTP状态/响应原因，因此不能确定限流、参数拒绝或服务端故障，也不能把BadRequestError字面等同于上游HTTP400。缺失的3次usage/cost不可补算；服务目录公布零费率，不等于核实了实际账单或未计量调用为零。后续诊断需要保留安全的状态码与错误码，并查服务端请求记录；本轮不额外发推理来猜原因。

结束模型及服务身份检查通过；源冻结及95项上游摘要一致，旧Qwen843、DeepSeek920、修复后Qwen841个封存文件全部未变。仅一次proposal启动，无baseline/val/test/晋升，容器已清理。independent_audit.json和sealed_inventory.json保留本机，可复核。工程127项验收证明本次接口兼容可运行，不证明模型权重身份、推理输入完整或自改进收益。
