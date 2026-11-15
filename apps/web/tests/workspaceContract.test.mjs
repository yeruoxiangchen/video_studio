import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../CamCatWorkspacePage.tsx", import.meta.url), "utf8");
const appSource = await readFile(new URL("../CamCatApp.tsx", import.meta.url), "utf8");
const tailwindSource = await readFile(new URL("../tailwind.config.js", import.meta.url), "utf8");
const nginxSource = await readFile(new URL("../nginx.conf", import.meta.url), "utf8");

test("idle workspace never presents seeded evidence or completed production work", () => {
  for (const forbidden of [
    "traceRowsSeed",
    "evidenceItems",
    "Prototype ready",
    "最终视频_导出.mp4",
    "https://i.pravatar.cc",
    "washing machine drum background",
  ]) {
    assert.equal(source.includes(forbidden), false, `found seeded UI value: ${forbidden}`);
  }
});

test("idle workspace exposes an explicit real-data empty state", () => {
  assert.match(source, /等待真实素材与任务/);
  assert.match(source, /上传视频或输入多模态检索需求/);
});

test("editor reads output geometry from the authoritative canvas", () => {
  assert.equal(source.includes('label="9:16"'), false);
  assert.equal(source.includes("\n          9:16\n"), false);
  assert.match(source, /canvasRatio/);
  assert.match(source, /state\.canvas/);
  assert.doesNotMatch(source, /state\.settings/);
  assert.match(source, /multiple onChange=\{onFileChange\}/);
});

test("workstation has generic editing branding and chat localization entry points", () => {
  assert.match(source, /剪辑工作台/);
  assert.match(appSource, /剪辑工作台/);
  assert.match(source, /dispatchAgentAction/);
  assert.match(source, /next_job_id/);
  assert.doesNotMatch(source, />CamCat</);
  assert.doesNotMatch(source, /ASSISTANT \/ CAMCAT/);
});

test("the first search runs retrieval and plan generation as one workflow", () => {
  assert.match(source, /runSearchAndPlan/);
  assert.match(source, /setEditingSession\(result\.editingSession\)/);
});

test("a completed original upload opens a persisted session before panel localization", () => {
  const upload = source.slice(source.indexOf("async function handleUpload"), source.indexOf("async function handleSearch"));
  assert.match(upload, /api\.createEditingSession/);
  assert.match(upload, /setEditingSession\(session\)/);
});

test("project navigation stays beside content with an explicit two-column layout", () => {
  assert.match(appSource, /data-testid="project-layout"/);
  assert.match(appSource, /gridTemplateColumns:\s*"96px minmax\(0, 1fr\)"/);
  assert.doesNotMatch(appSource, /grid-cols-\[112px_minmax\(0,1fr\)\]/);
});

test("Tailwind scans every top-level React product page", () => {
  assert.match(tailwindSource, /\.\/\*\.tsx/);
});

test("the editor center column is a real vertical scroll region", () => {
  assert.match(source, /data-testid="editor-scroll-region"/);
  assert.match(source, /overflow-y-auto/);
  assert.match(source, /min-h-\[360px\].*shrink-0/s);
});

test("workspace chrome remains outside product-page conditionals", () => {
  const shellStart = source.indexOf("function AppShell");
  const shellEnd = source.indexOf("function TopHeader");
  const shell = source.slice(shellStart, shellEnd);
  assert.match(shell, /<TopHeader workspace=\{workspace\} \/>/);
  assert.match(shell, /<ProductRail workspace=\{workspace\} \/>/);
  assert.match(shell, /workspace\.page === "processing"/);
  assert.match(shell, /workspace\.page === "render"/);
});

test("same-origin proxy re-resolves the API after Compose service recreation", () => {
  assert.match(nginxSource, /resolver 127\.0\.0\.11 valid=10s;/);
  assert.match(nginxSource, /set \$camcat_api http:\/\/api:8000;/);
  assert.match(nginxSource, /proxy_pass \$camcat_api;/);
});

test("Compose content policy permits local MinIO thumbnails and playback", () => {
  for (const directive of ["img-src", "media-src"]) {
    const sources = nginxSource.match(new RegExp(`${directive} ([^;"]+)`))?.[1].split(" ");
    assert.ok(sources?.includes("http://127.0.0.1:18963"));
    assert.ok(sources?.includes("http://localhost:18963"));
  }
});

test("render failures expose an accessible alert", () => {
  const renderPage = source.slice(source.indexOf("function RenderStatusPage("));
  assert.ok(/<p role=\{failed \? "alert" : "status"\}/.test(renderPage));
});
