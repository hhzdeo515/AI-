import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AuthGate } from "../components/auth-gate";

vi.mock("../lib/mode", () => ({ IS_DEMO: true, DEMO_STORE_KEY: "glasses.demo.data.v1", DEMO_SESSION_KEY: "glasses.demo.session.v1" }));
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });

it("opens the real demo workspace without login, runs a sample and restores it on refresh", async () => {
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("No backend in demo"); }));
  const first = render(<AuthGate />);
  expect(screen.queryByLabelText("访问口令")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "进入工作空间" }));
  expect(screen.queryByText(/AI 处理使用真实服务/)).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "填入示例题目" }));
  fireEvent.click(screen.getByRole("button", { name: "开始处理" }));
  await screen.findByText("答案：126", {}, { timeout: 6000 });
  expect(screen.queryByText(/演示模式|以下为预设示例/)).toBeNull();
  const download = screen.getByRole("link", { name: "下载 Markdown 结果" });
  expect(download.getAttribute("href")).toMatch(/^data:text\/plain/);
  expect(download.getAttribute("download")).toMatch(/\.md$/);
  first.unmount();
  render(<AuthGate />);
  fireEvent.click(screen.getByRole("button", { name: "进入工作空间" }));
  await screen.findByText("答案：126");
  fireEvent.click(screen.getByRole("button", { name: "退出工作空间" }));
  await screen.findByRole("button", { name: "进入工作空间" });
  expect(fetch).not.toHaveBeenCalled();
}, 10000);
