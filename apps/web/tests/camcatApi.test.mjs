import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function loadApiModule() {
  const source = await readFile(new URL("../src/camcatApi.ts", import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}

test("project inventory follows cursors and keeps projects beyond the first page", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const calls = [];
  const client = createCamCatApiClient({baseUrl: "", userId: "owner", fetchImpl: async (url) => {
    calls.push(url);
    return response(calls.length === 1
      ? {items: [{project_id: "first", sessions: []}], next_cursor: "page-two"}
      : {items: [{project_id: "new-project", sessions: []}], next_cursor: null});
  }});
  const items = await client.listAllProjects();
  assert.deepEqual(items.map(item => item.project_id), ["first", "new-project"]);
  assert.equal(calls.length, 2);
  assert.equal(new URL(calls[1], "http://local").searchParams.get("cursor"), "page-two");
  assert.ok(calls.every(url => url.includes("include_sessions=true")));
});

test("project inventory stops when a broken API repeats its cursor", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  let calls = 0;
  const client = createCamCatApiClient({baseUrl: "", userId: "owner", fetchImpl: async () => {
    calls++;
    return response({items: [], next_cursor: "repeated"});
  }});
  await assert.rejects(() => client.listAllProjects(), /分页游标重复/);
  assert.equal(calls, 2);
});

test("localization translation is bound to a saved CamCat version", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const requests = [];
  const client = createCamCatApiClient({baseUrl: "", userId: "owner", fetchImpl: async (url, init) => {
    requests.push({url, init}); return response({job_id: "translation", status: "queued"});
  }});
  await client.translateSubtitles("session", {base_version: 7, target_language: "en", source_track_id: "captions", glossary: {"术语": "Term"}});
  assert.equal(requests[0].url, "/api/v1/editing/sessions/session/localization/translate");
  assert.equal(requests[0].init.headers["X-User-Id"], "owner");
  assert.equal(JSON.parse(requests[0].init.body).base_version, 7);
  assert.equal(JSON.parse(requests[0].init.body).glossary["术语"], "Term");
});

test("localization speech generation does not implicitly apply its result", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const calls = [];
  const client = createCamCatApiClient({baseUrl: "", userId: "owner", fetchImpl: async (url, init) => {
    calls.push({url, init}); return response({job_id: "speech", status: "queued"});
  }});
  await client.dubSubtitles("session", {base_version: 8, original_audio_volume: 0, allow_duration_rewrite: false});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/v1/editing/sessions/session/localization/dub");
  assert.equal(JSON.parse(calls[0].init.body).allow_duration_rewrite, false);
});

test("localization result application sends the explicit base version", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  let captured;
  const client = createCamCatApiClient({baseUrl: "", userId: "owner", fetchImpl: async (url, init) => {
    captured = {url, init}; return response({editing_session_id: "session", state_version: 9});
  }});
  await client.applyLocalizationResult("session", "speech", 8);
  assert.equal(captured.url, "/api/v1/editing/sessions/session/localization/speech/apply");
  assert.deepEqual(JSON.parse(captured.init.body), {base_version: 8});
});

test("delivery signoff is explicit and bound to the saved version", async () => {
  const {createCamCatApiClient} = await loadApiModule();
  let captured;
  const client = createCamCatApiClient({baseUrl: "", userId: "editor", fetchImpl: async (url, init) => {
    captured = {url, init}; return response({state_version: 10});
  }});
  await client.confirmLocalizationReview("session", 9);
  assert.equal(captured.url, "/api/v1/editing/sessions/session/localization/review");
  assert.deepEqual(JSON.parse(captured.init.body), {base_version: 9, confirmed: true});
});

