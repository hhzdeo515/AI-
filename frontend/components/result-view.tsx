"use client";
import { ArrowLeftIcon, ArrowRightIcon, DownloadSimpleIcon, PencilSimpleIcon } from "@phosphor-icons/react";
import { IS_DEMO } from "../lib/mode";
import { exportUrl } from "../lib/api";
import type { Progress, TaskRecord } from "../lib/types";
import { Markdown } from "./markdown";

const stepNames: Record<string, string> = { capture: "接收图片", recognize: "识别题面", solve: "处理与解答", verify: "核对结果" };
export function ResultView({ task, progress, error, uncertain, submitting, readOnly, index, setIndex, onRetry, onRecover, onCompose }: {
  task: TaskRecord | null; progress: Progress | null; error: string; uncertain: boolean; submitting: boolean; readOnly: boolean;
  index: number; setIndex: (value: number) => void; onRetry: () => void; onRecover: () => void; onCompose: () => void;
}) {
  const reply = task?.result;
  const questions = reply?.artifacts?.find(a => a.kind === "exam_batch")?.questions || [];
  const question = questions[Math.min(index, questions.length - 1)];
  const running = submitting || task?.status === "pending" || task?.status === "running";
  const failure = task?.status === "error" || reply?.status === "error" || reply?.status === "failed";
  const pendingResult = reply?.status === "pending";
  return <section className="result-panel">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4"><div><p className="eyebrow">镜片回显</p><h2 className="mt-1 text-lg font-medium">{running ? "正在处理" : task?.status === "interrupted" ? "任务已中断" : failure ? "处理未完成" : uncertain ? "正在确认提交状态" : "阅读结果"}</h2></div>
      <div className="flex gap-2">{reply?.archived_id && <a className="icon-button" href={exportUrl(reply.archived_id, "md")} download={IS_DEMO ? "ai-glasses-demo.md" : undefined} aria-label="下载 Markdown 结果"><DownloadSimpleIcon size={19} /></a>}<button className="button small" data-ring-action onClick={onCompose}><PencilSimpleIcon size={15} />{readOnly ? "开始新任务" : "继续输入"}</button></div>
    </header>
    {readOnly && <p className="notice mt-4">当前为历史任务，可查看或继续中断的处理。旧任务不代表当前练习进度；新的作答请从“新任务”开始。</p>}{error && <div className="error-box mt-4" role="alert"><p>{error}</p><button className="button small mt-3" onClick={onRecover}>重新查询原任务</button></div>}
    {uncertain && <p className="notice mt-4">提交回执尚未确认，正在查找同一请求。不会自动再次上传。</p>}
    {running && <div className="py-7" role="status"><div className="flex items-center gap-3 text-accent"><span className="processing-dot" />{IS_DEMO ? "正在演示处理流程…" : submitting ? "正在上传并建立任务…" : task?.status === "pending" ? "任务已保存，等待处理…" : "服务正在处理，请稍候…"}</div>
      {!!progress?.steps?.length && <ol className="mt-6 grid gap-3">{progress.steps.map(step => <li key={step.id} className={`progress-step ${step.state}`}><span />{stepNames[step.id] || step.id}<small>{step.state === "done" ? "完成" : step.state === "active" ? "进行中" : step.state === "error" ? "异常" : "等待"}</small></li>)}</ol>}
      {progress?.batch && progress.batch.total > 0 && <p className="mt-4 text-sm muted">已处理 {progress.batch.done} / {progress.batch.total} 道题</p>}
      <div className="skeleton mt-7 h-3 w-4/5" /><div className="skeleton mt-3 h-3 w-3/5" />
      <p className="muted mt-6 text-xs leading-6">{IS_DEMO ? "正在播放示例进度。刷新后可继续查看，不会调用后台服务。" : "任务已交给后台。刷新页面后会继续显示同一个任务。"}</p></div>}
    {task?.status === "interrupted" && <div className="notice my-5"><p>{task.error || "服务重启或外部处理尚未结束，任务需要你确认后继续。"}</p><button className="button primary mt-4" disabled={submitting} onClick={onRetry}>确认继续此任务</button></div>}
    {failure && <div className="error-box my-5" role="alert"><p>{task?.error || reply?.error || reply?.text || "处理失败，请保留素材并重试。"}</p>{task?.status === "error" && <button className="button mt-4" disabled={submitting} onClick={onRetry}>重试此任务</button>}</div>}
    {pendingResult && <p className="notice my-4">外部处理仍未完成。请查看任务状态，不要把当前内容视为已完成结果。</p>}
    {reply?.note && <p className="notice my-4">{reply.note}</p>}
    {!!reply?.rejected_files?.length && <p className="error-box my-4">未接收的附件：{reply.rejected_files.join("、")}</p>}
    {questions.length > 0 && <nav className="question-nav" aria-label="逐题结果"><button className="icon-button" disabled={index <= 0} onClick={() => setIndex(index - 1)} aria-label="上一题"><ArrowLeftIcon size={17} /></button><label className="sr-only" htmlFor="question-select">选择题目</label><select id="question-select" value={Math.min(index, questions.length - 1)} onChange={e => setIndex(Number(e.target.value))}>{questions.map((q, i) => <option key={q.id} value={i}>{q.label || `第 ${i + 1} 题`}{q.status !== "answered" ? " · 待核对" : ""}</option>)}</select><button className="icon-button" disabled={index >= questions.length - 1} onClick={() => setIndex(index + 1)} aria-label="下一题"><ArrowRightIcon size={17} /></button><span className="muted text-xs">{index + 1} / {questions.length}</span></nav>}
    {reply && <div key={`${task?.id}-${index}`} className="result-body" data-result-body tabIndex={0} aria-label={IS_DEMO ? "示例结果，可滚动阅读" : "AI 结果，可滚动阅读"}>
      {question ? <><p className="text-sm muted mb-4">{question.preview}</p>{question.status === "answered" ? <><p className="answer-line">答案：{question.answer}</p><Markdown text={question.explanation || "暂无详细解析"} /></> : <div className="notice"><strong>{question.status === "needs_photo" ? "需要补拍" : "暂未确定"}</strong><p className="mt-2">{question.needed || "请核对完整题面后重试。"}</p></div>}</> : <Markdown text={reply.text || "服务没有返回正文。请检查任务状态或打开历史记录。"} />}
    </div>}
    {!task && !submitting && !uncertain && <div className="py-10 muted"><p>结果将显示在这里。</p><button className="button mt-5" onClick={onCompose}>返回输入</button></div>}
  </section>;
}
