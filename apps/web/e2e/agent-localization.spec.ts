import {expect, test} from "@playwright/test";
import {readFile, writeFile} from "node:fs/promises";

const input = process.env.CAMCAT_AGENT_DEMO_VIDEO;
const api = process.env.CAMCAT_E2E_API_BASE ?? "http://127.0.0.1:18960";
test.skip(!input, "Requires a real Chinese narrated input video");
test.use({viewport: {width: 1920, height: 1080}, video: {mode: "on", size: {width: 1920, height: 1080}}});

test("原片直接译制、文本翻译配音、真实交付与下载", async ({page, request}, info) => {
  const started = Date.now();
  const markers: Array<{name: string; seconds: number}> = [];
  const mark = (name: string) => markers.push({name, seconds: (Date.now() - started) / 1000});
  const name = "地球科普 · 英文译制演示 " + Date.now();
  let sessionId = "";
  page.on("response", async response => {
    if (response.url().endsWith("/api/v1/editing/sessions") && response.request().method() === "POST" && response.ok())
      sessionId = (await response.json()).editing_session_id;
  });
  await page.goto("/");
  await expect(page).toHaveTitle("剪辑工作台");
  mark("home");
  await page.getByRole("button", {name: "新建项目", exact: true}).first().click();
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", {name: "创建并打开", exact: true}).click();
  mark("upload_start");
  await page.locator('input[type="file"][accept="video/*"]').setInputFiles(input!);
  await expect(page.getByText(/未写入素材库或向量库/)).toBeVisible({timeout: 600_000});
  mark("analysis_done");
  await page.getByRole("button", {name: "进入编辑计划", exact: true}).click();
  await page.getByRole("button", {name: "译制", exact: true}).click();
  const panel = page.getByRole("region", {name: "译制工作区"});
  await panel.getByRole("button", {name: "载入原片与识别字幕", exact: true}).click();
  await expect(panel.getByLabel("字幕 1 文本", {exact: true})).toBeVisible();
  mark("source_ready");
  const path = `${api}/api/v1/editing/sessions/${sessionId}`;
  const ctxResponse = await request.get(path + "/localization");
  expect(ctxResponse.ok()).toBeTruthy();
  const ctx = await ctxResponse.json();
  expect(ctx.source_cues.length).toBeGreaterThan(0);
  // Let spoken phrases use their trailing pauses; all text remains actual ASR output.
  const cues = ctx.source_cues as Array<{start_us: number; duration_us: number}>;
  const referencePath = process.env.CAMCAT_AGENT_DEMO_REFERENCE;
  const sourceCorrections = referencePath ? (await readFile(referencePath, "utf8")).trim().split(/\r?\n/) : [];
  if (sourceCorrections.length) expect(sourceCorrections.length).toBe(cues.length);
  for (let i = 0; i < cues.length; i++) {
    if (sourceCorrections[i]) await panel.getByLabel(`字幕 ${i + 1} 文本`, {exact: true}).fill(sourceCorrections[i]);
    const end = i + 1 < cues.length ? cues[i + 1].start_us / 1e6 - 0.1 : 29.8;
    if (end > (cues[i].start_us + cues[i].duration_us) / 1e6)
      await panel.getByLabel(`字幕 ${i + 1} 终点秒`, {exact: true}).fill(end.toFixed(3));
  }
  const save = panel.getByRole("button", {name: "保存字幕修改", exact: true});
  if (await save.isEnabled()) await save.click();
  await page.screenshot({path: info.outputPath("01-source-subtitles.png"), fullPage: true});
  mark("source_timing_saved");
  await page.getByLabel("剪辑需求", {exact: true}).fill("把原文字幕翻译成英文并配音，允许一次时长改写，替换原声。");
  await page.getByRole("button", {name: "发送剪辑需求", exact: true}).click();
  mark("agent_submitted");
  await expect(page.getByText("译制草稿已写入时间线。打开“译制”可试听、校订并确认交付审校。", {exact: true})).toBeVisible({timeout: 360_000});
  mark("agent_done");
  const current = await request.get(path + "/localization");
  const data = await current.json();
  expect(data.delivery.state).toBe("needs_review");
  expect(data.jobs.filter((j: {status: string}) => j.status === "succeeded").length).toBe(2);
  const dub = data.jobs.find((j: {kind: string}) => j.kind === "dub_subtitles");
  await panel.getByLabel("译制任务记录").selectOption(dub.job_id);
  const audio = panel.getByLabel("配音 1 试听", {exact: true});
  await expect(audio).toBeVisible();
  await audio.evaluate((element: HTMLAudioElement) => element.load());
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.readyState)).toBeGreaterThanOrEqual(2);
  await audio.evaluate((element: HTMLAudioElement) => element.play());
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBeGreaterThan(1);
  await audio.evaluate((element: HTMLAudioElement) => element.pause());
  await page.screenshot({path: info.outputPath("02-agent-localized.png"), fullPage: true});
  mark("speech_previewed");
  const delivery = panel.getByRole("region", {name: "英文视频交付"});
  await delivery.getByRole("button", {name: "已校对字幕并试听，确认交付审校", exact: true}).click();
  await delivery.getByRole("button", {name: "生成交付视频", exact: true}).click();
  mark("render_submitted");
  const download = delivery.getByRole("button", {name: "下载交付包", exact: true});
  await expect(download).toBeEnabled({timeout: 180_000});
  mark("render_done");
  await expect(page.getByText("成片已生成", {exact: true})).toBeVisible();
  const video = delivery.getByLabel("交付成片预览", {exact: true});
  await video.evaluate((element: HTMLVideoElement) => {element.muted = false; return element.play();});
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(2);
  await page.screenshot({path: info.outputPath("03-delivery-ready.png"), fullPage: true});
  const pending = page.waitForEvent("download"); await download.click();
  await (await pending).saveAs(info.outputPath("delivery.zip"));
  mark("downloaded");
  await writeFile(info.outputPath("acceptance.json"), JSON.stringify({project: name, sessionId, input, sourceCorrections, markers, localization: data,
    source: "Real uploaded NASA visuals and locally synthesized Chinese narration; actual ASR, Qwen translation and Piper English speech"}, null, 2));
});
