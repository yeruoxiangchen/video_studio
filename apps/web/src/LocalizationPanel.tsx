import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  createCamCatApiClient, DomainEditCommand, EditingSessionResponse, JobResponse, LocalizationContext,
} from "./camcatApi";
import type { LocalizationCue } from "./generated/api";

type DraftRow = { text?: string; start?: string; end?: string };
type Draft = { base: number; rows: Record<string, DraftRow> };
type AudioPreview = { cue_id: string; audio_url: string; speed: number; padding_seconds: number; cache_hit: boolean };
type Result = { cues?: LocalizationCue[]; audio?: AudioPreview[]; source_version?: number;
  applied_version?: number; subtitle_download_url?: string; cache_hits?: number };
type Props = { api: ReturnType<typeof createCamCatApiClient>; session: EditingSessionResponse;
  onChanged: () => Promise<void>; onRenderReady?: (job: JobResponse) => void; disabled?: boolean };

const control = "rounded-lg border border-[#30353a] bg-[#111315] px-2 py-1.5 text-xs text-[#e8edf1] disabled:opacity-40";
const statuses: Record<string, string> = { queued: "排队中", running: "处理中", succeeded: "已生成",
  failed: "失败", cancelled: "已取消", dead_letter: "需要处理" };
const active = (job?: JobResponse) => job?.status === "queued" || job?.status === "running";
const message = (error: unknown) => error instanceof Error ? error.message : "操作失败，请重试";
const deliveryLabels: Record<string, string> = {needs_translation: "校对原文并翻译", needs_dubbing: "试听并应用配音",
  needs_review: "确认交付审校", ready_to_render: "生成交付视频", rendering: "正在导出", ready: "可下载交付包", failed: "导出需要重试"};

