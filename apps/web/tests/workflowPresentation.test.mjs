import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function presentation() {
  const source = await readFile(new URL("../src/workflowPresentation.ts", import.meta.url), "utf8");
  const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext}});
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}

test("a completed localization task presents draft review as its next step", async () => {
  const {workflowPresentation} = await presentation();
  const view = workflowPresentation({status: "ready", activeJob: {kind: "dub_subtitles", status: "succeeded"},
    editingSession: {state_version: 5, state: {metadata: {localization: {dubbing_job_id: "dub"}}}}});
  assert.equal(view.assistantStatus, "草稿已生成");
  assert.equal(view.workflow, "字幕译制");
  assert.match(view.nextStep, /试听.*审校/);
});

test("real rendered output and a reviewed version get the appropriate delivery action", async () => {
  const {workflowPresentation} = await presentation();
  const session = {state_version: 6, state: {metadata: {localization: {dubbing_job_id: "dub", reviewed_version: 6}}}};
  assert.equal(workflowPresentation({status: "ready", editingSession: session}).nextStep, "生成交付视频");
  const view = workflowPresentation({status: "ready", editingSession: session,
    renderResult: {status: "succeeded", result: {output_url: "http://local/video.mp4"}}});
  assert.equal(view.assistantStatus, "成片已生成");
  assert.equal(view.nextStep, "预览并下载交付成片");
});
