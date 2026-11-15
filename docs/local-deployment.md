# 本机 CamCat 完整部署

源码基线为 `upstream.lock.json` 中的 CamCat 提交。工作台、领域命令、LangGraph、
任务队列和渲染沿用上游，译制接入原时间线。

## 存储与运行单元

源码：`/home/zjr/Tracker/internship_project_lab/camcat_studio`。
模型、容器层、卷、缓存、日志及媒体：`/data/zjr/camcat_studio`。

`camcat-studio-docker.service` 运行本项目自己的 rootless Docker；socket 位于
`/data/zjr/camcat_studio/docker/docker.sock`。`studio.sh` 会选用此 socket。
宿主机前置依赖 `uidmap`、`slirp4netns` 已由用户安装。

| 服务 | 配置 |
| --- | --- |
| translation | 核心模型服务中的本地 Qwen3-VL-8B-Instruct，回环 18969，独立于 18974 的剪辑规划角色 |
| local-models | 官方 Qwen 模型源码和固定权重版本，回环 18969；按明确运行配置预检空闲卡 |
| embedding | Qwen3-VL-Embedding-8B，BF16，4096 维输出截断为 2048 后归一化，独占第一张卡 |
| reranker / visual | 默认双卡配置使用标准 bitsandbytes NF4，共用第二张卡串行推理；可选三卡配置让视觉生成使用独立 BF16 卡 |
| ASR | 固定版本 faster-whisper-large-v3-turbo，多语言自动检测；当前为独立 GPU 1 / float16 子进程，4 GiB 预算；支持显式 CPU INT8 配置 |
| source analysis | 固定 Qwen3.5-9B / BF16，本机 18972；原片景别另用真实 RTMPose CPU 关键点，横移方向用 OpenCV 背景光流 |
| editing | 复用本地 Qwen3.6-27B / Q4_K_M，本机 18974，负责需求理解、领域命令及文字约束 |
| narrative | 固定 Qwen3.6-27B / Q4_K_M 与官方 llama.cpp 源码版本，本机 18974，处理全部原片的叙事排序 |
| Compose | PostgreSQL、etcd、MinIO、Milvus、迁移、API、分析 Worker、译制 Worker、渲染 Worker、Web 和 Piper |

核心模型每张卡共用 0.86 的进程显存上限，要求启动前至少 22528 MiB 空闲。
原独立 Qwen3-8B 翻译服务已停止以释放 GPU 2，其验证记录保留。当前原生验证命令
使用 `config/active-translation.env`；原独立服务配置仍在 `config/translation.env`。
物理 GPU 编号、UUID、角色和量化方式记录在 `checks/local-models-server.json`。
2026-11-15 的译制质量优化继续使用核心服务的 Qwen3-VL-8B-Instruct：
完整含义优先、确定性生成，配音反馈后才进行用户允许的时长调整。
真实词概率、词时间戳和原始识别文本保存在后端原片分析证据中，随临时原片四小时过期。
可选 Hy-MT 专用翻译适配器经过同样本比较后未被选为当前配置，
`camcat-studio-specialist-translation.service` 已停止；其固定权重与失败结果仍保留在数据盘。
配置、比较与本轮完整验收见[实际语音译制质量优化](localization-quality-20261115.md)。
业务交付流程与新增接口见[英文视频交付](business-delivery.md)。
`CAMCAT_DELIVERY_MAX_BYTES` 默认 536870912（512 MiB），
控制单个交付包内视频的大小；打包临时文件保存在数据盘 `runtime/delivery-tmp/`。

重载时可在 `config/local-models.env` 设置 `export CAMCAT_LOCAL_GPU_INDICES=4,5`，
固定嵌入模型和重排/视觉模型各自的物理卡。指定卡必须分别有至少 22528 MiB 空闲，
否则预检失败；每卡仍共用 0.86 的进程显存预算。
可选三卡配置只在同一个 `config/local-models.env` 设置：

```bash
export CAMCAT_LOCAL_GPU_INDICES=4,5,2
export CAMCAT_LOCAL_VISUAL_PRECISION=bfloat16
```

该配置保持 embedding 在第一张卡使用 BF16、reranker 在第二张卡使用 NF4，视觉生成
在第三张卡使用原 BF16 权重和官方 Transformers 加载方式。每卡预算仍为 0.86，
不共享重排/生成锁。模式与卡数量不一致直接报配置错误；短暂的显存释放延迟由 Tenacity
限时等待 30 秒，仍不可用则退出，服务不自动重启。物理卡、精度和启动源码摘要保存在
`checks/local-models-server.json` 并通过模型 `/health` 返回。
模型常驻服务使用最低优先级的 best-effort I/O（priority 7），避免 idle I/O 在共享磁盘
持续繁忙时无限延后权重读取；资源预检及模型加载失败仍直接退出，不自动重启。
权重固定修订见 `infra/local-models.lock.json`；官方模型文件按原字节保存并检查散列。