test("delivery ZIP download carries owner identity and requested version", async () => {
  const {createCamCatApiClient} = await loadApiModule();
  let captured;
  const client = createCamCatApiClient({baseUrl: "", userId: "editor", fetchImpl: async (url, init) => {
    captured = {url, init}; return new Response("unit ZIP bytes", {headers: {"content-type": "application/zip"}});
  }});
  const file = await client.downloadLocalizationDelivery("session", "render", 10);
  assert.equal(captured.url, "/api/v1/editing/sessions/session/localization/delivery/render?base_version=10");
  assert.equal(captured.init.headers["X-User-Id"], "editor");
  assert.equal(await file.text(), "unit ZIP bytes");
});

test("API conflict keeps structured version details for recovery", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const client = createCamCatApiClient({baseUrl: "", userId: "u", fetchImpl: async () =>
    response({code: "version_conflict", message: "conflict", details: {current_version: 4}, request_id: "r1"}, {status: 409})});
  await assert.rejects(() => client.getEditingSession("s"), error => {
    assert.equal(error.status, 409);
    assert.equal(error.details.current_version, 4);
    assert.equal(error.requestId, "r1");
    return true;
  });
});

test("job polling recovers from a temporary network failure", async () => {
  const { waitForJob } = await loadApiModule();
  let calls = 0;
  const job = await waitForJob(async () => {
    if (++calls < 3) throw new TypeError("Failed to fetch");
    return {job_id: "j", status: "succeeded"};
  }, "j", {intervalMs: 0});
  assert.equal(calls, 3);
  assert.equal(job.status, "succeeded");
});

test("job polling stops after three network errors", async () => {
  const { waitForJob } = await loadApiModule();
  let calls = 0;
  await assert.rejects(() => waitForJob(async () => {
    calls++; throw new TypeError("Failed to fetch");
  }, "j", {intervalMs: 0}));
  assert.equal(calls, 3);
});

test("a newly created session is retained when its first agent request fails", async () => {
  const { runSearchAndPlan } = await loadApiModule();
  const session = {editing_session_id: "s", state_version: 1};
  let retained;
  await assert.rejects(() => runSearchAndPlan({
    createEditingSession: async () => session,
    runEditingAgentStream: async () => { throw new Error("provider unavailable"); },
  }, {query: "剪辑", sourceJobId: "j", onSessionCreated: value => { retained = value; }}));
  assert.equal(retained, session);
});

function response(body, init = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

test("uploadVideo sends real media and license provenance", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const requests = [];
  const client = createCamCatApiClient({
    baseUrl: "http://camcat.test",
    userId: "user_001",
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      return response({ video_id: "vid_1", status: "uploaded", job_id: "job_1" });
    },
  });
  const file = new File(["video"], "source.mp4", { type: "video/mp4" });

  await client.uploadVideo(file, { licenseName: "CC0", sourceUrl: "https://example.test/source" });

  const form = requests[0].init.body;
  assert.equal(requests[0].url, "http://camcat.test/api/v1/videos");
  assert.equal(requests[0].init.headers["X-User-Id"], "user_001");
  assert.ok(form instanceof FormData);
  assert.equal(form.get("license_name"), "CC0");
  assert.equal(form.get("source_url"), "https://example.test/source");
  assert.equal(form.get("analysis_mode"), "keyframes");
});

test("permanent library upload rejects missing or local-only provenance", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  let requestCount = 0;
  const client = createCamCatApiClient({
    baseUrl: "http://camcat.test",
    userId: "user_001",
    fetchImpl: async () => {
      requestCount += 1;
      return response({ video_id: "should-not-exist" });
    },
  });
  const file = new File(["video"], "source.mp4", { type: "video/mp4" });

  await assert.rejects(() => client.uploadVideo(file), /license and HTTP\(S\) source URL/i);
  await assert.rejects(
    () =>
      client.uploadVideo(file, {
        licenseName: "user-provided",
        sourceUrl: "local://user-upload",
      }),
    /license and HTTP\(S\) source URL/i,
  );
  assert.equal(requestCount, 0);
});

