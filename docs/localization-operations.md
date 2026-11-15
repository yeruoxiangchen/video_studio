# 剪辑工作台运行说明

## 项目定位

直接基于 CamCat 的真实源码、完整 Git 历史和原有界面开发，上游固定提交见 `upstream.lock.json`。
原片分析、多模态素材检索、Milvus、LangGraph 剪辑、版本管理与 FFmpeg 导出构成项目主体。
「译制」是同一工作台中的新增页签；翻译和配音写入原有时间线，继续使用编辑、审计、回滚与导出入口。

首版支持字幕翻译、术语表、原文/译文校订、时间编辑、双语字幕、逐句英文配音、试听、原声音量处理、
SRT 下载和原生 MP4 导出。Piper 使用一个英文单人音色；配音前检查译文语言与音色是否匹配。
现已增加原文校订、低置信词提示、随版本保存的术语表、交付审校和完整 ZIP 交付包。
业务操作与真实验收见[中文视频的英文交付](business-delivery.md)。

## 产品流程

1. 上传原片后生成带字幕的剪辑时间线，或在「译制」点击「载入原片与识别字幕」。也可在聊天区提出翻译、配音需求。
2. 打开「译制」，选择原文字幕轨和目标语言，可填写 `原词 = 译词` 格式的术语表。
3. 提交翻译，检查结果并点击「确认并写入时间线」，产生新版本。
4. 修改字幕文本或起止时间，保存后生成配音。
5. 逐句试听，检查语速及补齐时长，再确认写入时间线。
6. 点击「已校对字幕并试听，确认交付审校」，保存本版本审校记录。
7. 点击「生成交付视频」，完成后播放检查，再点击「下载交付包」。
   包含 MP4、原文/译文/双语 SRT、术语 CSV 与交付清单。
8. 原文或时间改变后重新翻译，译文改变后重新配音；新版本重新确认交付审校。

普通剪辑预览继续使用原有 Export。交付记录绑定已确认版本，打包校验实际视频 SHA256。
原片保留四小时，交付区显示截止时间，请及时下载。

先测量每句的实际音频时长，允许最高 1.2 倍速，短音频补齐静音。过长时提示修改；
勾选「允许一次时长改写」才会调用模型缩写，改写结果仍需确认。
版本冲突会保留本标签页的文本草稿；刷新后可重新检查并保存。

## 本机目录和服务

