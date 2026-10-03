import { ApiError } from "./api-error";
import { demoApi, demoExportUrl } from "./demo";
import type { Artifact, KnowledgeDocument, Progress, Reply, Resource, TaskRecord, Transcript } from "./types";

type BrowserWindow = Window & { LegacyDemoExport?: (id: string, format: string) => void };
const unsupported = "当前工作空间暂不支持此功能。";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
}
function requestUrl(input: RequestInfo | URL): URL {
  return new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, window.location.href);
}
function businessRoute(url: URL): boolean {
  return url.origin === window.location.origin && (url.pathname === "/health" || url.pathname === "/api" || url.pathname.startsWith("/api/"));
}
function meetingText(text: string): string {
  return text.replace("\n\n## 发言记录（可修订的示例）\n", "\n\n---\n\n## 会议文字记录\n\n可修订的示例发言记录\n\n");
}
function legacyReply(reply: Reply): Reply {
  const id = reply.archived_id;
  const artifacts: (Artifact & { id?: string; editable?: boolean })[] = (reply.artifacts || []).map(artifact => ({
    ...artifact, id,
    ...(artifact.kind === "practice" && !artifact.stages ? {
      stages: [{ id: /点评|草稿点评/.test(reply.text) ? "feedback" : /追问/.test(reply.text) ? "follow_up" : "sample", label: "示例内容", text: reply.text }],
    } : {}),
  }));
  if (reply.scene === "meeting" && id) artifacts.push({ kind: "transcript", id, editable: true });
  return { ...reply, text: reply.scene === "meeting" ? meetingText(reply.text) : reply.text, artifacts };
}
function legacyTask(task: TaskRecord): TaskRecord {
  return { ...task, result: task.result ? legacyReply(task.result) : task.result };
}
function legacyResource(resource: Resource) {
  const content = resource.scene === "meeting" ? meetingText(resource.content || "") : resource.content || "";
  return { ...resource, content, size: content.length };
}
function legacyDocument(document: KnowledgeDocument) {
  return { ...document, chars: document.content?.length || 0 };
}
function legacyTranscript(transcript: Transcript) {
  return {
    ...transcript, content: meetingText(transcript.content || ""), report: "可修订的示例发言记录",
    names: Object.fromEntries(transcript.speakers.map(speaker => [String(speaker.id), speaker.label])),
    speakers: transcript.speakers.map(speaker => {
      const turns = transcript.utterances.filter(turn => turn.speaker === speaker.id);
      return { ...speaker, turns: turns.length, seconds: turns.reduce((sum, turn) => sum + Math.max(0, (turn.end || 0) - (turn.start || 0)), 0) };
    }),
    utterances: transcript.utterances.map(turn => ({ ...turn, begin_ms: (turn.start || 0) * 1000, end_ms: (turn.end || 0) * 1000 })),
  };
}
async function requestOptions(input: RequestInfo | URL, init: RequestInit): Promise<RequestInit> {
  const request = typeof Request !== "undefined" && input instanceof Request ? input : null;
  const method = (init.method || request?.method || "GET").toUpperCase();
  let body = init.body;
  if (!body && request && !["GET", "HEAD"].includes(method)) body = await request.clone().formData();
  return { ...init, method, body, signal: init.signal || request?.signal };
}
function chatOptions(init: RequestInit): RequestInit {
  const form = new FormData();
  if (init.body instanceof FormData) for (const [key, value] of init.body) {
    // Files stay in the old UI's local previews. Only textual routing metadata reaches the sample transport.
    if (typeof value === "string") form.append(key, value);
  }
  if (!form.get("request_id")) form.set("request_id", crypto.randomUUID());
  return { ...init, body: form };
}
function pause(signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    const aborted = () => { clearTimeout(timer); signal?.removeEventListener("abort", aborted); reject(new DOMException("已取消", "AbortError")); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", aborted); resolve(); }, 100);
    if (signal?.aborted) aborted(); else signal?.addEventListener("abort", aborted, { once: true });
  });
}

