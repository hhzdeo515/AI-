import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { TaskRecord, Transcript, KnowledgeDocument, Resource } from "../lib/types";

beforeEach(() => {
  vi.resetModules(); vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo");
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Demo must not access a server"); }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

async function start(scene = "exam", practice?: Record<string, string>) {
  const { api, chatForm } = await import("../lib/api");
  const form = chatForm({ sessionId: `test-${scene}`, requestId: "request-1", text: "private input", scene: scene as "exam" | "meeting", backend: "original", event: practice ? { practice } : {} });
  return { api, form, started: await api<{ task_id: string }>("/api/chat/async", { method: "POST", body: form }) };
}

it("demo tasks complete and survive module reload without backend requests or real session changes", async () => {
  localStorage.setItem("glasses.session.v1", "full-version-session");
  vi.useFakeTimers();
  const { api, form, started } = await start();
  expect((await api<TaskRecord>(`/api/task?task_id=${started.task_id}`)).status).toBe("pending");
  const duplicate = await api<{ task_id: string }>("/api/chat/async", { method: "POST", body: form });
  expect(duplicate.task_id).toBe(started.task_id);
  vi.setSystemTime(Date.now() + 5000); vi.resetModules();
  const reloaded = await import("../lib/api");
  const result = await reloaded.api<TaskRecord>(`/api/task?task_id=${started.task_id}`);
  expect(result.status).toBe("done");
  expect(result.result?.note).toContain("示例");
  expect(result.result?.artifacts?.[0].questions?.length).toBeGreaterThan(1);
  expect(decodeURIComponent(reloaded.exportUrl(result.result!.archived_id!, "md"))).toContain("示例");
  expect((await reloaded.api<{ tasks: TaskRecord[] }>("/api/tasks")).tasks).toHaveLength(1);
  const { saveSession } = await import("../lib/tasks");
  saveSession(localStorage, { sessionId: "demo-session", taskId: started.task_id });
  expect(localStorage.getItem("glasses.session.v1")).toBe("full-version-session");
  expect(fetch).not.toHaveBeenCalled();
});

it.each(["essay", "interview"])("demo %s supports follow-up actions and labels its feedback as an example", async agent => {
  vi.useFakeTimers();
  const { api, started } = await start("exam", { agent, action: "run" });
  vi.setSystemTime(Date.now() + 5000);
  const first = await api<TaskRecord>(`/api/task?task_id=${started.task_id}`);
  expect(first.result?.artifacts?.find(a => a.kind === "practice")?.next_actions?.length).toBeGreaterThan(0);
  const { chatForm } = await import("../lib/api");
  const next = await api<{ task_id: string }>("/api/chat/async", { method: "POST", body: chatForm({ sessionId: "test-exam", requestId: "request-2", scene: "exam", backend: "jev", text: "继续", event: { practice: { agent, action: agent === "essay" ? "critique" : "answer", draft: "我的草稿", answer: "我的回答" } } }) });
  vi.setSystemTime(Date.now() + 5000);
  const second = await api<TaskRecord>(`/api/task?task_id=${next.task_id}`);
  expect(second.result?.text).toContain("示例");
  expect(second.result?.text).not.toBe(first.result?.text);
});

it("demo meeting speaker edits persist into exported local transcript", async () => {
  vi.useFakeTimers();
  const { api, started } = await start("meeting");
  vi.setSystemTime(Date.now() + 5000);
  const task = await api<TaskRecord>(`/api/task?task_id=${started.task_id}`);
  const id = task.result!.archived_id!;
  const form = new FormData();
  Object.entries({ id, action: "rename", speaker: "0", name: "测试发言人" }).forEach(([k, v]) => form.set(k, v));
  const edited = await api<Transcript>("/api/transcript", { method: "POST", body: form });
  expect(edited.speakers[0].label).toBe("测试发言人");
  form.set("action", "resummarize");
  const summary = await api<Transcript>("/api/transcript", { method: "POST", body: form });
  expect(summary.content).toContain("测试发言人");
  const archived = await api<Resource>(`/api/resource?id=${id}`);
  expect(archived.content).toBe(summary.content);
});

it("demo library supports local import, reading and deletion; unknown endpoints never fall through to fetch", async () => {
  const { api } = await import("../lib/api");
  const form = new FormData(); form.set("title", "示例资料"); form.set("content", "仅在此浏览器保存");
  const doc = await api<KnowledgeDocument>("/api/exam/knowledge", { method: "POST", body: form });
  expect((await api<KnowledgeDocument>(`/api/exam/knowledge/${doc.id}`)).content).toBe("仅在此浏览器保存");
  await api(`/api/exam/knowledge/${doc.id}`, { method: "DELETE" });
  expect((await api<{ documents: KnowledgeDocument[] }>("/api/exam/knowledge")).documents).toEqual([]);
  await expect(api("/api/unknown")).rejects.toThrow();
  expect(fetch).not.toHaveBeenCalled();
});
