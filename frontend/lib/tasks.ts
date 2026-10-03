import { ApiError } from "./api";
import { DEMO_SESSION_KEY, IS_DEMO } from "./mode";
import type { TaskRecord } from "./types";
export function taskScene(task: TaskRecord): "meeting" | "exam" | null {
  if (task.scene === "meeting" || task.result?.scene === "meeting" || task.session_id?.endsWith("-meeting")) return "meeting";
  if (task.scene === "exam" || task.result?.scene === "exam" || task.session_id?.endsWith("-exam")) return "exam";
  return null;
}

export const SESSION_KEY = IS_DEMO ? DEMO_SESSION_KEY : "glasses.session.v1";
export interface SavedSession { sessionId: string; taskId?: string; requestId?: string; readonlyTaskId?: string }
export function readSavedSession(storage: Pick<Storage, "getItem">): SavedSession | null {
  try {
    const data = JSON.parse(storage.getItem(SESSION_KEY) || "null");
    return data && typeof data.sessionId === "string" && (!data.taskId || typeof data.taskId === "string") && (!data.requestId || typeof data.requestId === "string")
      ? { sessionId: data.sessionId, taskId: data.taskId, requestId: data.requestId, readonlyTaskId: typeof data.readonlyTaskId === "string" ? data.readonlyTaskId : undefined } : null;
  } catch { return null; }
}
export function saveSession(storage: Pick<Storage, "setItem">, value: SavedSession) {
  try { storage.setItem(SESSION_KEY, JSON.stringify(value)); } catch { /* Private mode can disable storage. The current task still works. */ }
}
export function findRecoveryTask(tasks: TaskRecord[], saved: SavedSession) {
  return tasks.find(task => saved.taskId ? task.id === saved.taskId : !!saved.requestId && task.request_id === saved.requestId);
}
export function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException("已取消", "AbortError")); return; }
    const cancel = () => { clearTimeout(timer); reject(new DOMException("已取消", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", cancel); resolve(); }, ms);
    signal.addEventListener("abort", cancel, { once: true });
  });
}
export async function pollTask(taskId: string, options: {
  read: (id: string, signal: AbortSignal) => Promise<TaskRecord>;
  update: (record: TaskRecord) => void; signal: AbortSignal;
  networkError?: (error: unknown) => void; delay?: (ms: number, signal: AbortSignal) => Promise<void>;
}) {
  let failures = 0;
  while (!options.signal.aborted) {
    try {
      const record = await options.read(taskId, options.signal);
      if (options.signal.aborted) return;
      failures = 0; options.update(record);
      if (!["pending", "running"].includes(record.status)) return;
    } catch (error) {
      if (options.signal.aborted) return;
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) throw error;
      failures++; options.networkError?.(error);
    }
    await (options.delay || sleep)(Math.min(1500 * 2 ** failures, 15000), options.signal);
  }
}
