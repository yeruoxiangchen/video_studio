# CamCat 当前结果、代码问题与标准评测路线

审阅日期：2026-11-13（UTC）。本次读取两轮原始报告、当前源码、失败 Graph Run 和会话，
并查阅下列作者仓库与官方评测文档。本文保留六例回归结束后的审阅快照；当时公开
benchmark 尚未运行。此后已完成公开检索及两轮剪辑小测，并继续部署修复，最新状态见
[公开数据小测与改动同步](public-benchmarks.md)。本文的源码与哈希核对结论对应原审阅时点。

## 1. 目前完成到了哪里

工程已有真实的上传分析、检索、领域命令、版本保存、字幕翻译、配音和 FFmpeg 导出链路。
`docs/localization-verification.md` 和 `docs/performance-reliability.md` 记录了此前真实集成、
浏览器和 30 分钟译制流程测试。这些记录说明对应链路曾执行成功；本次编辑修复的浏览器验收仍待完成。

| 六例任务 | 首轮 | 修复后 | 修复后单例耗时 |
| --- | --- | --- | --- |
| 原片第 2–4 秒裁剪 | 源起点错误、画布覆盖、空字幕轨 | 通过 | 27.567 秒 |
| 只改字幕 | 竖屏被改成横屏 | 通过 | 27.356 秒 |
| 中点拆分 | 画布覆盖、空字幕轨 | 通过 | 24.718 秒 |
| 两轮修改 | 第二轮覆盖第一轮竖屏 | 第一轮生成方案时模型接口 408 | 230.748 秒 |
| 翻译及配音 | 自动检查通过 | 自动检查通过 | 39.334 秒 |
| 修改字幕后拦截旧配音 | 通过 | 通过 | 0.194 秒 |

Agent 为 **0/4 → 3/4**，流程与守卫各 **1/1**；最新总计 **5/6**，六例总耗时 425.73 秒。
翻译忠实度和声音自然度仍列为人工待审，不能由关键词与音频窗口检查代替。

首轮报告：`/data/zjr/camcat_studio/checks/small_eval/20261113T001557Z-c1aebf/report.json`。
修复后报告：`/data/zjr/camcat_studio/checks/small_eval/20261113T005115Z-a93d01/report.json`。
总流程：`/data/zjr/camcat_studio/checks/edit_regressions/20261113T005114Z-3247708/status.json`。

六例执行器的 `completed` 表示全部用例已执行，父流程检查失败数后正确返回 `failed`，
并停在六例阶段。此轮浏览器没有运行，两个状态含义一致。
失败 Graph Run 为 `c2c3f3e3-ee51-4682-8936-35be4b807b21`，已完成理解、查询规划和检索。
只读 API 查询显示会话 `e236b3f0-2849-420d-ac80-434006987389` 仍为版本 2，保留原 1280×720 画布。
审阅时 `/health/ready` 返回 `ready`。

## 2. 当前数据和结论有多可靠

可用的部分：任务、评测器和运行器在两轮之间保持同一 SHA256；当前报告记录的 7 个源码哈希
也与本地一致。两轮输入哈希相同，报告保存真实前后状态、独立条件检查和实际导出文件。
NASA 素材来源、视觉素材使用说明和排除原配乐的记录位于
`/data/zjr/camcat_studio/demos/earth_full_20261111/provenance.json`。

覆盖范围：**同一段 12 秒静音素材、人工建立的起始时间线、人工写入的两句字幕、每例一次。**
起始片段特意有 1.056 秒原片偏移，这有助于发现原片时间和时间线时间混淆。
人工字幕属于输入条件，后续 Agent、翻译、TTS 和导出使用实际服务。

这套数据适合做固定开发回归。修复已经依据这六例进行，它们也就承担了开发集的作用。
它们尚未覆盖真实语音识别质量、多素材叙事、长视频、噪声、说话人切换及未见过的表达。
重复同一素材能观察运行波动，素材多样性仍需公开数据或独立素材提供。
六例中没有检索标注，检索节点成功只表明调用完成，不能计算检索准确率。

## 3. 本次实际复现的代码问题

复现直接加载纯函数，未调用模型，也未改写运行服务。

| 位置 | 输入 | 应有含义 | 当前函数结果 |
| --- | --- | --- | --- |
| `editing/policies.py:23` | 先改成16:9，算了，最终改成9:16。 | 最新更正为 9:16 | 16:9 |
| 同上 | 不要改16:9而是改成9:16。 | 修改为 9:16 | None |
| 同上 | 把字幕改成‘9:16’，其他设置保持不变。 | 保留画布 | 9:16 |
| `editing/policies.py:75` | 将主视频裁剪为原片第2秒至第4秒，不对，改为原片第5秒至第7秒。 | 原片 5–7 秒 | 原片 2–4 秒 |

