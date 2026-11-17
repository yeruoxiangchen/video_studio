# 版本与复现

| 入口 | 用途 |
| --- | --- |
| [`resume-20261116`](https://github.com/yeruoxiangchen/video_studio/tree/resume-20261116) | 简历固定源码：多轮剪辑、译制、媒体交付及 Go 网关接入。保留已发布提交 61d5d42。 |
| [`main`](https://github.com/yeruoxiangchen/video_studio/tree/main) | 最新源码，包含 C++ 批量抽帧、可取消的 FFmpeg 编码、音频采样时钟修复及完整流程计时。 |
| [本次发布记录](releases/20261116/README.md) | 源码 SHA-256、评测对应关系及发布检查 |
| [操作与成片](demos/README.md) | 自然语言剪辑、多轮修改、成片对照与视频译制 |

```bash
git clone https://github.com/yeruoxiangchen/video_studio.git
cd video_studio
git checkout resume-20261116
```

最新版本使用 `git checkout main`。启动与模型配置见 [README](../README.md)。
简历标签保持固定；main 在此基础上修复了同源音视频的联动裁剪和原片默认预览，并补充剪辑演示与成片对照。

[数据摘要](reports/2026-11-16-resume.json) · [既有完整发布检查](reports/2026-11-16-publication.json) · [Go 网关接入](go-gateway.md)。
历史指标的样本、输入链路和原始日期保留在对应评测记录中。

最新主分支补充了自然语言剪辑、多轮字幕修改、两版成片对照和版本回滚演示，入口见 [演示目录](demos/README.md)。

2026-11-17 的工程更新及测量口径见 [媒体预处理与完整流程计时](native-pipeline-performance.md)。