ASR 使用项目自己的 `/data/zjr/camcat_studio/asr-venv`，不改动其他项目的环境或缓存。
首次准备或升级后执行 `bash scripts/studio.sh prepare-asr`；完整 bootstrap 也包含该步骤。
固定依赖见 `services/local_models/asr-requirements.txt`，其中 PyAV 固定为 16.0.1，
与 faster-whisper 1.2.1 的解码接口匹配。权重来自固定修订的
`dropbox-dash/faster-whisper-large-v3-turbo`，修订为
`0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`，运行时只读取本地权重并启用 VAD。
该仓库提供 OpenAI Whisper turbo 的 CTranslate2 转换。中文转写使用 OpenCC 1.1.9
转换为简体，其他语言保留原文。默认可移植配置为 CPU INT8、四线程；当前质量部署在
`config/local-models.env` 明确设置 `CAMCAT_ASR_DEVICE=cuda`、`CAMCAT_ASR_GPU_INDEX=1`，
使用 float16、两线程和 4096 MiB 预算。所选卡需至少有 5120 MiB 空闲，核心 Qwen 角色
不得占用该卡。启动和推理后检查实际进程显存，超预算退出并记录错误。
模型 `/health` 返回实际 ASR 修订、推理库与解码器版本；转录返回检测语言、概率和真实时间戳。
坏音频返回 422，后续请求可正常识别；静音保持空文本和空字幕。

2026-11-13 本轮实际三卡映射为 `4,3,2`。GPU 5 已被其他任务占用，因此只迁移本项目
的 reranker 到空闲 GPU 3，显存预算保持不变。启动失败记录保留，服务仍为 `Restart=no`。
图像或视频预处理失败会中止请求，避免官方包装器的 NULL 文本回退进入向量库。
图文重排会将内联图片按原字节写入临时文件，适配官方包装器的路径输入；请求完成后删除。
本地部署显式启用 `CAMCAT_LLM_JSON_SCHEMA_ENABLED`，将原生 Pydantic 命令契约用于
规划提示和 XGrammar 0.2.9 解码，继续执行原有模拟、校验、版本比较与原子持久化。
解码明确使用严格属性模式；普通 JSON-object 调用仍支持自由对象。服务使用标准
JSON Schema 校验结果，协议错误返回带字段路径的非重试 422，最多触发一次模型纠正；
401/403、输入超限和模型协议错误不会重复整个 Worker 任务。调用记录保留请求 ID、
schema 摘要、token 数量、各阶段耗时、结束原因和失败类别，不包含媒体描述或密钥。
失败节点通过 Python 异常附注保留调用记录，再由 API 持久化到 Graph Run。
需求理解同样使用 Pydantic 契约，字段必须位于顶层；拒绝 `default` 包装及多余 URL。
字幕、转场禁令来自明确指令，普通短视频目标保留自动字幕生成。
译制模型使用同样的 Pydantic/JSON Schema 契约，限制字幕数量、输入编号和译文字段；
已有编号去重、时间窗口、术语及英文输出检查继续执行。
“保持当前片段原片范围”保留现有入点和出点；只有指令明确要求完整文件时才检查全片范围。
结构性编辑及从紧凑契约升级的编辑，在有界修复中继续提供完整领域命令，包含拆分和重排。
没有原片的编辑返回 `source_required` / HTTP 422，原工程版本保持不变。
有界修复后仍不可执行的计划返回 `edit_plan_invalid` / HTTP 422，附带具体校验原因；
流式请求使用相同错误码及说明，工程版本保持不变。
其他 provider 默认保持 JSON-object 协议；需确认支持 JSON Schema 后再启用该开关。
生成请求的排队与推理共用 180 秒服务端时限，超时返回 HTTP 408 并释放锁；
超时生成不作为成功结果返回。健康检查会报告仍在超时执行的请求。
`bash scripts/studio.sh grammar-contract` 使用真实 tokenizer 和完整命令 schema，
在 CPU 检查语法编译、逐 token 掩码及非法命令拒绝；模型推理另由真实集成测试验收。

后台队列按任务种类分三路：`worker` 执行分析与入库，`localization-worker`
执行翻译与配音，`render-worker` 执行渲染。三者在 PostgreSQL 领取时使用互斥过滤条件，
拥有独立 Worker ID；视频导出期间可继续译制。此拆分复用既有 GPU 模型服务。
查看和停止三路后台任务：

```bash
bash scripts/studio.sh compose logs --tail 50 worker localization-worker render-worker
bash scripts/studio.sh compose stop worker localization-worker render-worker
```

MinIO 两个官方镜像源实测拒绝访问，因此镜像由官方提交
`0d7408fc9969caf07de6a8c3a84f9fbb10a6739e` 构建，版本为
`RELEASE.2025-04-22T22-12-26Z`。构建文件在 `infra/minio/Dockerfile`，保留原程序和许可证。

## 启动和监视

