// @vitest-environment node
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import {JSDOM} from "jsdom";
import {afterEach, expect, it, vi} from "vitest";

const {HardwareNavigation, modes} = createRequire(import.meta.url)("../../langgraph-app/lg_assistant/web/static/hardware-engine.js");
const source = name => readFileSync(new URL(`../../langgraph-app/lg_assistant/web/static/${name}`, import.meta.url), "utf8");
let dom;
afterEach(() => { dom?.window.LensNavigation?.stop(); dom?.window.close(); vi.useRealTimers(); });

function setup(supported = true) {
  const markup = source("hardware.js").match(/host\.innerHTML = `([\s\S]+?)`;/)[1];
  dom = new JSDOM(`<div id="hardware-demo">${markup}</div>`, {url: "https://assistant.example/#hardware", runScripts: "outside-only", pretendToBeVisual: true});
  const win = dom.window, doc = win.document, nav = new HardwareNavigation(), sessions = [];
  let available = true;
  class Recognition {
    constructor() { this.abort = vi.fn(); this.stop = vi.fn(); this.start = vi.fn(); sessions.push(this); }
  }
  if (supported) win.SpeechRecognition = Recognition;
  win.HTMLMediaElement.prototype.pause = vi.fn();
  win.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue();
  const api = {
    state: () => ({...nav.state, options: nav.state.screen === "home" ? modes : nav.options(), available}),
    act: vi.fn(async action => { nav.commit(nav.plan(action)); win.LensNavigation?.refresh(); }),
    slide: vi.fn()
  };
  win.HardwareLens = {menu: api};
  const menu = doc.getElementById("hw-nav-options");
  menu.innerHTML = '<button data-hw-option="0"><span>拍照解题</span></button>';
  menu.getBoundingClientRect = () => ({width: 500});
  const clicked = vi.fn();
  menu.addEventListener("click", clicked);
  win.eval(source("lens-optics.js"));
  win.eval(source("lens-navigation.js"));
  const voice = doc.getElementById("hw-menu-voice"), status = doc.getElementById("hw-menu-voice-status");
  const result = (text, final = true) => ({resultIndex: 0, results: [{0: {transcript: text}, isFinal: final}]});
  const pointer = (type, x, y = 100, id = 1, target = menu.querySelector("button")) => {
    const event = new win.Event(type, {bubbles: true, cancelable: true});
    Object.assign(event, {button: 0, pointerId: id, clientX: x, clientY: y});
    target.dispatchEvent(event);
    return event;
  };
  return {win, doc, nav, api, menu, clicked, sessions, voice, status, result, pointer, unavailable() { available = false; }};
}

it("only starts recognition on explicit click, then selects one final spoken function", async () => {
  const s = setup();
  expect(s.sessions).toHaveLength(0);
  s.voice.click();
  const speech = s.sessions[0], callback = speech.onresult;
  expect(speech.lang).toBe("zh-CN"); expect(speech.continuous).toBe(false);
  await callback(s.result("实时", false));
  expect(s.api.act).not.toHaveBeenCalled();
  expect(s.status.textContent).toContain("正在听");
  await callback(s.result("请帮我打开实时翻译。"));
  expect(s.api.act).toHaveBeenCalledExactlyOnceWith("scene2");
  expect(s.nav.state.mode).toBe(2); expect(s.nav.state.screen).toBe("actions");
  expect(speech.abort).toHaveBeenCalledOnce();
  await callback(s.result("地图"));
  expect(s.api.act).toHaveBeenCalledTimes(1);
  expect(s.status.textContent).toContain("已选择：实时翻译");
});

it.each(["不要打开地图", "地图和翻译", "今天的会议还没结束", "未知功能"])("does not navigate for unrelated or ambiguous speech: %s", async text => {
  const s = setup(); s.voice.click();
  await s.sessions[0].onresult(s.result(text));
  expect(s.api.act).not.toHaveBeenCalled();
  expect(s.status.textContent).toContain("未匹配");
  expect(s.sessions[0].abort).toHaveBeenCalledOnce();
});

