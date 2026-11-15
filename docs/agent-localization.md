# 文本与面板译制

当前产品界面统一称为「剪辑工作台」。`camcat` 包名、环境变量和运行目录保留兼容；上游源码及许可记录在 `upstream.lock.json`、`LICENSE`。

## 两个入口

- 面板：上传原片后，在「译制」点击「载入原片与识别字幕」。保留原片顺序和时长，将真实 ASR 时间戳映射到原片时间线；已有字幕时间线会保留。
  字幕排除重叠的 `event_context` 检索窗口，合并同一片段内停顿不超过 0.5 秒的短碎句，
  每句最多 12 秒、200 字符，保留原识别文字和原分析证据。随后可逐步翻译、校订、配音、试听与应用。
- 聊天：输入「把原文字幕翻译成英文并配音」「只翻译成英文」或「给当前译文配音」。工具生成结果自动写入新的草稿版本，最终交付仍需手动确认审校。

时长改写默认为关闭，只有用户明确允许才启用。超出真实字幕时间窗会失败并保留已生成的翻译版本，可延长字幕窗口或修改译文后重新配音；不会改变原片总时长以掩盖失败。

## 工具与持久化

`POST /api/v1/editing/sessions/{id}/agent/dispatch` 接收现有 `AgentEditRequest`。普通剪辑快速返回 `action=edit`，由原有剪辑流处理；包含译制意图的请求使用真实本地 Qwen 结构化解析，返回 `translate`、`dub`、`translate_and_dub` 或 `clarify`。返回 `session` 和已入队的真实 `job`，不等待生成完成。

用户疑问、否定或目标语言不明确时应解释或澄清；模型只能选择有类型和边界的工具参数，不能输出任意领域命令、审校确认或渲染指令。实际请求仍经过语言/音色、字幕、所有者、会话有效期和版本校验。

`localization/operations.py` 为面板和 Agent 共用的提交、应用服务。翻译与配音继续使用原来的两类 Job、真实 Qwen/Piper 与对象存储，未增加另一套翻译实现或数据库枚举。`agent_action` 记录任务来源及后续操作。

连续任务完成翻译后，在同一个事务中写入：

1. 翻译结果及其 `applied_version`。
2. 基于新版本的配音任务及 `next_job_id`。
3. 原有领域命令 CAS、新版本和审计补丁。

事务失败整体回滚。配音 Worker 应用声音时使用相同 CAS；人工已保存更新版本时，旧任务以版本冲突失败，不重复覆盖。浏览器只负责展示与轮询，任务接续在后台完成。面板仍可查询、试听、取消和重试相关任务。

`POST /localization/prepare` 同样携带 `base_version`，用于从实际原片和 ASR 建立初始草稿；没有识别字幕时返回有说明的 422。

## 验证方法

```bash
bash scripts/studio.sh unit
bash scripts/studio.sh lint
bash scripts/studio.sh contracts --check
bash scripts/studio.sh test-web
bash scripts/studio.sh build-web

# 已有真实、未过期的原片分析任务：
export CAMCAT_BUSINESS_SOURCE_JOB=<source-analysis-job-id>
PYTHONPATH=apps/api .venv/bin/python -B -m pytest -q tests/integration/test_localization_agent_real.py

# 带口播的真实 30 秒输入，过程、截图、录屏和交付包保存到数据盘：
export CAMCAT_AGENT_DEMO_VIDEO=/data/zjr/camcat_studio/demos/user_trial_20261115/earth_zh_30s_input.mp4
# 可选：使用自撰脚本进行原文人工校订，未替换任何模型输出。
export CAMCAT_AGENT_DEMO_REFERENCE=/data/zjr/camcat_studio/demos/user_trial_20261115/reference_zh.txt
bash scripts/studio.sh browser e2e/agent-localization.spec.ts
```

API 集成包括普通剪辑分流、不支持的配音语言、连续任务、应用幂等、人工版本冲突及审校门槛。输入中的语句时间窗可人工调整；模型输出来自真实服务。浏览器覆盖原片上传、直接面板入口、文本译制、试听、确认、FFmpeg 导出、真实播放和 ZIP 下载。

本轮为功能编排验收；质量分数仍引用 `docs/localization-quality-20261115.md` 中固定 v7 协议，不将新 UI 验证当作质量重测。


此次交付的实际结果：后端 543、前端 37 项单元测试、真实 API 2 项、浏览器三条旅程逐条通过；验收摘要见 [workstation-acceptance-20261115.json](workstation-acceptance-20261115.json)。
