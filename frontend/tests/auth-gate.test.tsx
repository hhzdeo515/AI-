import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthGate } from "../components/auth-gate";

vi.mock("../components/studio", () => ({ Studio: () => <main>已进入工作空间</main> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

it("登录服务缺失时停止等待，连接恢复后才允许口令登录", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response("Not Found", { status: 404 }))
    .mockResolvedValueOnce(Response.json({ authenticated: false, auth_enabled: true }))
    .mockResolvedValueOnce(Response.json({ authenticated: true, auth_enabled: true }))
    .mockResolvedValueOnce(Response.json({ authenticated: true, auth_enabled: true }));
  vi.stubGlobal("fetch", fetcher);
  render(<AuthGate />);
  await waitFor(() => expect(screen.getByRole("heading", { name: "暂时无法登录" })).toBeTruthy());
  expect(screen.getByRole("alert").textContent).toContain("尚未连接登录服务");
  expect(screen.queryByText("正在确认服务与登录状态。")).toBeNull();
  expect(screen.queryByLabelText("访问口令")).toBeNull();
  expect(screen.queryByText("已进入工作空间")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "重新连接" }));
  const password = await screen.findByLabelText("访问口令");
  fireEvent.change(password, { target: { value: "test-only-password" } });
  fireEvent.click(screen.getByRole("button", { name: "进入眼镜视野" }));
  await screen.findByText("已进入工作空间");
  expect(fetcher.mock.calls[2][0]).toBe("/api/login");
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ token: "test-only-password" });
  expect(localStorage.length).toBe(0);
});