test("user originals use the transient multi-file endpoint", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const requests = [];
  const client = createCamCatApiClient({
    baseUrl: "http://camcat.test",
    userId: "user_001",
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      return response({ batch_id: "b1", status: "queued", job_id: "j1", media: [] });
    },
  });

  await client.uploadSourceMedia([
    new File(["a"], "one.mp4", { type: "video/mp4" }),
    new File(["b"], "two.mov", { type: "video/quicktime" }),
  ]);

  assert.equal(requests[0].url, "http://camcat.test/api/v1/source-media");
  assert.equal(requests[0].init.body.getAll("files").length, 2);
});

test("text and image search share the multimodal request contract", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const requests = [];
  const client = createCamCatApiClient({
    baseUrl: "http://camcat.test",
    userId: "designer",
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      return response({ graph_run_id: "g1", ranked_segments: [], node_trace: [] });
    },
  });

  await client.runAgenticSearch("找海边日落", 6, "data:image/jpeg;base64,YQ==");

  const body = JSON.parse(requests[0].init.body);
  assert.equal(body.query_text, "找海边日落");
  assert.equal(body.query_image_base64, "data:image/jpeg;base64,YQ==");
  assert.equal(body.retrieval_mode, "multimodal");
  assert.equal(body.top_k, 6);
});

test("agent edit, render and job polling carry the state version", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const requests = [];
  const client = createCamCatApiClient({
    baseUrl: "http://camcat.test",
    userId: "designer",
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      if (url.endsWith("/agent")) {
        return response({ editing_session_id: "s1", state_version: 2, state: { timeline: { tracks: [] } } });
      }
      if (url.endsWith("/commands")) {
        return response({ editing_session_id: "s1", state_version: 3, state: { timeline: { tracks: [] } } });
      }
      if (url.endsWith("/render")) {
        return response({ job_id: "j1", status: "queued", progress: 0 });
      }
      return response({ job_id: "j1", status: "succeeded", progress: 1, result: { output_url: "https://cdn.test/out.mp4" } });
    },
  });

  await client.runEditingAgent("s1", 1, "剪成 15 秒");
  await client.applyEditCommands("s1", 2, [{ type: "update_title", title: "V2" }], "rename");
  await client.renderEditingSession("s1", 3);
  const job = await client.getJob("j1");

  assert.deepEqual(JSON.parse(requests[0].init.body), { base_version: 1, instruction: "剪成 15 秒" });
  assert.deepEqual(JSON.parse(requests[1].init.body), {
    base_version: 2,
    commands: [{ type: "update_title", title: "V2" }],
    reason: "rename",
  });
  assert.match(requests[1].url, /\/commands$/);
  assert.equal(JSON.parse(requests[2].init.body).base_version, 3);
  assert.equal("resolution" in JSON.parse(requests[2].init.body), false);
  assert.equal(job.result.output_url, "https://cdn.test/out.mp4");
});

test("structured backend errors expose the conflict message", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const client = createCamCatApiClient({
    baseUrl: "http://camcat.test",
    userId: "designer",
    fetchImpl: async () =>
      response(
        { error: { code: "state_version_conflict", message: "状态已更新", details: { current_version: 4 } } },
        { status: 409 },
      ),
  });

  await assert.rejects(() => client.renderEditingSession("s1", 3), /状态已更新/);
});

test("Bailian transport failures become actionable configuration errors", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const client = createCamCatApiClient({
    baseUrl: "http://camcat.test",
    userId: "designer",
    fetchImpl: async () =>
      response(
        { detail: "transient provider response 502: {\"detail\":\"Bailian transport failure: ConnectError\"}" },
        { status: 502 },
      ),
  });

  await assert.rejects(
    () => client.runAgenticSearch("测试百炼连接"),
    /无法连接阿里云百炼.*CAMCAT_BAILIAN_API_HOST.*API Key/,
  );
});

