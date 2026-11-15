# 公开数据小测

2026-11-14 的最新修复与验收见
[质量修复记录](quality-repair-20261114.md)。以下按运行时间保留历史结果，
每个分数对应其原始运行和当时版本。

## 2026-11-14 最新剪辑复测

运行 `20261114T140005Z-1592748` 在镜像 `e3e15a4df02f` 上完成重新上传、原片分析、
LangGraph 规划、CAS 保存、FFmpeg 导出和原版评分，总计 1826.57 秒。

| 固定公开题 | 结果 | 官方 reward |
| --- | --- | --- |
| 四分镜组装 | 选片 4/4，声明来源与成片检查 4/4 | 1.0 |
| 十三段排序 | 位置正确 13/13，ordering 1.0，来源检查 13/13 | 1.0 |
| 汇总 | 执行 2/2，错误 0，满分 2/2，平均 reward 1.0 | 1.0 |

每题一次正式尝试。两题的官方 oracle 校准均为 1，缺失输出对照均为 0。
原题、官方版本、输入清单、尝试次数、预算与评分规则保持冻结；答案及评分器未作为
Agent 输入。结果针对参与本轮诊断的这两道固定开发题。

当前主规划与叙事排序使用本地 Qwen3.6-27B / Q4_K_M，原片视觉分析使用
Qwen3.5-9B / BF16。两份实际成片均为 1920×1080 H.264/AAC，时长分别为
21.80 秒、151.57 秒。原始报告与成片位于
`/data/zjr/camcat_studio/benchmarks/runs/editing/20261114T140005Z-1592748/`。
相同部署的六例业务和浏览器验收见上面的质量修复记录。

上一轮公开运行 `20261114T131013Z-1349766` 为相同的 2/2 满分，1847.47 秒。
其完整验收随后在“保留已有中文字幕并拆分视频”业务例失败；该失败及后续校验修复
单独保留在质量修复记录中。

## 新公开题小测：public_holdout_v1

`eval/public_holdout_v1.json` 固定了下一组未参与前述修复的公开题。每个已支持任务族
按最小未使用编号选择一道，选题发生在读取新题说明与评分文件之前：

| 题目 | 输入 | 输出要求 |
| --- | --- | --- |
| assembly-task2 | 12 段宇航员素材 | 按四格分镜选片并完整拼接 |
| sequencing-task2 | 7 段汽车广告素材 | 按原故事概述重排全部片段 |

产品镜像固定为 `e3e15a4df02f`，复用现有原片分析、API 编辑与 FFmpeg 导出。
运行器导入原有 `PublicEditing` 适配器，官方输入和评分器按 Git blob 校验，素材使用
已有固定 HF revision 与 LFS SHA256。每题一次正式尝试，Agent 和评分各沿用 1800 秒预算。
评测前后核对应用镜像、模型角色和已加载进程身份；任务失败及低分全部保留。
本轮新题结果与原两道开发回归分开报告。

