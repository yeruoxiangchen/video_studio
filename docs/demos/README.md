# 剪辑工作台演示

本页展示实际本地部署的操作和成片。源码基于 CamCat；新增字幕翻译、逐句配音、人工审校、版本交付，以及聊天调用译制工具。

| 演示 | 内容 |
| --- | --- |
| [01 · 文本驱动译制](01-agent-workflow.mp4) | 上传中文口播，载入原片字幕并校订，发送「翻译成英文并配音」，试听、确认并下载。 |
| [02 · 面板逐步译制](02-panel-workflow.mp4) | 从已有字幕时间线开始，在面板完成术语、翻译、配音和交付操作。 |
| [03 · 30 秒英文成片](03-earth-english.mp4) | 实际 Qwen 翻译、Piper 英文配音及 FFmpeg 双语字幕输出，含音轨。 |

点击链接打开或下载 MP4。文本操作录屏剪短了等待段并标注该次实际耗时；面板录屏为连续操作，画面来自真实浏览器。录屏为无声操作演示，配音效果请播放第三条成片。

![文本译制与交付工作区](workstation.png)

## 复现这次操作

1. 新建项目，上传带口播的原片并等待分析。
2. 在「译制」载入原片和识别字幕，校订错词及每句时间。
3. 输入「把原文字幕翻译成英文并配音，允许一次时长改写，替换原声」。
4. 等待草稿版本生成，打开「译制」试听、校订，再确认交付审校。
5. 生成交付视频，预览并下载 ZIP。也可全程通过面板逐步处理。

第一条演示的中文脚本为自撰输入，识别错词由界面校订；译文、配音和视频来自真实服务。它是业务演示，与公开评测样本及质量统计分开记录。逐步面板演示使用自撰的原文校订输入，没有把参考英文作为模型输出。

## 来源

- 地球画面：[NASA — Seeing Earth as Only NASA Can](https://svs.gsfc.nasa.gov/14831/)，Credit: NASA；源视频的 Universal Production Music 音轨已移除。
- 面板演示背景：[MDN flower.webm](https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.webm)，[上游 CC0 许可](https://github.com/mdn/interactive-examples/blob/13024db581b08fae11a0ca229497198b9b0534e7/LICENSE)；用于识别链路的短语音来自 [FLEURS](https://huggingface.co/datasets/google/fleurs)，CC BY 4.0。
- 英文音轨：本地 Piper `en_US-lessac-medium`，权重版本及 [MODEL_CARD](https://huggingface.co/rhasspy/piper-voices/blob/b145f2ac26522d6a2ccde0164b7b0e48b1e3199c/en/en_US/lessac/medium/MODEL_CARD) 与数据许可链接保留在部署记录。中文合成输入仅用于本地流程验证，公开文件为操作录屏和英文成片。
- 操作录屏：Playwright 对真实 Compose 网站录制；剪短等待和格式转换使用 FFmpeg。没有替换模型输出、伪造成功响应或合成界面。

文件摘要、实际时长及此次验收范围见 [manifest.json](manifest.json)。源码使用 MIT 许可，第三方媒体及音色适用各自条款。
