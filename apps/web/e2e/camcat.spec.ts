import { expect, test, type Locator } from "@playwright/test";
import { existsSync } from "node:fs";

const videoPath = process.env.CAMCAT_E2E_VIDEO;
const imagePath = process.env.CAMCAT_E2E_IMAGE;
const apiBase = process.env.CAMCAT_E2E_API_BASE ?? "http://localhost:8000";
const userId = process.env.VITE_CAMCAT_USER_ID ?? "camcat-local-user";

test.skip(!videoPath || !imagePath || !existsSync(videoPath) || !existsSync(imagePath), "real E2E video and image fixtures are required");

async function waitForStage(success: Locator, errors: Locator, timeout: number) {
  await expect(success.or(errors).first()).toBeVisible({ timeout });
  expect(await errors.allTextContents()).toEqual([]);
  await expect(success).toBeVisible();
}

test("real multimodal import, edit, rollback, conflict and render journey", async ({ page, request }, testInfo) => {
  let latestSession: { editing_session_id: string; state_version: number } | undefined;
  page.on("response", async (response) => {
    if (response.url().endsWith("/api/v1/editing/sessions") && response.request().method() === "POST" && response.ok()) {
      latestSession = await response.json();
    }
  });

  await page.goto("/");
  await expect(page.getByText("剪辑工作台", { exact: true }).first()).toBeVisible();

  // Isolate the editing journey from other browser tests and existing user projects.
  await page.getByRole("button", { name: "新建项目", exact: true }).first().click();
  await page.getByLabel("项目名称").fill(`完整剪辑验证 ${Date.now()}`);
  await page.getByRole("button", { name: "创建并打开" }).click();
  await expect(page.getByLabel("剪辑需求", {exact: true})).toBeVisible();

  const errors = page.getByRole("alert");
  await page.locator('input[type="file"][accept="video/*"]').setInputFiles(videoPath!);
  await waitForStage(page.getByText(/未写入素材库或向量库/), errors, 20 * 60 * 1000);
  const enterEditing = page.getByRole("button", { name: "进入编辑计划", exact: true });
  if (await enterEditing.count()) {
    await enterEditing.click();
  }
  await expect(page.getByLabel("剪辑需求", {exact: true})).toBeVisible();

  await page.locator('input[type="file"][accept="image/*"]').setInputFiles(imagePath!);
  await expect(page.getByText(imagePath!.split("/").pop()!)).toBeVisible();
  const query = page.getByLabel("剪辑需求", {exact: true});
  const goalSeconds = process.env.CAMCAT_E2E_GOAL_SECONDS ?? "12";
  await query.fill(`寻找与参考图相似的有活力镜头，剪成 ${goalSeconds} 秒竖屏短片`);
  await query.press("Enter");
  await waitForStage(page.getByText(/领域命令已持久化/).first(), errors, 10 * 60 * 1000);
  await expect(page.getByText("validate_project", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: /Export/ }).click();
  await waitForStage(page.getByText("渲染完成", { exact: true }), errors, 20 * 60 * 1000);
  const download = page.locator('a[download]');
  await expect(download).toHaveAttribute("href", /^https?:\/\//);
  await expect(page.locator("video")).toHaveAttribute("src", /^https?:\/\//);
  await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThanOrEqual(2);
  expect(latestSession).toBeTruthy();

  await page.getByRole("button", { name: "确认并返回编辑", exact: true }).click();
  const beforeRevisionResponse = await request.get(`${apiBase}/api/v1/editing/sessions/${latestSession!.editing_session_id}`, {
    headers: { "X-User-Id": userId },
  });
  expect(beforeRevisionResponse.ok()).toBeTruthy();
  const beforeRevision = await beforeRevisionResponse.json();
  const initialClips = beforeRevision.state.timeline.tracks
    .filter((track: {type: string}) => track.type === "video")
    .flatMap((track: {segments: Array<{transform: {x: number; y: number; scale_x: number; scale_y: number}}>}) => track.segments);
  expect(initialClips.length).toBeGreaterThan(0);
  for (const clip of initialClips) {
    expect(clip.transform).toMatchObject({x: 0, y: 0, scale_x: 1, scale_y: 1});
  }
  await query.fill("节奏更紧凑，字幕更短");
  await query.press("Enter");
  await waitForStage(page.getByTestId("app-header").getByText("v3", { exact: true }), errors, 10 * 60 * 1000);
  const afterRevisionResponse = await request.get(`${apiBase}/api/v1/editing/sessions/${latestSession!.editing_session_id}`, {
    headers: { "X-User-Id": userId },
  });
  expect(afterRevisionResponse.ok()).toBeTruthy();
  const afterRevision = await afterRevisionResponse.json();
  expect(afterRevision.state.canvas).toEqual(beforeRevision.state.canvas);
  const captions = (document: {
    timeline: {tracks: Array<{type: string; segments: Array<{segment_id: string; text?: string}>}>};
  }) => document.timeline.tracks.filter((track) => track.type === "text")
    .flatMap((track) => track.segments);
  const originalTexts = new Map(captions(beforeRevision.state)
    .map((segment) => [segment.segment_id, segment.text ?? ""]));
  const wordingLength = (text: string) => [...text].filter((letter) => /[\p{L}\p{N}]/u.test(letter)).length;
  expect(captions(afterRevision.state).some((segment) => {
    const original = originalTexts.get(segment.segment_id);
    return original !== undefined && wordingLength(segment.text ?? "") < wordingLength(original);
  })).toBeTruthy();
  for (const segment of captions(afterRevision.state)) {
    const original = originalTexts.get(segment.segment_id);
    if (original && /^[\x00-\x7F]+$/.test(original)) {
      expect(segment.text).not.toMatch(/[\u3400-\u9fff]/);
    }
    if (original?.toLowerCase().includes("camcat")) {
      expect(segment.text?.toLowerCase()).toContain("camcat");
    }
  }
  await page.getByRole("button", { name: /Rollback/ }).click();
  await expect(page.getByTestId("app-header").getByText("v4", { exact: true })).toBeVisible();

  const sessionId = latestSession!.editing_session_id;
  const externalCommand = await request.post(`${apiBase}/api/v1/editing/sessions/${sessionId}/commands`, {
    headers: { "X-User-Id": userId },
    data: {
      base_version: 4,
      commands: [{ type: "update_goal", goal: "另一个窗口的更新" }],
      reason: "E2E deliberate concurrent edit",
    },
  });
  expect(externalCommand.ok()).toBeTruthy();

  await query.fill("这是使用过期版本发起的编辑");
  await query.press("Enter");
  await expect(page.getByRole("alert")).toContainText(/状态已更新/);
  await page.getByRole("button", { name: "刷新剪辑版本", exact: true }).click();
  await expect(page.getByTestId("app-header").getByText("v5", { exact: true })).toBeVisible();
  await expect(query).toHaveValue("这是使用过期版本发起的编辑");

  if (process.env.CAMCAT_E2E_LOCALIZATION === "1") {
    await test.step("translate, review, dub and render through the existing CamCat timeline", async () => {
      await page.getByRole("button", {name: "译制", exact: true}).click();
      const panel = page.getByRole("region", {name: "译制工作区"});
      await expect(panel.getByRole("button", {name: "翻译字幕", exact: true})).toBeEnabled();
      await panel.getByRole("button", {name: "翻译字幕", exact: true}).click();
      const apply = panel.getByRole("button", {name: "确认并写入时间线", exact: true});
      const readyToApply = apply.and(panel.locator(":enabled"));
      await waitForStage(readyToApply, errors, 5 * 60 * 1000);
      await apply.click();
      await expect(panel.getByText("已写入 v6", {exact: true})).toBeVisible();
      await expect(panel.getByLabel("字幕 1 文本")).toHaveValue(/[A-Za-z]/);
      const stateResponse = await request.get(`${apiBase}/api/v1/editing/sessions/${sessionId}`, {
        headers: { "X-User-Id": userId },
      });
      expect(stateResponse.ok()).toBeTruthy();
      const saved = await stateResponse.json();
      const primary = saved.state.timeline.tracks.find((track: {type: string}) => track.type === "video");
      const videoEnd = Math.max(...primary.segments.map((segment: {
        timeline_start_us: number; timeline_duration_us: number;
      }) => segment.timeline_start_us + segment.timeline_duration_us)) / 1_000_000;
      const cueEnds = panel.getByRole("spinbutton", {name: /^字幕 \d+ 终点秒$/});
      const lastEnd = cueEnds.last();
      let localizationVersion = 6;
      if (Number(await lastEnd.inputValue()) < videoEnd) {
        await lastEnd.fill(String(videoEnd));
        await panel.getByRole("button", {name: "保存字幕修改", exact: true}).click();
        await expect(panel.getByText("字幕已保存为新版本。", {exact: true})).toBeVisible();
        localizationVersion++;
      }
      await panel.getByRole("checkbox", {name: "允许一次时长改写，确认后再应用"}).check();
      await panel.getByRole("button", {name: "生成配音", exact: true}).click();
      await waitForStage(readyToApply, errors, 5 * 60 * 1000);
      const preview = panel.getByLabel("配音 1 试听");
      await expect(preview).toHaveAttribute("src", /^https?:\/\//);
      await preview.evaluate((audio: HTMLAudioElement) => audio.load());
      await expect.poll(() => preview.evaluate((audio: HTMLAudioElement) => audio.readyState))
        .toBeGreaterThanOrEqual(2);
      await apply.click();
      await expect(panel.getByText(`已写入 v${localizationVersion + 1}`, {exact: true})).toBeVisible();
      await page.screenshot({path: testInfo.outputPath("camcat-localization.png"), fullPage: true});
      await page.getByRole("button", {name: /Export/}).click();
      await waitForStage(page.getByText("渲染完成", {exact: true}), errors, 20 * 60 * 1000);
      await expect.poll(() => page.locator("video").evaluate((video: HTMLVideoElement) => video.readyState))
        .toBeGreaterThanOrEqual(2);
      const download = page.waitForEvent("download");
      await page.locator("a[download]").click();
      const exported = await download;
      expect(exported.suggestedFilename()).toMatch(/\.mp4$/);
      await exported.saveAs(testInfo.outputPath("camcat-localized.mp4"));
    });
  }
});
