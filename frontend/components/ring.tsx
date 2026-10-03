"use client";
import { IS_DEMO } from "../lib/mode";
import { useRef, useState } from "react";
import { ArrowDownIcon, ArrowUpIcon, ArrowUUpLeftIcon, CheckIcon } from "@phosphor-icons/react";

export function Ring({ lens, onBack }: { lens: React.RefObject<HTMLElement | null>; onBack: () => void }) {
  const index = useRef(-1);
  const gesture = useRef<{ y: number; started: number } | null>(null);
  const [hint, setHint] = useState("上下滑动选择，轻点确认");
  const controls = () => Array.from(lens.current?.querySelectorAll<HTMLElement>("[data-ring-action]:not(:disabled)") || []).filter(node => node.getClientRects().length);
  function move(direction: number) {
    const result = lens.current?.querySelector<HTMLElement>("[data-result-body]");
    if (result) { result.scrollBy({ top: direction * 150, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" }); setHint(direction > 0 ? "向下阅读" : "向上阅读"); return; }
    const items = controls(); if (!items.length) return;
    index.current = (index.current + direction + items.length) % items.length;
    items.forEach(item => item.removeAttribute("data-ring-selected"));
    const item = items[index.current]; item.setAttribute("data-ring-selected", "true"); item.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    setHint(`已选择 · ${item.getAttribute("aria-label") || item.textContent?.trim().slice(0, 22) || "输入"}`);
  }
  function press() {
    const items = controls(); const item = items[Math.max(0, index.current) % items.length];
    if (!item) return;
    if (item.matches("textarea,input,select")) { item.focus(); setHint("请在镜片内输入或选择"); }
    else { item.click(); setHint("已确认选择"); }
  }
  const back = () => { index.current = -1; onBack(); setHint("已返回输入视野"); };
  return <aside className="ring-panel" aria-label="戒指控制器">
    <div className="flex items-center justify-between"><h2 className="text-sm font-medium">戒指控制</h2></div>
    <button type="button" className="ring-device" aria-label="智能戒指：上下滑动选择，轻点确认，长按返回" onKeyDown={e => {
      if (["ArrowUp", "ArrowLeft", "ArrowDown", "ArrowRight", "Enter", " ", "Escape"].includes(e.key)) e.preventDefault();
      if (["ArrowUp", "ArrowLeft"].includes(e.key)) move(-1);
      if (["ArrowDown", "ArrowRight"].includes(e.key)) move(1);
      if (["Enter", " "].includes(e.key)) press();
      if (e.key === "Escape") back();
    }} onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); gesture.current = { y: e.clientY, started: Date.now() }; }} onPointerCancel={() => { gesture.current = null; }} onPointerUp={e => {
      const start = gesture.current; gesture.current = null; if (!start) return;
      const delta = e.clientY - start.y;
      if (Math.abs(delta) > 22) move(delta > 0 ? 1 : -1); else if (Date.now() - start.started >= 700) back(); else press();
    }} onClick={e => { if (e.detail === 0 && !gesture.current) { /* Keyboard is handled above. */ } }}>
      <span className="ring-band"><span className="ring-hole" /><span className="ring-touch"><span /><span /><span /></span></span>
    </button>
    <p className="ring-hint" aria-live="polite">{hint}</p>
    <div className="grid grid-cols-2 gap-2 mt-5"><button className="button justify-center" onClick={() => move(-1)} aria-label="戒指上滑"><ArrowUpIcon size={17} />上滑</button><button className="button justify-center" onClick={() => move(1)} aria-label="戒指下滑"><ArrowDownIcon size={17} />下滑</button><button className="button primary justify-center" onClick={press}><CheckIcon size={17} />确认</button><button className="button justify-center" onClick={back}><ArrowUUpLeftIcon size={17} />返回</button></div>
    <div className="mt-6 border-t border-line pt-4 text-xs leading-6 muted"><p>方向键选择 · Enter 确认</p><p>长按 0.7 秒 · Esc 返回</p>{!IS_DEMO && <p className="mt-3">AI 处理使用真实服务。</p>}</div>
  </aside>;
}