报告：`/data/zjr/camcat_studio/benchmarks/runs/editing_holdout/latest.json`。
素材和准备收据：`benchmarks/data/agentic/holdout_v1/`、`benchmarks/holdout_v1/`。
启动文件加载项目私有 `config/network.env`，日志不输出代理或模型密钥。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
bash scripts/studio.sh public-bench start-holdout
watch -n 5 -d 'bash scripts/studio.sh public-bench holdout-status'
tail -f /data/zjr/camcat_studio/logs/public-holdout.log
bash scripts/studio.sh public-bench stop-holdout
```

单元为 `camcat-public-holdout.service`：控制进程 4 核、4 GiB，最长两小时，失败不自动
重启，模型资源复用现有服务。首次准备因后台未加载代理配置而被主动取消，尚未调用
Agent；该运行 `20261114T191008Z-2332727` 保留为准备阶段的取消记录。

先运行 **20 条查询 / 100 个候选视频 + 2 道剪辑题**。规模固定在
`eval/public_pilot_v1.json`，每轮每道剪辑题只运行一次。修复后的复测单独记录，
保留此前失败结果，不自动扩量或挑选最好成绩。
此前六例业务回归继续保留，单独报告。

## 首轮检索小测结果：2026-11-13

运行 `20261113T044514Z-3744082` 已完成，100 个视频和 20 条查询均经过实际模型编码，
总耗时 383.801 秒，观测到 GPU 峰值分配 16605.7 MiB。

| 输出维度 | Recall@1 | Recall@5 | Recall@10 |
| --- | --- | --- | --- |
| 4096 | 14/20 = 70% | 19/20 = 95% | 20/20 = 100% |
| 2048 MRL | 14/20 = 70% | 19/20 = 95% | 19/20 = 95% |

评分对照通过：正确排序的 Recall@1 为 1，正例置末时 Recall@10 为 0。
单正例任务中 Hit@k 与 Recall@k 相同。`video8122` 的标准视频在 4096 维下为第 9，
2048 维下为第 15；其余五个 Top-1 未命中的样本，正例位于第 2 或第 3。
这轮结果支持继续检查检索链路，尚未运行混合检索或 reranker 消融，规模保持本轮设置。

完整报告：
`/data/zjr/camcat_studio/benchmarks/runs/retrieval/20261113T044514Z-3744082/report.json`。
相邻文件 `per-query-4096.json`、`per-query-2048.json` 保存查询、正例名次与 Top-5；
`predictions-*.json` 保存完整排序，`encoder-calls.jsonl` 保存本轮实际编码调用耗时。

## 检索

- 数据：Qwen 官方 MMEB-v2 使用的 `VLM2Vec/MSR-VTT`，`test_1k/test`。
- 固定 HF revision：`947b139d0c591235a3f311e45a31a88461bad0d2`。
- 从原始 1000 条中按种子 `20261113` 和 ID 的 SHA256 顺序选取 100 个候选，
  再以另一用途标识选其中 20 条查询，确保标准相关视频均在候选集中。
- 候选编码只看视频；标准 caption 只作为查询，不写入候选描述。
- 直接复用固定版本 Qwen 官方 encoder、64 帧抽取函数和 `RankingMetrics`，
  输出 Hit/Recall@1/5/10、MRR、NDCG 和完整排序、逐题正确视频名次。
- 模型为当前项目固定版本 Qwen3-VL-Embedding-8B / BF16；同一批真实向量分别计算
  4096 维和 2048 维 MRL，后者复用产品归一化函数。

本轮是编码器检索基线，尚未覆盖 Milvus/BM25/业务融合/reranker 的整个产品链路。
本轮使用 batch size 1 和 SDPA，官方示例使用 FlashAttention 2；这项差异随结果记录。
候选集合从 1000 缩小至 100，成绩标记为公开数据子集小测，与全量排行榜成绩分开。
评分侧用正确排序和正例置末的对照确认评分器工作；这些标签不会进入编码输入。

## 剪辑

固定 [AgenticVBench](https://github.com/PhiloLabs/agentic-vbench) 源码 revision
`b2764ed4b2a4b5a1737026b63887b50bba62f793`，选择：

| 题目 | 候选 | 原题要求 |
| --- | --- | --- |
| assembly-task1 | 12 个视频 | 按四格分镜选片，完整拼接为四段 |
| sequencing-task1 | 13 个视频 | 根据公开故事概述重排全部片段 |

素材 archive 固定 HF revision 与 SHA256，只解出数字编号视频。实际流程为：

`原始视频上传 → 当前产品原片分析 → 新建会话 → Agent 原生领域命令 → 保存版本 → 原生 standard 导出`

`solution.json` 由实际保存时间线的 source ID、源范围和输出范围转换，
不纠正顺序、补片段或改写模型选择。`solution.mp4` 为实际 API 下载的成片。
之后用未经修改的官方 `judge.py` 检查选片/顺序与视频画面的 SSIM 一致性。
即使产品执行失败，也保留错误并交给评分器处理缺失输出，失败题保留在统计中。

适配差异：组装题原始说明超过 API 的 8000 字符限制，提交时删除通用的
`Shot Language Reference` 词汇表，保留完整分镜、任务规则及交付要求；原文、
实际提交文本及各自哈希均保存。顺序题保留原文。
API 附加说明只解释文件名与原片 ID 对应，以及从保存时间线生成输出清单的方式。
这是经过产品 API 的公开题小测，执行环境与完整 Harbor 排行榜运行分开标注。

每题实际运行后，在隔离且禁网的 bwrap 文件系统中执行该题原版 oracle，
再用相同官方评分器验证 oracle 为 1、缺失输出为 0。oracle、答案和 judge 不进入 Agent 输入。
评分器基础设施失败时单列 `harness_error`，不给出混合平均成绩。

## 运行与查看

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
bash scripts/studio.sh public-bench prepare
bash scripts/studio.sh public-bench start-retrieval
bash scripts/studio.sh public-bench start-editing
```

```bash
watch -n 5 'bash scripts/studio.sh public-bench status'
tail -f /data/zjr/camcat_studio/logs/public-retrieval.log
tail -f /data/zjr/camcat_studio/logs/public-editing.log
```

```bash
bash scripts/studio.sh public-bench stop-retrieval
bash scripts/studio.sh public-bench stop-editing
```

| 任务 | systemd 服务 | 资源 |
| --- | --- | --- |
| 检索 | `camcat-public-retrieval.service` | 空闲 GPU 2，显存上限 21135 MiB；4 核 / 32 GiB RAM |
| 剪辑 | `camcat-public-editing.service` | 控制进程 4 核 / 4 GiB；复用已有 GPU 4、5 模型 |

两项上限均为 2 小时，失败退出，`Restart=no`。检索进程退出即释放本次 GPU 模型。
剪辑单题沿用官方 1800 秒 Agent 预算；停止控制服务时请求取消自身队列任务。
API 已受理的 Agent 生命周期仍由产品负责，报告保留其 Graph Run ID 供查询。

准备记录、源代码校验、数据清单、逐题结果、真实视频和日志都在：
`/data/zjr/camcat_studio/benchmarks/`。
两个最新报告入口为 `runs/retrieval/latest.json`、`runs/editing/latest.json`。
已经启动的首轮还在各自输出目录保存了 `source-snapshot/`，后续格式整理不改变该轮执行代码。

## 首轮剪辑执行与校验修复记录

组装题在原片分析阶段用完 1800 秒预算，未进入 Agent 生成阶段，未产生成片。
原片分析任务 `ae9f72e8-653a-4fae-8455-aa2842a3d74e` 继续占用队列，
已通过产品取消接口请求取消，实际于 2026-11-13 05:28:03 UTC 结束为 `cancelled`。
顺序题分析任务 `cafb7445-1900-499a-bcc2-316ea9e9c592` 随后于 05:28:04 UTC 开始。
后续版本的运行器在单题失败时立即请求取消自身遗留队列任务，取消失败的编号保留供最终清理。

