"use client";
import { CameraIcon, ImageIcon, PaperPlaneTiltIcon, PaperclipIcon, SlidersHorizontalIcon, XIcon } from "@phosphor-icons/react";
import type { Agent, Artifact, PracticeAction, Scene } from "../lib/types";
import { IS_DEMO } from "../lib/mode";
import { isImage } from "../lib/media";
import { Recorder } from "./recorder";

export const agentNames: Record<Agent, string> = { auto: "自动识别", ability: "职业能力", essay: "策论", interview: "面试" };
export function Composer({ scene, agent, setAgent, text, setText, files, urls, onFiles, onRemove, busy, recording, onRecording, onCamera, onSubmit, onSettings, practice, draft, setDraft, answer, setAnswer, error, hasResult, onResult }: {
  scene: Scene; agent: Agent; setAgent: (agent: Agent) => void; text: string; setText: (text: string) => void;
  files: File[]; urls: string[]; onFiles: (files: File[], replace?: boolean) => void; onRemove: (index: number) => void;
  busy: boolean; recording: boolean; onRecording: (value: boolean) => void; onCamera: () => void;
  onSubmit: (action?: PracticeAction) => void; onSettings: () => void; practice?: Artifact;
  draft: string; setDraft: (value: string) => void; answer: string; setAnswer: (value: string) => void;
  error: string; hasResult: boolean; onResult: () => void;
}) {
  const effective = practice?.agent && practice.agent !== "unknown" && practice.agent !== "auto" ? practice.agent : agent;
  return <section className="composer">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="eyebrow">{scene === "exam" ? "看见问题，理清思路" : "专注交流，留下要点"}</p><h2 className="mt-2 text-2xl font-medium tracking-tight">{scene === "exam" ? "把题目放进视野" : "记录这一场会议"}</h2></div><button className="icon-button" data-ring-action onClick={onSettings} aria-label="参考资料与解题设置" disabled={busy || recording}><SlidersHorizontalIcon size={20} /></button></div>
    <p className="muted mt-3 text-sm leading-6">{scene === "exam" ? "拍摄、导入或粘贴完整题目，也可以直接输入文字。" : "录音停止后先试听，确认内容再发送生成纪要。"}</p>
    {IS_DEMO && <div className="notice mt-4"><p className="text-sm">可使用示例走完整个流程。输入内容和附件不会被识别，处理结果为预设展示。</p><button className="button small mt-3" disabled={busy || recording} onClick={() => setText(scene === "meeting" ? "小林：首页保留一个标语。小周：下周一完成拍照解题和会议记录演示。小林：我会在周五前整理反馈。" : agent === "essay" ? "如何提升社区公共服务质量？请给出分析思路与提纲。" : agent === "interview" ? "团队成员对任务优先级产生分歧，你会如何协调？" : "计算 (18 + 24) × 3；一件商品原价 200 元，打八折后多少钱？")}>{scene === "meeting" ? "填入示例会议" : "填入示例题目"}</button></div>}
    {scene === "exam" && <div className="mode-options mt-5" role="group" aria-label="学习模式">{(Object.keys(agentNames) as Agent[]).map(value => <button key={value} data-ring-action aria-pressed={agent === value} disabled={busy || recording} onClick={() => setAgent(value)}>{agentNames[value]}</button>)}</div>}
    <div className="mt-5 flex flex-wrap gap-2">
      {scene === "exam" && <button className="button" data-ring-action disabled={busy || recording} onClick={onCamera}><CameraIcon size={19} />拍照</button>}
      <label className={`button file-button ${busy || recording ? "disabled" : ""}`}><span className="flex items-center gap-2">{scene === "exam" ? <ImageIcon size={19} /> : <PaperclipIcon size={19} />}{scene === "exam" ? "相册 / 图片" : "上传音频"}</span><input data-ring-action aria-label={scene === "exam" ? "相册或图片上传" : "会议音频上传"} disabled={busy || recording} type="file" multiple={scene === "exam"} accept={scene === "exam" ? "image/*,.tif,.tiff" : ".wav,.mp3,.m4a,.aac,.flac,.ogg,.amr,.wma,.webm,audio/*"} onChange={e => { onFiles(Array.from(e.target.files || []), scene === "meeting"); e.target.value = ""; }} /></label>
      {scene === "meeting" && <Recorder disabled={busy} onRecording={onRecording} onFile={file => onFiles([file], true)} />}
    </div>
    {!!files.length && <ul className="attachments mt-4">{files.map((file, index) => <li key={`${file.name}-${file.lastModified}-${index}`}><div className="flex items-center gap-3">{isImage(file) && urls[index] && <img src={urls[index]} alt={`待提交图片 ${index + 1}`} />}<span className="min-w-0 flex-1"><span className="block truncate text-xs">{file.name}</span><small className="muted">{(file.size / 1024 / 1024).toFixed(1)} MB</small></span><button className="icon-button" disabled={busy || recording} onClick={() => onRemove(index)} aria-label={`移除 ${file.name}`}><XIcon size={16} /></button></div>{!isImage(file) && urls[index] && <audio className="mt-3 w-full" src={urls[index]} controls preload="metadata" aria-label="录音试听" />}</li>)}</ul>}
    <label className="field mt-5">{scene === "meeting" ? "会议文字或补充说明" : "题目 / 练习主题"}<textarea data-ring-action aria-label={scene === "meeting" ? "会议文字或补充说明" : "题目或练习主题"} rows={scene === "exam" ? 4 : 5} value={text} maxLength={4000} disabled={busy || recording} onChange={e => setText(e.target.value)} placeholder={scene === "meeting" ? "粘贴会议文字，或补充会议主题、人员信息…" : agent === "essay" ? "输入策论题目、材料和作答要求…" : agent === "interview" ? "输入面试题目或希望练习的主题…" : "输入文字题目，或为图片补充要求…"} /></label>
    {error && <p className="error-box mt-4" role="alert">{error}</p>}
    <div className="mt-5 flex flex-wrap items-center justify-between gap-3"><span className="muted text-xs">{recording ? "麦克风正在使用，请先停止录音" : files.some(file => !isImage(file)) ? "可先试听，发送后才会处理" : scene === "meeting" ? "可录音、选择音频或填写会议文字" : "支持 Ctrl / ⌘ + V 粘贴图片"}</span><button data-ring-action className="button primary" disabled={busy || recording} onClick={() => onSubmit()}><PaperPlaneTiltIcon size={18} />{busy ? "任务处理中…" : scene === "meeting" ? "发送并生成纪要" : "开始处理"}</button></div>
    {hasResult && <button className="button ghost small mt-3" data-ring-action onClick={onResult}>返回上次结果</button>}
    {scene === "exam" && practice && <section className="practice-followup mt-7 border-t border-line pt-5"><div className="flex items-center gap-2"><span className="status-dot" /><h3 className="text-sm font-medium">继续{effective === "essay" ? "策论练习" : effective === "interview" ? "面试对练" : "当前题目"}</h3></div>
      {effective === "essay" && <label className="field mt-4">我的草稿<textarea data-ring-action aria-label="我的草稿" rows={5} maxLength={40000} value={draft} disabled={busy || recording} onChange={e => setDraft(e.target.value)} placeholder="粘贴自己写的草稿后，再选择“批改草稿”。" /></label>}
      {effective === "interview" && <><label className="field mt-4">我的真实回答<textarea data-ring-action aria-label="我的真实回答" rows={4} maxLength={20000} value={answer} disabled={busy || recording} onChange={e => setAnswer(e.target.value)} placeholder="填写你对当前面试题的回答，或录制语音。" /></label><div className="mt-3"><Recorder disabled={busy} onRecording={onRecording} onFile={file => onFiles([file], true)} /></div></>}
      <div className="mt-4 flex flex-wrap gap-2">{practice.next_actions?.map(action => <button key={action.id} data-ring-action className="button small" disabled={busy || recording} onClick={() => onSubmit(action.id)}>{action.label}</button>)}<button data-ring-action className="button small" disabled={busy || recording || agent === "auto"} onClick={() => onSubmit("run")}>按所选模式继续题目</button></div>
    </section>}
  </section>;
}
