import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Ring } from "../components/ring";
import { mount } from "../lib/smart-ring";

vi.mock("../lib/smart-ring", async importOriginal => ({
  ...await importOriginal<typeof import("../lib/smart-ring")>(),
  mount: vi.fn(),
}));

const model = { setState: vi.fn(), rotate: vi.fn(), reset: vi.fn(), setVisible: vi.fn(), destroy: vi.fn() };
beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(mount).mockReturnValue(model);
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as unknown as DOMRectList);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); vi.restoreAllMocks(); });

function setup(reading = false) {
  const lens = createRef<HTMLElement>();
  const first = vi.fn(), second = vi.fn(), third = vi.fn(), onBack = vi.fn();
  const focus = vi.fn();
  const view = render(<><section ref={lens} data-ring-context={reading ? "result" : "home"}>
    {reading ? <div data-result-body>结果正文</div> : <>
      <button data-ring-action aria-pressed="true" onClick={first}>拍照解题</button>
      <button data-ring-action aria-pressed="false" onClick={second} onFocus={focus}>会议纪要</button>
      <button data-ring-action aria-pressed="false" onClick={third}>导入资料</button>
    </>}
    <textarea aria-label="文字输入" />
  </section><Ring lens={lens} onBack={onBack} /></>);
  return { ...view, lens: lens.current!, first, second, third, focus, onBack, ring: screen.getByRole("button", { name: /上下滑动操控眼镜/ }) };
}

function pointer(target: HTMLElement, type: string, y: number, x = 30) {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 });
  Object.defineProperties(event, { pointerId: { value: 1 }, isPrimary: { value: true } });
  fireEvent(target, event);
}

describe("原戒指操控", () => {
  it("连续滑动只更新选中项，松开不执行；轻点确认当前项", () => {
    const { ring, first, second, third, focus } = setup();
    pointer(ring, "pointerdown", 20);
    pointer(ring, "pointermove", 66);
    expect(screen.getByRole("button", { name: "会议纪要" }).getAttribute("data-ring-selected")).toBe("true");
    expect(focus).toHaveBeenCalledTimes(1);
    pointer(ring, "pointermove", 110);
    expect(screen.getByRole("button", { name: "导入资料" }).getAttribute("data-ring-selected")).toBe("true");
    pointer(ring, "pointerup", 110);
    fireEvent.click(ring, { detail: 1 }); // Browser click following a drag is ignored.
    expect(first).not.toHaveBeenCalled(); expect(second).not.toHaveBeenCalled(); expect(third).not.toHaveBeenCalled();
    pointer(ring, "pointerdown", 20); pointer(ring, "pointerup", 20);
    fireEvent.click(ring, { detail: 1 });
    act(() => vi.advanceTimersByTime(280));
    expect(third).toHaveBeenCalledTimes(1);
  });

  it("长按和双击各返回一次，双击不会先执行确认", () => {
    const { ring, first, onBack } = setup();
    pointer(ring, "pointerdown", 20);
    act(() => vi.advanceTimersByTime(700));
    pointer(ring, "pointerup", 20); fireEvent.click(ring, { detail: 1 });
    expect(onBack).toHaveBeenCalledTimes(1); expect(first).not.toHaveBeenCalled();
    pointer(ring, "pointerdown", 20); pointer(ring, "pointerup", 20); fireEvent.click(ring, { detail: 1 });
    act(() => vi.advanceTimersByTime(100));
    pointer(ring, "pointerdown", 20); pointer(ring, "pointerup", 20); fireEvent.click(ring, { detail: 2 });
    act(() => vi.advanceTimersByTime(400));
    expect(onBack).toHaveBeenCalledTimes(2); expect(first).not.toHaveBeenCalled();
  });

  it("阅读随拖动连续滚动，取消手势恢复起点", () => {
    const { ring, lens } = setup(true);
    const result = lens.querySelector<HTMLElement>("[data-result-body]")!;
    Object.defineProperties(result, { scrollHeight: { value: 1600 }, clientHeight: { value: 240 } });
    result.scrollTop = 100;
    pointer(ring, "pointerdown", 20); pointer(ring, "pointermove", 45);
    expect(result.scrollTop).toBe(180);
    pointer(ring, "pointermove", 70);
    expect(result.scrollTop).toBe(260);
    pointer(ring, "pointercancel", 70);
    expect(result.scrollTop).toBe(100);
    expect(ring.classList.contains("is-flowing")).toBe(false);
  });

  it("仅看旋转不操作镜片，方向键调整造型，离开时复位", () => {
    const { ring, first, second, onBack } = setup();
    fireEvent.click(screen.getByRole("button", { name: "仅看旋转" }));
    pointer(ring, "pointerdown", 20, 20); pointer(ring, "pointermove", 60, 60); pointer(ring, "pointerup", 60, 60);
    fireEvent.click(ring, { detail: 1 });
    fireEvent.keyDown(ring, { key: "ArrowRight" });
    expect(model.rotate).toHaveBeenCalledWith(40, 40);
    expect(model.rotate).toHaveBeenCalledWith(15, 0);
    expect(first).not.toHaveBeenCalled(); expect(second).not.toHaveBeenCalled(); expect(onBack).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "返回操控" }));
    expect(model.reset).toHaveBeenCalledTimes(1);
  });

  it("全局键盘可选择确认，文字输入保留原生按键，卸载释放监听和模型", () => {
    const { ring, first, second, onBack, unmount } = setup();
    fireEvent.keyDown(document.body, { key: "ArrowDown" });
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(second).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(onBack).not.toHaveBeenCalled(); expect(first).not.toHaveBeenCalled();
    fireEvent.keyDown(ring, { key: "Escape" });
    expect(onBack).toHaveBeenCalledTimes(1);
    fireEvent.click(ring, { detail: 1 });
    unmount();
    act(() => vi.advanceTimersByTime(400));
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onBack).toHaveBeenCalledTimes(1); expect(first).not.toHaveBeenCalled();
    expect(model.destroy).toHaveBeenCalledTimes(1);
  });

  it("隐藏页面取消待确认操作；上下文丢失禁用旋转但触控仍可操作", () => {
    const { ring, first } = setup();
    fireEvent.click(ring, { detail: 1 });
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    fireEvent(document, new Event("visibilitychange"));
    act(() => vi.advanceTimersByTime(400));
    expect(first).not.toHaveBeenCalled(); expect(model.setVisible).toHaveBeenLastCalledWith(false);
    hidden.mockReturnValue(false); fireEvent(document, new Event("visibilitychange"));
    const unavailable = vi.mocked(mount).mock.calls[0][1]?.onUnavailable!;
    act(unavailable);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "仅看旋转" }).disabled).toBe(true);
    fireEvent.click(ring, { detail: 0 });
    expect(first).toHaveBeenCalledTimes(1);
  });
});