/** Fetch-compatible, browser-only compatibility layer. Business routes never fall through to a server. */
export async function legacyDemoFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  try {
    const url = requestUrl(input);
    if (!businessRoute(url)) return json({ error: unsupported }, 404);
    const options = await requestOptions(input, init);
    if (options.signal?.aborted) throw new DOMException("已取消", "AbortError");
    const path = url.pathname + url.search;
    const route = url.pathname, method = options.method;
    if (route === "/health" && method === "GET") return json({ ok: true, status: "ok", engine: "demo", model_backends: ["original", "jev"], jev_ready: true, api_key_configured: false, auth_enabled: false });
    if (route === "/api/state" && method === "GET") return json({ meeting: { status: "idle", transcript: "", id: "" } });
    if (route === "/api/exam/public-knowledge" && method === "GET") return json({ count: 0, modules: {}, concepts: [], sources: [], scope: "使用本机示例内容。" });
    if (["/api/chat", "/api/chat/async"].includes(route) && method === "POST") {
      const started = await demoApi<{ task_id: string; request_id: string }>("/api/chat/async", chatOptions(options));
      if (route === "/api/chat/async") return json(started);
      while (true) {
        const task = await demoApi<TaskRecord>(`/api/task?task_id=${encodeURIComponent(started.task_id)}`, { signal: options.signal });
        if (task.status === "done" && task.result) return json(legacyReply(task.result));
        if (!["pending", "running"].includes(task.status)) throw new ApiError(task.error || "任务未完成。", 400);
        await pause(options.signal);
      }
    }
    if (route === "/api/export" && method === "GET") {
      const id = url.searchParams.get("id") || "", format = url.searchParams.get("format") || "";
      const exported = demoExportUrl(id, format);
      if (!exported) return json({ error: unsupported }, 404);
      return new Response(decodeURIComponent(exported.slice(exported.indexOf(",") + 1)), { headers: { "Content-Type": "text/plain; charset=utf-8", "Content-Disposition": `attachment; filename="${id.replace(/[^\w-]/g, "_")}.${format}"` } });
    }
    const data = await demoApi<unknown>(path, options);
    if (route === "/api/task") return json(legacyTask(data as TaskRecord));
    if (route === "/api/tasks") return json({ tasks: (data as { tasks: TaskRecord[] }).tasks.map(legacyTask) });
    if (route === "/api/progress") {
      const progress = data as Progress;
      return json({ ...progress, steps: progress.steps?.map(step => ({ ...step, label: step.id })) });
    }
    if (route === "/api/resource") return json(legacyResource(data as Resource));
    if (route === "/api/resources") {
      const scene = url.searchParams.get("scene");
      return json({ resources: (data as { resources: Resource[] }).resources.filter(resource => !scene || resource.scene === scene).map(legacyResource) });
    }
    if (route === "/api/transcript") return json(legacyTranscript(data as Transcript));
    if (route === "/api/exam/knowledge" && method === "GET") return json({ documents: (data as { documents: KnowledgeDocument[] }).documents.map(legacyDocument) });
    if (route === "/api/exam/knowledge" || (route.startsWith("/api/exam/knowledge/") && method === "GET")) return json(legacyDocument(data as KnowledgeDocument));
    return json(data);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return json({ error: error instanceof Error ? error.message : unsupported }, error instanceof ApiError && error.status ? error.status : 400);
  }
}

/** Install before the original scripts load. The build rewrites their export navigation to this local hook. */
export function installLegacyDemoTransport(): () => void {
  if (typeof window === "undefined") return () => {};
  const host = window as BrowserWindow;
  const originalFetch = globalThis.fetch;
  const originalDownload = host.LegacyDemoExport;
  const wrapped: typeof fetch = (input, init) => businessRoute(requestUrl(input)) ? legacyDemoFetch(input, init) : originalFetch(input, init);
  globalThis.fetch = wrapped;
  host.LegacyDemoExport = (id, format) => {
    const href = demoExportUrl(id, format);
    if (!href) return;
    const link = document.createElement("a"); link.href = href; link.download = `${id}.${format}`;
    document.body.append(link); link.click(); link.remove();
  };
  // These operations have no sample implementation; avoid offering controls that cannot work locally.
  const hideUnavailable = () => {
    document.querySelectorAll<HTMLElement>('[data-fmt="docx"],[data-fmt="pdf"],.spk-merge,#tr-reset,#hw-result-speak').forEach(element => { if (!element.hidden) element.hidden = true; });
    document.querySelectorAll<HTMLOptionElement>('.tr-spk option[value="new"]').forEach(option => { if (!option.hidden) option.hidden = true; option.disabled = true; });
  };
  const stopUnavailable = (event: MouseEvent) => {
    if (!(event.target instanceof Element) || !event.target.closest('#hw-result-speak,[data-fmt="docx"],[data-fmt="pdf"]')) return;
    event.preventDefault(); event.stopImmediatePropagation();
  };
  document.addEventListener("click", stopUnavailable, true);
  hideUnavailable();
  const observer = new MutationObserver(hideUnavailable);
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
  return () => {
    observer.disconnect();
    document.removeEventListener("click", stopUnavailable, true);
    if (globalThis.fetch === wrapped) globalThis.fetch = originalFetch;
    if (originalDownload) host.LegacyDemoExport = originalDownload; else delete host.LegacyDemoExport;
  };
}
