# 剪辑工作台验证记录

## 2026-11-15 文本译制与工作台交付版

界面统一为「剪辑工作台」，直接译制可从原片及识别字幕建立草稿；聊天支持单独翻译、给当前译文配音及连续翻译配音。翻译应用和后续配音任务入队在同一个 CAS 事务中完成，人工修改抢先写入时旧任务明确失败。

本轮后端 543、前端 37 项单元测试通过，Ruff、mypy 116 文件、OpenAPI 契约和前端构建通过。真实 API 两项通过；文本译制、面板交付、原有完整剪辑三条浏览器旅程逐条通过。新增任务卡也显示版本，因此原有 E2E 的版本定位改为只查询页头，随后完整复跑通过。

演示为真实服务、模型与 FFmpeg 输出；三段 MP4 完整解码通过。记录在数据盘 `checks/agent_workstation/acceptance-v2.json`，可公开的范围与镜像摘要见 [验收摘要](workstation-acceptance-20261115.json)，演示见 [视频与来源](demos/README.md)。原 v7 质量协议、分数和旧版本收据保持历史记录，不以新 UI 验证代替质量重测。

## 2026-11-15 业务交付版验证

当前业务交付版增加原文校订、项目术语表、版本审校和完整交付包，
具体流程见[英文视频交付](business-delivery.md)。
本轮后端 536、前端 34 项单元测试通过，Ruff、mypy 112 文件、契约与前端构建通过。
真实 API 交付测试通过，新增业务浏览器旅程和原有完整浏览器旅程各 1 项通过，零跳过。
实际 ZIP 内的视频摘要与原渲染结果一致，FFprobe 检查视频/音频流通过，
原文修改使旧交付失效，跨会话下载返回 404。

记录保存在 `/data/zjr/camcat_studio/checks/business_delivery/`，
模型质量基线为下文的 v7 固定镜像和评测记录。

## 2026-11-15 译制优化版 v7 最终验收

v7 固定镜像的整站运行 `20261115T055239Z-896015` 于 06:48:19 UTC
完成，耗时 55 分 40 秒。自动功能和公开剪辑质量检查均通过，失败阶段为空。
模型比较、保留方案和逐句问题见[实际语音译制质量优化](localization-quality-20261115.md)。

| 检查 | v7 实际结果 |
| --- | --- |
| 真实 ASR 后翻译 BLEU | 23.86283，v5 为 23.39495 |
| 真实 ASR 后翻译 chrF++ | 50.45888，v5 为 49.65579 |
| 中文识别 CER | 6.29275%，与 v5 相同 |
| 人工中文输入翻译 BLEU / chrF++ | 26.06167 / 53.95633；v5 为 27.66743 / 53.62797 |
| ASR、两条翻译路径、配音组件 | 各 20/20，零缺失；配音回识别 WER 2.76498%，时长改写 0/20 |
| 实际 ASR 到译制视频 | 5/5；五幅实际成片抽帧的双语区域分离、文字位于画布内 |
| 两道公开排序 / 两道公开选片 | 13/13、7/7 / 4/4、4/4，四题 reward 均为 1.0 |
| 契约与异常、时限恢复 / 业务 / 浏览器 | 24 + 1 / 6 / 1 项通过，零跳过 |
| 四客户端十分钟稳定性 | 8/8 流程、111 次健康采样通过，无待清理项 |
| 含排队 P95：翻译 / 配音 / 渲染 | 13.879 / 13.153 / 20.053 秒，从已分析素材开始 |
| 源码与代码检查 | 174 项宿主摘要、168 项容器比对匹配；后端 529、前端 32 项测试通过，Ruff、mypy 110 文件及前端构建通过 |

该结果来自同一组已查看的 20 条 FLEURS 录音和四道 AgenticVBench 开发回归题。
20 条配音组件沿用人工中文对照路径的实际英文输出，五条成片覆盖真实 ASR 到配音与导出。
英文参考只用于离线评分，官方剪辑评分源码保持一致，标准/缺失输出校准为 1.0/0.0。
原有外文专名、识别错词、部分代词及从句问题保留在审校记录中；人工语义和听感审阅仍待完成。

所有路径相对 `/data/zjr/camcat_studio/`：