源码位于 `/home/zjr/Tracker/internship_project_lab/camcat_studio`；依赖、模型、容器数据、
媒体、日志、临时文件与测试产物位于 `/data/zjr/camcat_studio`。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
bash scripts/studio.sh setup
bash scripts/studio.sh prepare-speech
bash scripts/studio.sh prepare-deployment
bash scripts/studio.sh start-speech
bash scripts/studio.sh speech-contract
```

本机首次准备使用 `prepare-speech --import-from /data/zjr/video_clip_studio`，将已有的 Piper 权重、
FFmpeg 和字体复制至新项目并校验 SHA256，保留来源和模型卡。新项目有自己的环境、语音服务和密钥；
原项目文件和进程保持原状。干净环境中 `prepare-speech` 从固定 Hugging Face 修订下载音色，
Compose 镜像安装 FFmpeg 和中文字体。

配音服务 `camcat-studio-speech.service` 仅监听 `127.0.0.1:18961`，CPU 两线程、1 GiB 内存上限。
私有配置在 `/data/zjr/camcat_studio/config/speech.env`，不打印密钥。

```bash
systemctl --user show camcat-studio-speech -p MainPID -p ActiveState -p MemoryCurrent
tail -n 30 /data/zjr/camcat_studio/logs/speech.log
bash scripts/studio.sh stop-speech
```

显式复用现有本机 OpenAI 兼容翻译服务：

```bash
bash scripts/studio.sh import-translation-provider --from-env /path/to/private-local.env --model model-name
bash scripts/studio.sh translation-contract
```

该配置单独保存在 `config/translation.env`，用于宿主机验证。容器内需要填写容器实际可访问的
模型地址；宿主机回环地址不直接作为容器地址。

也可使用项目自己的官方 vLLM 启动入口：

```bash
bash scripts/studio.sh start-translation
bash scripts/studio.sh status
tail -n 20 /data/zjr/camcat_studio/logs/translation-server.log
bash scripts/studio.sh translation-contract   # 状态为 ready 后执行
bash scripts/studio.sh stop-translation
```

该入口复用现有本地 Qwen3-8B 权重和 vLLM 0.11.0 环境，保持它们只读。
启动前选择至少有 22528 MiB 空闲显存的 GPU，预算为显卡容量的 0.78，本机为 19169 MiB。
只监听回环 18968 端口，单请求、8192 上下文、eager 模式，缓存和日志归本项目所有。
状态记录在 `checks/translation-server.json`；启动失败写出状态、释放服务资源，不自动重启。
服务名为 `camcat-studio-translation.service`，停止命令只作用于该服务。

## Compose 部署

本机已安装项目专属 rootless Docker 和 Compose，`studio.sh` 自动选择数据盘 socket。
本地模型部署、显存预算与持续监视见 [本机完整部署](local-deployment.md)。

`prepare-deployment` 准备独立持久目录和忽略入库的 `.env`，生成 MinIO 密钥并填入已有 TTS 密钥。
核心模型需要按实际部署填写：上游百炼适配器使用真实百炼配置和百炼可访问的 HTTPS 对象存储地址；
本地服务需提供 embedding、reranker、LLM/视觉分析、ASR 的真实端点和密钥，遵循 `provider-contract.md`。
Canonical embedding 仍为 Qwen3-VL-Embedding-8B / MRL 2048，同空间直接处理文字、图片和视频，
reranker 仍为真实 Qwen3-VL-Reranker-8B。

本地模型模式还需将 `CAMCAT_OBJECT_STORE_PUBLIC_ENDPOINT` 设为浏览器可达的 MinIO 地址，
本机默认端口对应 `http://127.0.0.1:18963`；跨机器访问时使用实际可达的地址。

```bash
# 核心模型已在 .env 配置，采用上游托管适配器和本地 CPU 配音：
bash scripts/studio.sh compose --profile hosted --profile local-speech config --quiet
bash scripts/studio.sh compose --profile hosted --profile local-speech up --build -d
# 若已配置本地核心模型端点，省去 --profile hosted。
bash scripts/studio.sh compose ps
bash scripts/studio.sh compose logs --tail 50 api worker localization-worker render-worker speech
bash scripts/studio.sh compose down
```

端口均绑定回环：API 18960、Web 18962、MinIO 18963、控制台 18964、Milvus 18965。
持久卷绑定本项目 `/data/.../containers` 和 `runtime`。服务缓存/临时目录使用挂载目录；
本项目 Docker 引擎的镜像层和构建缓存位于 `/data/zjr/camcat_studio/docker/data`。

## 检查与验收

```bash
bash scripts/studio.sh format
bash scripts/studio.sh lint
bash scripts/studio.sh unit apps/api/tests/unit
bash scripts/studio.sh test-web
bash scripts/studio.sh contracts --check
bash scripts/studio.sh build-web
bash scripts/studio.sh speech-contract
bash scripts/studio.sh media-contract
```

新增真实任务测试 `test_localization_jobs_real.py` 覆盖翻译、配音、幂等、显式应用、409、缓存复用与旧配音阻止导出。
原 `camcat.spec.ts` 保留上传、图文检索、Agent、渲染、修改、回滚与冲突旅程，设置
`CAMCAT_E2E_LOCALIZATION=1` 时追加翻译、配音试听和再次导出。`studio.sh browser` 默认启用该扩展，
要求真实视频/参考图和完整服务，不替换后端或模型响应。

```bash
export CAMCAT_E2E_VIDEO=/data/zjr/camcat_studio/media/licensed-input.mp4
export CAMCAT_E2E_IMAGE=/data/zjr/camcat_studio/media/reference.jpg
bash scripts/studio.sh prepare-browser
bash scripts/studio.sh browser e2e/camcat.spec.ts
```

最终验收还需实际迁移 up/down/up、Postgres/Milvus/S3/模型集成及 Compose 浏览器旅程。
实际执行结果与具体缺项记录在 `localization-verification.md`。