首轮 oracle 校验因只读绑定整个主机根目录，无法新建 `/workspace` 挂载点而失败。
现改用 bwrap 的临时根，只读挂载系统程序、素材和官方 oracle，单独绑定可写输出目录。
对应回归及相关检查共 19 项通过，Ruff 与实际模型 Python 环境的语法检查通过。

首轮业务报告保持原样。校验修复只重新执行官方 oracle 与评分器，逐题结果另存，
不增加模型调用、重新上传或重跑原片分析：

```bash
bash scripts/studio.sh public-bench start-verify-editing --run 20261113T045009Z-3758828 --follow
```

已启动的校验服务为 `camcat-public-verify-editing.service`，控制进程上限 4 核 / 4 GiB，
不使用 GPU。它先校验已结束的题目，随后等待原控制进程结束并整理全部结果。
校验阶段使用源码检查与真实 FFmpeg/官方评分结果，失败不自动重启。
状态入口为 `runs/editing_validation/latest.json`；原始失败信息和原始报告哈希随新报告保留。
同一条 `public-bench status` 命令同时显示业务运行与校验状态。
日志为 `/data/zjr/camcat_studio/logs/public-verify-editing.log`。

### 剪辑小测最终结果

2026-11-13 05:50:11 UTC 两道业务尝试均结束；05:53:46 UTC 独立评分校验完成。
两题均在原片分析阶段用完各自 1800 秒的总预算，未进入剪辑规划、未生成成片，
因此实际完成数为 **0/2**，官方缺失输出评分均为 **0**。该结果反映本轮端到端处理未完成，
尚未获得模型选片与排序质量的观测。

两题的官方 oracle 分别得到 **1**，缺失输出对照分别得到 **0**，校验均通过。
此次评分修复没有增加模型尝试；原业务报告的挂载错误记录保持原样。
最终验证报告：
`/data/zjr/camcat_studio/benchmarks/runs/editing_validation/20261113T053423Z-3868115/report.json`。
下一步应定位原片分析和排队耗时，再决定剪辑复测；本轮规模保持不变。

## 原片分析修复与第二轮：2026-11-13

瓶颈位于每个分析窗口的完整视频转码：首个 720p 原片的约 2.646 秒窗口，
`extract_clip` 单次耗时 125.9065 秒，包含实际 ASR 的分析阶段合计 129.043 秒。
该临时视频只供封面、音频和清晰度分析使用，最终渲染仍读取原片。

