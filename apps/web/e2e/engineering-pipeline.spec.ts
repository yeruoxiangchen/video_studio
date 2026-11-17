import {expect, test} from "@playwright/test";
import {writeFileSync} from "node:fs";
import {writeFile} from "node:fs/promises";
import {performance} from "node:perf_hooks";

const input = process.env.CAMCAT_PIPELINE_VIDEO;
const api = process.env.CAMCAT_E2E_API_BASE ?? "http://127.0.0.1:18960";
test.skip(!input, "Requires the real licensed 30 second Chinese narrated source");
test.use({viewport: {width: 1920, height: 1080}, actionTimeout: 30_000, navigationTimeout: 30_000});

test("完整上传、原片分析、Agent剪辑、译制与成片下载的阶段计时", async ({page, request}, info) => {
  const started = performance.now();
  const markers: Array<{name: string; seconds: number}> = [];
  const jobIds = new Set<string>();
  const graphIds = new Set<string>();
  const pending: Promise<void>[] = [];
  let sessionId = "";
  let sourceJobId = "";
  const saveProgress = () => {
    const data = JSON.stringify({session_id: sessionId, job_ids: [...jobIds], markers}, null, 2);
    writeFileSync(info.outputPath("progress.json"), data);
    if (process.env.CAMCAT_PIPELINE_PROGRESS) writeFileSync(process.env.CAMCAT_PIPELINE_PROGRESS, data);
  };
  const mark = (name: string) => {
    markers.push({name, seconds: (performance.now() - started) / 1000});
    saveProgress();
  };
  page.on("response", response => {
    const url = response.url();
    if (!url.includes("/api/v1/") || !response.ok() || response.request().method() !== "POST") return;
    const task = (async () => {
      if (url.endsWith("/agent/stream")) {
        const stream = await response.text();
        for (const match of stream.matchAll(/"graph_run_id"\s*:\s*"([a-f0-9-]{36})"/g)) graphIds.add(match[1]);
      } else {
        const packet = await response.json();
        if (url.endsWith("/editing/sessions")) sessionId = packet.editing_session_id;
        if (packet.job_id) jobIds.add(packet.job_id);
        if (packet.job?.job_id) jobIds.add(packet.job.job_id);
        if (url.endsWith("/source-media")) sourceJobId = packet.job_id;
      }
      saveProgress();
    })();
    pending.push(task);
  });
  const get = async (path: string) => {
    const response = await request.get(api + path);
    expect(response.ok(), path).toBeTruthy();
    return response.json();
  };
  const waitDelivery = async (ready: string, budget: number) => {
    const deadline = performance.now() + budget;
    while (performance.now() < deadline) {
      const context = await get(`/api/v1/editing/sessions/${sessionId}/localization`);
      const jobs = [...context.jobs, ...(context.delivery.render_job ? [context.delivery.render_job] : [])];
      for (const job of jobs) {
        jobIds.add(job.job_id);
        expect(["failed", "cancelled", "dead_letter"].includes(job.status), job.error ?? job.kind).toBeFalsy();
      }
      saveProgress();
      if (context.delivery.state === ready) return context;
      await page.waitForTimeout(500);
    }
    throw new Error(`Delivery did not reach ${ready} within its time budget`);
  };
  await page.goto("/");
  await expect(page).toHaveTitle("剪辑工作台");
  await page.getByRole("button", {name: "新建项目", exact: true}).first().click();
  await page.getByLabel("项目名称").fill("完整链路性能测试 " + Date.now());
  await page.getByRole("button", {name: "创建并打开", exact: true}).click();
  mark("upload_start");
  await page.locator('input[type="file"][accept="video/*"]').setInputFiles(input!);
  await expect(page.getByText(/未写入素材库或向量库/)).toBeVisible({timeout: 600_000});
  mark("analysis_done");
  await expect.poll(() => sourceJobId).not.toBe("");
  const analysis = await get(`/api/v1/jobs/${sourceJobId}`);
  expect(analysis.status).toBe("succeeded");
  expect(analysis.result.frame_backend).toBe("native");
  await page.getByRole("button", {name: "进入编辑计划", exact: true}).click();
  await expect.poll(() => sessionId).not.toBe("");
  const path = `/api/v1/editing/sessions/${sessionId}`;
  const query = page.getByLabel("剪辑需求", {exact: true});
  await query.fill("仅使用我上传的地球科普原片，从第0秒到第30秒完整保留，制作30秒的16:9横屏短片，保留中文原声，自动添加中文字幕。标题设为《从太空看地球》，剪辑说明使用简短中文。");
  mark("plan_start");
  await query.press("Enter");
  const version = page.getByTestId("app-header").getByText("v2", {exact: true});
  await expect(version.or(page.getByRole("alert")).first()).toBeVisible({timeout: 600_000});
  expect(await page.getByRole("alert").allTextContents()).toEqual([]);
  await expect(version).toBeVisible();
  mark("plan_done");
  const planned = await get(path);
  expect(planned.state_version).toBe(2);
  const video = planned.state.timeline.tracks.filter((track: {type: string}) => track.type === "video")
    .flatMap((track: {segments: Array<{timeline_start_us: number; timeline_duration_us: number}>}) => track.segments);
  const duration = Math.max(...video.map((clip: {timeline_start_us: number; timeline_duration_us: number}) =>
    clip.timeline_start_us + clip.timeline_duration_us)) / 1e6;
  expect(duration).toBeCloseTo(30, 1);
  await page.screenshot({path: info.outputPath("01-planned.png"), fullPage: true});

  const contextReady = page.waitForResponse(response =>
    new URL(response.url()).pathname === path + "/localization" &&
    response.request().method() === "GET" && response.ok());
  await page.getByRole("button", {name: "译制", exact: true}).click();
  const initialContext = await (await contextReady).json();
  const panel = page.getByRole("region", {name: "译制工作区"});
  const loadSource = panel.getByRole("button", {name: "载入原片与识别字幕", exact: true});
  const firstCue = panel.getByLabel("字幕 1 文本", {exact: true});
  if (!initialContext.cues.length) await loadSource.click();
  await expect(firstCue).toBeVisible();
  const context = await get(path + "/localization");
  const cues = context.source_cues as Array<{start_us: number; duration_us: number}>;
  expect(cues.length).toBeGreaterThan(0);
  // This models an explicit subtitle timing edit using actual ASR text and trailing silence.
  for (let i = 0; i < cues.length; i++) {
    const end = i + 1 < cues.length ? cues[i + 1].start_us / 1e6 - 0.1 : duration - 0.2;
    if (end > (cues[i].start_us + cues[i].duration_us) / 1e6)
      await panel.getByLabel(`字幕 ${i + 1} 终点秒`, {exact: true}).fill(end.toFixed(3));
  }
  const save = panel.getByRole("button", {name: "保存字幕修改", exact: true});
  if (await save.isEnabled()) {
    const saved = page.waitForResponse(response => new URL(response.url()).pathname === path + "/commands" &&
      response.request().method() === "POST");
    await save.click();
    const response = await saved;
    expect(response.ok()).toBeTruthy();
    const updated = await response.json();
    await expect(page.getByTestId("app-header").getByText(`v${updated.state_version}`, {exact: true})).toBeVisible();
    await expect(save).toBeDisabled();
  }
  await query.fill("把原文字幕翻译成英文并配音，允许一次时长改写，替换原声。");
  const dispatching = page.waitForResponse(response => new URL(response.url()).pathname === path + "/agent/dispatch" &&
    response.request().method() === "POST", {timeout: 120_000});
  mark("localization_start");
  await page.getByRole("button", {name: "发送剪辑需求", exact: true}).click();
  const dispatch = await dispatching;
  expect(dispatch.ok()).toBeTruthy();
  expect((await dispatch.json()).action).toBe("translate_and_dub");
  const localized = await waitDelivery("needs_review", 360_000);
  const localizedMessage = page.getByText("译制草稿已写入时间线。打开“译制”可试听、校订并确认交付审校。", {exact: true});
  await expect(localizedMessage.or(page.getByRole("alert")).first()).toBeVisible({timeout: 360_000});
  expect(await page.getByRole("alert").allTextContents()).toEqual([]);
  await expect(localizedMessage).toBeVisible();
  mark("localization_done");
  expect(localized.delivery.state).toBe("needs_review");
  const dub = localized.jobs.find((job: {kind: string}) => job.kind === "dub_subtitles");
  expect(dub.status).toBe("succeeded");
  for (const job of localized.jobs) jobIds.add(job.job_id);
  await panel.getByLabel("译制任务记录").selectOption(dub.job_id);
  const audio = panel.getByLabel("配音 1 试听", {exact: true});
  await expect(audio).toBeVisible();
  await audio.evaluate((element: HTMLAudioElement) => {element.load(); return element.play();});
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBeGreaterThan(1);
  await audio.evaluate((element: HTMLAudioElement) => element.pause());
  const delivery = panel.getByRole("region", {name: "英文视频交付"});
  await delivery.getByRole("button", {name: "已校对字幕并试听，确认交付审校", exact: true}).click();
  mark("render_start");
  await delivery.getByRole("button", {name: "生成交付视频", exact: true}).click();
  await waitDelivery("ready", 300_000);
  const download = delivery.getByRole("button", {name: "下载交付包", exact: true});
  await expect(download).toBeEnabled({timeout: 300_000});
  mark("render_done");
  const preview = delivery.getByLabel("交付成片预览", {exact: true});
  await preview.evaluate((element: HTMLVideoElement) => {element.muted = true; return element.play();});
  await expect.poll(() => preview.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(1);
  const downloading = page.waitForEvent("download");
  await download.click();
  await (await downloading).saveAs(info.outputPath("delivery.zip"));
  mark("downloaded");
  await page.screenshot({path: info.outputPath("02-delivered.png"), fullPage: true});
  await Promise.all(pending);
  const final = await get(path + "/localization");
  if (final.delivery.render_job?.job_id) jobIds.add(final.delivery.render_job.job_id);
  const jobs = await Promise.all([...jobIds].map(id => get(`/api/v1/jobs/${id}`)));
  expect(jobs.some(job => job.kind === "render")).toBeTruthy();
  for (const job of jobs) expect(job.status).toBe("succeeded");
  expect(graphIds.size).toBeGreaterThan(0);
  const graph = await get(`/api/v1/graph-runs/${[...graphIds][0]}`);
  expect(graph.status).toBe("succeeded");
  await writeFile(info.outputPath("acceptance.json"), JSON.stringify({
    state: "passed", session_id: sessionId, input, markers, jobs, graph,
    source: "Actual Compose and local models, browser interactions, ASR text, FFmpeg and downloaded delivery package",
  }, null, 2));
});