- 整站：`benchmarks/runs/full_validation/20261115T055239Z-896015/report.json`。
- 四题：`benchmarks/runs/editing_repair/20261115T055239Z-896011/report.json`。
- 译制：`benchmarks/runs/localization_public/20261115T055241Z-896376/report.json`。
- 最终收据：`checks/localization_improvement_20261115/final-acceptance-v7.json`。

`config/active-validation.json` 已指向固定协议 `eval/localization_public_quality_v7.json`；
本轮两个测试服务已正常退出，应用与模型服务继续就绪。

## 2026-11-15 修复版最终验收

03:30 UTC 验收的 v5 完整修复过程与复跑命令见
[字幕、语音与分镜修复记录](quality-repair-20261115.md)。本轮使用已查看过的
20 条 FLEURS 录音与四道 AgenticVBench 题做开发回归，于 03:30 UTC 结束，
自动功能和公开剪辑质量检查均通过。

| 检查 | v5 实际结果 |
| --- | --- |
| 两道公开选片 | 4/4、4/4，reward 均为 1 |
| 两道公开排序 | 13/13、7/7，reward 均为 1 |
| 中文识别 CER | 6.29275%，原始基线 20.93023%；包含模型升级和 OpenCC 简繁规范化 |
| 原文 / ASR 后翻译 chrF++ | 53.62797 / 49.65579，ASR 后翻译原始基线 45.52899 |
| ASR、两条翻译路径、配音 | 各 20/20；配音回识别 WER 3.55450%，时长重写 0/20 |
| 完整译制、双语字幕 | 5/5 视频；五幅实际抽帧的双语区域分离、文字位于画布内 |
| 契约、异常、恢复 / 业务 / 浏览器 | 24 + 1 / 6 / 1 项通过，零跳过 |
| 四客户端、十分钟稳定性 | 8/8 流程、112 轮健康检查通过 |
| 含排队 P95：翻译 / 配音 / 渲染 | 13.129 / 13.357 / 19.101 秒，从已分析素材开始 |

整站报告为 `benchmarks/runs/full_validation/20261115T023200Z-4117874/report.json`，
四题报告为 `benchmarks/runs/editing_repair/20261115T023200Z-4117836/report.json`，
公开译制报告为 `benchmarks/runs/localization_public/20261115T023202Z-4118241/report.json`，
路径均相对 `/data/zjr/camcat_studio/`。最终收据为
`checks/quality_repair_20261114/final-acceptance-v5.json`。

人工语义和听感审阅保持待审。AI 文字抽查已记录人名、月海术语与一处识别错误向翻译
传播的情况，见 `checks/quality_repair_20261114/translation-text-review-v5.json`；
自动检查通过后，正式发布继续使用现有字幕审校与配音试听流程。

## 2026-11-14 公开译制与整站完整测试

固定协议见 `eval/localization_public_pilot_v1.json`。本轮复用已验收镜像
`sha256:e3e15a4df02fe09d3d91f374d26c0a74613551e380f85e29d9be63fad1646bad`，
运行期间核对 API、普通 Worker、渲染 Worker 和模型进程身份。

| 范围 | 数量与判定 |
| --- | --- |
| 公开中文识别 | FLEURS 中文 test 固定 20 个独立句子，各选一条原始录音；报告微平均 CER |
| 公开中英翻译 | 同句 ID 对齐英文参考，分别测原文输入和实际 ASR 输入；报告 chrF++、corpus BLEU 和缺失数 |
| 英文单音色配音 | 20 条真实翻译结果按原录音时长配音；检查时间窗与最高 1.2 倍语速，报告回识别 WER、改写次数与改写后翻译分数 |
| 完整译制导出 | 前 5 条公开录音搭配已登记许可的背景视频；真实上传、分析、翻译、应用版本、配音、渲染与下载；保留成片和字幕检查帧 |
| 字幕文件 | 下载实际 SRT，用 pysubs2 检查字幕条数、文字与毫秒时间 |
| 真实契约与异常 | 临时数据库迁移、图文视频 embedding、重排、原生规划、ASR 损坏/静音/错误模型恢复、翻译、配音缓存、任务幂等、CAS 冲突、限流与错误命令 |
| 既有业务与浏览器 | 重新执行六例业务回归与完整剪辑/译制浏览器旅程 |
| 并发稳定性 | 四客户端持续 10 分钟，使用本轮新分析的授权视频；测任务完成、延迟、健康与清理，负载阶段从已分析素材开始 |

