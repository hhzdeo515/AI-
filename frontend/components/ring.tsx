"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { bindInput, mount, type RingFlow, type RingInput, type RingModel } from "../lib/smart-ring";

export function Ring({ lens, onBack }: { lens: RefObject<HTMLElement | null>; onBack: () => void }) {
  const button = useRef<HTMLButtonElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const model = useRef<RingModel | null>(null);
  const input = useRef<RingInput | null>(null);
  const inspecting = useRef(false);
  const backCallback = useRef(onBack);
  backCallback.current = onBack;
  const [inspect, setInspect] = useState(false);
  const [hasModel, setHasModel] = useState(false);
  const [reading, setReading] = useState(false);
  const [hint, setHint] = useState("等待操作");

  function setInspection(value: boolean) {
    input.current?.cancel();
    inspecting.current = value;
    setInspect(value);
    setHint(value ? "旋转查看中" : "等待操作");
    if (!value) model.current?.reset();
  }

  useEffect(() => {
    const ring = button.current;
    if (!ring || !canvas.current) return;
    let index = -1;
    let context = lens.current?.dataset.ringContext;
    let drag: { items: HTMLElement[]; startIndex: number; previewIndex: number; body: HTMLElement | null; startTop: number } | null = null;
    const controls = () => Array.from(lens.current?.querySelectorAll<HTMLElement>("[data-ring-action]:not(:disabled)") || [])
      .filter(node => node.getClientRects().length && !node.closest("[hidden]"));
    const body = () => lens.current?.querySelector<HTMLElement>("[data-result-body]") || null;
    const currentIndex = (items: HTMLElement[]) => {
      const marked = items.findIndex(item => item.hasAttribute("data-ring-selected"));
      const chosen = items.findIndex(item => item.getAttribute("aria-pressed") === "true");
      return marked >= 0 ? marked : chosen >= 0 ? chosen : Math.max(0, Math.min(index, items.length - 1));
    };
    const mark = (items: HTMLElement[], selected: number) => {
      if (!items.length) return;
      index = ((selected % items.length) + items.length) % items.length;
      items.forEach(item => item.removeAttribute("data-ring-selected"));
      const item = items[index];
      item.setAttribute("data-ring-selected", "true");
      // Update the lens carousel without moving browser focus away from the ring.
      item.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
      item.scrollIntoView?.({ block: "nearest", inline: "nearest", behavior: "instant" });
      setHint(`已选择 · ${item.getAttribute("aria-label") || item.textContent?.trim().slice(0, 22) || "输入"}`);
    };
    const scroll = (direction: number) => {
      const result = body();
      if (!result) return false;
      result.scrollBy?.({ top: direction * Math.max(150, result.clientHeight * 0.65), behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
      setHint(direction > 0 ? "向下阅读" : "向上阅读");
      return true;
    };
    const move = (direction: number) => {
      if (scroll(direction)) return;
      const items = controls();
      mark(items, currentIndex(items) + direction);
    };
    const press = () => {
      if (scroll(1)) return;
      const items = controls();
      const item = items[currentIndex(items)];
      if (!item) return;
      if (item.matches("textarea,input,select")) {
        item.focus();
        setHint("请在镜片内输入或选择");
      } else {
        item.click();
        setHint("已确认选择");
      }
    };
    const back = () => {
      index = -1;
      controls().forEach(item => item.removeAttribute("data-ring-selected"));
      backCallback.current();
      setHint("已返回上一页");
    };
    const clearFlow = () => {
      ring.classList.remove("is-flowing");
      ring.style.setProperty("--flow-thumb", "0px");
      ring.style.setProperty("--flow-ticks", "0px");
    };
    const followFlow = (event: RingFlow) => {
      if (event.phase === "start") {
        const items = controls();
        const result = body();
        const selected = currentIndex(items);
        drag = { items, startIndex: selected, previewIndex: selected, body: result, startTop: result?.scrollTop || 0 };
        ring.classList.add("is-flowing");
        return;
      }
      if (!drag) return;
      if (event.phase === "move") {
        const total = event.total || 0;
        ring.style.setProperty("--flow-thumb", `${Math.max(-42, Math.min(42, total * 0.55))}px`);
        ring.style.setProperty("--flow-ticks", `${total % 16}px`);
        if (drag.body) {
          drag.body.scrollTop = Math.max(0, Math.min(drag.body.scrollHeight - drag.body.clientHeight, drag.startTop + total * 3.2));
          setHint("跟随拖动阅读");
        } else if (drag.items.length) {
          const selected = ((drag.startIndex + Math.trunc(total / 44)) % drag.items.length + drag.items.length) % drag.items.length;
          if (selected !== drag.previewIndex) {
            drag.previewIndex = selected;
            mark(drag.items, selected);
          }
        }
        return;
      }
      if (event.phase === "cancel") {
        if (drag.body) drag.body.scrollTop = drag.startTop;
        else if (drag.previewIndex !== drag.startIndex) mark(drag.items, drag.startIndex);
      }
      drag = null;
      clearFlow();
    };
    model.current = mount(canvas.current, { onUnavailable: () => {
      setHasModel(false);
      inspecting.current = false;
      setInspect(false);
      input.current?.cancel();
      setHint("上下滑动选择 · 轻点确认");
    } });
    setHasModel(Boolean(model.current));
    input.current = bindInput(ring, {
      action: action => {
        if (action === "back" || action === "double") back();
        else if (action === "press") press();
        else move(action === "previous" || action === "up" ? -1 : 1);
      },
      inspect: () => inspecting.current,
      rotate: (dx, dy) => model.current?.rotate(dx, dy),
      feedback: setHint,
      vertical: () => true,
      doubleTap: true,
      flow: true,
      onFlow: followFlow,
    });
    const sync = () => {
      const nextContext = lens.current?.dataset.ringContext;
      if (nextContext !== context) {
        context = nextContext;
        index = -1;
        input.current?.cancel();
        controls().forEach(item => item.removeAttribute("data-ring-selected"));
      }
      const busy = lens.current?.getAttribute("aria-busy") === "true";
      ring.disabled = busy;
      model.current?.setState({ connected: true, busy, error: false });
      setReading(Boolean(body()));
    };
    const observer = new MutationObserver(sync);
    if (lens.current) observer.observe(lens.current, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-ring-context", "aria-busy", "disabled"] });
    sync();
    const visibility = () => {
      model.current?.setVisible(!document.hidden);
      if (document.hidden) input.current?.cancel();
    };
    document.addEventListener("visibilitychange", visibility);
    visibility();
    const keyboard = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || ring.disabled) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (target?.closest("input,textarea,select,[contenteditable=true]")) return;
      if (inspecting.current) {
        if (event.key === "Escape") { event.preventDefault(); setInspection(false); }
        return;
      }
      if (["ArrowUp", "ArrowLeft", "ArrowDown", "ArrowRight"].includes(event.key)) {
        event.preventDefault();
        move(event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 1);
      } else if (event.key === "Escape") {
        event.preventDefault(); back();
      } else if (["Enter", " "].includes(event.key) && (!target?.closest("button,a") || target === ring)) {
        event.preventDefault(); press();
      }
    };
    document.addEventListener("keydown", keyboard);
    return () => {
      observer.disconnect();
      document.removeEventListener("keydown", keyboard);
      document.removeEventListener("visibilitychange", visibility);
      input.current?.destroy(); input.current = null;
      model.current?.destroy(); model.current = null;
      controls().forEach(item => item.removeAttribute("data-ring-selected"));
      clearFlow();
    };
  }, [lens]);

  return <aside className={`hw-ring-panel${inspect ? " is-inspecting" : ""}${reading ? " is-reading" : ""}`} aria-label="戒指操控窗口">
    <div className="hw-device-label"><h2>智能戒指</h2><span className="hw-status">触控操控</span></div>
    <div className="hw-ring-stage">
      <button ref={button} type="button" className="hw-ring" id="hw-ring" aria-label={inspect ? "旋转查看智能戒指，拖动或使用方向键" : "上下滑动操控眼镜，轻点确认，双击或长按返回上一页"} aria-describedby="hw-gesture-guide">
        <span className="hw-ring-inner" /><span className="hw-ring-touch" />
        <canvas ref={canvas} id="hw-ring-canvas" aria-hidden="true" />
        <span className="hw-flow-rail" aria-hidden="true"><svg className="hw-flow-up" viewBox="0 0 16 16"><path d="m4 10 4-4 4 4" /></svg><span className="hw-flow-track"><span className="hw-flow-ticks" /><span className="hw-flow-thumb" /></span><svg className="hw-flow-down" viewBox="0 0 16 16"><path d="m4 6 4 4 4-4" /></svg></span>
        <span className="hw-flow-caption" aria-hidden="true">上下滑动 · 轻点确认</span>
      </button>
    </div>
    <div className="hw-ring-tools"><span id="hw-ring-state" role="status" aria-live="polite">{hint}</span><button type="button" id="hw-ring-inspect" aria-pressed={inspect} disabled={!hasModel} onClick={() => setInspection(!inspect)}>{inspect ? "返回操控" : "仅看旋转"}</button></div>
    <p className="hw-instruction">上下滑动选择，轻点确认<br /><kbd>Space</kbd> 或 <kbd>Enter</kbd> 确认</p>
    <p className="hw-gesture-guide" id="hw-gesture-guide">{inspect ? "拖动戒指或使用方向键旋转，返回操控后可继续控制眼镜。" : "拖动戒指连续选择或阅读；双击，或长按 0.7 秒后松开，返回上一页。"}</p>
    <p className="hw-small">方向键选择 · Esc 返回</p>
  </aside>;
}