原因分别是：按固定比例顺序选第一个命中；整句否定过滤把肯定更正一起移除；引号类型覆盖不足；
时间更正的第二句不含“裁剪/截取”等前缀，被旧区间提取器漏掉。
这些值会进入服务器画布选择或源区间约束。特别是旧的时间约束可能把模型正确提出的最新区间拒绝。
修复应统一产生结构化编辑意图，明确引号内容、修改对象、否定、更正顺序、原片时间/时间线时间；
用这些例子先做回归，再检查最终领域命令与成片。

`scripts/small_eval_checks.py:132` 的翻译检查也有明确盲点。下面两句意思与原文不符，
但当前 `check_translation` 的全部检查仍通过：

```text
c1: Do not welcome anyone to CamCat.
c2: Earth has no story. Do not share it in three steps.
```

它们保留了 CamCat、welcome、Earth、story、three，因此可通过关键词检查。
现有 1/1 应继续描述为“明示词项、时间窗口及导出检查通过”；语义质量应增加标准翻译评测与人工复核。

另有两个影响定位和评分的问题：

- `QwenChatClient._json_completion_once` 返回解析后的内容，丢弃 `usage` 和 `finish_reason`。
  服务虽能返回 token 数，业务报告没有逐次调用账本。408 被转换为错误字符串时也丢失具体响应详情。
  本地生成默认预算为 180 秒，包含等锁时间；目前报告不足以分开排队、生成和修复尝试的耗时。
- Graph Run 仅持久化成功流出的节点。此次失败记录只有三个成功节点；失败规划节点的耗时、
  中间状态和调用次数未完整落库。需要在失败路径持久化节点与调用事件。

`scripts/run_small_eval.py:346` 的 FFprobe 检查回答的是帧数、分辨率与音轨是否存在。
它根据最终工程计算导出参数，适合验证渲染与工程一致；内容选对、字幕确实烧录和声音可懂，
需要额外的独立内容检查。分辨率等交付要求也应另与任务 brief 的固定期望比较。

## 4. 推荐接入哪些公开或标准 benchmark

### 剪辑任务：AgenticVBench