数据来自 [FLEURS 官方数据集](https://huggingface.co/datasets/google/fleurs)，
固定版本 `70bb2e84b976b7e960aa89f1c648e09c59f894dd`，CC-BY 4.0。
从两种语言 test 交集的 349 个句子 ID 中按种子 `20261114` 的哈希顺序固定选取 20 个，
原始音频包校验与参考文本校验分别记录。英文参考只供离线评分；失败和缺失输出保留在分母中。
五例视频是公开音频派生的业务流程测试。配音使用当前配置的英文 Piper 音色；
`manual-review.csv` 保留前十句语义、数字专名和自然度的人工审阅栏，自动结果中标记待审阅。

这套测试同时引用已经完成的新公开剪辑题原始结果：排序 task2 为 7/7，选片 task2 为 1/4，
两题均正常导出，完整奖励 1/2。总报告保留该质量缺口，功能通过与公开剪辑质量分别报告。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
bash scripts/studio.sh start-full-validation
watch -n 5 -d 'bash scripts/studio.sh full-validation-status'
# 停止本轮总控及其子进程：
bash scripts/studio.sh stop-full-validation
```

总控为 `camcat-studio-full-validation.service`，CPU 上限四核、内存上限 4 GiB，
两小时硬上限，失败不自动重启，复用现有模型服务。输出位于
`/data/zjr/camcat_studio/benchmarks/runs/full_validation/<run_id>/`，
公开逐项输出位于同级 `localization_public/<run_id>/`；总控日志为
`/data/zjr/camcat_studio/logs/full-validation.log`。
`state=completed` 表示流程结束，最终结论读取 `summary`、`public_localization_quality` 和人工审阅状态。
同目录的 `review-findings.json` 保存抽帧审阅发现，持续监视也会显示它。
本轮首个样本 `1968` 已观察到长段落双语字幕重叠，记录为显示质量失败；
识别、翻译、配音和导出成功的计数继续如实保留。

### 本轮实际结果与汇总修复

运行 `20261114T203139Z-2585345` 在 2026-11-14 21:11:51 UTC 结束，耗时
2411.621 秒（40 分 12 秒）。公开译制子运行是 `20261114T203142Z-2585597`。

| 检查 | 实际结果 |
| --- | --- |
| 公开录音 ASR / 原文翻译 / ASR 后翻译 / 配音 | 各 20/20 完成，无缺失 |
| 公开录音派生成片 | 5/5 导出，字幕文件与时长检查通过；首例抽帧存在长字幕重叠 |
| 真实契约、异常与服务端时限恢复 | 24 + 1 项通过，零跳过 |
| 既有业务回归 | 6/6 通过 |
| 真实浏览器完整旅程 | 1/1 通过，零失败、跳过或 flaky |
| 四客户端、10 分钟稳定性 | 两批 8/8 流程成功，111 轮健康检查通过，无待清理任务或会话 |
| 汇总脚本修复后单元测试 | 479 项通过，含稳定性结果读取与持续监视回归 |

稳定性原始报告位于 `checks/reliability/soak-20261114T210144Z-fbb8be/report.json`，
子进程 PID 为 `2691719`。本轮总控错误地读取 `checks/reliability/latest.json`，
实际测量器写入 `latest-soak.json`，导致进程退出码为 0 时仍误报失败。
读取路径和持续监视已修正，并核对同一子进程、运行 ID、完成状态、真实完成数及清理结果。
总报告按原始记录重新汇总为 `automated_functional_pass=true`。
同目录 `report.original-aggregation.json` 保留修正前报告，
`aggregation-correction.json` 记录修正原因、原报告与原始稳定性报告哈希、子进程及回归依据。
测试期间的应用镜像和模型进程身份一致。

| 固定 20 条的质量指标 | 结果 |
| --- | --- |
| 中文 ASR CER，原定口径 | 20.93% |
| 原文直接翻译 | BLEU 28.35，chrF++ 54.13 |
| 实际 ASR 后翻译 | BLEU 20.61，chrF++ 45.53 |
| 配音回识别 WER，相对于实际配音文字 | 2.60% |
| 配音时长重写 | 0/20，最高语速约 1.061 倍 |

另对这 20 条已保存的转录做繁简差异诊断：使用
[OpenCC 官方实现](https://github.com/BYVoid/OpenCC) 1.1.9 的 `t2s`，
在原有文字归一化后同时转换参考与识别结果，CER 为 11.90%。
该补充分析保存在公开译制输出的 `asr-script-diagnostic.json`，原定 CER 与所有模型输出保留。
脚本差异之外仍有专名、同音字和漏译问题，需逐例定位；配音自然度的人工试听栏仍待填写。
修复顺序优先处理长段字幕的分句和双语排版，再用同一批输入检查识别及翻译损失，
并单独定位公开选片 task2 的 1/4 结果。

记录日期：2026-11-09 至 2026-11-10 UTC。上游基线：`1e8d0492a9793f47d9a69362378fb2535f386b52`。
本轮交付为 CamCat 原有剪辑工作台上的译制扩展，以及独立本地模型/媒体验证。

## 已执行的检查

| 检查 | 实际结果 | 日志（相对 `/data/zjr/camcat_studio/`） |
| --- | --- | --- |
| 后端单元测试 | 179 passed，含生成时限、语义修复、授权音频注册、转场上下文与实测音频时长传递 | `logs/unit-audio-duration.log` |
| 前端单元/契约测试 | 28 passed | `logs/web-unit.log` |
| Ruff 格式与规范 | 通过 | `logs/format.log`、`logs/lint.log` |
| mypy | 72 个源码文件，无错误 | `logs/lint-audio-duration.log` |
| TypeScript / Vite 生产构建 | 通过 | `logs/web-build.log` |
| OpenAPI / TypeScript 契约同步 | `--check` 退出码 0 | `logs/contracts.log` |
| 真实本地 Qwen3-8B 翻译 | 1 passed，编号、时间、术语和数字保留 | `logs/translation-real.log` |
| 真实共享 Qwen3-VL 翻译 | 1 passed，27.56 秒，编号、时间、术语和数字保留 | `logs/translation-shared-real.log` |
| 真实整站首轮集成 | 18 passed / 2 failed；图文重排和 Agent 命令字段已修正，复验中 | `logs/integration-real.log` |
| 真实整站第二轮集成 | 16 passed / 4 failed；图文重排已通过，4 项失败集中在生成及翻译超时 | `logs/integration-real.log` |
| 真实整站第三轮集成 | 21 passed，207.81 秒；含真实模型超时后下一请求恢复 | `logs/integration-real.log` |
| GitHub 二进制类型导入 | 红测为 MIME 422；修复后真实下载、导入、索引和幂等复验通过，96.88 秒 | `logs/licensed-import-red.log`、`checks/integration-mime-20261110.xml` |
| 真实整站第四轮集成 | 21 passed / 1 failed，292.32 秒；仅 Agent 引用了不存在的 `audio:bgm` 轨道 | `logs/integration-real.log` |
| 命令语义修复聚焦单测 | 3 passed；缺失轨道反馈给模型，最多一次修复，失败不修改原状态 | `logs/agent-semantic-red.log`、`logs/agent-semantic-green.log` |
| 校验原因摘要 | 4 项聚焦单测通过；保留领域校验原因，不含原始模型输入 | `logs/validation-feedback-red.log`、`logs/validation-feedback-green.log` |
| 授权音频注册 | 8 项聚焦单测通过；覆盖 AddAudioSegment、InsertSegment、带片段 AddTrack，拒绝目录外音频 | `logs/audio-registry-red.log`、`logs/audio-registry-green.log` |
| 转场修复上下文 | 9 项聚焦单测通过；以原生 reducer 最后有效状态提供片段顺序、可用转场对和时长上限 | `logs/transition-context-red.log`、`logs/transition-context-green.log` |
| 音频时长规划上下文 | 10 项聚焦单测通过；实测时长同时进入素材窗口及注册来源 | `logs/audio-duration-red.log`、`logs/audio-duration-green.log` |
| 真实授权音频时长核验 | 1 passed，5.83 秒；三段目录时长与 S3 原文件的实际 FFprobe 结果一致 | `logs/audio-duration-real-red.log`、`logs/audio-duration-real-green.log` |
| 修复后的真实 Agent 聚焦复验 | 1 passed，99.38 秒；真实上传、后台断线恢复、七节点及版本持久化 | `logs/agent-duration-real.log`、`checks/agent-duration-real.xml` |
| 失败修复聚焦测试 | 25 passed，含图片原字节物化、命令 Schema 和兼容开关 | `logs/contract-repair-green.log` |
| 服务端生成时限回归 | 3 passed；红测为缺少时限模块，绿测验证真实线程锁释放 | `logs/generation-deadline-red.log`、`logs/generation-deadline-green.log` |
| XGrammar 命令语法检查 | 完整 schema 20158 字节，编译 1.08 秒，85 token 掩码 0.13 秒 | `logs/grammar-contract.log`；此项不执行模型推理 |
| 真实 Piper / 配音时间窗 / 翻译到成片 | 3 passed | `logs/speech-real.log` |
| CamCat 原多轨、转场、音频渲染 | 1 passed | `logs/native-render.log` |
| Playwright 脚本收集 | 3 个用例可加载 | `logs/browser-collection.log` |
| 真实浏览器界面与草稿恢复 | 2 passed，42.9 秒；完整剪辑/译制旅程另行验收 | 本轮 `studio.sh browser e2e/design-walkthrough.spec.ts e2e/input-recovery.spec.ts` 输出 |
| Chromium 准备 | 下载到本项目缓存，退出码 0 | `logs/browser-setup.log` |
| Compose YAML 静态检查 | 语法、可选模型 profile、数据挂载检查通过 | 直接命令检查 |
| Shell / Git 差异检查 | `bash -n`、`git diff --check` 通过 | 直接命令检查 |
| 源码存储检查 | `.env`、模型、媒体、日志、虚拟环境、node_modules 均未被跟踪 | `git ls-files` 检查 |

## 本地模型与成片证据

实际模型返回：

| 原文 | Qwen3-8B 译文 |
| --- | --- |
| 欢迎使用 CamCat。 | Welcome to CamCat. |
| 用 3 个步骤分享你的故事。 | Share your story in 3 easy steps. |

翻译结果：`checks/translation-real/test_real_translation_preserve0/translation.json`。

第三个媒体用例由真实 Qwen3-8B 返回译文，再交给真实 Piper，经过 CamCat 原有领域命令、
V2 时间线编译、构建、FFmpeg 渲染和输出验证。背景是生成的诊断画面，原始上传/ASR/Agent/
数据库队列旅程单独验收。

诊断样片目录：`checks/speech-real/test_real_speech_renders_throu0/`。

- `camcat-bilingual-dub.mp4`：640×360、30 fps、4 秒、120 帧，包含实际配音音轨。
- `preview.png`：抽帧已目视检查，中英文字幕均烧录到画面，中文可读。
- `verification.json`：帧数与时长偏差均为 0，包含音轨，完整解码成功。
- `build/`：实际编译时间线、ASS/SRT 与构建指纹。

技术验证输出保留原生 `editorial_qc_pending` 状态，正式内容仍通过 CamCat 原工作台审阅确认。
这些短输入用于功能集成验证，不构成翻译质量或剪辑能力基准评测。

## 独立服务验证时的启动记录

| 服务 | 启动后观察到的状态 | 资源与地址 |
| --- | --- | --- |
| `camcat-studio-translation.service` | ready；控制 PID 1971156，服务 PID 1971168 | GPU 2，UUID `GPU-834ab60c-111d-5eec-61d8-eb4dadec8996`，显存预算 19169 MiB（0.78），回环 18968 |
| `camcat-studio-speech.service` | active；PID 502374 | CPU 两线程，内存上限 1 GiB，回环 18961 |

翻译复用现有 Qwen3-8B 权重和 vLLM 0.11.0 环境，只读使用；新服务有独立缓存、密钥、日志和状态。
为在两张 GPU 上完成整站部署，当前翻译已配置为共享本地 Qwen3-VL 服务，
原独立翻译进程已停止。上表和翻译样例保留此前实测来源；共享模型验收结果另行记录。
首次启动的 UUID 兼容错误已定位并改为编号；状态继续记录物理 UUID。
Piper 首次启动的线程上限错误已通过限制数值库线程修复。失败记录保留，不自动重启服务。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
bash scripts/studio.sh status
tail -n 20 /data/zjr/camcat_studio/logs/translation-server.log
tail -n 20 /data/zjr/camcat_studio/logs/speech.log
# 按需停止本项目服务：
bash scripts/studio.sh stop-translation
bash scripts/studio.sh stop-speech
```

## 整站验收过程

本轮已安装并实际运行项目专属 rootless Docker 29.9.3 / Compose 5.7.0，
已拉取 PostgreSQL、etcd 和 Milvus 官方镜像；MinIO 改用固定官方源码构建。
两个核心模型的固定版本权重已完整下载，模型曾实际就绪，八个常驻容器已启动。
配音容器使用进程 CPU 亲和性限制两核（实测为 62、63），兼容本机缺少 CPU cgroup 委派的环境。
首轮真实集成发现图文重排将内联图片当路径、编辑命令缺少字段约束两项失败；
已修正图片物化与 JSON Schema 解码。第二轮图文重排通过；复杂命令 schema 的
Python 逐 token 解码持续占用 CPU，GPU 空闲，生成阻塞又拖住共享模型的后续翻译请求。
切换至 XGrammar，并加入含排队时间的生成时限与超时健康状态后，第三轮 21 项真实集成全部通过。
后续授权素材导入收到 GitHub 的 `application/octet-stream` 响应，被原 MIME 限制拒绝。
导入现已接受二进制传输类型，并保留原有文件头/FFprobe 验证；新增真实导入与索引回归。
状态页也会显示 HTTP 状态码和 API 错误消息，具体导入任务另记 `logs/library-import.log`。
第四轮新增导入用例通过；唯一失败为模型引用不存在的音频轨道。原修复循环只覆盖
Pydantic 字段解析，现将来源校验、命令模拟及原片/素材库比例检查纳入同一轮有界修复，
并明确命令顺序与轨道引用规则；实际持久化仍在所有校验通过之后。
进一步真实复验定位到 `mixkit-music-738`：它在会话的授权音频目录中，但原注册逻辑只
收集视频 InsertSegment 的引用，合法 BGM 未注册到项目 sources。现将授权音频加入
服务器来源注册表，并覆盖音频插入、轨道内置片段和媒体替换；只注册实际使用的来源，
存储路径、许可证和来源 URL 均来自受信目录。后续真实复验继续发现非相邻转场引用；
现将最后有效模拟状态的轨道/片段顺序、合法转场对和时长上限交给模型修复，
并只记录不含媒体文本的结构化校验元数据。真实 Agent 正在复验。
随后真实校验显示音频目录缺少长度，且生成的非循环音频源时长短于时间线时长。
音频导入现通过真实 FFprobe 记录 `duration_us`，规划获得可用源范围，并明确循环、
裁剪和淡入淡出约束；验收顺序改为先准备音频目录，再执行集成测试。
镜像末尾的重复联网打包曾因构建工具 TLS 下载失败；运行时现通过既有 PYTHONPATH
加载已复制源码，依赖复用前置安装层，实际重建已通过。
补齐实测音频时长与规划约束后，完整 23 项集成通过。浏览器复验进一步定位到项目首页
批量请求触发限流、模型素材来源选择错误、二次剪辑重复字幕和实际渲染少帧问题。

## 2026-11-11 整站验收完成

最终部署在 UTC 01:30:27 写入 `complete | succeeded`。八个常驻容器运行正常，
核心模型就绪，翻译共享 GPU 5 的本地模型，Piper 配音可用。

| 检查 | 本轮结果 |
| --- | --- |
| 后端单元测试 | 184 项通过 |
| 前端测试 | 30 项通过 |
| Ruff 与静态类型检查 | 通过，mypy 覆盖 72 个源文件 |
| OpenAPI 与前端类型契约 | 检查通过 |
| 真实集成与数据库迁移 | 24 项通过，用时 170.86 秒 |
| 完整浏览器旅程 | 3 项通过，用时 2.6 分钟 |
| 浏览器实际下载的成片 | H.264/AAC，1080×1920，30 fps，4 秒，120 帧 |

本轮修复使用项目列表的可选会话展开查询消除首页逐项目并发请求。规划阶段由后端计算
视频倍速对应的时间线长度，首次空时间线使用创建类命令。当已校验来源的初次提案仍未
排入原片，修正调用只开放真实上传素材，模型选择的范围经实测边界约束后生成原生单段命令。
多轮字幕会保留已编辑文字，并随原片裁剪调整时间。FFmpeg 在每个片段的编译帧边界上
截断或保持末帧，覆盖音频容器尾部略长于视频的输入。

浏览器完整旅程覆盖上传、参考图检索、Agent 剪辑、渲染、二次修改、回滚、409 冲突恢复、
字幕翻译、时间校订、配音试听、应用新版本、再次渲染、播放和下载。配音前通过现有字幕
编辑器把最后一句延长到片尾可用区间，翻译和配音均调用真实本地模型。

所有运行文件存放在 `/data/zjr/camcat_studio/` 下。

- `checks/bootstrap.json` 保存最终部署状态。
- `logs/integration-real.log` 和 `runtime/acceptance/integration-results.xml` 保存集成结果。
- `logs/browser-real.log` 和 `checks/browser/report/` 保存完整浏览器验收结果。
- `checks/browser/results/camcat-real-multimodal-imp-6ae65-conflict-and-render-journey/`
  保存实际下载的 `camcat-localized.mp4` 和工作台截图 `camcat-localization.png`。

## 2026-11-11 一分钟完整译制样片

实际项目为「完整样片 · 从太空看地球」，编辑会话
`cc33276d-405c-4fde-9397-fdb16e0ae7f7`，字幕下移后的版本为 v10。
文件保存在 `/data/zjr/camcat_studio/demos/earth_full_20261111/`。

样片使用 NASA SVS 14831 的视觉素材，移除来源视频原有音乐，另行编写六段中文示例旁白并
通过本地 Piper 合成中文输入。原片经过真实上传、镜头分析与 Agent 规划；时间线、中文文案
和字幕时间经过编辑校订。翻译由本地 Qwen 任务生成，第 2、6 句英文再经编辑润色，随后由
真实 Piper 配音任务和原生 CamCat FFmpeg 导出。背景音乐来自已登记许可证的 Mixkit 音频库。
这份作品展示经过编辑审阅的业务流程。

- `CamCat_完整样片_从太空看地球_英文配音.mp4`：60 秒、1920×1080、30 fps、1800 帧，H.264/AAC。
- `CamCat_完整操作演示.mp4`：116.51 秒，包含真实工作台录屏与完整成片；剪除了等待和失败尝试。
- `workflow.json`：实际会话、版本和翻译/配音任务标识；`translation.json` 保留最初模型译文。
- `english-cues.json`：最终配音文案；`provenance.json`：素材来源和中文输入的合成记录。
- `sample-preview.jpg`：字幕排版检查图。按用户要求下移至中文 y=0.745、英文 y=0.815，保持 NASA 原片底部的航天器标注可读；两份视频已重新导出并完整解码。

两份视频均通过完整解码；六段配音的实际输出时间窗均有音频，语速均为 1.00 倍。
英文校订后的配音任务复用 4 句原音频，仅重新生成 2 句，记录见 `audio-qc.json`。

这一分钟输入还暴露了规划上下文重复的问题：完整项目 metadata 中的分析记录与独立素材
候选被重复发送，超过本地视觉模型的 12288 token 输入上限。规划提示现只排除项目的
metadata，并保留独立候选、来源及时间线；持久化状态保持完整，同时使用紧凑 JSON。
新增回归先观察到预期失败，修复后 185 项后端单元测试通过，Ruff、mypy 和契约检查通过；
实际重建 API/worker 后，同一份一分钟原片的 Agent 规划、翻译、配音与导出成功。

## 性能与稳定性检查

后续按用户要求完成字幕下移，原样片和操作演示均更新为 v10 的实际导出。
并发与异常回归发现并修复任务去重竞争、限流状态码、领域命令错误处理、规划来源比例、
音轨/字幕边界、多轮编辑重建片段以及项目列表分页问题。队列租约组件测试改用独立
临时 PostgreSQL 数据库，以免与常驻 Worker 抢任务。

最新后端单元测试 191 项、前端测试 32 项、完整浏览器 3 项通过。一次完整真实集成批次
30 项通过，后续规划修复通过新增单元回归与最新实际浏览器旅程验证。
640 次数据库读请求全部成功；四客户端译制流程均完成。30 分钟周期性持续测试现已
完成：6 批、24 次流程零失败，329 轮健康检查通过；测试进程正常退出且会话已清理。
测量参数、耗时和资源采样见 [性能与稳定性验收](performance-reliability.md)。