当前质量配置在三个核心模型角色之外使用两个独立模型服务。首次准备或从冷启动恢复时，
先准备这两个服务，再启动应用。模型选择和 GPU 选择均显式记录；以下 GPU 7、0 是本轮
实际使用的卡，启动预检要求目标卡空闲，不会终止其他进程。两项模型服务失败后都停止。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
bash scripts/studio.sh setup-visual-model
bash scripts/studio.sh prepare-visual-model
bash scripts/studio.sh prepare-geometry
bash scripts/studio.sh prepare-planning-model --cuda-compiler /home/zjr/cuda-12.1/bin/nvcc
bash scripts/studio.sh configure-local --visual-model Qwen/Qwen3.5-9B --editing-model Qwen/Qwen3.6-27B --narrative-model Qwen/Qwen3.6-27B
bash scripts/studio.sh start-visual-model --gpu 7 --port 18972
bash scripts/studio.sh start-planning-model --gpu 0 --port 18974
```

等待模型 `http://127.0.0.1:18972/health`、`http://127.0.0.1:18974/health` 就绪后，
执行下面的完整 bootstrap。已运行的模型服务直接保留。准备步骤按固定修订下载，CPU
模型按固定 SHA256 验证；GGUF 权重首次计算完整 SHA256，之后验证来源、大小和 mtime。
`prepare-planning-model --offline` 检查现有收据，不重新下载或反复扫描 17 GB 权重。
GGUF 构建使用未修改的官方源码 archive，CUDA 构建目录与收据均在本项目数据盘。

Qwen3.5 使用项目独立 `quality-models-venv`；RTMLib CPU 工具使用独立 `ocr-venv`，
通过官方 ONNX Runtime 执行真实 YOLOX/RTMPose，推理限两线程。原片只在请求临时目录
内处理，结果携带测量来源；无人物时保留视觉模型的景别描述。
固定版本和物理目录见 `infra/visual-model.lock.json`、`infra/planning-model.lock.json`、
`infra/geometry.lock.json`。健康响应保留实际运行源码摘要、模型版本及显存预算。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
bash scripts/studio.sh status
watch -n 5 -d 'bash scripts/studio.sh status'
```

已准备模型、环境和私有配置时，启动完整部署与验收：

```bash
bash scripts/studio.sh start-bootstrap
tail -f /data/zjr/camcat_studio/logs/bootstrap.log
```

任务按实际命令依次构建镜像、启动基础设施、等待模型、迁移并启动应用，
准备带 FFprobe 实测时长的授权音频目录，运行真实服务集成测试，导入授权视频，
最后执行原剪辑及译制浏览器旅程。
状态、当前日志和失败阶段记录在 `checks/bootstrap.json`。失败停止，不自动重启。

本机集成测试从宿主机访问真实 Compose 服务，PostgreSQL 仅在回环 18970 开放。
应用和测试共享数据盘的绝对媒体路径；容器也保留 `/var/lib/camcat` 映射。
这样对象存储签名链接在宿主模型服务、实际浏览器和测试进程中使用同一地址。
测试命令为 `bash scripts/studio.sh integration-local`，报告保存在
`runtime/acceptance/integration-results.xml`；本机 Agent / 原片测试时限为 900 秒，原测试默认值保留。

```bash
systemctl --user show camcat-studio-bootstrap -p MainPID -p ActiveState -p ExecMainStatus
tail -n 30 /data/zjr/camcat_studio/logs/stack-build.log
tail -n 30 /data/zjr/camcat_studio/logs/local-models.log
tail -n 30 /data/zjr/camcat_studio/logs/integration-real.log
tail -n 30 /data/zjr/camcat_studio/logs/browser-real.log
```

停止部署任务不会停止已独立启动的模型或容器。按需分别停止本项目单元：

```bash
bash scripts/studio.sh stop-bootstrap
bash scripts/studio.sh compose --profile local-speech down
bash scripts/studio.sh stop-local-models
bash scripts/studio.sh stop-translation
bash scripts/studio.sh stop-speech
bash scripts/studio.sh stop-docker
```

应用就绪后访问 `http://127.0.0.1:18962`。远程使用时转发 18962、18960、18963 三个端口，
分别供页面、API 产物及对象存储播放链接使用。

## 网络与验收素材

`capture-network` 将代理保存在数据盘的私有配置中。Docker daemon 使用宿主机代理，
容器构建与授权媒体下载使用 rootless 网关代理；内部服务与 `10.0.2.2` 直接访问。
`configure-local` 写入忽略跟踪的 `.env`，各模型使用独立私有密钥。

Pixabay 文档样例在本机返回 403。当前验收采用固定 MDN 修订的 CC0 花朵视频，
来源与许可证在 `docs/open-video-library.json`。原生导入 API 将其作为授权素材入库；
另一份加上真实 Piper 旁白的衍生视频作为临时用户上传，两个通道分别验收。
GitHub 原始文件可能返回 `application/octet-stream`；视频导入接受此传输类型，
随后检查容器文件头和真实 FFprobe 视频流，再写入对象存储与素材记录。
短输入的浏览器目标为 4 秒，原测试默认目标仍为 12 秒。

输入、参考图和来源清单在 `runtime/acceptance/`；浏览器报告、截图和失败轨迹在
`checks/browser/`。这些检查用于功能验收，模型质量评测需另设数据集和指标。
