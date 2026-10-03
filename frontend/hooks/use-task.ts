"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, chatForm, errorText, jsonPost } from "../lib/api";
import { findRecoveryTask, pollTask, readSavedSession, saveSession, sleep, taskScene, type SavedSession } from "../lib/tasks";
import type { ChatInput, Progress, TaskRecord } from "../lib/types";

export function useTask() {
  const saved = useRef<SavedSession>({ sessionId: "" });
  const controller = useRef<AbortController | null>(null);
  const lock = useRef(false);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [ready, setReady] = useState(false);
  const persist = useCallback((value: SavedSession) => { saved.current = value; saveSession(localStorage, value); }, []);

  const monitor = useCallback((id: string) => {
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    setUncertain(false); setUnavailable(false); setError("");
    void pollTask(id, {
      signal: current.signal,
      read: (taskId, signal) => api<TaskRecord>(`/api/task?owner=local&task_id=${encodeURIComponent(taskId)}`, { signal }),
      update: record => {
        setTask(record); setError("");
        persist({ ...saved.current, taskId: record.id, requestId: record.request_id || saved.current.requestId });
        if (record.request_id && ["pending", "running"].includes(record.status)) {
          void api<Progress>(`/api/progress?request_id=${encodeURIComponent(record.request_id)}`, { signal: current.signal })
            .then(value => { if (!current.signal.aborted) setProgress(value); }).catch(() => {});
        }
      },
      networkError: e => setError(errorText(e)),
    }).catch(e => { if (!current.signal.aborted) { setError(errorText(e)); if (e instanceof ApiError && e.status === 404) setUnavailable(true); } });
  }, [persist]);

  const recover = useCallback(async () => {
    setError("");
    if (saved.current.taskId) { monitor(saved.current.taskId); return; }
    if (!saved.current.requestId) return;
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    setUncertain(true);
    // The POST may have reached Python even if its response was lost. Only GET here.
    for (let attempt = 0; attempt < 5 && !current.signal.aborted; attempt++) {
      try {
        const response = await api<{ tasks: TaskRecord[] }>("/api/tasks?owner=local", { signal: current.signal });
        const found = findRecoveryTask(response.tasks, saved.current);
        if (found) { setTask(found); persist({ ...saved.current, taskId: found.id }); monitor(found.id); return; }
      } catch (e) {
        if (current.signal.aborted) return;
        setError(errorText(e));
        if (e instanceof ApiError && e.status === 401) return;
      }
      if (attempt < 4) { try { await sleep(2500, current.signal); } catch { return; } }
    }
    if (!current.signal.aborted) setError("暂未找到提交回执。请再次查询，或在确认任务列表中没有该请求后开始新任务。");
  }, [monitor, persist]);

  useEffect(() => {
    const value = readSavedSession(localStorage) || { sessionId: `web-${crypto.randomUUID()}` };
    persist(value); setReadOnly(!!value.readonlyTaskId && value.readonlyTaskId === value.taskId); setReady(true);
    if (value.taskId) monitor(value.taskId);
    else if (value.requestId) void recover();
    return () => controller.current?.abort();
  }, [monitor, persist, recover]);

  const submit = useCallback(async (input: Omit<ChatInput, "sessionId" | "requestId">) => {
    if (lock.current || readOnly || uncertain || task && ["pending", "running"].includes(task.status)) return false;
    lock.current = true; setSubmitting(true); setTask(null); setError(""); setProgress(null); setUnavailable(false);
    const requestId = crypto.randomUUID();
    const sessionId = saved.current.sessionId;
    persist({ sessionId, requestId });
    try {
      const started = await api<{ task_id: string; request_id: string }>("/api/chat/async", {
        method: "POST", body: chatForm({ ...input, sessionId: task?.session_id && taskScene(task) === input.scene ? task.session_id : `${sessionId}-${input.scene}`, requestId }),
      }, 60000);
      persist({ sessionId, taskId: started.task_id, requestId: started.request_id });
      setTask({ id: started.task_id, request_id: started.request_id, status: "pending", scene: input.scene });
      monitor(started.task_id); return true;
    } catch (e) {
      setError(errorText(e));
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        persist({ sessionId: saved.current.sessionId });
      } else { setUncertain(true); void recover(); }
      return false;
    } finally { lock.current = false; setSubmitting(false); }
  }, [monitor, persist, readOnly, recover, task, uncertain]);

  const retry = useCallback(async () => {
    if (!task || lock.current) return;
    lock.current = true; setSubmitting(true); setError("");
    try {
      const response = await jsonPost<{ task_id?: string; id?: string }>("/api/task/retry", { task_id: task.id, owner: "local" });
      const id = response.task_id || response.id || task.id;
      setTask({ ...task, id, status: "pending", error: "" }); monitor(id);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status === 0 || e.status >= 500) monitor(task.id);
      else setError(errorText(e));
    } finally { lock.current = false; setSubmitting(false); }
  }, [monitor, task]);

  const select = (record: TaskRecord) => {
    if (lock.current) return;
    const sessionId = record.session_id?.replace(/-(exam|meeting)$/, "") || saved.current.sessionId;
    persist({ sessionId, taskId: record.id, requestId: record.request_id, readonlyTaskId: record.id });
    setReadOnly(true); setTask(record); setProgress(null); monitor(record.id);
  };
  const reset = () => {
    if (lock.current) return;
    controller.current?.abort(); setTask(null); setProgress(null); setError(""); setUncertain(false); setUnavailable(false); setReadOnly(false);
    persist({ sessionId: `web-${crypto.randomUUID()}` });
  };
  return { task, progress, error, ready, submitting, uncertain, readOnly, busy: submitting || uncertain || !unavailable && !!task && ["pending", "running"].includes(task.status), submit, retry, recover, select, reset };
}
