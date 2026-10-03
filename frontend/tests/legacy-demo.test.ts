import { afterEach, beforeEach, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo");
  localStorage.clear();
  document.body.replaceChildren();
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Legacy Demo must not access a server"); }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it("passes real translation through while keeping neighbouring business routes local", async () => {
  const network = vi.fn(async () => new Response(JSON.stringify({ translation: "Bonjour", provider: "MyMemory" })));
  vi.stubGlobal("fetch", network);
  const { installLegacyDemoTransport } = await import("../lib/legacy-demo");
  const restore = installLegacyDemoTransport();
  try {
    const response = await fetch("/api/translate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "Hello", source: "en", target: "fr" }) });
    expect(await response.json()).toMatchObject({ translation: "Bonjour", provider: "MyMemory" });
    expect(network).toHaveBeenCalledTimes(1);
    expect((await fetch("/api/translate/extra")).status).toBe(404);
    expect((await fetch("/api/resources")).status).toBe(200);
    expect(network).toHaveBeenCalledTimes(1);
  } finally { restore(); }
});

it("passes exact Amap configuration and proxy requests through without altering authentication or errors", async () => {
  const network = vi.fn(async () => new Response(JSON.stringify({code:"map_not_configured"}),{status:503}));
  vi.stubGlobal("fetch",network);
  const { installLegacyDemoTransport } = await import("../lib/legacy-demo");
  const restore=installLegacyDemoTransport();
  try {
    const options={headers:{Authorization:"Bearer test-only-map-access"},credentials:"same-origin" as const};
    for (const path of ["/api/map-config","/api/amap-proxy?path=v3/place/text","/_AMapService/v3/place/text"]) {
      const result=await fetch(path,options);
      expect(result.status).toBe(503);
      expect(await result.json()).toEqual({code:"map_not_configured"});
      expect(network).toHaveBeenLastCalledWith(path,options);
    }
    for (const path of ["/api/map-config/extra","/api/amap-proxy/extra"]) expect((await fetch(path)).status).toBe(404);
    expect(network).toHaveBeenCalledTimes(3);
  } finally { restore(); }
});

function chat(scene = "exam", requestId = crypto.randomUUID(), practice?: Record<string, string>) {
  const body = new FormData();
  body.set("scene", scene); body.set("session_id", "legacy-session"); body.set("request_id", requestId);
  body.set("text", "private source text"); body.set("exam_backend", "jev");
  if (practice) body.set("event", JSON.stringify({ practice }));
  return body;
}

it("serves the legacy startup contracts locally and rejects unknown API routes without network access", async () => {
  const { legacyDemoFetch } = await import("../lib/legacy-demo");
  const health = await (await legacyDemoFetch("/health")).json();
  expect(health.status).toBe("ok");
  expect(health.model_backends).toEqual(["original", "jev"]);
  expect(health.jev_ready).toBe(true);
  expect(await (await legacyDemoFetch("/api/state?owner=local")).json()).toEqual({ meeting: { status: "idle", transcript: "", id: "" } });
  expect(await (await legacyDemoFetch("/api/exam/public-knowledge")).json()).toMatchObject({ count: 0, modules: {}, concepts: [], sources: [] });
  const unknown = await legacyDemoFetch("/api/unknown", { method: "POST", body: chat() });
  expect(unknown.status).toBe(404); expect(await unknown.json()).toHaveProperty("error");
  expect(fetch).not.toHaveBeenCalled();
});

it("adapts uploaded sample tasks, progress and archive sizes without persisting the files or replacing existing data", async () => {
  vi.useFakeTimers();
  const { legacyDemoFetch } = await import("../lib/legacy-demo");
  localStorage.setItem("glasses.session.v1", "full-version-session");
  const body = chat("exam", "photo-request"); body.append("files", new File(["private photo"], "question.png", { type: "image/png" }));
  const started = await (await legacyDemoFetch("/api/chat/async", { method: "POST", body })).json();
  const progress = await (await legacyDemoFetch("/api/progress?request_id=photo-request")).json();
  expect(progress.steps.every((step: { label?: string }) => !!step.label)).toBe(true);
  vi.setSystemTime(Date.now() + 5000);
  const record = await (await legacyDemoFetch(`/api/task?task_id=${started.task_id}`)).json();
  expect(record.status).toBe("done");
  expect(record.result.artifacts[0]).toMatchObject({ id: started.task_id, kind: "exam_batch" });
  expect(record.result.artifacts[0].questions[0].answer).toBe("126");
  const archive = await (await legacyDemoFetch("/api/resources?scene=exam")).json();
  expect(archive.resources).toHaveLength(1);
  expect(archive.resources[0].size).toBe(archive.resources[0].content.length);
  expect((await (await legacyDemoFetch("/api/resources?scene=meeting")).json()).resources).toEqual([]);
  const stored = localStorage.getItem("glasses.demo.data.v1")!;
  expect(stored).not.toMatch(/private source text|private photo|question\.png/);
  expect(localStorage.getItem("glasses.session.v1")).toBe("full-version-session");
  vi.resetModules();
  const reloaded = await import("../lib/legacy-demo");
  expect((await (await reloaded.legacyDemoFetch("/api/resources")).json()).resources[0].id).toBe(started.task_id);
  expect(localStorage.getItem("glasses.demo.data.v1")).toBe(stored);
  expect(fetch).not.toHaveBeenCalled();
});

