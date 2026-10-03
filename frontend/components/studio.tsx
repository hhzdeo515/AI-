"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpenIcon, CameraIcon, ClockCounterClockwiseIcon, EyeglassesIcon, MicrophoneIcon, PlusIcon, SignOutIcon } from "@phosphor-icons/react";
import { IS_DEMO } from "../lib/mode";
import { errorText, mediaUrl } from "../lib/api";
import { isImage, validateAttachments } from "../lib/media";
import { taskScene } from "../lib/tasks";
import type { Agent, Backend, PracticeAction, Scene, TaskRecord } from "../lib/types";
import { useObjectUrls } from "../hooks/use-object-urls";
import { useTask } from "../hooks/use-task";
import { Camera } from "./camera";
import { Composer } from "./composer";
import { History } from "./history";
import { Library } from "./library";
import { ResultView } from "./result-view";
import { Ring } from "./ring";
import { defaultSettings, Settings } from "./settings";

export function Studio({ authEnabled, onLogout }: { authEnabled: boolean; onLogout: () => Promise<void> }) {
  const [scene, setScene] = useState<Scene>("exam");
  const [agent, setAgent] = useState<Agent>("auto");
  const [backend, setBackend] = useState<Backend>("original");
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [draft, setDraft] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [recording, setRecording] = useState(false);
  const [view, setView] = useState<"compose" | "result">("compose");
  const [modal, setModal] = useState<"camera" | "library" | "history" | "settings" | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [settings, setSettings] = useState(defaultSettings);
  const [questionIndex, setQuestionIndex] = useState(0);
  const lens = useRef<HTMLElement>(null);
  const task = useTask();
  const urls = useObjectUrls(files);
  const practice = task.task?.result?.artifacts?.find(a => a.kind === "practice");
  const route = task.task?.result?.artifacts?.find(a => a.kind === "practice_route");
  const currentTaskId = task.task?.id;
  const [seenTaskId, setSeenTaskId] = useState("");
  useEffect(() => {
    if (currentTaskId && currentTaskId !== seenTaskId) {
      setSeenTaskId(currentTaskId); setView("result"); setQuestionIndex(0);
    }
  }, [currentTaskId, seenTaskId, task.task]);
  useEffect(() => {
    const current = task.task;
    if (!current) return;
    const restoredScene = taskScene(current);
    if (restoredScene) setScene(restoredScene);
  }, [currentTaskId, task.task?.scene, task.task?.result?.scene, task.task?.session_id]);
  useEffect(() => {
    const hidden = () => { if (document.hidden) document.querySelectorAll("audio").forEach(audio => audio.pause()); };
    document.addEventListener("visibilitychange", hidden);
    return () => document.removeEventListener("visibilitychange", hidden);
  }, []);
  const addFiles = useCallback((incoming: File[], replace = false) => {
    // Recorder emits its file in the same event as onRecording(false).
    // Do not read the previous render's recording flag here.
    if (!incoming.length || task.busy) return;
    const next = replace ? incoming : [...files, ...incoming];
    const failure = validateAttachments(next); if (failure) { setError(failure); return; }
    setFiles(next); setError(""); setView("compose");
  }, [files, recording, task.busy]);
  useEffect(() => {
    const paste = (event: ClipboardEvent) => {
      if (scene !== "exam" || modal || task.busy || recording) return;
      const incoming = Array.from(event.clipboardData?.files || []).filter(isImage);
      if (incoming.length) { event.preventDefault(); addFiles(incoming); }
    };
    window.addEventListener("paste", paste); return () => window.removeEventListener("paste", paste);
  }, [addFiles, modal, recording, scene, task.busy]);
  function changeScene(next: Scene) { if (task.busy || recording || task.readOnly) return; setScene(next); setView("compose"); setFiles([]); setText(""); setError(""); }
  async function submit(action?: PracticeAction) {
    if (task.busy || recording || task.readOnly) return;
    setError("");
    let payload = text.trim();
    let attached = files;
    const event: Record<string, unknown> = {};
    if (scene === "exam") {
      if (!action && attached.some(file => !isImage(file))) {
        if (practice?.agent === "interview") action = "answer";
        else { setError("回答音频仅用于面试对练，请先开始面试题目。"); return; }
      }
      event.exam = { ...settings, document_ids: selected, require_knowledge: settings.require_knowledge || selected.length > 0 || settings.profile === "internal", allow_web: settings.allow_web && !selected.length && settings.profile !== "internal", use_public_knowledge: settings.use_public_knowledge && !selected.length && !settings.require_knowledge && settings.profile !== "internal" };
      if (action || files.some(isImage) || ["essay", "interview"].includes(agent)) {
        const effective = action && action !== "run" && practice?.agent && practice.agent !== "unknown" ? practice.agent : agent;
        const practiceInput: Record<string, string> = { agent: effective, action: action || (agent === "interview" ? "start" : "run") };
        if (!action && !files.length && payload && ["essay", "interview"].includes(agent)) practiceInput.topic = payload;
        if (action) {
          attached = []; payload = "请按当前学习模式继续处理。";
          if (action === "critique") { if (!draft.trim()) { setError("请先填写你自己的草稿，再提交批改。"); return; } practiceInput.draft = draft.trim(); }
          if (action === "answer") {
            if (answer.trim()) practiceInput.answer = answer.trim();
            else { attached = files.filter(file => !isImage(file)); if (!attached.length) { setError("请先填写或录制你自己的回答，再提交点评。"); return; } }
          }
        }
        event.practice = practiceInput;
      }
      if (!payload && !attached.length && !action) { setError("请拍照、选择图片或输入题目后再发送。"); return; }
      if (!payload) payload = "请识别完整题面并按学习模式处理，保留全部题目，缺失内容请明确提示。";
    } else {
      if (!payload && !attached.length) { setError("请先录制、上传音频，或填写会议文字。"); return; }
      payload = attached.length ? `请转写会议音频并生成纪要。${payload ? `补充说明：${payload}` : ""}` : `请根据以下会议文字生成纪要。\n${payload}`;
    }
    setQuestionIndex(0); setView("result");
    const accepted = await task.submit({ scene, backend, text: payload, files: attached, event });
    if (!accepted && !task.uncertain) setView("result");
  }
  function selectTask(record: TaskRecord) { setFiles([]); setText(""); setDraft(""); setAnswer(""); setScene(taskScene(record) || "exam"); task.select(record); setView("result"); }
  function newTask() { if (task.busy && !task.uncertain || recording) return; task.reset(); setFiles([]); setText(""); setDraft(""); setAnswer(""); setError(""); setView("compose"); setSeenTaskId(""); }
  const questions = task.task?.result?.artifacts?.find(a => a.kind === "exam_batch")?.questions;
  const sourcePage = Math.max(0, (questions?.[questionIndex]?.page || 1) - 1);
  const serverImages = task.task?.media?.filter(media => media.type === "image").map(media => mediaUrl(media.url)).filter(Boolean) || [];
  const localImages = files.map((file, i) => isImage(file) ? urls[i] : "").filter(Boolean);
  const sourceImages = view === "result" && serverImages.length ? serverImages : localImages;
  const source = sourceImages[Math.min(sourcePage, sourceImages.length - 1)];
  const visiblePractice = scene === "exam" && (practice || route && { ...route, kind: "practice", next_actions: [] });
  return <div className="studio-shell min-h-[100dvh]">
    <header className="topbar mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-4 px-5 py-5 md:px-8"><button className="brand" onClick={() => setView("compose")} aria-label="返回眼镜视野"><EyeglassesIcon size={28} /><span>AI 眼镜<span className="brand-sub">智能助手</span></span></button><nav className="flex flex-wrap items-center gap-2" aria-label="全局操作"><div className="segmented mr-1" aria-label="解题模型">{(["original", "jev"] as Backend[]).map(value => <button key={value} aria-pressed={backend === value} disabled={task.busy || recording} onClick={() => setBackend(value)}>{value === "original" ? "原版" : "JEV"}</button>)}</div><button className="button ghost" disabled={recording} onClick={() => setModal("library")}><BookOpenIcon size={19} /><span>资料库</span>{selected.length > 0 && <span className="count">{selected.length}</span>}</button><button className="button ghost" disabled={recording || task.submitting} onClick={() => setModal("history")}><ClockCounterClockwiseIcon size={19} /><span>历史</span></button>{authEnabled && <button className="icon-button" disabled={recording} aria-label={IS_DEMO ? "退出工作空间" : "退出登录"} onClick={() => void onLogout().catch(e => setError(errorText(e)))}><SignOutIcon size={20} /></button>}</nav></header>
    <main className="mx-auto max-w-[1400px] px-5 pb-8 md:px-8"><section className="stage-heading flex flex-wrap items-end justify-between gap-4"><div><h1 className="mt-2 text-[clamp(25px,2.7vw,38px)] font-medium tracking-tight">眼前所见，即刻展开。</h1></div><div className="flex items-center gap-3"><span className="status-tag"><span className="status-dot" />交互控制</span><button className="button small" disabled={task.busy && !task.uncertain || recording} onClick={newTask}><PlusIcon size={16} />新任务</button></div></section>
      <div className="workspace-grid"><section className="optical-assembly" aria-label="眼镜镜片"><div className="lens-hinge" aria-hidden="true" /><div className="lens-bridge" aria-hidden="true" /><div className="lens-rim"><section ref={lens} className={`lens ${source && view === "result" ? "has-source" : ""}`}>
        <header className="lens-header"><nav className="scene-nav" aria-label="场景选择"><button data-ring-action aria-pressed={scene === "exam"} disabled={task.busy || recording} onClick={() => changeScene("exam")}><CameraIcon size={18} />拍照解题</button><button data-ring-action aria-pressed={scene === "meeting"} disabled={task.busy || recording} onClick={() => changeScene("meeting")}><MicrophoneIcon size={18} />会议纪要</button></nav><span className="lens-status">{recording ? "正在录音" : task.busy ? "处理中" : "视野就绪"}</span></header>
        <div className={`lens-content ${view === "result" && source ? "with-source" : ""}`}>
          {view === "result" && source && <figure className="source-view"><a href={source} target="_blank" rel="noopener noreferrer" aria-label="查看完整原图"><img src={source} alt={`题目原图，第 ${sourcePage + 1} 页`} /></a><figcaption>原始题面 <span>点击查看完整图片</span></figcaption></figure>}
          {view === "compose" ? <Composer scene={scene} agent={agent} setAgent={setAgent} text={text} setText={setText} files={files} urls={urls} onFiles={addFiles} onRemove={i => setFiles(files.filter((_, index) => index !== i))} busy={task.busy || task.readOnly || !task.ready} recording={recording} onRecording={setRecording} onCamera={() => setModal("camera")} onSubmit={action => void submit(action)} onSettings={() => setModal("settings")} practice={visiblePractice || undefined} draft={draft} setDraft={setDraft} answer={answer} setAnswer={setAnswer} error={task.readOnly ? "历史任务只读，请点击页面上方“新任务”开始新的工作。" : error || task.error} hasResult={!!task.task} onResult={() => setView("result")} /> : <ResultView task={task.task} progress={task.progress} error={task.error || error} uncertain={task.uncertain} submitting={task.submitting} readOnly={task.readOnly} index={questionIndex} setIndex={setQuestionIndex} onRetry={() => void task.retry()} onRecover={() => void task.recover()} onCompose={() => task.readOnly ? newTask() : setView("compose")} />}
        </div><footer className="lens-footer"><span>镜片视野 · {scene === "exam" ? "题解与练习" : "会议纪要"}</span><span>{backend === "original" ? "原版" : "JEV"} · {IS_DEMO ? "结果回显" : "AI 实际处理"}</span></footer>
      </section></div></section><Ring lens={lens} onBack={() => { setModal(null); setView("compose"); }} /></div>
      <footer className="studio-footer"><p>摄像头和麦克风使用当前设备。</p>{!IS_DEMO && <a href="/legacy">打开兼容界面</a>}</footer>
    </main>
    {modal === "camera" && <Camera onCapture={file => addFiles([file])} onClose={() => setModal(null)} />}
    {modal === "library" && <Library selected={selected} onSelect={setSelected} onClose={() => setModal(null)} />}
    {modal === "history" && <History onTask={selectTask} onClose={() => setModal(null)} />}
    {modal === "settings" && <Settings value={settings} onChange={setSettings} count={selected.length} onLibrary={() => setModal("library")} onClose={() => setModal(null)} />}
  </div>;
}