[作者仓库](https://github.com/PhiloLabs/agentic-vbench)公开了 100 项后期任务，
分为 Assembly、Sequencing、Repair、Repurpose，并基于 Harbor 执行。
建议先用 Assembly、Sequencing 各 2 题开发 CamCat 适配器，再冻结适配器，正式运行这两个完整 family。
它们共 46 题；成绩标明对应 family，完整 100 题另行执行。
Repair 含当前产品没有的修复能力，需要预先列明支持范围。
Repurpose 更接近长视频转短竖屏，官方 judge 需要额外模型凭据和费用预算。

适配器只把公开输入素材与 brief 交给 CamCat 原生 API，收集产品实际输出，交由原版 verifier 评分。
任务期望、参考答案和 oracle 不进入 Agent 上下文。先在隔离评测侧运行官方 oracle 与坏答案对照，
核实 verifier 可区分好坏结果；官方文档中一些保存性子分会让未修改输入获得少量分数，应以实际
[verifier 定义](https://github.com/PhiloLabs/agentic-vbench/blob/main/docs/VERIFIER_DESIGN.md)为准。

这是作者公开的专业任务基准；CamCat 的版本冲突、多轮保持和重启恢复仍由产品回归单独验收。

### 检索：Qwen 官方 MMEB-v2

[Qwen 官方复现入口](https://github.com/QwenLM/Qwen3-VL-Embedding#reproducing-evaluation)
提供 embedding 和 reranker 评测代码。现有项目已经固定官方代码版本
`393e2978d27852b0d0230d6994f37f9c15bed73c`，并包含模型封装、数据读取和指标代码，
后续补齐同版本的配置、脚本和数据入口即可复用。

第一项选其 MSR-VTT `test_1k/test` 文本到视频检索任务，保留官方查询、候选集合、标签、
抽帧和指标定义。按官方配置输出 Hit/Recall@1/5/10 等项目，保存完整候选排序。
先复现官方模型路径，再比较产品服务路径；将 4096 维参考设置与产品 2048 维 MRL、
BF16 与 NF4 等配置分别列出，避免把不同配置的差异误归因于检索代码。

产品流水线另作同数据上的消融：dense → 加 BM25/过滤与融合 → 加真实 reranker，
同时记录召回、重排质量和延迟。索引文本只能来自允许的输入或产品分析，禁止把测试查询、
标准 caption 或相关性标签反写成素材描述。产品分块/融合适配结果单列，保留原协议成绩。
MMEB 的候选片段检索与自由预测起止时间的定位任务是不同协议；后者需使用对应数据集的
官方 tIoU/mAP 等评测器。

### 译制：FLEURS 与 FLORES

[Google FLEURS](https://huggingface.co/datasets/google/fleurs)可用于真实音频转录，
先测当前中文 ASR 覆盖的语言，报告 CER，并固定文本归一化规则。
[FLORES-200 官方说明](https://github.com/facebookresearch/flores/tree/main/flores200)
给出 chrF++、spBLEU 的计算方式；可冻结该版本的中英 devtest 作为翻译对照。
新版本 [FLORES+](https://huggingface.co/datasets/openlanguagedata/flores_plus)由 OLDI 在 HF 维护，
数据文件需接受访问条件；版本与语言对在运行前锁定。
这些成绩分别回答转录与翻译质量，配音的自然度、语速和时间对齐另留真人盲评及音频检查。

### 全流程中期目标：TimelineBench

[TimelineBench](https://timelinebench.tensortest.com/about)覆盖 56 个从原始制作素材到成片的任务，
与项目长期目标相近。但其[素材访问说明](https://timelinebench.tensortest.com/docs/inputs)
明确由维护方持有素材并安排内部评测，不提供本地下载。它适合作为后续外部评测目标，
当前本地接入优先从可运行的 AgenticVBench 和 Qwen 官方评测做起。

## 5. 哪些部分继续参考成熟实现

| 模块 | 当前与建议 |
| --- | --- |
| 多模态 embedding/reranker | 已使用 Qwen 官方封装，继续锁定版本，并用官方数据/脚本核对封装前后行为 |
| 结构化生成服务 | 当前已接 XGrammar 的 HF 集成；补调用记录后，对照 [vLLM 官方结构化输出](https://docs.vllm.ai/en/latest/features/structured_outputs/)进行冻结请求比较，再决定服务实现 |
| 时间范围与帧换算 | 参考 [OpenTimelineIO RationalTime/TimeRange](https://opentimelineio.readthedocs.io/en/latest/api/python/opentimelineio.opentime.html)，与现有微秒/整数帧模型做边界对照 |
| 字幕分句、术语与配音节奏 | 参考 [VideoLingo](https://github.com/Huanshere/VideoLingo)的实际流程，按模块适配，保留 CamCat 的版本、队列及时间线入口 |
| 公开任务执行与评分 | 复用 Harbor 和 benchmark 原版 verifier；产品自身的变更保持、CAS、恢复语义继续独立测试 |

## 6. 下一步实施顺序与验收

1. **补证据与修复回归。** 为每次模型调用保存 request/run/node/attempt、模型版本与精度、
   schema 哈希、排队/输入处理/生成耗时、token、结束原因和失败类别；失败节点也落库。
   为上面的否定、更正和引用文本写最小失败测试。行为修改按仓库 TDD 合同执行。
2. **建立官方检索对照。** 先接一个完整 MSR-VTT `test_1k`，复用同版本官方程序；
   记录模型、数据 revision、候选规模、MRL 维度、抽帧、精度与归一化配置。
   数据、缓存和输出均在 `/data/zjr/camcat_studio/benchmarks/`。
3. **接剪辑基准。** 先完成四个开发任务的生产 API 适配与官方 verifier 检查，再锁定支持范围、
   时间预算和测试 task ID。正式分 family 跑，保留每题产物、轨迹、官方 reward 与异常。
4. **结束本轮修复验收。** 固定六例逐例重复 3 次观察稳定性，全部原始结果保留；
   硬约束全部满足且无超时，再执行本轮尚未运行的真实浏览器回归。

成绩至少分成：首次提案合法率、产品有界修复后的任务成功率、语义/约束失败、服务超时、
渲染失败和未执行。端到端分母保留超时与失败，显式重试的结果单列。
公开 benchmark 使用其原有指标与完整约定范围；内部回归报告自己的严格条件。
多次尝试报告波动，统计时按任务/素材聚合，不能把同一题的重复运行当成新的独立样本。
开发任务用于改代码，保留任务用于冻结后的最终评测。

本次仅新增审阅记录和补齐六例结果说明，未修改生产行为、评分规则或原始报告，未重启模型或启动长测。
