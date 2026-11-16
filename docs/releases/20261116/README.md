# 2026-11-16 源码发布

本次发布保存多模态视频剪辑 Agent 的完整当前源码：素材理解、混合检索、
LangGraph 多轮剪辑、时间线版本与冲突处理、字幕翻译、配音、审校、FFmpeg 渲染和下载交付。
保留 CamCat 上游来源、MIT 许可及演示素材和音色的独立许可。

## 版本与演示

- [简历固定快照](https://github.com/yeruoxiangchen/video_studio/tree/resume-20261116)
- [最新 main](https://github.com/yeruoxiangchen/video_studio/tree/main)
- [连续面板操作、文本驱动译制与英文成片](../../demos/README.md)
- [源码及 SHA-256 清单](source-manifest.json)、[发布检查](verification.json)

本次相对 11 月 15 日发布新增应用侧 Go 网关配置：带鉴权的模型健康检查、
显式输出 Token 预算、请求编号与用量回执记录，以及重新配置时保留已连接网关的模型角色。
实际凭证与运行数据由本机配置提供。

## 评测与结果入口

| 范围 | 材料 |
| --- | --- |
| 视频检索、AgenticVBench 开发回归与 MSR-VTT 子集 | [公开评测协议及范围](../../public-benchmarks.md) |
| FLEURS 20 条识别/翻译与配音回识别 | [质量记录](../../localization-quality-20261115.md) |
| 4 客户端、10 分钟、8/8 译制及渲染流程 | [原始运行汇总](reliability-summary.json)、[工作负载与性能说明](../../performance-reliability.md) |
| 部署、版本锁及本地启动 | [部署说明](../../local-deployment.md)、根目录 README |

固定开发题与小样本的规模、输入路径和指标分母均在对应报告中记录。
本次发布检查覆盖新增配置和代码完整性，完整业务验收沿用各自冻结记录。
