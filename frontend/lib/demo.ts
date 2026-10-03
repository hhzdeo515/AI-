import { ApiError } from "./api-error";
import { DEMO_STORE_KEY } from "./mode";
import type { Artifact, KnowledgeDocument, Reply, Resource, TaskRecord, Transcript } from "./types";

const legacyNote = "演示模式：以下为预设示例，不是对输入内容、图片或录音的真实 AI 分析。";
interface DemoTask extends TaskRecord { created: number; result: Reply; transcript?: Transcript }
interface DemoStore { version: 1; tasks: DemoTask[]; documents: KnowledgeDocument[] }

function read(): DemoStore {
  try {
    const raw = localStorage.getItem(DEMO_STORE_KEY);
    if (!raw) return { version: 1, tasks: [], documents: [] };
    const data: DemoStore = JSON.parse(raw);
    if (data.version !== 1 || !Array.isArray(data.tasks) || !Array.isArray(data.documents)) throw new Error();
    // Remove only the retired generated notice; preserve existing records and user documents.
    for (const task of data.tasks) {
      if (task.result?.note !== legacyNote) continue;
      delete task.result.note;
      task.result.text = task.result.text.replace(`> ${legacyNote}\n\n`, "");
      if (task.transcript?.content) task.transcript.content = task.transcript.content.replace(`> ${legacyNote}\n\n`, "");
    }
    return data;
  } catch { throw new ApiError("无法读取本机记录。请允许浏览器存储，或在浏览器设置中清除此网站的数据后重试。", 400); }
}
function write(data: DemoStore) {
  try { localStorage.setItem(DEMO_STORE_KEY, JSON.stringify(data)); }
  catch { throw new ApiError("浏览器存储不可用或已满。请检查浏览器存储权限，或在浏览器设置中清除此网站的数据后重试。", 507); }
}
function required<T>(value: T | undefined): T {
  if (!value) throw new ApiError("本机没有这条记录，请开始新任务。", 404);
  return value;
}
function snapshot(task: DemoTask): TaskRecord {
  const elapsed = Date.now() - task.created * 1000;
  const status = elapsed < 600 ? "pending" : elapsed < 2600 ? "running" : "done";
  const { transcript: _transcript, ...record } = task;
  return { ...record, status, result: status === "done" ? task.result : null };
}
function meetingContent(transcript: Transcript): string {
  const turns = transcript.utterances.map(turn => `- **${transcript.speakers.find(s => s.id === turn.speaker)?.label || "发言人"}**：${turn.text}`).join("\n");
  return `# 产品讨论会 · 示例纪要\n\n## 讨论要点\n- 首页突出一个核心标语，降低信息负担。\n- 优先完善拍照解题和会议记录两条流程。\n\n## 决定与待办\n- 小林：周五前整理体验反馈。\n- 小周：下周一完成交互流程。\n- 下次会议检查任务入口是否清晰。\n\n## 发言记录（可修订的示例）\n${turns}`;
}
function sample(id: string, scene: string, agent: string, action: string): { reply: Reply; transcript?: Transcript } {
  if (scene === "meeting") {
    const transcript: Transcript = { id, text: "示例会议转写", speakers: [{ id: 0, label: "小林" }, { id: 1, label: "小周" }], utterances: [
      { speaker: 0, text: "首页保留一个标语，让思路更加清晰。", start: 0, end: 5 },
      { speaker: 1, text: "先完善拍照解题和会议记录，下周一完成交互。", start: 6, end: 12 },
      { speaker: 0, text: "我会在周五前整理体验反馈。", start: 13, end: 18 },
    ], verification: { status: "verified" } };
    transcript.content = meetingContent(transcript);
    return { reply: { text: transcript.content, scene, status: "ok", archived_id: id }, transcript };
  }
  if (agent === "essay" || agent === "interview") {
    const essay: Record<string, string> = {
      run: "## 策论审题示例\n主题：如何提升社区公共服务质量？\n\n从居民需求、服务可达性和反馈机制三个角度展开。先界定问题，再提出可以执行的措施。",
      analyze: "## 材料分析示例\n1. 需求分散：先做分类走访。\n2. 信息不对称：统一服务入口。\n3. 缺少反馈：建立回访机制。",
      outline: "## 写作提纲示例\n- 开篇：以居民需求为出发点。\n- 分论点一：优化服务供给。\n- 分论点二：改善服务流程。\n- 分论点三：用反馈持续改进。\n- 结尾：形成可持续的服务机制。",
      draft: "## 参考段落示例\n公共服务的质量，体现在居民日常办事的细节中。社区应从高频需求入手，提供清晰的办事指引，明确责任人与办理时限，并通过回访检查实际效果。",
      critique: "## 草稿点评示例\n- 结构：可采用“问题—原因—措施”的顺序。\n- 论据：为每条措施补充具体场景。\n- 表达：减少空泛表述，写清由谁完成、何时完成。",
    };
    const interview: Record<string, string> = {
      run: "## 面试题示例\n团队成员对任务优先级产生分歧，你会如何协调？\n\n先明确共同目标，再说明你会如何收集信息、组织讨论和确认分工。点击“继续输入”可提交自己的回答，体验点评流程。",
      start: "## 面试题示例\n团队成员对任务优先级产生分歧，你会如何协调？\n\n可以从倾听意见、核对事实、明确优先级三个方面回答。",
      answer: "## 回答点评示例\n- 先说明共同目标，避免只描述沟通形式。\n- 加入一个具体行动及其预期结果。\n- 结尾交代分工与复盘方式。",
      follow_up: "## 追问示例\n如果讨论之后仍然无法达成一致，你会怎样推动决策？\n\n可以继续填写回答，体验下一轮反馈。",
    };
    const actions: Artifact["next_actions"] = agent === "essay" ? [{ id: "analyze", label: "分析材料" }, { id: "outline", label: "生成提纲" }, { id: "draft", label: "参考段落" }, { id: "critique", label: "批改草稿" }] : [{ id: "answer", label: "点评回答" }, { id: "follow_up", label: "继续追问" }];
    const texts = agent === "essay" ? essay : interview;
    const text = `${texts[action] || texts.run}`;
    return { reply: { text, scene, status: "ok", archived_id: id, artifacts: [{ kind: "practice", agent, next_actions: actions }] } };
  }
  const questions = [
    { id: "sample-1", label: "示例第 1 题", page: 1, status: "answered", preview: "计算 (18 + 24) × 3。", answer: "126", explanation: "先算括号：18 + 24 = 42。\n\n再做乘法：42 × 3 = **126**。\n\n注意先括号、后乘除、再加减的运算顺序。" },
    { id: "sample-2", label: "示例第 2 题", page: 1, status: "answered", preview: "一件商品原价 200 元，打八折后多少钱？", answer: "160 元", explanation: "八折表示按原价的 80% 付款。\n\n200 × 0.8 = **160 元**。\n\n可用上方箭头切换题目，用戒指上下滑动阅读。" },
  ];
  const text = `# 题解示例\n\n${questions.map(q => `## ${q.label}\n${q.preview}\n\n答案：${q.answer}\n\n${q.explanation}`).join("\n\n")}`;
  return { reply: { text, scene, status: "ok", archived_id: id, artifacts: [{ kind: "exam_batch", questions }] } };
}
function resource(task: DemoTask): Resource {
  return { id: task.id, title: task.scene === "meeting" ? "产品讨论会 · 示例纪要" : "题解与练习 · 示例结果", scene: task.scene || "exam", created: new Date(task.created * 1000).toLocaleString("zh-CN"), content: task.result.text, has_transcript: !!task.transcript };
}