test("job polling reports real progress before completion", async () => {
  const { waitForJob } = await loadApiModule();
  const jobs = [
    { job_id: "j1", status: "queued", progress: 0 },
    { job_id: "j1", status: "running", progress: 0.45 },
    { job_id: "j1", status: "succeeded", progress: 1 },
  ];
  const progress = [];

  const completed = await waitForJob(async () => jobs.shift(), "j1", {
    intervalMs: 0,
    timeoutMs: 1000,
    onProgress: (job) => progress.push([job.status, job.progress]),
  });

  assert.equal(completed.status, "succeeded");
  assert.deepEqual(progress, [
    ["queued", 0],
    ["running", 0.45],
    ["succeeded", 1],
  ]);
});

test("first edit creates a session and runs exactly one retrieval graph", async () => {
  const { runSearchAndPlan } = await loadApiModule();
  const calls = [];
  const api = {
    async createEditingSession(videoId, goal, sourceJobId) {
      calls.push(["create", videoId, goal, sourceJobId]);
      return { editing_session_id: "s1", state_version: 1, state: { timeline: { tracks: [] } } };
    },
    async runEditingAgentStream(sessionId, version, instruction, onEvent, image) {
      calls.push(["edit", sessionId, version, instruction, image]);
      return {
        session: { editing_session_id: "s1", state_version: 2, state: { timeline: { tracks: [{ type: "video", segments: [{ segment_id: "seg-1" }] }] } } },
        agentRun: { graph_run_id: "g1", ranked_segments: [{ video_id: "vid_retrieved" }] },
      };
    },
  };

  const result = await runSearchAndPlan(api, {
    query: "剪成一条节奏明快的视频",
    sourceJobId: "source_job_1",
    queryImageBase64: "data:image/png;base64,YQ==",
  });

  assert.equal(result.editingSession.state.timeline.tracks[0].segments.length, 1);
  assert.deepEqual(calls, [
    ["create", undefined, "剪成一条节奏明快的视频", "source_job_1"],
    ["edit", "s1", 1, "剪成一条节奏明快的视频", "data:image/png;base64,YQ=="],
  ]);
});

test("SSE parser keeps node progress and completed session boundaries", async () => {
  const { parseServerSentEvents } = await loadApiModule();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: node_completed\ndata: {"message":"检索完成"}\n'));
      controller.enqueue(new TextEncoder().encode('\nevent: completed\ndata: {"message":"完成"}\n\n'));
      controller.close();
    },
  });

  const events = [];
  for await (const event of parseServerSentEvents(stream)) events.push(event);
  assert.deepEqual(events.map((item) => item.event), ["node_completed", "completed"]);
});

test("interrupted agent stream replays numbered events before polling truth", async () => {
  const { createCamCatApiClient } = await loadApiModule();
  const urls = [];
  const progress = [];
  const interrupted = new ReadableStream({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode(
          'id: 0\nevent: run_started\ndata: {"graph_run_id":"g1","message":"started"}\n\n',
        ),
      );
      controller.close();
    },
  });
  const replay = new ReadableStream({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode(
          'id: 1\nevent: node_completed\ndata: {"graph_run_id":"g1","node":"retrieve","message":"replayed"}\n\n',
        ),
      );
      controller.close();
    },
  });
  const client = createCamCatApiClient({
    baseUrl: "http://camcat.test",
    userId: "designer",
    fetchImpl: async (url) => {
      urls.push(url);
      if (url.endsWith("/agent/stream")) return new Response(interrupted);
      if (url.endsWith("/graph-runs/g1/events?after=0")) return new Response(replay);
      if (url.endsWith("/graph-runs/g1")) {
        return response({ status: "succeeded", editing_session_id: "s1" });
      }
      if (url.endsWith("/editing/sessions/s1")) {
        return response({ editing_session_id: "s1", state_version: 2, state: {} });
      }
      return response({ graph_run_id: "g1", ranked_segments: [], node_trace: [] });
    },
  });

  const result = await client.runEditingAgentStream("s1", 1, "edit", (event) => {
    progress.push(event);
  });

  assert.equal(result.session.state_version, 2);
  assert.ok(urls.some((url) => url.endsWith("/graph-runs/g1/events?after=0")));
  assert.ok(progress.some((event) => event.node === "retrieve" && event.message === "replayed"));
});
