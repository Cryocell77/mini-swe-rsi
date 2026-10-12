# 新模型单次自改进检查（2026-10-13）

用户授权使用新.env尝试；新模型为SenseNova认证接口的glm-5.2。仅一次H0提案，有运行源码修改才测开发题django__django-14752；不重跑基线/val/test，不追加失败提案。沿用1800秒独立诊断保护，不改写旧24小时实验。

## 必要兼容及审查

原预检0推理：服务/models返回context_length/max_output_length，与现有context_window/max_output_tokens名称不同，误回落Ollama查询404。broker新增仅token.sensenova.cn的显式映射，验证正整数、相互约束及标准字段冲突；冻结原能力、支持功能/采样参数、量化和价格声明。没有修改.env、模型策略、七步、角色权限、归档选择或预算作用域。

认证目录声明context1048576、output131072、quantization=fp8，未公布权重摘要；模型名不能证明实际权重字节。所有角色统一同模型、temperature请求0、思考使用服务默认。远程服务没有已验证tokenizer，无Qwen的本地发送前计数/明显输入缺失保证；由服务限制及实际usage审核，不能声称输入精确保留。

价格目录prompt/completion/input_cache_read/image/request均显式0；只接受完整全零合同避免猜币种/单位，额外收费字段或非零/缺失均保持未知并拒绝benchmark推理。报告零费率是服务声明，不代表独立账单核验。OpenAI兼容形式参考[官方API说明](https://github.com/OpenSenseNova/SenseNova6.8/blob/main/API.md)，此文对应另一模型，GLM容量仅据本次认证目录，不外推文中其他模型能力。

主agent已审查diff：仅服务兼容，固定SICA5控制/9工作流/7预算及mini74共95项源码摘要相同。原SICA已有多provider模型调用抽象，本次是mini宿主broker的新服务适配，不能称原SICA整套runtime原样运行。此前v6工具边界和已披露差异不变；本次同时改变模型、服务和容量，不能据单次结果作纯模型因果结论。

定向37项验收通过，连续两次真实metadata核验一致、0推理。工程和真实结果于完成后追加；本机产物目录runs/new-model-single-20261013。

完整含Docker模拟端点验收127 passed/119.46秒，失败0（1条依赖弃用警告）。src/runtime/tests冻结摘要一致。接受本次兼容，提交后仅一次真实提案，不追加模型采样。