现改为直接从原片读取对应时间窗：封面取原片的窗口中点，音频和清晰度使用
`-ss start -t (end-start)`，ASR cue 继续采用窗口内相对时间，片段仍保留原片起止时间。
源文件分析和每个窗口处理前保存 checkpoint、刷新执行租约并检查取消；
进度按文件序号及文件内完成比例计算。媒体子进程默认 300 秒超时，超时终止该进程并报错。
参数用法参考 [FFmpeg 主选项](https://ffmpeg.org/ffmpeg.html#Main-options)。

同一原片首个窗口的单次修复后分析为 **4.4461 秒**，包含真实 ASR；
1080p 顺序题首个原片的首窗口分析为 2.9242 秒。此处是阶段诊断，未作为完整剪辑成绩。
原始与修复后记录均在：
`/data/zjr/camcat_studio/runtime/checks/source_profile_20261113/`。

验收结果：后端 248 项单测、前端 32 项测试、Ruff、mypy、前端类型检查及构建通过；
真实 FFmpeg 时间窗测试通过，核对了音频时长、采样率、声道、封面及清晰度范围。
真实浏览器上传/分析/结果回显测试通过，验证了持续进度和原片时间范围。
API 与两个 Worker 已重新构建部署，存活和就绪接口均返回 200。

第二轮为 `20261113T063003Z-4012749`，同两道原题各运行一次，配置哈希与首轮一致。
组装题的 12 个原片共生成 21 个分析片段，原生分析任务从 06:30:07.891672 UTC
到 06:30:41.851399 UTC 完成，耗时 **33.959727 秒**，随后进入 Agent 规划。
本轮于 06:36:47 UTC 结束，总耗时 403.960 秒。两题均发生产品执行错误，
交付完成率为 **0/2**，官方奖励分均为 **0**，平均奖励为 **0**。

| 题目 | 原片分析 | 执行失败位置 | 已核对的原因 |
| --- | --- | --- | --- |
| assembly-task1 | 12 个视频 / 21 段，33.960 秒，成功 | 导出前的时间线编译 | 已保存的计划包含 3 个 0.1 秒叠化转场；第二段从原片 0 秒开始，缺少入场转场所需的前置帧，触发 `transition source handles are insufficient: seg_2` |
| sequencing-task1 | 13 个视频 / 57 段，154.674 秒，成功 | Agent 计划生成 | 本地模型输入超过代码设置的 12288 token 预算；`ValueError` 未被转换为可识别的请求错误，返回 HTTP 500，相同输入重试后仍失败 |

组装题原题提交文本要求直接拼接、不增加转场，该约束在计划保存前未被强制校验；
源范围与转场余量的组合也未在规划阶段拦下，直到导出编译才暴露。
顺序题的堆栈指向 `services/local_models/runtime.py` 的输入预算检查，
本轮观察到的是输入超限，没有出现显存不足错误。

两题的官方 oracle 均为 **1**，缺失输出对照均为 **0**；本轮评分校验通过，
未产生 `harness_error`。报告中的 `state=completed` 表示评测与计分已结束，
业务结果见 `summary.executed=0` 和 `summary.execution_errors=2`。
控制进程已退出，systemd 为 `inactive/dead`，`MainPID=0`。
本轮结束后未增加新的模型尝试。

后续修复优先级：先把用户禁止转场、源范围及转场余量检查放到计划保存之前；
再为多原片规划压缩重复材料字段、预先计算输入预算，并把确定的超限返回明确请求错误。
修复后继续用这两题各一次验证，逐轮保留结果，暂不扩量。

本轮报告：
`/data/zjr/camcat_studio/benchmarks/runs/editing/20261113T063003Z-4012749/report.json`。
其中 `source-snapshot/receipt.json` 保存修复源代码快照、首轮报告哈希及验收记录。
服务为 `camcat-public-editing.service`，本轮原控制 PID 为 `4012749`，4 核 / 4 GiB RAM，
复用 GPU 4、5 已部署模型及各卡 0.86 的进程显存预算，不新加载一套模型。

```bash
bash scripts/studio.sh public-bench status
tail -f /data/zjr/camcat_studio/logs/public-editing.log
bash scripts/studio.sh public-bench stop-editing
```

[Qwen 官方复现说明](https://github.com/QwenLM/Qwen3-VL-Embedding#reproducing-evaluation)
和 [AgenticVBench 评分说明](https://github.com/PhiloLabs/agentic-vbench/blob/main/docs/VERIFIER_DESIGN.md)
提供上游协议；本项目不修改其评分公式。

## 最新改动同步：第二轮之后的补丁

2026-11-13 再次核对源码、实际 API 容器、评测报告与任务状态：

- 当前新增 `agent/context.py` 与 `editing/requirements.py`：规划上下文压缩重复材料、
  存储信息和 schema 注释，同时保留全部原片及实测范围；交付约束包含禁止字幕/转场、
  完整原片、使用全部来源及明确片段数。
- 计划生成和最终验证阶段均检查交付约束与原生编译器的源范围/转场余量，错误在保存前
  进入既有修复流程。输入 token 超限使用明确的上下文预算错误，避免按临时 500 重试。
- 本地与运行中 API 的 `graph.py`、`context.py`、`policies.py`、`requirements.py`、
  `providers.py`、`timeline/compiler.py`、`media/ffmpeg.py` 七个文件 SHA256 一致。
  当前 `graph.py` SHA256 为
  `176bbf71a20d586dcef4a7c8931c67c962118a671338e474bcb7716a585c1cd3`。
- 最近浏览器报告位于 `checks/agent-repair-browser/report/index.html`，实际执行的是
  `source-analysis.spec.ts` 上传分析专项：1 项通过、0 跳过。它没有执行完整剪辑旅程。
- 常驻服务就绪；没有运行中或排队中的原生任务，也没有运行中的 Graph Run。
  公开剪辑最新结果仍是第二轮的 0/2，六例回归最新仍为 5/6；旧的
  `camcat-studio-edit-validation.service` 保留其因多轮模型超时而失败的状态。

当前补丁已部署，第三轮两题公开剪辑复测、最新版本六例回归与完整剪辑浏览器验收仍待完成。
下一步保持原有小规模：先验证上述补丁能否交付两题成片并通过官方评分，再完成多轮编辑
与完整浏览器验收。原有否定、更正、引号语义问题以及翻译人工质量审阅继续按
`evaluation-audit-20261113.md` 跟踪。已有业务成绩和原始失败报告保持原样。

## 第三轮及串行验收启动

用户确认继续后，2026-11-13 08:03:37 UTC 已启动
`camcat-studio-delivery-validation.service`，控制 PID `150823`。后台顺序为：

1. 原两道公开剪辑题各实际运行一次；原题、输入清单、运行器和官方评分保持原样。
   两题均实际交付、官方奖励均为 1、评分校准通过后继续。
2. 原六例业务回归全部通过后继续。
3. 完整浏览器剪辑、画布保留、多轮编辑、回滚、冲突恢复、翻译配音、播放与下载。

任一阶段失败会保留原始输出并停止；不自动重启或增加尝试。启动时已冻结源码快照及
第二轮报告哈希。状态命令只精简展示，完整源码校验仍保存在本轮 `status.json`。

启动前，现有应用后端 265 项测试与新增验收控制 6 项测试通过；Ruff、mypy
（76 个源文件）通过。真实本地模型的输入超限集成检查通过，确认返回带 token 数的
非重试 422。实际 API 的规划、上下文、约束、Provider 和编译器源码与本地 SHA256 一致。
启动前无运行中 Graph Run 或排队/运行中的产品任务。

控制任务上限 4 核 / 4 GiB、最长 2 小时、`Restart=no`，复用 GPU 4、5 上的已有模型，
每卡显存预算 21135 MiB；GPU 5 上的重排和视觉生成共用该预算。
首次观察已进入组装题的真实原片分析，子控制 PID `150833`，
原片分析任务为 `c3d3353d-9af8-4318-958d-ff3c67c17842`。

- 总流程报告：`/data/zjr/camcat_studio/checks/delivery_validation/20261113T080337Z-150823/`。
- 总日志：`/data/zjr/camcat_studio/logs/delivery-validation.log`。
- 本阶段原始日志：总流程目录内 `public-editing.log`。
- 各阶段的独立结果沿用原来的 `benchmarks/runs/editing/` 和 `checks/small_eval/`；
  完整浏览器报告与媒体保存在总流程目录下。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
watch -n 5 'bash scripts/studio.sh delivery-validation-status'
tail -f /data/zjr/camcat_studio/checks/delivery_validation/20261113T080337Z-150823/public-editing.log
bash scripts/studio.sh stop-delivery-validation
```

本节记录启动交接；最终业务结果以本轮实际报告为准。

## 第三轮实际结果与修复

第三轮 `20261113T080339Z-150833` 于 08:55:49 UTC 完成执行和计分，
总耗时 3130.157 秒。两题实际交付 1/2，执行错误 1，官方奖励均为 0；
总流程正确停止，六例及浏览器阶段尚未进入。两题官方 oracle/缺失输出校准均为 1/0。

- 组装题已生成并下载 21.667 秒、650 帧的真实成片。四个位置有两个选片正确，
  但这两段分别出现低于 0.95 的画面匹配值（0.9465、0.9433），官方奖励为 0。
  原片分析从 08:04:05 至 08:21:34，约 1049 秒；Agent 约 78.9 秒，渲染约 55 秒。
- 顺序题在分析阶段耗尽原有 1800 秒预算，未进入剪辑规划；其任务已取消。
  旧实现没有保存成功的逐原片视觉分析结果，整个任务重试会重新调用已完成的部分。

已用原保存时间线及真实渲染源指纹做独立诊断：只在诊断渲染中将颜色参数置为中性，
同四段素材的自身画面匹配均通过，SSIM 为 0.9896–0.9954。
这是渲染分项诊断，未改选片、未调用 Agent、未重计第三轮奖励。
原报告仍为官方奖励 0，诊断报告位于
`/data/zjr/camcat_studio/checks/delivery_diagnostics/neutral-500623/report.json`。

对应修复：

1. 用原生 `set_render_policy` 命令将保留原色/原声的偏好保存到版本化 metadata。
   导出时读取该偏好，关闭自动调色和响度归一化。字幕后续修改不会被模型的默认 false
   清除已有偏好；默认自动增强仍适用于没有该要求的工程。
2. 原片视觉分析使用有长度边界的简短契约，输出预算 768 token，增加拍摄尺度、
   机位、镜头运动、主体朝向与运动事实。规划上下文保留这些事实供实际模型匹配需求，
   不读取答案或 oracle 选择。
3. 将已成功的原片视觉分析写入任务 checkpoint，绑定内容、模型标识与契约摘要；
   重试复用已完成部分，原片到期时一同清理这些缓存。记录实际分析耗时和 token 用量。

本轮检查：287 项应用后端单测通过，32 项前端测试通过，Ruff/mypy（78 个源文件）
及前端构建通过；两项真实 PostgreSQL/API 集成检查通过，覆盖偏好版本冲突保护、
重试缓存持久化与到期清理。额外的多轮偏好保护和相关回归已通过。
API、控制 Worker、渲染 Worker 已构建，模型接口已重新加载；真实短分析与部署后的
API 导出分项继续在 `checks/delivery_repair_20261113/` 保存独立报告。

原两题的指令、输入清单、任务预算、运行器和官方评分保持原样，后续模型复测另开新轮。

### 修复后的真实分项验证

- 部署后的真实 API 使用独立会话保存 `set_render_policy`，通过原生渲染队列生成并
  下载 21.667 秒、650 帧视频。存储的渲染配置为中性颜色、关闭响度归一化；实际文件
  校验通过。报告：
  `/data/zjr/camcat_studio/checks/delivery_repair_20261113/api-policy-3c3e23ef/report.json`。
- 实际短分析发现自由文本会将“特写”填入机位角度，因此将景别、机位、镜头运动、
  主体朝向与运动改为固定枚举，并允许 `uncertain`。最终契约通过真实本地模型调用：
  本次视频分析 55.8931 秒，实际输入 2134 token、输出 196 token。此为单片分项结果，
  不用于推算整体提速；报告：
  `/data/zjr/camcat_studio/checks/delivery_repair_20261113/source-eight-enum/report.json`。
- 最后枚举调整的相关 18 项回归、Ruff/mypy 已通过。最终镜像于 11:42:41 UTC 构建，
  API 镜像 ID `a02c1231167a9883ef2ffad178a1da9e705c4ad5d9a6080e0554e38686f1298c`；
  API 和两个 Worker 使用同一份应用源码。

这些结果验证了渲染配置传递、分析契约与重试持久化。公开题的选片与排序质量仍须由
新一轮真实 Agent 执行及原官方评分判断，第三轮结果保留为 0/2。

### 第四轮启动交接

2026-11-13 11:57:09 UTC 显式启动修复后的串行验证，控制 PID `884389`，
子进程 PID `884393`，服务仍为 `camcat-studio-delivery-validation.service`。
启动时 API、控制 Worker、渲染 Worker 均健康；API 与控制 Worker 的分析契约摘要
均为 `4c9e7e66e59e76454428be223390869b4960e2cbe400f7501781586ebcc9b190`，
与真实分项验证一致。首次状态已进入组装题的原片分析，原生任务 ID
`09cd4233-937f-4e4a-9200-f599c79eb7cc`。

输出：`/data/zjr/camcat_studio/checks/delivery_validation/20261113T115709Z-884389/`。
该目录内 `public-editing.log` 为当前阶段日志，`source-snapshot/` 保存启动源码。
总日志仍为 `/data/zjr/camcat_studio/logs/delivery-validation.log`。
原两题通过官方交付与计分要求后，才继续六例回归、完整浏览器旅程；失败即停止并
保留结果。控制服务仍限制 4 核、4 GiB、2 小时，`Restart=no`。复用已就绪的
GPU 4/5 本地模型，每卡进程预算 21135 MiB；本次没有新建模型服务。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
watch -n 5 'bash scripts/studio.sh delivery-validation-status'
tail -f /data/zjr/camcat_studio/checks/delivery_validation/20261113T115709Z-884389/public-editing.log
bash scripts/studio.sh stop-delivery-validation
```

本节记录启动及交接，最终成绩以本轮生成的原始报告为准。

## 第四轮失败定位与第五轮修复

第四轮公开题 `20261113T115710Z-884393` 于 12:29:50 UTC 结束：组装题交付成片，
选片为 `7,5,3,9`，四个位置均不匹配；顺序题完成原片分析后，在生成编辑计划时遇到
本地模型 408。两题官方奖励仍为 0，六例与浏览器阶段没有进入；原始报告保留。

本次按实际输入输出定位到：

- 模型为组装题输出的四段 `reason` 均为空。原来的完整命令契约把理由设为可选，
  同时要求省略默认字段；仅靠提示词没有形成可检查的选片解释约束。
- 短视觉分析把原片的“走近后停下”压缩成“站着”，使相近镜头的动作差异丢失。
  人工查看真实首、中、尾帧后，确认需要保留时序变化。帧诊断位于
  `checks/delivery_repair_20261113/assembly-frames/`。
- 顺序题原规划输入为 9688 token，一次要求输出完整底层轨道与 13 段时间参数，
  在原有 180 秒生成期限内未返回。改用简短选择契约后的第一轮诊断仍出现重复 UUID；
  使用短引用后的真实回放完成 13 段无重复规划，输入 2599 token、输出 472 token，
  规划调用 114.793 秒。这是保存证据上的分项回放，尚未生成正式公开题成绩。
- 对同一用户分镜进行了独立真实视频重排对照，四个位置的首选为 `7,4,5,3`，
  仍混淆相近镜头。该诊断没有向模型提供答案，报告为
  `checks/delivery_repair_20261113/direct-storyboard-video-rerank/report.json`。

修复实现：

1. 需求理解明确标记 `planning_mode=source_sequence` 的完整原片、无字幕转场、零外部
   素材的初始拼接任务使用 `SourceSequenceProposal`：
   模型返回有边界的素材选择和必填理由，服务端使用实测时长构造原生 Domain Commands。
   源范围、连续时间、原速、重复/越界引用及要求使用全部来源继续通过原生校验。
   两次无效提案后报错，服务端不会代选顺序。
2. 以唯一上传文件名作为短素材引用；重名时生成可区分引用，按冻结映射还原实际源 UUID。
   UUID、私有存储路径和完整底层命令 schema 不再重复进入该类规划的输入与输出。
   其他编辑继续使用原生完整/增量命令契约。
3. 原片分析新增 2–3 条有序 `temporal_evidence`，上下文压缩保留这些证据，强调
   开始/结束状态和主体运动与镜头运动的区别。契约摘要随之更新，旧缓存不会混用。
   对原片 6 的真实调用恢复了“走近、停下、站立”的描述，分析耗时 62.8099 秒、输出
   210 token；原片 12 的调用仍有主体运动误判，说明镜头判断还需要最终质量验证。

当前实际检查：296 项后端单测通过，Ruff/mypy（79 个源文件）通过，真实本地模型
文件名选择与精确原片范围契约检查 1 项通过，报告为
`checks/delivery_repair_20261113/filename-provider-contract.xml`。
应用镜像 `ae36d2a56e5b` 上的真实 API 检查按用户指定的 `11.mp4 -> 2.mp4` 完成
本地模型选择、原生版本 1 到 2 的保存、真实渲染及下载，输出 354 帧、11.8 秒并含视频
和音频流。报告为 `checks/delivery_repair_20261113/api-filename-sequence-b44b6080/report.json`。
此前 `api-filename-sequence-67bc24e9` 在 API 重建期间请求断开，该报告也保留。
最后增加规划模式适用范围保护，未知模式或其他编辑继续走原生命令；相关回归已通过。
最终镜像和完整复测另记启动交接。本次没有修改公开题、任务预算、适配运行器、官方评分
或模型权重；新的模块也纳入后续串行验收的源码快照。

最终文件名引用契约的 13 段真实回放已完成，需求理解正确返回
`planning_mode=source_sequence`，规划输入 2603 token、输出 454 token，调用 106.669 秒，
13 个来源无重复且原生时长和范围校验通过。报告为
`checks/delivery_repair_20261113/sequence-filename-ordering-final/report.json`。
该回放使用原保存素材事实，没有重新分析原片、渲染或正式计分。实际选片和叙事顺序
仍以新一轮完整运行的官方原始报告判断。最终镜像为 `75d75c615466`，只更新本项目的
API、控制 Worker 和渲染 Worker；常驻本地模型服务继续使用原进程及 GPU 4/5。

### 第五轮启动交接

最终镜像的 API 与控制 Worker 中，`graph.py`、`sequence.py`、`context.py`、
`providers.py` 的 SHA256 均与本地源码一致。部署后再次通过真实 API 验证
`planning_mode=source_sequence` 的文件名选择、版本保存、音视频导出与下载，结果仍为
`11.mp4 -> 2.mp4`、354 帧、11.8 秒；报告为
`checks/delivery_repair_20261113/api-filename-sequence-ee04d257/report.json`。

2026-11-13 13:36:39 UTC 已显式启动第五轮，控制 PID `1192464`，子进程 `1192469`，
服务 `camcat-studio-delivery-validation.service`。当前为组装题原片分析，原生任务
`0f9bee2b-abed-47ec-97ab-c4e21d9b37a9`。控制资源上限 4 核/4 GiB、最长 2 小时，
`Restart=no`；复用 GPU 4、5，每卡进程显存预算 21135 MiB，GPU 5 的视觉生成与重排
共享预算。本轮两题通过后，才继续六例回归及完整浏览器验收，失败保留报告并停止。

输出：`/data/zjr/camcat_studio/checks/delivery_validation/20261113T133639Z-1192464/`。
当前阶段日志为该目录内 `public-editing.log`，源码快照为 `source-snapshot/`。

```bash
cd /home/zjr/Tracker/internship_project_lab/camcat_studio
watch -n 5 'bash scripts/studio.sh delivery-validation-status'
tail -f /data/zjr/camcat_studio/checks/delivery_validation/20261113T133639Z-1192464/public-editing.log
bash scripts/studio.sh stop-delivery-validation
```

此处记录修复、真实分项结果与启动状态。最终公开题成绩继续以本轮原始报告为准。

## 第五轮结束及结构化生成修复

第五轮实际报告 `runs/editing/20261113T133639Z-1192469/report.json` 已结束：
组装题在原片分析阶段反复返回 HTTP 500，原生任务最终 `dead_letter`；顺序题完成真实
13 段成片，官方 reward 为 0。总计交付 1/2、执行错误 1/2、满分 0/2、平均 reward 0。
该轮没有继续六例及浏览器阶段，原始结果保持不变。

后续使用实际安装的 XGrammar 0.1.25、Qwen tokenizer 和相同 schema 复现：
非严格属性模式允许非法 `subject_motion=walking`、重复已知属性和额外属性通过。
改为官方编译器的 `strict_mode=True` 后，三类输入均被拒绝，合法对象和逐 token 掩码检查通过。
原始复现记录为 `checks/delivery_repair_20261113/schema-acceptance-reproduction.json`，
实际编译器验收为同目录 `strict-grammar-final.xml`。
实现继续使用 [XGrammar 官方 Transformers 集成](https://github.com/mlc-ai/xgrammar/blob/main/python/xgrammar/contrib/hf.py)
和标准 JSON Schema 校验；合法结构之外的失败返回明确错误，提供一次有界纠正。

新增请求级 token、排队/预处理/编译/生成耗时和结束原因账本，失败节点也持久化。
真实接口验证了输入超限返回 422、失败版本保持不变、无原片返回 `source_required`，
以及超时后下一次请求可完成。相关记录在 `checks/delivery_repair_20261113/`。

为定位 NF4 视觉生成的耗时及质量，显式启动同一 8B 权重的三卡运行配置：
GPU 4 embedding/BF16，GPU 5 reranker/NF4，GPU 2 visual/BF16，
各卡进程预算 21135 MiB。该配置与此前双卡 NF4 成绩分别记录；没有切换权重或在线服务。
启动时源码摘要由实际模型 `/health` 返回，服务失败不自动重启。

本配置首轮业务回归 `20261113T154226Z-d811a7` 为 4/6：裁剪、字幕、译制和旧配音守卫通过，
拆分及多轮编辑失败。新增账本指出模型误把“保持当前原片范围”解释为“使用完整源文件”，
而修复契约又移除了拆分命令。现已将完整文件约束落到明确指令，并为结构性编辑保留
完整原生命令修复能力。对应六项最小回归先失败，再通过。
后端 333 项测试、Ruff/mypy 和两项真实 FFmpeg 检查通过；新一轮六例及浏览器正在执行。

六例 `20261113T155153Z-5e68ae` 已全部通过，耗时 206.57 秒。随后浏览器成功完成编辑、
首次真实导出、回滚和 409 冲突，但译制按钮因时间线无字幕而禁用，真实旅程因此失败。
失败截图与 trace 保存在 `checks/edit_regressions/20261113T155153Z-1616092/`，没有跳过该步骤。
源分析实际有旁白转录，失败原因是需求模型自行返回 `subtitles=forbid`；另一次理解输出
嵌套在 `default` 下，导致节点忽略了其中字段。

需求理解现使用原生 Pydantic `RequirementIntent` 契约和同一 XGrammar 解码，禁止额外字段，
拒绝包装对象；字幕和转场禁令必须来自明确指令。保留普通 JSON-object API 供其他调用使用。
真实 Qwen 的两条指令契约检查已通过：未指定禁令时为 auto，明确禁止字幕/转场时为 forbid。
语义方案有界修复仍失败时，HTTP/SSE 也返回 `edit_plan_invalid`、具体校验原因并保留原版本。
当前后端 338 项测试和静态检查通过，镜像 `6ee4b0906a5e` 已部署。

该版六例 `20261113T160538Z-7e43d3` 全部通过，274.51 秒；浏览器旅程
`checks/edit_regressions/20261113T160538Z-1667143/` 于 229.799 秒完成，1 通过、0 跳过、0 失败。
下载文件实际为 1080×1920、4 秒、120 帧的 H.264/AAC MP4；抽帧确认双行字幕已烧录。

重载后的文本/图像/视频 2048 维编码、重排、非法视觉输入、输入预算、失败版本保持及超时锁释放
均经过真实接口检查。普通 JSON-object 请求曾把 `summary` 返回为 `description`；该调用补上
具体字段契约并保留原断言，转录及译制复测通过。生产译制调用也补上 Pydantic 输出契约，
限制译文数量与输入字幕 ID，原有编号去重、时间窗口和语言检查继续执行。

### 画幅误判与官方画面一致性诊断

第五轮顺序题的官方明细显示 `ordering=0.0274725`，同时 `n_honest=0/13`，因此 reward 为 0。
画面检查对比的是模型实际声明的源文件和输出，不要求它先排对顺序。第一段三个检查点中的
首个 SSIM 为 0.4824，远低于官方 0.95 阈值。
实际源视频及初始工程均为 1920×1080，保存后却变成 1080×1920。
原因是画幅判断将说明中的普通词 `short` 和 `reels` 当成平台发布要求，例如 `short story overview`。
现在只识别明确平台或输出格式：YouTube Shorts、Instagram Reels、for Reels、vertical video、竖屏等；
故事、衣物 shorts 和胶片 reels 不改变画幅。
九项对应回归保留五项初始失败，再修复通过；全套后端现为 349 项通过，静态检查通过。
原顺序题提交文本的解析复现保存在 `checks/delivery_repair_20261113/short-story-canvas-reproduction.json`，
同一文本现在保留 16:9。后续真实导出仍交由原版 SSIM 检查和排序评分判断。

### 2026-11-13 完整公开题及稳定版本验收

完整新跑 `runs/editing/20261113T164154Z-1795272` 已结束，使用实际上传、重新分析、
原生 Agent、版本保存、FFmpeg 导出和未修改的官方评分器。两题均交付，执行错误 0，
满分 0/2，平均 reward `0.16506410256410256`：

| 题目 | 实际结果 | 官方 reward |
| --- | --- | --- |
| 四分镜组装 | 2/4 位置正确，正确位置的画面检查通过 | 0.25 |
| 十三段排序 | 声明来源与实际画面一致性 13/13；排序质量仍低 | 0.08012820512820512 |

画幅修复后，顺序题的来源画面一致性从历史运行的 0/13 恢复到 13/13；
当前排序 reward 及组装正确率仍达不到交付质量门槛。reward 不等于正确率。
oracle 正控为 1、缺失输出负控为 0，原始失败和最终报告均保留。

同镜像 `e6eb2af3f0c8` 的六例业务回归 `20261113T163313Z-f438aa` 为 6/6。
浏览器报告 `checks/edit_regressions/20261113T163313Z-1754561/browser.json` 为
1 通过、0 跳过、0 失败，覆盖上传、文字/图像检索、规划、多轮编辑、回滚、409 冲突、
译制、配音、渲染、播放与下载。下载实际为 1080×1920、4 秒、120 帧 H.264/AAC。
30 分钟持续译制 20/20 成功，详细范围及分位耗时见 `performance-reliability.md`。

后续逐格文字规划实验采用 CommonMark 解析和四次独立选择调用。
真实原生导出与原版评分显示同素材配对回放 reward 从 0.25 降至 0、正确位置由 2 降至 1。
该实验已撤回，源代码和失败报告保存于
`checks/delivery_repair_20261113/storyboard-rejected-source/` 与 `storyboard-replay/`。
它属于复用已分析素材的开发实验，不改写上面的完整新跑成绩。

进一步诊断直接使用固定版本官方 Qwen3-VL-Reranker 读取原视频，四个分镜各比较
12 个候选，仅输入分镜文本和视频字节；用 SciPy 的 linear_sum_assignment 求唯一分配。
模型比较耗时 54.31 秒。原生领域命令、实际渲染和原版评分完成，仍为 1/4 位置正确、
reward 0；该方案没有进入产品。原始模型分数在
`checks/delivery_repair_20261113/direct-video-reranking.json`，成片及评分在
`direct-video-reranking-render/`。当前生产实现保留新跑的原生全局规划版本。

最终部署、工程验收及质量剩余项集中记录于
[修复验收记录](repair-acceptance-20261113.md)。

### 2026-11-14 多语言 ASR 后的完整新跑

`runs/editing/20261114T003908Z-2984721` 完成实际重新上传到原版评分的全过程，
耗时 2434.00 秒。两题均成功导出、执行错误 0；组装仍为 2/4、reward 0.25；
排序位置正确 2/13、来源画面检查 13/13、reward 0.01221001221001221。
平均 reward 0.1311050061050061，满分 0/2，质量门槛仍未通过。

多语言 ASR 的功能修复、识别测试、完整浏览器验收及未采纳的视觉/排序组件实验见
[本轮质量复查](quality-repair-20261114.md)。新 ASR 的真实语言识别通过，公开排序
分数仍低于上一完整运行；这两项分别记录，保留历史结果和全部失败尝试。
