# 版本与复现

| 入口 | 用途 |
| --- | --- |
| [`resume-20261116`](https://github.com/yeruoxiangchen/video_studio/tree/resume-20261116) | 简历固定源码：多轮剪辑、译制、媒体交付及 Go 网关接入。保留已发布提交 61d5d42。 |
| [`main`](https://github.com/yeruoxiangchen/video_studio/tree/main) | 最新源码与发布记录；本次追加完整源码清单、针对性检查和历史评测入口。 |
| [本次发布记录](releases/20261116/README.md) | 源码 SHA-256、评测对应关系及发布检查 |
| [操作与成片](demos/README.md) | 文本译制、面板操作和英文成片 |

```bash
git clone https://github.com/yeruoxiangchen/video_studio.git
cd video_studio
git checkout resume-20261116
```

最新版本使用 `git checkout main`。启动与模型配置见 [README](../README.md)。
简历标签保持固定；当前 main 的应用代码与该标签一致，额外整理了发布文档与报告。

[数据摘要](reports/2026-11-16-resume.json) · [既有完整发布检查](reports/2026-11-16-publication.json) · [Go 网关接入](go-gateway.md)。
历史指标的样本、输入链路和原始日期保留在对应评测记录中。