/** Browser-only transport. Unknown routes fail closed; never fall back to a server. */
export async function demoApi<T>(path: string, init: RequestInit): Promise<T> {
  if (init.signal?.aborted) throw new DOMException("已取消", "AbortError");
  const url = new URL(path, "https://demo.invalid");
  const route = url.pathname, method = init.method || "GET";
  const store = read();
  const form = init.body instanceof FormData ? init.body : new FormData();
  const value = (key: string) => String(form.get(key) || "");
  let result: unknown;
  if (route === "/api/chat/async" && method === "POST") {
    const existing = store.tasks.find(t => t.request_id === value("request_id"));
    if (existing) return { task_id: existing.id, request_id: existing.request_id } as T;
    let event: { practice?: { agent?: string; action?: string } };
    try { event = JSON.parse(value("event") || "{}"); } catch { throw new ApiError("请求格式有误。", 400); }
    const id = `demo-${crypto.randomUUID()}`;
    const scene = value("scene") === "meeting" ? "meeting" : "exam";
    const generated = sample(id, scene, event.practice?.agent || "auto", event.practice?.action || "run");
    const task: DemoTask = { id, request_id: value("request_id"), session_id: value("session_id"), scene, created: Date.now() / 1000, status: "pending", result: { ...generated.reply, exam_backend: value("exam_backend") === "jev" ? "jev" : "original" }, transcript: generated.transcript };
    // Keep a bounded local history; source files and user prompts are not persisted.
    store.tasks = [task, ...store.tasks].slice(0, 50); write(store);
    result = { task_id: id, request_id: task.request_id };
  } else if (route === "/api/tasks" && method === "GET") result = { tasks: store.tasks.map(snapshot) };
  else if (route === "/api/task" && method === "GET") result = snapshot(required(store.tasks.find(t => t.id === url.searchParams.get("task_id"))));
  else if (route === "/api/progress" && method === "GET") {
    const task = required(store.tasks.find(t => t.request_id === url.searchParams.get("request_id")));
    const step = Math.min(4, Math.floor((Date.now() - task.created * 1000) / 650));
    result = { finished: step === 4, steps: ["准备素材", "整理内容", "呈现结果", "保存本机记录"].map((id, i) => ({ id, state: i < step ? "done" : i === step ? "active" : "pending" })) };
  } else if (route === "/api/resources" && method === "GET") result = { resources: store.tasks.filter(t => snapshot(t).status === "done").map(resource) };
  else if (route === "/api/resource" && method === "GET") result = resource(required(store.tasks.find(t => t.id === url.searchParams.get("id") && snapshot(t).status === "done")));
  else if (route === "/api/transcript" && ["GET", "POST"].includes(method)) {
    const task = required(store.tasks.find(t => t.id === (value("id") || url.searchParams.get("id")) && snapshot(t).status === "done"));
    const transcript = required(task.transcript);
    if (method === "POST") {
      if (value("action") === "rename") {
        const name = value("name").trim().slice(0, 80);
        if (!name) throw new ApiError("请填写发言人名字。", 400);
        required(transcript.speakers.find(s => s.id === Number(value("speaker")))).label = name;
        transcript.verification = { status: "stale" };
      } else if (value("action") === "reassign") {
        const speaker = required(transcript.speakers.find(s => s.id === Number(value("speaker"))));
        required(transcript.utterances[Number(value("index"))]).speaker = speaker.id;
        transcript.verification = { status: "stale" };
      } else if (value("action") === "resummarize") {
        transcript.content = meetingContent(transcript); task.result.text = transcript.content;
        transcript.verification = { status: "verified" };
      } else throw new ApiError("暂不支持此修订操作。", 400);
      write(store);
    }
    result = transcript;
  } else if (route === "/api/exam/knowledge" && method === "GET") result = { documents: store.documents };
  else if (route === "/api/exam/knowledge" && method === "POST") {
    let content = value("content"); const file = form.get("file");
    if (file instanceof File && file.size) {
      if (!/\.(txt|md)$/i.test(file.name) || file.size > 100000) throw new ApiError("请选择 100 KB 以内的 TXT 或 Markdown 文件。", 400);
      content = await file.text();
    }
    if (!value("title").trim() || !content.trim()) throw new ApiError("请填写资料标题与正文。", 400);
    if (content.length > 50000 || store.documents.length >= 20) throw new ApiError("最多保存 20 份资料，每份不超过 5 万字。", 400);
    const doc = { id: `demo-doc-${crypto.randomUUID()}`, title: value("title").slice(0, 200), version: value("version").slice(0, 80), content, created: new Date().toISOString() };
    store.documents.unshift(doc); write(store); result = doc;
  } else if (route.startsWith("/api/exam/knowledge/") && ["GET", "DELETE"].includes(method)) {
    const doc = required(store.documents.find(d => d.id === decodeURIComponent(route.slice("/api/exam/knowledge/".length))));
    if (method === "DELETE") { store.documents = store.documents.filter(d => d.id !== doc.id); write(store); result = { ok: true }; }
    else result = doc;
  } else throw new ApiError("当前工作空间暂不支持此功能。", 404);
  return result as T;
}

export function demoExportUrl(id: string, format: string): string {
  if (typeof window === "undefined" || !["md", "txt"].includes(format)) return "";
  try {
    const task = read().tasks.find(t => t.id === id && snapshot(t).status === "done");
    return task ? `data:text/plain;charset=utf-8,${encodeURIComponent(task.result.text)}` : "";
  } catch { return ""; }
}
