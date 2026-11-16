import {expect, test} from "@playwright/test";
import {writeFileSync} from "node:fs";
import {writeFile} from "node:fs/promises";

const input = process.env.CAMCAT_EDITING_DEMO_VIDEO;
const api = process.env.CAMCAT_E2E_API_BASE ?? "http://127.0.0.1:18960";
test.skip(!input, "Requires a real source video for the editing demonstration");
test.use({viewport: {width: 1920, height: 1080}, video: {mode: "on", size: {width: 1920, height: 1080}}});

type Segment = {
  segment_id: string;
  source_start_us?: number;
  source_duration_us?: number;
  timeline_start_us: number;
  timeline_duration_us: number;
  text?: string;
};
type State = {canvas: {width: number; height: number}; metadata?: {source_media?: Array<{playback_url?: string}>}; timeline: {tracks: Array<{type: string; segments: Segment[]}>}};
type Session = {state_version: number; state: State};

const videoSegments = (state: State) => state.timeline.tracks.filter(track => track.type === "video").flatMap(track => track.segments);
const captions = (state: State) => state.timeline.tracks.filter(track => track.type === "text").flatMap(track => track.segments);
const duration = (state: State) => Math.max(...videoSegments(state).map(segment => segment.timeline_start_us + segment.timeline_duration_us)) / 1e6;
const letters = (text: string) => [...text].filter(letter => /[\p{L}\p{N}]/u.test(letter)).length;

