# 版本与复现

| 入口 | 用途 |
| --- | --- |
| [`resume-20261116`](https://github.com/yeruoxiangchen/video_studio/tree/resume-20261116) | 简历对应的固定源码：多轮剪辑、译制、媒体交付及 Go 网关接入。 |
| [`main`](https://github.com/yeruoxiangchen/video_studio/tree/main) | 最新发布代码。2026-11-16 本次上传与简历标签使用同一份应用代码。 |

```bash
git clone https://github.com/yeruoxiangchen/video_studio.git
cd video_studio
git checkout resume-20261116
```

最新版本使用 `git checkout main`。启动、真实模型配置与数据目录见 [README](../README.md)。
既有录屏和英文成片保留在 [docs/demos](demos/README.md)。

指标、样本范围和真实网关接入记录见 [数据摘要](reports/2026-11-16-resume.json) 与 [Go 网关接入](go-gateway.md)。
MSR-VTT 数字属于编码器检索子集；AgenticVBench/FLEURS 数字沿用已记录的固定测试，
各自协议、输入链路和原始日期见原有评测文档。代码冻结与上传不重新计算模型指标。
