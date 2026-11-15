import type {EditingSessionResponse, JobResponse} from "./camcatApi";

type Input = {status: string; activeJob?: Pick<JobResponse, "kind" | "status">;
  editingSession?: EditingSessionResponse; renderResult?: JobResponse; agentRun?: unknown};

export function workflowPresentation(input: Input) {
  const value = input.editingSession?.state.metadata?.localization;
  const localization = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const localized = Boolean(localization.translation_job_id || localization.dubbing_job_id ||
    ["translate_subtitles", "dub_subtitles"].includes(input.activeJob?.kind ?? ""));
  const rendered = input.renderResult?.status === "succeeded" && Boolean(input.renderResult.result?.output_url);
  const reviewed = localization.reviewed_version === input.editingSession?.state_version && Boolean(localization.reviewed_version);
  const workflow = localized ? "字幕译制" : "视频剪辑";
  const assistantStatus = input.status === "error" ? "需处理" : input.status === "uploading" ? "上传中" :
    input.status === "searching" ? "处理中" : input.status === "rendering" ? "导出中" : rendered ? "成片已生成" :
    input.agentRun || localization.dubbing_job_id || localization.translation_job_id ? "草稿已生成" : "待执行";
  const nextStep = rendered ? "预览并下载交付成片" : reviewed ? "生成交付视频" : localization.dubbing_job_id ?
    "试听配音并确认交付审校" : localization.translation_job_id ? "核对译文并生成配音" :
    input.agentRun ? "检查时间线并导出" : "描述剪辑或译制需求";
  const stateLabel = localization.dubbing_job_id ? "已配音" : localization.translation_job_id ? "已翻译" : input.agentRun ? "已剪辑" : "待执行";
  return {workflow, assistantStatus, nextStep, stateLabel, rendered};
}
