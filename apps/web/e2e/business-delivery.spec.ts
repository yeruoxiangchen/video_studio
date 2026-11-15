import {expect, test} from "@playwright/test";
import {execFileSync} from "node:child_process";
import {readFile, writeFile} from "node:fs/promises";

const sourceJob = process.env.CAMCAT_BUSINESS_SOURCE_JOB;
const api = process.env.CAMCAT_E2E_API_BASE ?? "http://127.0.0.1:18960";
const headers = {"X-User-Id": "camcat-local-user"};
test.skip(!sourceJob, "A real analyzed source job is required for business delivery");
test.use({viewport: {width: 1920, height: 1080}, video: {mode: "on", size: {width: 1920, height: 1080}}});

test("中文口播校订、术语复用、配音审校与完整交付", async ({page, request}, info) => {
  const name = `旅行口播英文交付 ${Date.now()}`;
  const projectResponse = await request.post(`${api}/api/v1/projects`, {headers, data: {name}});
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json();
  const response = await request.post(`${api}/api/v1/editing/sessions`, {headers, data: {
    project_id: project.project_id, source_job_id: sourceJob, current_goal: "旅行口播英文交付",
  }});
  expect(response.ok()).toBeTruthy();
  const session = await response.json();
  const path = `${api}/api/v1/editing/sessions/${session.editing_session_id}`;
  try {
    const source = session.state.sources.find((s: {origin: string}) => s.origin === "user_upload");
    const duration = Math.round(Number(session.state.metadata.source_media[0].duration_seconds) * 1_000_000);
    const text = "这里几乎都是沙滩，游泳很安全，大部分地方都有新西兰圣诞树的树荫。";
    const edited = await request.post(path + "/commands", {headers, data: {base_version: 1,
      reason: "Authored business input subtitles; actual providers generate translations and audio",
      commands: [{type: "update_title", title: "旅行口播英文交付演示"}, {type: "add_track", track: {type: "video", track_id: "main", name: "旅行口播", segments: [{
        type: "video", segment_id: "original", source_id: source.source_id, timeline_start_us: 0,
        timeline_duration_us: duration, source_start_us: 0, source_duration_us: duration,
      }]}}, {type: "add_track", track: {type: "text", track_id: "captions", name: "原文字幕", segments: [{
        type: "text", segment_id: "source-cue", start_us: 0, duration_us: duration, text,
      }]}}]}});
    expect(edited.ok()).toBeTruthy();
    async function openProject() {
      await page.goto("/");
      await page.getByRole("button", {name: `打开项目 ${name}`, exact: true}).click();
      await page.getByRole("button", {name: "译制", exact: true}).click();
    }
    await openProject();
    const panel = page.getByRole("region", {name: "译制工作区"});
    const delivery = panel.getByRole("region", {name: "英文视频交付"});
    await expect(delivery.getByRole("button", {name: "下载交付包", exact: true})).toBeDisabled();
    await panel.getByRole("button", {name: "原文校订", exact: true}).click();
    await panel.getByLabel("原文字幕 1 文本", {exact: true}).fill(text.replace("沙滩", "沙质海滩"));
    await panel.getByRole("button", {name: "保存字幕修改", exact: true}).click();
    await expect(panel.getByText("字幕已保存为新版本。", {exact: true})).toBeVisible();
    await panel.locator("details").filter({has: page.locator("summary", {hasText: "术语表"})}).locator("summary").click();
    await panel.getByLabel("翻译术语表").fill("新西兰 = New Zealand");
    await panel.getByRole("button", {name: "翻译字幕", exact: true}).click();
    const apply = panel.getByRole("button", {name: "确认并写入时间线", exact: true});
    await expect(apply).toBeEnabled({timeout: 180_000}); await apply.click();
    await expect(panel.getByText("已写入 v4", {exact: true})).toBeVisible();
    await page.evaluate((id) => sessionStorage.removeItem(`camcat:localization:${id}:glossary`), session.editing_session_id);
    await openProject();
    await panel.locator("details").filter({has: page.locator("summary", {hasText: "术语表"})}).locator("summary").click();
    await expect(panel.getByLabel("翻译术语表")).toHaveValue("新西兰 = New Zealand");
    await panel.getByRole("checkbox", {name: "允许一次时长改写，确认后再应用"}).check();
    await panel.getByRole("button", {name: "生成配音", exact: true}).click();
    await expect(apply).toBeEnabled({timeout: 180_000});
    const speech = panel.getByLabel("配音 1 试听");
    await speech.evaluate((a: HTMLAudioElement) => a.load());
    await expect.poll(() => speech.evaluate((a: HTMLAudioElement) => a.readyState)).toBeGreaterThanOrEqual(2);
    await apply.click();
    await expect(panel.getByText("已写入 v5", {exact: true})).toBeVisible();
    await delivery.getByRole("button", {name: "已校对字幕并试听，确认交付审校", exact: true}).click();
    await expect(delivery.getByRole("button", {name: "生成交付视频", exact: true})).toBeEnabled();
    await delivery.getByRole("button", {name: "生成交付视频", exact: true}).click();
    const download = delivery.getByRole("button", {name: "下载交付包", exact: true});
    await expect(download).toBeEnabled({timeout: 240_000});
    const video = delivery.getByLabel("交付成片预览");
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
    await page.screenshot({path: info.outputPath("business-delivery-ready.png"), fullPage: true});
    const pending = page.waitForEvent("download"); await download.click();
    const saved = await pending; const zip = info.outputPath("customer-delivery.zip"); await saved.saveAs(zip);
    expect((await readFile(zip)).subarray(0, 2).toString()).toBe("PK");
    const inspection = JSON.parse(execFileSync("python", ["-c",
      "import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps({'files':z.namelist(),'manifest':json.loads(z.read('delivery.json'))}))", zip], {encoding: "utf8"}));
    expect(inspection.files.sort()).toEqual(["bilingual.srt", "delivery.json", "glossary.csv", "source.srt", "translation.srt", "video.mp4"]);
    expect(inspection.manifest.version).toBe(6);
    expect(inspection.manifest.project_title).toBe("旅行口播英文交付演示");
    await writeFile(info.outputPath("business-delivery.json"), JSON.stringify({project_id: project.project_id,
      source_job: sourceJob, session_id: session.editing_session_id, download: zip, ...inspection}, null, 2));
    await panel.getByRole("button", {name: "原文校订", exact: true}).click();
    await panel.getByLabel("原文字幕 1 文本", {exact: true}).fill(text + "请再次核对。");
    await panel.getByRole("button", {name: "保存字幕修改", exact: true}).click();
    await expect(panel.getByText("原文已修改，请重新翻译并应用后，再生成配音或交付。", {exact: true})).toBeVisible();
    await expect(download).toBeDisabled();
  } finally {
    await request.delete(path, {headers});
  }
});
