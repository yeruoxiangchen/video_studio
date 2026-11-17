# 媒体预处理、渲染生命周期与完整流程计时

本轮补强三个工程环节：C++ 抽帧进入业务调用路径；真实 FFmpeg 运行可取消、可超时；
首次上传到成片下载的计时包含原片分析和 Agent 规划。

## C++ 与 Python 的调用链

`Worker.analyze_source / NarrativeService -> extract_analysis_frames -> pybind11 -> libavformat/libavcodec/libswscale`

批量调用复用解码上下文，利用关键帧索引跳过采样点之间不需要的 GOP。
帧选择使用 PTS 和流时间基，兼容可变帧率、非零起始时间和 90/180/270 度旋转。
FFmpeg 对象通过 RAII 释放，NumPy 数组持有独立的 C++ 缓冲区，解码期间释放 GIL。
每次业务批次最多 16 帧；原生接口另限制线程、时间、图像尺寸和总输出内存。

Compose 多阶段构建并安装原生扩展，默认设置 `CAMCAT_MEDIA_FRAME_BACKEND=native`。
宿主机开发安装与实现参考见 [原生模块](../native/media/README.md)。

实际 Compose 镜像使用 FFmpeg 5.1.9，对同一段 30 秒视频抽取 12 帧、宽度 512，
包括 JPEG 写盘，交替执行三轮。旧 CLI 路径中位耗时 5.646 秒，C++ 路径 1.034 秒，
该阶段耗时降低 81.70%。采样到的进程树峰值 RSS 中位数为 130.879 / 89.195 MiB。
CLI 使用 q=2，原生路径使用 Pillow quality=95，JPEG 像素平均绝对差 2.916/255。
原始逐轮时间、CPU 和采样内存口径见 [抽帧报告](reports/2026-11-17-native-media.json)。
这是抽帧与 JPEG 写入的对比；CPU 字段覆盖该测量子进程及其子进程的完整生命周期。

## 渲染与任务状态

每次 FFmpeg 编码创建独立进程组，每两秒更新数据库心跳并检查取消请求。
超过 `CAMCAT_RENDER_TIMEOUT_SECONDS` 或收到取消时，先发送 SIGTERM，超出两秒宽限后
发送 SIGKILL，等待回收进程，并删除未完成的成片。该过程只操作本次创建的进程组。
`renderer-process.json` 原子记录 PID、状态、退出码和耗时；成功记录同时进入任务结果，
失败记录随原有 24 小时诊断目录保留。数据库终态与取消请求通过行锁仲裁。
编码超时记为 `execution_timeout`，进入明确失败状态，由用户显式重试。

真实 API 取消检查在 FFmpeg 启动后发起取消，4.106 秒内完成回收，原 PID 已不存在，
半成品被删除，任务在后续读取中保持 `cancelled`，未自动重新执行。

## 音频采样时钟修复

完整流程发现 Piper 的 22.05 kHz 输出在重采样前按秒裁剪时，
6.366670 秒窗口可能只生成 6.366667 秒音频，触发已有的过短校验。
现按 [FFmpeg 的采样计数语义](https://ffmpeg.org/ffmpeg-filters.html#atrim)，
先 `aresample=48000`，再 `atempo`、补静音和 `atrim=end_sample=N`，
其中 `N=ceil(window_us*48000/1000000)`。
16/22.05/44.1 kHz 输入、非整采样窗口及变速共七项实际 FFmpeg 检查通过；
失败的真实配音任务显式重试后完成，四句音频均符合时间窗。

## 实际流程与复现

`measure_full_pipeline.py` 启动真实 Playwright 浏览器，上传有许可的 30 秒中文口播视频，
经过画面分析与 ASR、普通剪辑 Agent、字幕时间调整、聊天翻译与配音、试听和审校、
实际渲染、播放以及 ZIP 下载。测试记录 Job/GraphRun 关联标识和模型调用时间，
另采样容器 CPU、内存和线程；共享 GPU 的整卡数据明确保留为宿主机总量。

```bash
export PLAYWRIGHT_BROWSERS_PATH=/data/zjr/camcat_studio/cache/playwright
export PYTHONPATH="$PWD/apps/api:$PWD"
/data/zjr/camcat_studio/venv/bin/python -B scripts/measure_full_pipeline.py \
  --video /data/zjr/camcat_studio/demos/user_trial_20261115/earth_zh_30s_input.mp4 \
  --output /data/zjr/camcat_studio/checks/full-pipeline-<新运行目录>
```

控制器最长运行 27 分钟，失败保留浏览器日志、截图和 trace，取消自己的未完成任务；
不自动重试。`status.json`、`progress.json` 可查看当前阶段，`report.json` 只在完整流程
通过后生成。上传到下载耗时直接取同一次单调时钟标记，不累加不同流程的 P95。

旧的四客户端译制并发报告继续描述其原有工作负载，见 [性能与稳定性](performance-reliability.md)。

## 2026-11-17 完整流程结果

修复后的独立新项目 `full-flow-05` 通过真实浏览器验证，四个持久化任务均一次执行成功。
30 秒输入生成 1920×1080、30 fps 成片，共 900 帧，帧数差和时长差均为零，完整解码通过。
下载的 ZIP 包含视频、三份 SRT、术语表和交付清单；包内视频散列与渲染任务结果一致。

| 阶段 | 浏览器实际耗时（秒） |
| --- | ---: |
| 上传与原片分析 | 70.970 |
| 剪辑 Agent 规划 | 90.885 |
| 文本译制指令、翻译与配音 | 35.764 |
| 渲染、校验与交付就绪 | 58.047 |
| 播放确认与交付包下载 | 2.053 |
| 上传到下载的直接墙钟时间 | 260.727 |

总时间包含自动化的页面操作和审校确认。服务已预热，结果来自本次单条真实流程；
各阶段与 Job 的排队/执行时间、Agent 节点及模型请求记录分别保留在
[完整计时报告](reports/2026-11-17-full-pipeline.json)。

本轮后端单元测试 567 项、前端测试 41 项、真实集成测试 41 项通过，
实际部署内的原生媒体 15 项与音频采样时钟 7 项通过；另完成实际 API 编码取消检查。
开发期的音频失败、浏览器状态等待问题和每次尝试分别保留在数据盘任务目录中。
