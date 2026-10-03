import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { Markdown } from "../components/markdown";
import { Recorder } from "../components/recorder";
import { Composer } from "../components/composer";
import { useTask } from "../hooks/use-task";
import { SESSION_KEY } from "../lib/tasks";
import { Studio } from "../components/studio";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });
const response = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });

describe("任务恢复与提交保护", () => {
  it("历史中断任务仍可显式继续原任务，但不会开放旧题的新作答", async () => {
    let resumed = false;
    const fetcher = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/task/retry") { resumed = true; return Promise.resolve(response({ task_id: "stopped", request_id: "r" })); }
      return Promise.resolve(response({ id: "stopped", request_id: "r", session_id: "old-exam", status: resumed ? "done" : "interrupted", result: resumed ? { text: "恢复的结果" } : null }));
    });
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.ready).toBe(true));
    act(() => result.current.select({ id: "stopped", session_id: "old-exam", status: "interrupted" }));
    await waitFor(() => expect(result.current.task?.status).toBe("interrupted"));
    await act(async () => { await result.current.retry(); });
    await waitFor(() => expect(result.current.task?.status).toBe("done"));
    const retries = fetcher.mock.calls.filter(call => call[0] === "/api/task/retry");
    expect(retries).toHaveLength(1);
    expect(JSON.parse(retries[0][1].body)).toEqual({ task_id: "stopped", owner: "local" });
    expect(result.current.readOnly).toBe(true);
  });
  it("历史任务只读标识跨刷新保留，不能向旧题目提交后续回答", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(response({ id: "old", session_id: "old-session-exam", status: "done", result: { text: "旧题目" } }))));
    const first = renderHook(useTask);
    await waitFor(() => expect(first.result.current.ready).toBe(true));
    act(() => first.result.current.select({ id: "old", session_id: "old-session-exam", status: "done" }));
    await waitFor(() => expect(first.result.current.readOnly).toBe(true));
    expect(JSON.parse(localStorage.getItem(SESSION_KEY) || "{}").readonlyTaskId).toBe("old");
    first.unmount();
    const restored = renderHook(useTask);
    await waitFor(() => expect(restored.result.current.readOnly).toBe(true));
    let accepted = true;
    await act(async () => { accepted = await restored.result.current.submit({ text: "对旧题作答", scene: "exam", backend: "original" }); });
    expect(accepted).toBe(false);
    expect(vi.mocked(fetch).mock.calls.every(call => call[1]?.method !== "POST")).toBe(true);
  });
  it("刷新恢复会议任务后，继续输入仍是会议场景", async () => {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ sessionId: "s", taskId: "meeting", requestId: "r" }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ id: "meeting", session_id: "s-meeting", status: "interrupted", request_id: "r" })));
    render(<Studio authEnabled={false} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "会议纪要" }).getAttribute("aria-pressed")).toBe("true"));
    fireEvent.click(screen.getByRole("button", { name: "继续输入" }));
    expect(screen.getByLabelText("会议文字或补充说明")).toBeTruthy();
  });
  it("刷新后读取已保存任务，绝不重新 POST", async () => {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ sessionId: "s", taskId: "existing", requestId: "r" }));
    const fetcher = vi.fn().mockResolvedValue(response({ id: "existing", status: "done", request_id: "r", result: { text: "4" } }));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.task?.result?.text).toBe("4"));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toContain("task_id=existing");
    expect(fetcher.mock.calls[0][1].method).toBeUndefined();
  });
  it("连续点击发送只建立一个任务", async () => {
    let resolvePost!: (value: Response) => void;
    const fetcher = vi.fn().mockImplementation((url: string) => url === "/api/chat/async"
      ? new Promise(resolve => { resolvePost = resolve; })
      : Promise.resolve(response({ id: "once", request_id: "r", status: "done", result: { text: "4" } })));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.ready).toBe(true));
    const input = { text: "2+2", scene: "exam" as const, backend: "original" as const };
    let first!: Promise<boolean>;
    const originalSession = JSON.parse(localStorage.getItem(SESSION_KEY) || "{}").sessionId;
    act(() => {
      first = result.current.submit(input); void result.current.submit(input);
      result.current.select({ id: "other", session_id: "other-session-exam", status: "done" });
    });
    await act(async () => { resolvePost(response({ task_id: "once", request_id: "r" })); await first; });
    await waitFor(() => expect(result.current.task?.status).toBe("done"));
    expect(fetcher.mock.calls.filter(call => call[0] === "/api/chat/async")).toHaveLength(1);
    expect(localStorage.getItem(SESSION_KEY)).not.toContain("2+2");
    expect(JSON.parse(localStorage.getItem(SESSION_KEY) || "{}").sessionId).toBe(originalSession);
    expect(result.current.readOnly).toBe(false);
  });
});
describe("安全、真实的交互", () => {
  it("模型返回的 HTML 和危险链接不会被执行", () => {
    const { container } = render(<Markdown text={'<script>alert("x")</script>\n\n<img src=x onerror=alert(1)>\n\n[链接](javascript:alert(1))\n\n**正常结果**'} />);
    expect(container.querySelector("script,img")).toBeNull();
    expect(container.querySelector("a")?.getAttribute("href") || "").not.toContain("javascript:");
    expect(screen.getByText("正常结果")).toBeTruthy();
  });
  it("停止录音只生成可试听文件，不自动发送", async () => {
    const stopTrack = vi.fn();
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack, addEventListener: vi.fn() }] }) } });
    class FakeRecorder {
      static isTypeSupported() { return true; }
      mimeType = "audio/webm"; state = "inactive";
      ondataavailable?: (event: { data: Blob }) => void; onstop?: () => void;
      start() { this.state = "recording"; }
      stop() { this.state = "inactive"; this.ondataavailable?.({ data: new Blob(["recorded audio"]) }); this.onstop?.(); }
    }
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    const file = vi.fn(); const recording = vi.fn();
    render(<Recorder disabled={false} onRecording={recording} onFile={file} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录音" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /停止录音/ })).toBeTruthy());
    expect(file).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /停止录音/ }));
    await waitFor(() => expect(file).toHaveBeenCalledTimes(1));
    expect(file.mock.calls[0][0]).toBeInstanceOf(File);
    expect(stopTrack).toHaveBeenCalled();
    expect(recording.mock.lastCall?.[0]).toBe(false);
  });
  it("会议附件保留试听与独立发送按钮", () => {
    const onSubmit = vi.fn();
    render(<Composer scene="meeting" agent="auto" setAgent={vi.fn()} text="" setText={vi.fn()} files={[new File(["audio"], "会议.webm", { type: "audio/webm" })]} urls={["blob:preview"]} onFiles={vi.fn()} onRemove={vi.fn()} busy={false} recording={false} onRecording={vi.fn()} onCamera={vi.fn()} onSubmit={onSubmit} onSettings={vi.fn()} draft="" setDraft={vi.fn()} answer="" setAnswer={vi.fn()} error="" hasResult={false} onResult={vi.fn()} />);
    expect(screen.getByLabelText("录音试听")).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "发送并生成纪要" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});