it("selects submenu actions without starting recording, capture or a native picker", async () => {
  const s = setup(); s.nav.commit(s.nav.plan("scene1")); s.win.LensNavigation.refresh();
  s.voice.click(); await s.sessions[0].onresult(s.result("选择导入录音"));
  expect(s.api.act).toHaveBeenCalledExactlyOnceWith("focus1");
  expect(s.nav.state.screen).toBe("actions");
  expect(s.nav.state.focus).toBe(1);
});

it("drops cancelled callbacks and never lets an old end event stop a new session", async () => {
  const s = setup(); s.voice.click();
  const first = s.sessions[0], lateResult = first.onresult, lateEnd = first.onend;
  s.voice.click(); s.voice.click();
  await lateResult(s.result("地图")); lateEnd();
  expect(s.api.act).not.toHaveBeenCalled();
  expect(s.sessions[1].abort).not.toHaveBeenCalled();
  expect(s.voice.getAttribute("aria-pressed")).toBe("true");
});

it("cleans up on timeout, permission errors and leaving the menu", async () => {
  vi.useFakeTimers();
  const s = setup(); s.voice.click();
  vi.advanceTimersByTime(12001);
  expect(s.sessions[0].abort).toHaveBeenCalledOnce();
  expect(s.status.textContent).toContain("聆听已结束");
  s.voice.click(); s.sessions[1].onerror({error: "not-allowed"});
  expect(s.status.textContent).toContain("权限未开启");
  s.voice.click(); const late = s.sessions[2].onresult;
  s.unavailable(); s.doc.getElementById("hw-nav-menu").hidden = true; s.win.LensNavigation.refresh();
  await late(s.result("地图"));
  expect(s.sessions[2].abort).toHaveBeenCalledOnce(); expect(s.api.act).not.toHaveBeenCalled();
});

it("aborts speech on hidden pages and page exit", () => {
  const s = setup(); s.voice.click();
  Object.defineProperty(s.doc, "hidden", {configurable: true, value: true});
  s.doc.dispatchEvent(new s.win.Event("visibilitychange"));
  expect(s.sessions[0].abort).toHaveBeenCalledOnce();
  Object.defineProperty(s.doc, "hidden", {configurable: true, value: false});
  s.voice.click(); s.win.dispatchEvent(new s.win.Event("pagehide"));
  expect(s.sessions[1].abort).toHaveBeenCalledOnce();
});

it("keeps manual navigation available when speech recognition is unsupported", () => {
  const s = setup(false);
  expect(s.voice.disabled).toBe(true);
  expect(s.status.textContent).toContain("不支持语音");
  s.doc.getElementById("hw-menu-next").click();
  expect(s.api.act).toHaveBeenCalledExactlyOnceWith("next");
});

it("swipes the menu without rotating the glasses or accidentally confirming the card", async () => {
  const s = setup(), model = s.doc.querySelector(".hw-optical-model"), initial = model.getAttribute("style");
  s.pointer("pointerdown", 350); s.pointer("pointermove", 150); s.pointer("pointerup", 150);
  const click = new s.win.MouseEvent("click", {bubbles: true, cancelable: true});
  s.menu.querySelector("button").dispatchEvent(click);
  expect(s.clicked).not.toHaveBeenCalled(); expect(click.defaultPrevented).toBe(true);
  expect(model.getAttribute("style")).toBe(initial);
  expect(s.api.slide.mock.calls[0][0].phase).toBe("start");
  expect(s.api.slide.mock.calls.at(-1)[0]).toEqual({phase: "end", total: 44});
  s.pointer("pointerdown", 150); s.pointer("pointerup", 150);
  s.menu.querySelector("button").click(); expect(s.clicked).toHaveBeenCalledOnce();
});

it("cancels incomplete gestures, ignores other pointers and prevents commands while busy", () => {
  const s = setup();
  s.pointer("pointerdown", 100); s.pointer("pointermove", 300, 100, 2);
  expect(s.api.slide).not.toHaveBeenCalled();
  s.pointer("pointermove", 300); s.pointer("pointercancel", 300);
  expect(s.api.slide.mock.calls.at(-1)[0].phase).toBe("cancel");
  expect(s.doc.getElementById("hardware-demo").classList.contains("is-lens-swiping")).toBe(false);
  s.api.slide.mockClear(); s.unavailable();
  s.pointer("pointerdown", 100); s.pointer("pointermove", 300); s.pointer("pointerup", 300);
  expect(s.api.slide).not.toHaveBeenCalled();
});
