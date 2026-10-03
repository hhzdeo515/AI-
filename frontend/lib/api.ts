import type { ChatInput } from "./types";

export class ApiError extends Error {
  constructor(message: string, public status = 0) { super(message); this.name = "ApiError"; }
}

/** Cookie credentials stay same-origin. No access token is stored in browser storage. */
export async function api<T>(path: string, init: RequestInit = {}, timeoutMs = 20000): Promise<T> {
  if (!path.startsWith("/") || path.startsWith("//")) throw new ApiError("只能访问同源服务");
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (init.signal?.aborted) controller.abort();
  init.signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, timeoutMs);
  try {
    const response = await fetch(path, { ...init, signal: controller.signal, credentials: "same-origin", cache: "no-store" });
    let data: unknown;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("assistant:unauthorized"));
      const message = data && typeof data === "object" && "error" in data ? String(data.error) : "";
      throw new ApiError(message || (response.status === 413 ? "附件总大小超过 32 MB，请压缩或分段后提交。" : `服务返回错误（${response.status}），请稍后重试。`), response.status);
    }
    if (!data || typeof data !== "object") throw new ApiError("服务未返回有效数据，请检查连接后重试。");
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (init.signal?.aborted) throw new DOMException("已取消", "AbortError");
    if (controller.signal.aborted) throw new ApiError("连接超时，任务可能仍在后台处理。将继续查询原任务。");
    throw new ApiError("暂时无法连接服务，请检查网络。已有任务会保留。");
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener("abort", cancel);
  }
}

export function jsonPost<T>(path: string, data: unknown) {
  return api<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

export function chatForm(input: ChatInput): FormData {
  const form = new FormData();
  form.set("owner", "local"); form.set("session_id", input.sessionId);
  form.set("request_id", input.requestId); form.set("text", input.text);
  form.set("scene", input.scene); form.set("exam_backend", input.backend);
  if (input.event) form.set("event", JSON.stringify(input.event));
  for (const file of input.files || []) form.append("files", file, file.name);
  return form;
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : "操作未完成，请重试。";
export const exportUrl = (id: string, format: string) => `/api/export?owner=local&id=${encodeURIComponent(id)}&format=${encodeURIComponent(format)}`;
export const mediaUrl = (value: string) => value.startsWith("/api/task/media?") ? value : "";