it("completes synchronous legacy chat locally and gives independent submissions unique request IDs", async () => {
  vi.useFakeTimers();
  const { legacyDemoFetch } = await import("../lib/legacy-demo");
  const firstBody = chat(); firstBody.delete("request_id");
  const first = legacyDemoFetch("/api/chat", { method: "POST", body: firstBody });
  await vi.advanceTimersByTimeAsync(3000);
  const reply = await (await first).json();
  expect(reply.artifacts[0].questions[0].answer).toBe("126");
  const secondBody = chat(); secondBody.delete("request_id");
  const second = await (await legacyDemoFetch("/api/chat/async", { method: "POST", body: secondBody })).json();
  expect(second.task_id).not.toBe(reply.archived_id);
  expect(fetch).not.toHaveBeenCalled();
});

it("converts practice stages and editable transcript fields required by the old result panels", async () => {
  vi.useFakeTimers();
  const { legacyDemoFetch } = await import("../lib/legacy-demo");
  const practice = await (await legacyDemoFetch("/api/chat/async", { method: "POST", body: chat("exam", "practice-request", { agent: "essay", action: "outline" }) })).json();
  const meeting = await (await legacyDemoFetch("/api/chat/async", { method: "POST", body: chat("meeting", "meeting-request") })).json();
  vi.setSystemTime(Date.now() + 5000);
  const practiceReply = (await (await legacyDemoFetch(`/api/task?task_id=${practice.task_id}`)).json()).result;
  expect(practiceReply.artifacts[0].stages[0].text).toContain("写作提纲示例");
  expect(practiceReply.artifacts[0].next_actions.some((action: { id: string }) => action.id === "critique")).toBe(true);
  const meetingReply = (await (await legacyDemoFetch(`/api/task?task_id=${meeting.task_id}`)).json()).result;
  expect(meetingReply.artifacts[0]).toMatchObject({ id: meeting.task_id, kind: "transcript", editable: true });
  expect(meetingReply.text).toContain("\n\n---\n\n## 会议文字记录");
  const transcript = await (await legacyDemoFetch(`/api/transcript?id=${meeting.task_id}`)).json();
  expect(transcript.speakers[0]).toMatchObject({ turns: 2, seconds: 10 });
  expect(transcript.utterances[1]).toMatchObject({ begin_ms: 6000, end_ms: 12000 });
  const rename = new FormData(); Object.entries({ id: meeting.task_id, action: "rename", speaker: "0", name: "测试发言人" }).forEach(([key, value]) => rename.set(key, value));
  const renamed = await (await legacyDemoFetch("/api/transcript", { method: "POST", body: rename })).json();
  expect(renamed.names["0"]).toBe("测试发言人");
  rename.set("action", "resummarize");
  expect((await (await legacyDemoFetch("/api/transcript", { method: "POST", body: rename })).json()).content).toContain("测试发言人");
  expect(fetch).not.toHaveBeenCalled();
});

it("provides knowledge character counts and serves only local Markdown and text exports", async () => {
  vi.useFakeTimers();
  const { legacyDemoFetch } = await import("../lib/legacy-demo");
  const body = new FormData(); body.set("title", "本机资料"); body.set("content", "仅在当前浏览器保存");
  const doc = await (await legacyDemoFetch("/api/exam/knowledge", { method: "POST", body })).json();
  expect(doc.chars).toBe(9);
  expect((await (await legacyDemoFetch("/api/exam/knowledge")).json()).documents[0].chars).toBe(9);
  await legacyDemoFetch(`/api/exam/knowledge/${doc.id}`, { method: "DELETE" });
  expect((await (await legacyDemoFetch("/api/exam/knowledge")).json()).documents).toEqual([]);
  const task = await (await legacyDemoFetch("/api/chat/async", { method: "POST", body: chat() })).json();
  vi.setSystemTime(Date.now() + 5000);
  for (const format of ["md", "txt"]) {
    const download = await legacyDemoFetch(`/api/export?id=${task.task_id}&format=${format}`);
    expect(download.status).toBe(200);
    expect(download.headers.get("Content-Disposition")).toContain(`.${format}`);
    expect(await download.text()).toContain("126");
  }
  expect((await legacyDemoFetch(`/api/export?id=${task.task_id}&format=pdf`)).status).toBe(404);
  expect(fetch).not.toHaveBeenCalled();
});

it("installs local business transport and download hooks while allowing ordinary static requests", async () => {
  vi.useFakeTimers();
  const network = vi.fn(async () => new Response("asset")); vi.stubGlobal("fetch", network);
  const { legacyDemoFetch, installLegacyDemoTransport } = await import("../lib/legacy-demo");
  const restore = installLegacyDemoTransport();
  try {
    expect((await fetch(new URL("/health", location.origin))).status).toBe(200);
    expect((await fetch(new Request(new URL("/api/unknown", location.origin)))).status).toBe(404);
    expect(network).not.toHaveBeenCalled();
    await fetch("/static/app.css"); expect(network).toHaveBeenCalledTimes(1);
    const task = await (await legacyDemoFetch("/api/chat/async", { method: "POST", body: chat() })).json();
    vi.setSystemTime(Date.now() + 5000);
    const clicks: { href: string; download: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicks.push({ href: this.href, download: this.download }); });
    const download = (window as Window & { LegacyDemoExport?: (id: string, format: string) => void }).LegacyDemoExport!;
    download(task.task_id, "md"); download(task.task_id, "pdf");
    expect(clicks).toHaveLength(1);
    expect(clicks[0].href).toMatch(/^data:text\/plain/);
    expect(clicks[0].download).toBe(`${task.task_id}.md`);
    const speech = document.createElement("button"); speech.id = "hw-result-speak";
    const startServerAudio = vi.fn(); speech.addEventListener("click", startServerAudio); document.body.append(speech);
    speech.click(); expect(startServerAudio).not.toHaveBeenCalled();
    await Promise.resolve(); expect(speech.hidden).toBe(true);
    expect(network).toHaveBeenCalledTimes(1);
  } finally { restore(); }
  expect(fetch).toBe(network);
});