test("自然语言裁剪、多轮字幕修改、两版导出与回滚", async ({page, request}, info) => {
  const started = Date.now();
  const markers: Array<{name: string; seconds: number}> = [];
  const mark = (name: string) => {
    markers.push({name, seconds: (Date.now() - started) / 1000});
    writeFileSync(info.outputPath("progress.json"), JSON.stringify({stage: name, markers}, null, 2));
  };
  const project = "地球科普 · 自然语言剪辑 " + Date.now();
  let sessionId = "";
  page.on("response", async response => {
    if (response.url().endsWith("/api/v1/editing/sessions") && response.request().method() === "POST" && response.ok())
      sessionId = (await response.json()).editing_session_id;
  });
  const state = async (): Promise<Session> => {
    const response = await request.get(`${api}/api/v1/editing/sessions/${sessionId}`);
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const waitVersion = async (version: number) => {
    const success = page.getByTestId("app-header").getByText(`v${version}`, {exact: true});
    await expect(success.or(page.getByRole("alert")).first()).toBeVisible({timeout: 600_000});
    expect(await page.getByRole("alert").allTextContents()).toEqual([]);
    await expect(success).toBeVisible();
    const current = await state();
    expect(current.state_version).toBe(version);
    expect(await page.getByRole("alert").allTextContents()).toEqual([]);
    return current;
  };
  const render = async (name: string) => {
    await page.getByRole("button", {name: /Export/}).click();
    mark(name + "_render_start");
    await expect(page.getByText("渲染完成", {exact: true})).toBeVisible({timeout: 600_000});
    const video = page.locator("video");
    await expect(video).toHaveAttribute("src", /^https?:\/\//);
    await video.evaluate((element: HTMLVideoElement) => {element.muted = false; return element.play();});
    await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(2);
    const pending = page.waitForEvent("download");
    await page.locator("a[download]").click();
    await (await pending).saveAs(info.outputPath(name + ".mp4"));
    await page.screenshot({path: info.outputPath(name + "-preview.png"), fullPage: true});
    mark(name + "_render_done");
    await page.waitForTimeout(1800);
    await page.getByRole("button", {name: "确认并返回编辑", exact: true}).click();
  };

  await page.goto("/");
  await expect(page).toHaveTitle("剪辑工作台");
  mark("home");
  await page.getByRole("button", {name: "新建项目", exact: true}).first().click();
  await page.getByLabel("项目名称").fill(project);
  await page.getByRole("button", {name: "创建并打开", exact: true}).click();
  mark("upload_start");
  await page.locator('input[type="file"][accept="video/*"]').setInputFiles(input!);
  await expect(page.getByText(/未写入素材库或向量库/)).toBeVisible({timeout: 900_000});
  mark("analysis_done");
  await page.screenshot({path: info.outputPath("source-analysis.png"), fullPage: true});
  await page.getByRole("button", {name: "进入编辑计划", exact: true}).click();
  expect(sessionId).not.toBe("");

  const query = page.getByLabel("剪辑需求", {exact: true});
  const firstPrompt = "仅使用我上传的地球科普原片，截取原片第3秒到第20秒，剪成17秒的16:9横屏短片，保留中文原声，自动添加中文字幕。标题设为《从太空看地球》，剪辑说明用简短中文。";
  await query.fill(firstPrompt);
  await page.screenshot({path: info.outputPath("first-request.png"), fullPage: true});
  mark("first_edit_start");
  await query.press("Enter");
  const first = await waitVersion(2);
  await expect(page.getByText(input!.split("/").pop()!, {exact: true}).first()).toBeVisible();
  const originalUrl = first.state.metadata?.source_media?.[0]?.playback_url;
  expect(originalUrl).toMatch(/^https?:\/\//);
  expect(new URL((await page.locator("video").getAttribute("src"))!).pathname)
    .toBe(new URL(originalUrl!).pathname);
  expect(duration(first.state)).toBeCloseTo(17, 1);
  expect(Math.min(...videoSegments(first.state).map(segment => segment.source_start_us ?? 0)) / 1e6).toBeCloseTo(3, 1);
  expect(Math.max(...videoSegments(first.state).map(segment => (segment.source_start_us ?? 0) + (segment.source_duration_us ?? 0))) / 1e6).toBeCloseTo(20, 1);
  expect(first.state.canvas.width).toBeGreaterThan(first.state.canvas.height);
  expect(captions(first.state).some(cue => /[\u3400-\u9fff]/u.test(cue.text ?? ""))).toBeTruthy();
  mark("first_edit_done");
  await writeFile(info.outputPath("first-state.json"), JSON.stringify(first, null, 2));
  await page.screenshot({path: info.outputPath("first-timeline.png"), fullPage: true});
  await page.waitForTimeout(1800);
  await render("first-cut");

  const secondPrompt = "把原片范围改为第3秒到第12秒，成片缩短到9秒；中文字幕改得更简短，保持中文原声和16:9横屏。";
  await query.fill(secondPrompt);
  await page.screenshot({path: info.outputPath("second-request.png"), fullPage: true});
  mark("second_edit_start");
  await query.press("Enter");
  const revised = await waitVersion(3);
  await expect(page.getByText(input!.split("/").pop()!, {exact: true}).first()).toBeVisible();
  expect(duration(revised.state)).toBeCloseTo(9, 1);
  expect(Math.min(...videoSegments(revised.state).map(segment => segment.source_start_us ?? 0)) / 1e6).toBeCloseTo(3, 1);
  expect(Math.max(...videoSegments(revised.state).map(segment => (segment.source_start_us ?? 0) + (segment.source_duration_us ?? 0))) / 1e6).toBeCloseTo(12, 1);
  expect(revised.state.canvas).toEqual(first.state.canvas);
  const beforeCharacters = captions(first.state).reduce((sum, cue) => sum + letters(cue.text ?? ""), 0);
  const afterCharacters = captions(revised.state).reduce((sum, cue) => sum + letters(cue.text ?? ""), 0);
  expect(afterCharacters).toBeLessThan(beforeCharacters);
  const originalText = new Map(captions(first.state).map(cue => [cue.segment_id, cue.text ?? ""]));
  expect(captions(revised.state).some(cue => {
    const previous = originalText.get(cue.segment_id);
    return previous !== undefined && letters(cue.text ?? "") < letters(previous);
  })).toBeTruthy();
  expect(captions(revised.state).some(cue => /[\u3400-\u9fff]/u.test(cue.text ?? ""))).toBeTruthy();
  mark("second_edit_done");
  await writeFile(info.outputPath("revised-state.json"), JSON.stringify(revised, null, 2));
  await page.screenshot({path: info.outputPath("revised-timeline.png"), fullPage: true});
  await page.waitForTimeout(1800);
  await render("revised-cut");

  await page.getByRole("button", {name: /Rollback/}).click();
  const restored = await waitVersion(4);
  expect(videoSegments(restored.state)).toEqual(videoSegments(first.state));
  expect(captions(restored.state)).toEqual(captions(first.state));
  mark("rollback_done");
  await page.screenshot({path: info.outputPath("rollback.png"), fullPage: true});
  await page.waitForTimeout(2500);
  await writeFile(info.outputPath("acceptance.json"), JSON.stringify({
    state: "passed", project, sessionId, input,
    prompts: [firstPrompt, secondPrompt], markers,
    first: {version: 2, duration_seconds: duration(first.state), caption_characters: beforeCharacters, clips: videoSegments(first.state).length},
    revised: {version: 3, duration_seconds: duration(revised.state), caption_characters: afterCharacters, clips: videoSegments(revised.state).length},
    rollback: {version: 4, restores_first_cut: true},
    source: "Actual browser interaction, local Qwen planning and ASR, persisted timeline, FFmpeg render and downloaded MP4 outputs.",
  }, null, 2));
});
