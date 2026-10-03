"use client";
import { useCallback, useEffect, useState } from "react";
import { BookOpenIcon, FileArrowUpIcon, TrashIcon } from "@phosphor-icons/react";
import { api, errorText } from "../lib/api";
import type { KnowledgeDocument } from "../lib/types";
import { Modal } from "./modal";
import { Markdown } from "./markdown";

export function Library({ selected, onSelect, onClose }: { selected: string[]; onSelect: (ids: string[]) => void; onClose: () => void }) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<KnowledgeDocument | null>(null);
  const [deleting, setDeleting] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try { const response = await api<{ documents: KnowledgeDocument[] }>("/api/exam/knowledge?owner=local"); setDocuments(response.documents); }
    catch (e) { setError(errorText(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); data.set("owner", "local");
    if (file && file.size > 2 * 1024 * 1024) { setError("资料文件最多 2 MB，请拆分后导入。"); return; }
    if (file) data.set("file", file); else data.delete("file");
    setBusy(true); setError(""); setNotice("");
    try { const doc = await api<KnowledgeDocument>("/api/exam/knowledge", { method: "POST", body: data }); await refresh(); onSelect([...new Set([...selected, doc.id])].slice(0, 20)); form.reset(); setFile(null); setNotice("资料已导入，并选为本轮参考资料。"); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true); setError("");
    try { await api(`/api/exam/knowledge/${encodeURIComponent(id)}?owner=local`, { method: "DELETE" }); onSelect(selected.filter(value => value !== id)); if (detail?.id === id) setDetail(null); setDeleting(""); await refresh(); setNotice("资料已删除。"); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function open(id: string) {
    setError("");
    try { setDetail(await api(`/api/exam/knowledge/${encodeURIComponent(id)}?owner=local`)); } catch (e) { setError(errorText(e)); }
  }
  return <Modal title="参考资料库" onClose={onClose} wide>
    <p className="muted text-sm leading-6">选中的资料用于本轮题目核对。选择资料后，将依据资料与原图处理，并关闭联网检索。最多选择 20 份。</p>
    {error && <p className="error-box mt-4" role="alert">{error}</p>}{notice && <p className="notice mt-4" role="status">{notice}</p>}
    <div className="mt-7 grid gap-8 md:grid-cols-[1.1fr_1fr]">
      <section><div className="flex justify-between items-center mb-4"><h3 className="font-medium">我的资料 <span className="muted text-xs">已选 {selected.length} 份</span></h3><button className="button small" onClick={() => void refresh()} disabled={loading}>刷新</button></div>
        {loading ? <div className="skeleton h-24" /> : !documents.length ? <div className="empty-state"><BookOpenIcon size={30} /><p>还没有参考资料</p><small>导入制度、课程材料或答题依据。</small></div> : <ul className="divide-y divide-line">{documents.map(doc => <li key={doc.id} className="py-4"><div className="flex items-start gap-3"><input type="checkbox" aria-label={`选择 ${doc.title}`} checked={selected.includes(doc.id)} disabled={busy || !selected.includes(doc.id) && selected.length >= 20} onChange={e => onSelect(e.target.checked ? [...selected, doc.id] : selected.filter(id => id !== doc.id))} /><button className="flex-1 text-left" onClick={() => void open(doc.id)}><span className="text-sm">{doc.title}</span><span className="block muted text-xs mt-1">{doc.version || "未标注版本"}</span></button><button className="icon-button" aria-label={`删除 ${doc.title}`} disabled={busy} onClick={() => setDeleting(doc.id)}><TrashIcon size={17} /></button></div>{deleting === doc.id && <div className="notice mt-3 text-sm">确认永久删除这份资料？<div className="mt-3 flex gap-2"><button className="button small" disabled={busy} onClick={() => void remove(doc.id)}>确认删除</button><button className="button small" onClick={() => setDeleting("")}>保留</button></div></div>}</li>)}</ul>}
      </section>
      <form onSubmit={add} className="grid content-start gap-4"><h3 className="font-medium">导入新资料</h3><label className="field">资料标题<input name="title" required maxLength={200} placeholder="例如：课程重点或单位制度" /></label><label className="field">版本 / 年份<input name="version" maxLength={80} placeholder="选填" /></label><label className="field">资料正文<textarea name="content" rows={5} required={!file} placeholder="粘贴原文，或选择下面的文件" /></label><label className="file-button button"><FileArrowUpIcon size={18} />{file?.name || "选择 TXT、Markdown 或 Word"}<input type="file" name="file" accept=".txt,.md,.docx" onChange={e => setFile(e.target.files?.[0] || null)} /></label><p className="muted text-xs">文件最多 2 MB；文本需使用 UTF-8 编码。</p><button className="button primary justify-center" disabled={busy} type="submit">{busy ? "正在保存…" : "导入并选用"}</button></form>
    </div>
    {detail && <section className="mt-8 border-t border-line pt-6"><h3 className="text-lg mb-4">{detail.title}</h3><div className="max-h-80 overflow-auto"><Markdown text={detail.content || "暂无正文"} /></div></section>}
  </Modal>;
}
