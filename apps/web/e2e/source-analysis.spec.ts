import { expect, test } from "@playwright/test";
import { existsSync } from "node:fs";

const videoPath = process.env.CAMCAT_E2E_VIDEO;
const apiBase = process.env.CAMCAT_E2E_API_BASE ?? "http://127.0.0.1:18960";

test.skip(!videoPath || !existsSync(videoPath), "a real source video is required");

test("uploaded source finishes analysis with monotonic progress and original windows", async ({ page, request }, testInfo) => {
  test.setTimeout(8 * 60 * 1000);
  let sessionId: string | undefined;
  page.on("response", async response => {
    if (response.url().endsWith("/api/v1/editing/sessions") && response.request().method() === "POST" && response.ok()) {
      sessionId = (await response.json()).editing_session_id;
    }
  });
  await page.goto("/");
  await page.getByRole("button", { name: "新建项目", exact: true }).first().click();
  await page.getByLabel("项目名称").fill(`原片分析验证 ${Date.now()}`);
  await page.getByRole("button", { name: "创建并打开" }).click();
  const uploadResponse = page.waitForResponse(response =>
    response.url().endsWith("/api/v1/source-media") && response.request().method() === "POST"
  );
  const started = Date.now();
  await page.locator('input[type="file"][accept="video/*"]').setInputFiles(videoPath!);
  const response = await uploadResponse;
  expect(response.ok()).toBeTruthy();
  const uploaded = await response.json();
  const progress: number[] = [];
  await expect.poll(async () => {
    const jobResponse = await request.get(`${apiBase}/api/v1/jobs/${uploaded.job_id}`);
    expect(jobResponse.ok()).toBeTruthy();
    const job = await jobResponse.json();
    expect(["failed", "cancelled", "dead_letter"]).not.toContain(job.status);
    progress.push(job.progress);
    return job.status;
  }, { timeout: 180000, intervals: [1000, 2000] }).toBe("succeeded");
  const finished = await (await request.get(`${apiBase}/api/v1/jobs/${uploaded.job_id}`)).json();
  expect(progress).toEqual([...progress].sort((a, b) => a - b));
  expect(finished.result.media_count).toBe(1);
  expect(finished.result.segment_count).toBeGreaterThan(0);
  expect(finished.result.storage_policy).toBe("transient-4h-no-asset-no-milvus");
  expect(finished.result.source_media[0].visual_semantics.description.length).toBeGreaterThan(0);
  expect(finished.result.source_media[0].visual_evidence.scope).toBe("whole_media");
  expect(finished.result.source_media[0].visual_evidence.media_id).toBe(uploaded.media[0].media_id);
  for (const segment of finished.result.source_segments) {
    expect(segment.start_time).toBeGreaterThanOrEqual(0);
    expect(segment.end_time).toBeGreaterThan(segment.start_time);
    expect(segment.end_time).toBeLessThanOrEqual(finished.result.source_media[0].duration_seconds + 0.001);
  }
  await expect(page.getByText(/未写入素材库或向量库/)).toBeVisible();
  await testInfo.attach("source-analysis-result", {
    body: JSON.stringify({ job_id: uploaded.job_id, elapsed_ms: Date.now() - started, progress,
      segment_count: finished.result.segment_count }),
    contentType: "application/json",
  });
  await test.step("plan and render the full original while honoring explicit bans", async () => {
    const enterEditing = page.getByRole("button", { name: "进入编辑计划", exact: true });
    if (await enterEditing.count()) await enterEditing.click();
    const query = page.getByLabel("剪辑需求", {exact: true});
    await query.fill("Use this one uploaded clip in full at its original speed and duration. No extra captions, music or transitions. 外部素材0%。按完整原片范围使用这一个视频，不要字幕，不要转场。");
    await query.press("Enter");
    await expect(page.getByText(/领域命令已持久化/).first()).toBeVisible({ timeout: 180000 });
    expect(sessionId).toBeTruthy();
    const packet = await (await request.get(`${apiBase}/api/v1/editing/sessions/${sessionId}`)).json();
    expect(packet.state.timeline.transitions).toEqual([]);
    expect(packet.state.timeline.tracks.filter((track: {type: string; segments: unknown[]}) =>
      track.type === "text" && track.segments.length)).toEqual([]);
    const clips = packet.state.timeline.tracks.filter((track: {type: string}) => track.type === "video")
      .flatMap((track: {segments: unknown[]}) => track.segments);
    expect(clips).toHaveLength(1);
    expect(clips[0].source_start_us).toBe(0);
    expect(clips[0].source_duration_us).toBe(Math.round(finished.result.source_media[0].duration_seconds * 1e6));
    const submitted = page.waitForResponse(response => response.url().endsWith("/render") && response.request().method() === "POST");
    await page.getByRole("button", { name: /Export/ }).click();
    const renderResponse = await submitted;
    expect(renderResponse.ok()).toBeTruthy();
    const renderJob = await renderResponse.json();
    await expect.poll(async () => {
      const job = await (await request.get(`${apiBase}/api/v1/jobs/${renderJob.job_id}`)).json();
      expect(["failed", "cancelled", "dead_letter"], JSON.stringify(job.error)).not.toContain(job.status);
      return job.status;
    }, { timeout: 180000, intervals: [1000, 2000] }).toBe("succeeded");
    await expect(page.getByText("渲染完成", { exact: true })).toBeVisible();
    await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThanOrEqual(2);
    const download = page.waitForEvent("download");
    await page.locator("a[download]").click();
    await (await download).saveAs(testInfo.outputPath("source-constraint-render.mp4"));
  });
});
