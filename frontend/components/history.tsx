"use client";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeftIcon, ClockCounterClockwiseIcon } from "@phosphor-icons/react";
import { IS_DEMO } from "../lib/mode";
import { api, errorText, exportUrl } from "../lib/api";
import type { Resource, TaskRecord, Transcript } from "../lib/types";
import { Modal } from "./modal";
import { Markdown } from "./markdown";
const statusNames = { pending: "排队中", running: "处理中", interrupted: "需确认继续", done: "已结束", error: "未完成" };
const taskDate = (task: TaskRecord) => task.created ? new Date(task.created * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : task.created_at || "已保存任务";

export function History({ onTask, onClose }: { onTask: (task: TaskRecord) => void; onClose: () => void }) {
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [resources, setResources] = useState<Resource[]>([]);
  const [detail, setDetail] = useState<Resource | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"tasks" | "resources">("tasks");
  const refresh = useCallback(async () => {
    setError(""); setLoading(true);
    const responses = await Promise.allSettled([api<{ tasks: TaskRecord[] }>("/api/tasks?owner=local"), api<{ resources: Resource[] }>("/api/resources?owner=local")]);
    if (responses[0].status === "fulfilled") setTasks(responses[0].value.tasks); else setError(errorText(responses[0].reason));
    if (responses[1].status === "fulfilled") setResources(responses[1].value.resources); else setError(errorText(responses[1].reason));
    setLoading(false);
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  async function open(id: string) {
    setBusy(true); setError(""); setTranscript(null);
    try {
      const resource = await api<Resource>(`/api/resource?owner=local&id=${encodeURIComponent(id)}`); setDetail(resource);
      if (resource.has_transcript) setTranscript(await api(`/api/transcript?owner=local&id=${encodeURIComponent(id)}`));
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function edit(action: string, fields: Record<string, string>) {
    if (!detail) return;
    setBusy(true); setError("");
    const form = new FormData(); Object.entries({ owner: "local", id: detail.id, action, ...fields }).forEach(([key, value]) => form.set(key, value));
    try {
      const updated = await api<Transcript>("/api/transcript", { method: "POST", body: form }, action === "resummarize" ? 180000 : 20000);
      setTranscript(updated); setDetail({ ...detail, content: updated.content || detail.content });
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <Modal title="任务与历史" onClose={onClose} wide>
    {error && <p className="error-box mb-4" role="alert">{error}</p>}
    {detail ? <><button className="button small mb-5" onClick={() => { setDetail(null); setTranscript(null); }}><ArrowLeftIcon size={16} />返回列表</button><div className="flex flex-wrap justify-between items-start gap-4"><div><p className="eyebrow">{detail.scene === "meeting" ? "会议纪要" : "题解与练习"}</p><h3 className="mt-2 text-xl">{detail.title}</h3></div><div className="flex gap-2 flex-wrap">{(IS_DEMO ? ["md", "txt"] : ["md", "txt", "docx", "pdf"]).map(format => <a key={format} className="button small" href={exportUrl(detail.id, format)} download={IS_DEMO ? `ai-glasses-demo.${format}` : undefined}>导出 {format.toUpperCase()}</a>)}</div></div><div className="mt-6 max-h-[55dvh] overflow-auto"><Markdown text={detail.content || "暂无正文"} /></div>
      {transcript && <section className="border-t border-line mt-7 pt-6"><h3 className="font-medium">修订发言人</h3><p className="muted text-sm mt-2">{IS_DEMO ? "可修改示例发言人和段落归属。更新后仅重排示例发言记录，纪要要点不会由 AI 重写。" : <>修改名字或某一段的归属后，可重新生成纪要。高级拆分与合并可在<a href="/legacy" className="text-accent">兼容界面</a>操作。</>}</p>
        {transcript.verification?.status === "stale" && <p className="notice mt-4">说话人信息已变更，旧纪要需要重新生成。</p>}
        <div className="grid gap-3 mt-5 md:grid-cols-2">{transcript.speakers.map(speaker => <form key={`${speaker.id}-${speaker.label}`} className="flex items-end gap-2" onSubmit={e => { e.preventDefault(); const form = new FormData(e.currentTarget); void edit("rename", { speaker: String(speaker.id), name: String(form.get("name")) }); }}><label className="field flex-1">{speaker.label}<input name="name" aria-label={`${speaker.label}的新名字`} defaultValue={speaker.label} required maxLength={80} /></label><button className="button" disabled={busy} type="submit">保存</button></form>)}</div>
        <details className="mt-5"><summary className="cursor-pointer text-sm text-accent">展开逐段转写并调整归属</summary><div className="max-h-80 overflow-auto mt-4 divide-y divide-line">{transcript.utterances.map((utterance, i) => <div key={i} className="py-3 grid gap-3 sm:grid-cols-[140px_1fr]"><label className="field"><span className="sr-only">第 {i + 1} 段发言人</span><select disabled={busy} value={utterance.speaker} onChange={e => void edit("reassign", { index: String(i), speaker: e.target.value })}>{transcript.speakers.map(speaker => <option key={speaker.id} value={speaker.id}>{speaker.label}</option>)}</select></label><p className="text-sm leading-7">{utterance.text}</p></div>)}</div></details>
        <button className="button primary mt-5" disabled={busy} onClick={() => void edit("resummarize", {})}>{busy ? "正在处理…" : IS_DEMO ? "更新示例发言记录" : "按修订结果重新生成纪要"}</button>
      </section>}
    </> : <><div className="flex items-center justify-between mb-5 gap-3"><div className="segmented"><button aria-pressed={tab === "tasks"} onClick={() => setTab("tasks")}>处理任务</button><button aria-pressed={tab === "resources"} onClick={() => setTab("resources")}>历史归档</button></div><button className="button small" disabled={loading} onClick={() => void refresh()}>刷新</button></div>
      {loading ? <div className="skeleton h-36" /> : tab === "tasks" ? tasks.length ? <ul className="divide-y divide-line">{tasks.map(task => <li key={task.id}><button className="history-row" onClick={() => { onTask(task); onClose(); }}><span><strong>{task.result?.scene === "meeting" || task.scene === "meeting" || task.session_id?.endsWith("-meeting") ? "会议纪要" : "题解与练习"}</strong><small>{task.id.slice(0, 12)} · {taskDate(task)}</small></span><span className={`status-tag ${task.status === "interrupted" || task.status === "error" ? "warning" : ""}`}>{statusNames[task.status]}</span></button></li>)}</ul> : <Empty text="还没有处理任务" /> : resources.length ? <ul className="divide-y divide-line">{resources.map(resource => <li key={resource.id}><button className="history-row" disabled={busy} onClick={() => void open(resource.id)}><span><strong>{resource.title}</strong><small>{resource.created}</small></span><span className="muted text-sm">查看</span></button></li>)}</ul> : <Empty text="还没有历史归档" />}
    </>}
  </Modal>;
}
function Empty({ text }: { text: string }) { return <div className="empty-state"><ClockCounterClockwiseIcon size={30} /><p>{text}</p><small>完成的题解、练习和会议会出现在这里。</small></div>; }
