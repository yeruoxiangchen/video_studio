# Go 网关接入

视频工作台通过独立的 [Go 大模型网关](https://github.com/yeruoxiangchen/llm_gateway/tree/resume-20261116) 调用剪辑规划及字幕翻译。
视觉分析、向量编码、重排、ASR、TTS 和 FFmpeg 继续使用各自接口。

在环境配置中指定网关 Chat 地址、账户 Key、规划/翻译模型别名，并设置
`CAMCAT_LLM_HEALTH_PATH=/v1/models`、`CAMCAT_LLM_MAX_OUTPUT_TOKENS=4096`。
Key 保存在本机 `.env`，回执保存目录由 `CAMCAT_GATEWAY_RECEIPTS_DIR` 指定，均不提交 Git。
完整配置和可复现验收命令见网关仓库的 [接入说明](https://github.com/yeruoxiangchen/llm_gateway/blob/resume-20261116/docs/video-studio.md)。

2026-11-16 实际验收包含剪辑与英文译制两条浏览器旅程，均通过。11 次模型调用的应用回执、
网关请求及账户记录均为 28,214 Token，残余预占为 0；逐请求 ID 和用量一致。
这是一次既有真实运行的记录，原始本地配置与视频不随报告发布。

[验收数据及简历指标范围](reports/2026-11-16-resume.json) · [版本选择](versions.md)