export default function LocalizationPanel({ api, session, onChanged, onRenderReady, disabled }: Props) {
  const sessionId = session.editing_session_id;
  const storageKey = `camcat:localization:${sessionId}`;
  const [context, setContext] = useState<LocalizationContext>();
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
      if (saved && typeof saved.base === "number" && saved.rows && typeof saved.rows === "object") return saved;
    } catch { /* A browser storage failure must not block the editor. */ }
    return {base: session.state_version, rows: {}};
  });
  const [language, setLanguage] = useState("en");
  const [sourceTrack, setSourceTrack] = useState("");
  const [glossary, setGlossary] = useState("");
  const restoredSettings = useRef(false);
  const [editMode, setEditMode] = useState<"source" | "target">("target");
  const [rewrite, setRewrite] = useState(false);
  const [originalVolume, setOriginalVolume] = useState(0);
  const [selected, setSelected] = useState("");
  const [job, setJob] = useState<JobResponse>();
  const [pollEpoch, setPollEpoch] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => { try { sessionStorage.setItem(storageKey, JSON.stringify(draft)); } catch { /* Keep in-memory draft. */ } }, [draft, storageKey]);
  const reload = useCallback(async (rebase = false) => {
    const current = await api.getLocalizationContext(sessionId, sourceTrack || undefined);
    setContext(current);
    if (!restoredSettings.current) {
      restoredSettings.current = true;
      let saved: string | null = null;
      try { saved = sessionStorage.getItem(`${storageKey}:glossary`); } catch { /* Server values remain usable. */ }
      setGlossary(saved ?? Object.entries(current.glossary ?? {}).map(([a, b]) => `${a} = ${b}`).join("\n"));
      setLanguage(current.language || "en");
    }
    setSourceTrack(previous => previous || current.source_track_id || "");
    setSelected(previous => previous || current.jobs[0]?.job_id || "");
    setDraft(previous => {
      if (rebase) return {base: current.base_version, rows: Object.fromEntries(
        Object.entries(previous.rows).map(([id, value]) => [id, {text: value.text}]),
      )};
      return Object.keys(previous.rows).length ? previous : {base: current.base_version, rows: {}};
    });
  }, [api, sessionId, sourceTrack, storageKey]);
  useEffect(() => { if (restoredSettings.current) { try { sessionStorage.setItem(`${storageKey}:glossary`, glossary); } catch { /* Keep the editable value in memory. */ } } }, [glossary, storageKey]);

  useEffect(() => { let alive = true; void reload().catch(e => { if (alive) setError(message(e)); });
    return () => { alive = false; }; }, [reload, session.state_version]);
  useEffect(() => {
    if (!selected) return;
    let disposed = false, failures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const current = await api.getJob(selected);
        if (disposed) return;
        failures = 0;
        setJob(current);
        if (active(current)) timer = setTimeout(() => void poll(), 1200);
        else {
          if (Number(current.result?.applied_version ?? 0) > session.state_version) await onChanged();
          await reload();
        }
      } catch (e) {
        if (disposed) return;
        if (++failures < 3) timer = setTimeout(() => void poll(), 1500);
        else setError(`${message(e)}。任务仍保留，可点击刷新任务重新查看。`);
      }
    }
    void poll();
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, [api, selected, pollEpoch, reload, session.state_version, onChanged]);

  const cues = editMode === "source" ? context?.source_cues ?? [] : context?.cues ?? [];
  const editableCues = [...new Map([...(context?.source_cues ?? []), ...(context?.cues ?? [])].map(c => [c.cue_id, c])).values()];
  const row = (cue: LocalizationCue) => ({text: draft.rows[cue.cue_id]?.text ?? cue.text,
    start: draft.rows[cue.cue_id]?.start ?? String(cue.start_us / 1_000_000),
    end: draft.rows[cue.cue_id]?.end ?? String((cue.start_us + cue.duration_us) / 1_000_000)});
  const dirty = editableCues.some(cue => { const value = row(cue); return value.text !== cue.text ||
    Number(value.start) * 1_000_000 !== cue.start_us ||
    Number(value.end) * 1_000_000 !== cue.start_us + cue.duration_us; });
  const stale = Boolean(context && draft.base !== context.base_version && Object.keys(draft.rows).length);
  const currentJob = job?.job_id === selected ? job : context?.jobs.find(candidate => candidate.job_id === selected);
  const result = currentJob?.result as Result | undefined;
  const locked = busy || disabled;
  const delivery = context?.delivery;
  const deliveredVideo = delivery?.render_job?.result?.["output_url"];
  const reportedRender = useRef("");
  useEffect(() => {
    const rendered = delivery?.render_job;
    if (delivery?.state === "ready" && rendered?.status === "succeeded" && context?.base_version === session.state_version && reportedRender.current !== rendered.job_id) {
      reportedRender.current = rendered.job_id;
      onRenderReady?.({...rendered, status: "succeeded", result: rendered.result ?? undefined, error: rendered.error ?? undefined});
    }
  }, [delivery, context?.base_version, session.state_version, onRenderReady]);
  useEffect(() => {
    if (delivery?.state !== "rendering") return;
    const timer = setTimeout(() => void reload().catch(e => setError(message(e))), 1500);
    return () => clearTimeout(timer);
  }, [delivery, reload]);
  const audioById = useMemo(() => new Map((result?.audio ?? []).map(item => [item.cue_id, item])), [result?.audio]);

  function edit(id: string, field: keyof DraftRow, value: string) {
    setDraft(previous => ({base: previous.base, rows: {...previous.rows,
      [id]: {...previous.rows[id], [field]: value}}}));
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function save() {
    if (!context || stale) throw new Error("请先刷新字幕，草稿文本会保留");
    const commands: DomainEditCommand[] = editableCues.filter(c => draft.rows[c.cue_id]).map(cue => {
      const value = row(cue), start = Number(value.start), end = Number(value.end);
      if (!value.text.trim() || !Number.isFinite(start + end) || start < 0 || end <= start)
        throw new Error("请填写有效字幕和起止时间");
      return {type: "update_text_segment", segment_id: cue.cue_id, text: value.text.trim(),
        start_us: Math.round(start * 1_000_000), duration_us: Math.round((end - start) * 1_000_000)};
    });
    await api.applyEditCommands(sessionId, context.base_version, commands, "编辑译制字幕");
    setDraft({base: context.base_version + 1, rows: {}});
    await onChanged(); await reload(); setNotice("字幕已保存为新版本。");
  }
  async function submit(kind: "translate" | "dub") {
    if (!context || dirty || stale) throw new Error("请先保存字幕修改，再提交译制任务");
    const terms: Record<string, string> = {};
    for (const line of glossary.split("\n").filter(line => line.trim())) {
      const split = line.indexOf("=");
      if (split <= 0 || !line.slice(split + 1).trim()) throw new Error("术语表请每行填写：原词 = 译词");
      terms[line.slice(0, split).trim()] = line.slice(split + 1).trim();
    }
    const queued = kind === "translate"
      ? await api.translateSubtitles(sessionId, {base_version: context.base_version, target_language: language,
        source_track_id: sourceTrack || undefined, glossary: terms})
      : await api.dubSubtitles(sessionId, {base_version: context.base_version,
        voice: context.tts_voice, allow_duration_rewrite: rewrite, original_audio_volume: originalVolume});
    setJob(queued); setSelected(queued.job_id); setPollEpoch(value => value + 1);
    await reload();
  }
  async function apply() {
    if (!context || !selected || dirty || stale) throw new Error("请先保存或刷新当前字幕");
    await api.applyLocalizationResult(sessionId, selected, context.base_version);
    await onChanged(); await reload(); setJob(await api.getJob(selected));
    setNotice("结果已写入新的草稿版本，可继续剪辑、回滚或导出。");
  }

  return <section aria-label="译制工作区" className="max-h-[380px] space-y-3 overflow-y-auto px-4 py-3 text-xs text-[#a7b0ba]">
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-white">字幕与配音 · v{context?.base_version ?? session.state_version}</span>
      <select aria-label="翻译原文字幕轨" className={control} value={sourceTrack} disabled={locked || dirty} onChange={e => setSourceTrack(e.target.value)}>
        <option value="">选择原文字幕轨</option>{context?.tracks.filter(t => !t.is_translation).map(t =>
          <option key={t.track_id} value={t.track_id}>{t.name}（{t.cue_count} 句）</option>)}
      </select>
      <select aria-label="目标语言" className={control} value={language} onChange={e => setLanguage(e.target.value)}>
        <option value="en">英文</option><option value="zh">中文</option><option value="ja">日文</option>
        <option value="fr">法文</option><option value="es">西班牙文</option>
      </select>
      <button type="button" className={control} disabled={locked || dirty || stale || !sourceTrack || active(currentJob)} onClick={() => void perform(() => submit("translate"))}>翻译字幕</button>
      <button type="button" className={control} disabled={locked || !dirty || stale} onClick={() => void perform(save)}>保存字幕修改</button>
      <button type="button" className={control} disabled={locked} onClick={() => void perform(async () => { await onChanged(); await reload(true); })}>刷新字幕</button>
    </div>
    <p>翻译后先检查并写入时间线，再生成配音；生成结果会保留原版本。字幕修改草稿保存在当前标签页。</p>
    {error && <p role="alert" className="rounded-lg border border-red-900 bg-red-950/30 p-2 text-red-200">{error}</p>}
    {notice && <p role="status" className="text-emerald-300">{notice}</p>}
    {stale && <p role="alert" className="text-amber-200">时间线已有新版本，草稿已保留。刷新字幕会载入新时间并保留同编号的文本草稿。</p>}
    {context?.audio_outdated && <p className="text-amber-200">字幕已修改，需要重新配音后再导出。</p>}
    {context?.translation_outdated && <p role="status" className="text-amber-200">原文已修改，请重新翻译并应用后，再生成配音或交付。</p>}
    {!!context?.review_hints?.length && <details className="rounded-lg border border-amber-900/70 p-2"><summary className="cursor-pointer text-amber-200">原始识别有 {context.review_hints.length} 处建议复核</summary>
      {context.review_hints.map((hint, index) => <p key={index} className="mt-2">{hint.filename} · {hint.start_seconds.toFixed(1)}–{hint.end_seconds.toFixed(1)} 秒：建议核对 {hint.words.join("、") || `${hint.low_confidence_word_count} 个低置信词`}。</p>)}
    </details>}
    <details><summary className="cursor-pointer">术语表</summary><textarea aria-label="翻译术语表" className={`${control} mt-2 w-full`} rows={2} placeholder="原词 = 译词，每行一项" value={glossary} onChange={e => setGlossary(e.target.value)} /></details>
    <div className="flex items-center gap-2"><button type="button" className={control} aria-pressed={editMode === "source"} onClick={() => setEditMode("source")}>原文校订</button>
      <button type="button" className={control} aria-pressed={editMode === "target"} onClick={() => setEditMode("target")}>译文校订</button>
      <span>{editMode === "source" ? "改正识别错词后，保存并重新翻译。" : "项目术语随应用的翻译结果保存。"}</span></div>
    <div className="space-y-2">
      {!cues.length && <div className="rounded-lg border border-dashed border-[#30353a] p-3 space-y-2"><p>可直接载入原片及识别字幕，保留原片顺序和时长开始译制。</p>
        <button type="button" className={control} disabled={locked} onClick={() => void perform(async () => {
          await api.prepareLocalizationSource(sessionId, session.state_version); await onChanged(); await reload();
        })}>载入原片与识别字幕</button></div>}
      {cues.map((cue, index) => <div key={cue.cue_id} className="grid gap-2 rounded-lg border border-[#25282b] bg-[#101213] p-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_140px]">
        <div><span className="text-[#68727c]">{index + 1} · 原文</span><p className="mt-1 whitespace-pre-wrap text-[#b8c0c9]">{cue.source_text}</p></div>
        <label className="space-y-1"><span>{editMode === "source" ? "当前原文" : "当前字幕"}</span><textarea aria-label={editMode === "source" ? `原文字幕 ${index + 1} 文本` : `字幕 ${index + 1} 文本`} rows={2} className={`${control} w-full`} value={row(cue).text} onChange={e => edit(cue.cue_id, "text", e.target.value)} /></label>
        <div className="space-y-1"><label className="flex items-center justify-between gap-1">起点秒<input aria-label={`字幕 ${index + 1} 起点秒`} type="number" min="0" step="0.001" className={`${control} w-20`} value={row(cue).start} onChange={e => edit(cue.cue_id, "start", e.target.value)} /></label>
          <label className="flex items-center justify-between gap-1">终点秒<input aria-label={`字幕 ${index + 1} 终点秒`} type="number" min="0" step="0.001" className={`${control} w-20`} value={row(cue).end} onChange={e => edit(cue.cue_id, "end", e.target.value)} /></label></div>
      </div>)}
    </div>
    <div className="flex flex-wrap items-center gap-2 border-t border-[#25282b] pt-3">
      <span>音色：{context?.tts_voice ?? "尚未配置"}</span>
      <select aria-label="原声处理" className={control} value={originalVolume} onChange={e => setOriginalVolume(Number(e.target.value))}><option value={0}>替换原声</option><option value={0.25}>轻量保留原声</option></select>
      <label className="flex items-center gap-1"><input type="checkbox" checked={rewrite} onChange={e => setRewrite(e.target.checked)} />允许一次时长改写，确认后再应用</label>
      <button type="button" className={control} disabled={locked || dirty || stale || !context?.target_track_id || !context.tts_configured || context.translation_outdated || !context.tts_languages.includes(context.language ?? "") || active(currentJob)} onClick={() => void perform(() => submit("dub"))}>生成配音</button>
      {!context?.tts_configured && <span className="text-amber-200">请先配置配音服务。</span>}
      {context?.target_track_id && !context.tts_languages.includes(context.language ?? "") && <span className="text-amber-200">当前音色仅支持 {context.tts_languages.join("、")} 配音；所选语言仍可下载翻译字幕。</span>}
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label="译制任务记录" className={control} value={selected} onChange={e => setSelected(e.target.value)}><option value="">选择译制任务</option>{context?.jobs.map(j => <option key={j.job_id} value={j.job_id}>{j.kind === "translate_subtitles" ? "翻译" : "配音"} · {statuses[j.status]} · {new Date(j.created_at ?? "").toLocaleTimeString()}</option>)}</select>
      <button type="button" className={control} disabled={locked} onClick={() => { setError(""); setPollEpoch(v => v + 1); void reload().catch(e => setError(message(e))); }}>刷新任务</button>
      {active(currentJob) && <><span role="status">{statuses[currentJob!.status]} {Math.round(currentJob!.progress * 100)}%</span><button type="button" className={control} disabled={locked} onClick={() => void perform(async () => { await api.cancelJob(selected); setPollEpoch(v => v + 1); })}>取消任务</button></>}
      {currentJob && ["failed", "cancelled", "dead_letter"].includes(currentJob.status) && <button type="button" className={control} disabled={locked} onClick={() => void perform(async () => { await api.retryJob(selected); setPollEpoch(v => v + 1); })}>重试任务</button>}
      {currentJob?.error && <p role="alert" className="text-red-200">{currentJob.error}</p>}
    </div>
    {result?.cues && <div className="space-y-2 rounded-lg border border-[#30353a] p-2">
      <div className="flex flex-wrap items-center gap-2"><span>生成结果 · 基于 v{result.source_version}</span>
        <button type="button" className={control} disabled={locked || dirty || stale || currentJob?.status !== "succeeded" || Boolean(result.applied_version) || result.source_version !== context?.base_version} onClick={() => void perform(apply)}>确认并写入时间线</button>
        {result.applied_version && <span>已写入 v{result.applied_version}</span>}
        {result.subtitle_download_url && <a className="text-sky-300 underline" href={result.subtitle_download_url} download>下载 SRT</a>}
        {result.cache_hits !== undefined && <span>复用 {result.cache_hits} 句音频</span>}
      </div>
      {result.cues.map((cue, i) => { const audio = audioById.get(cue.cue_id); return <div key={cue.cue_id} className="border-t border-[#25282b] pt-2"><p>{i + 1}. {cue.text}</p>{audio && <div className="mt-1 flex flex-wrap items-center gap-2"><audio aria-label={`配音 ${i + 1} 试听`} controls preload="none" src={audio.audio_url} className="h-8 max-w-full" /><span>{audio.speed.toFixed(2)}× · 补齐 {audio.padding_seconds.toFixed(2)} 秒</span></div>}</div>; })}
    </div>}
    {delivery && <section aria-label="英文视频交付" className="space-y-2 rounded-lg border border-[#30353a] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-white">交付 · {deliveryLabels[delivery.state]}</span>
        {context?.expires_at && <span>原片保留至 {new Date(context.expires_at).toLocaleTimeString()}，请及时下载</span>}</div>
      <p>校对原文 → 翻译审校 → 配音试听 → 确认版本 → 导出交付包</p>
      {(delivery.blockers ?? []).map((item, index) => <p key={index} className="text-amber-200">{item}</p>)}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={control} disabled={locked || dirty || stale || delivery.state !== "needs_review"} onClick={() => void perform(async () => {
          await api.confirmLocalizationReview(sessionId, context!.base_version); await onChanged(); await reload();
          setNotice("交付审校已确认。后续修改会要求重新审校。");
        })}>已校对字幕并试听，确认交付审校</button>
        <button type="button" className={control} disabled={locked || dirty || stale || !["ready_to_render", "failed"].includes(delivery.state)} onClick={() => void perform(async () => {
          await api.renderEditingSession(sessionId, context!.base_version); await reload(); setNotice("交付视频已进入导出队列。");
        })}>生成交付视频</button>
        <button type="button" className={control} disabled={locked || dirty || stale || delivery.state !== "ready" || !delivery.render_job} onClick={() => void perform(async () => {
          const blob = await api.downloadLocalizationDelivery(sessionId, delivery.render_job!.job_id, context!.base_version);
          const url = URL.createObjectURL(blob), link = document.createElement("a");
          link.href = url; link.download = `video-delivery-v${context!.base_version}.zip`; document.body.appendChild(link); link.click(); link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 30_000); setNotice("交付包已下载，包含视频、三份字幕、术语表和交付清单。");
        })}>下载交付包</button>
        {delivery.state === "rendering" && <span role="status">导出 {Math.round((delivery.render_job?.progress ?? 0) * 100)}%</span>}
      </div>
      {delivery.state === "ready" && typeof deliveredVideo === "string" && <video aria-label="交付成片预览" className="max-h-48 w-full rounded-lg bg-black" controls preload="metadata" src={deliveredVideo} />}
    </section>}
  </section>;
}
