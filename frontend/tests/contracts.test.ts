import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, chatForm } from "../lib/api";
import { findRecoveryTask, pollTask, readSavedSession, taskScene } from "../lib/tasks";
import { MediaLease } from "../lib/media";

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
describe("同源 API 与表单", () => {
  it("保留真实请求身份、学习选项和附件", () => {
    const file = new File(["photo"], "question.jpg", { type: "image/jpeg" });
    const form = chatForm({ sessionId: "session-a", requestId: "request-a", text: "题目", scene: "exam", backend: "jev", files: [file], event: { practice: { agent: "essay", action: "critique", draft: "我的草稿" } } });
    expect(form.get("request_id")).toBe("request-a");
    expect(form.get("session_id")).toBe("session-a");
    expect(form.get("exam_backend")).toBe("jev");
    expect(JSON.parse(String(form.get("event"))).practice.draft).toBe("我的草稿");
    expect((form.get("files") as File).name).toBe("question.jpg");
  });
  it("把未授权响应交给登录流程并禁止跨源请求", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "请登录" }), { status: 401 })));
    await expect(api("/api/task")).rejects.toMatchObject({ status: 401, message: "请登录" });
    await expect(api("https://example.com/api/task")).rejects.toThrow("同源");
  });
  it("上传超限与非 JSON 响应展示可理解的错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>large</html>", { status: 413 })));
    await expect(api("/api/chat/async")).rejects.toThrow("32 MB");
  });
});
describe("只恢复已有任务，不重新提交", () => {
  it("运行中的会议可从会话标识恢复场景，延迟结果也可补全", () => {
    expect(taskScene({ id: "meeting", session_id: "web-a-meeting", status: "running" })).toBe("meeting");
    expect(taskScene({ id: "meeting", status: "done", result: { text: "纪要", scene: "meeting" } })).toBe("meeting");
  });
  it("轮询经过断网继续读取相同任务直到完成", async () => {
    const read = vi.fn().mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce({ id: "a", request_id: "r", status: "running" })
      .mockResolvedValueOnce({ id: "a", request_id: "r", status: "done", result: { text: "4" } });
    const update = vi.fn();
    await pollTask("a", { read, update, delay: async () => {}, signal: new AbortController().signal });
    expect(read.mock.calls.map(call => call[0])).toEqual(["a", "a", "a"]);
    expect(update.mock.lastCall?.[0].status).toBe("done");
  });
  it("interrupted 立即停止轮询，必须显式重试", async () => {
    const read = vi.fn().mockResolvedValue({ id: "a", status: "interrupted" });
    await pollTask("a", { read, update: vi.fn(), delay: async () => {}, signal: new AbortController().signal });
    expect(read).toHaveBeenCalledTimes(1);
  });
  it("401 不会无限后台重试", async () => {
    const read = vi.fn().mockRejectedValue(new ApiError("请登录", 401));
    await expect(pollTask("a", { read, update: vi.fn(), delay: async () => {}, signal: new AbortController().signal })).rejects.toMatchObject({ status: 401 });
    expect(read).toHaveBeenCalledTimes(1);
  });
  it("响应丢失时按 request_id 找回同一任务", () => {
    const tasks = [{ id: "b", request_id: "other", status: "running" }, { id: "a", request_id: "r", status: "done" }] as const;
    expect(findRecoveryTask([...tasks], { sessionId: "s", requestId: "r" })?.id).toBe("a");
  });
  it("本地损坏记录不会破坏初始化", () => {
    localStorage.setItem("glasses.session.v1", "{invalid");
    expect(readSavedSession(localStorage)).toBeNull();
  });
});
describe("摄像头与麦克风生命周期", () => {
  it("取消后才完成的权限请求也必须关闭轨道", async () => {
    let resolve!: (stream: MediaStream) => void;
    const stop = vi.fn();
    const stream = { getTracks: () => [{ stop }] } as unknown as MediaStream;
    const lease = new MediaLease();
    const opening = lease.open(() => new Promise(r => { resolve = r; }));
    lease.stop(); resolve(stream);
    expect(await opening).toBeNull();
    expect(stop).toHaveBeenCalledTimes(1);
  });
  it("关闭活动流后不会遗留硬件占用", async () => {
    const stop = vi.fn();
    const lease = new MediaLease();
    await lease.open(async () => ({ getTracks: () => [{ stop }] }) as unknown as MediaStream);
    lease.stop(); lease.stop();
    expect(stop).toHaveBeenCalledTimes(1);
  });
});
