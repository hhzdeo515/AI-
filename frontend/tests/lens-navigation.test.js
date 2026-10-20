// @vitest-environment node
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import {JSDOM} from "jsdom";
import {afterEach, expect, it, vi} from "vitest";

const {HardwareNavigation, modes} = createRequire(import.meta.url)("../../langgraph-app/lg_assistant/web/static/hardware-engine.js");
const source = name => readFileSync(new URL(`../../langgraph-app/lg_assistant/web/static/${name}`, import.meta.url), "utf8");
const settle = async () => { for(let i=0;i<12;i++) await Promise.resolve(); };
let dom;
afterEach(() => { dom?.window.LensNavigation?.stop(); dom?.window.close(); vi.useRealTimers(); });

function setup(supported = true) {
  const markup = source("hardware.js").match(/host\.innerHTML = `([\s\S]+?)`;/)[1];
  dom = new JSDOM(`<div id="hardware-demo">${markup}</div>`, {url: "https://assistant.example/#hardware", runScripts: "outside-only", pretendToBeVisual: true});
  const win = dom.window, doc = win.document, nav = new HardwareNavigation(), sessions = [];
  let available = true;
  class Recognition {
    static available = vi.fn().mockResolvedValue("available");
    static install = vi.fn().mockResolvedValue(true);
    constructor() { this.abort = vi.fn(); this.stop = vi.fn(); this.start = vi.fn(); sessions.push(this); }
  }
  Recognition.prototype.processLocally = false;
  if(supported==='browser') {delete Recognition.prototype.processLocally;delete Recognition.available;delete Recognition.install;}
  if (supported) win.SpeechRecognition = Recognition;
  win.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({error:"云端编排服务尚未配置"}),{status:503}));
  win.HTMLMediaElement.prototype.pause = vi.fn();
  win.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue();
  const api = {
    state: () => ({...nav.state, options: nav.state.screen === "home" ? modes : nav.options(), commandAvailable:available, available:available&&["home","actions"].includes(nav.state.screen)}),
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
  win.eval(source("edge-intent-model.js"));
  win.eval(source("edge-intent.js"));
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
  s.voice.click(); await settle();
  const speech = s.sessions[0], callback = speech.onresult;
  expect(speech.lang).toBe("zh-CN"); expect(speech.continuous).toBe(false);
  expect(speech.processLocally).toBe(true);
  expect(s.win.SpeechRecognition.available).toHaveBeenCalledWith({langs:['zh-CN'],processLocally:true});
  await callback(s.result("实时", false));
  expect(s.api.act).not.toHaveBeenCalled();
  expect(s.status.textContent).toContain("正在听");
  await callback(s.result("请帮我打开实时翻译。"));
  expect(s.api.act).toHaveBeenCalledExactlyOnceWith("open2");
  expect(s.nav.state.mode).toBe(2); expect(s.nav.state.screen).toBe("feature");
  expect(speech.abort).toHaveBeenCalledOnce();
  await callback(s.result("地图"));
  expect(s.api.act).toHaveBeenCalledTimes(1);
  expect(s.win.fetch).not.toHaveBeenCalled();
  expect(s.status.textContent).toContain("已执行：实时翻译");
});

it('requires explicit browser speech consent and shares stop state across visible entries',async()=>{
  const s=setup();
  delete s.win.SpeechRecognition.prototype.processLocally;
  s.win.SpeechRecognition.available.mockResolvedValue('unavailable');
  const main=s.doc.getElementById('hw-intent-voice'),top=s.doc.getElementById('hw-toolbar-voice'),browser=s.doc.getElementById('hw-speech-browser');
  main.click();await settle();
  expect(s.sessions).toHaveLength(0);
  browser.click();await settle();
  expect(s.sessions).toHaveLength(1);
  expect(s.sessions[0].processLocally).not.toBe(true);
  expect(s.win.SpeechRecognition.available).toHaveBeenCalledTimes(1);
  for(const button of [main,top,s.voice]) expect(button.getAttribute('aria-pressed')).toBe('true');
  const late=s.sessions[0].onresult;
  top.click();await late(s.result('打开地图'));
  expect(s.api.act).not.toHaveBeenCalled();
  expect(s.sessions[0].abort).toHaveBeenCalledOnce();
  for(const button of [main,top,s.voice]) expect(button.getAttribute('aria-pressed')).toBe('false');
  browser.click();expect(browser.textContent).toContain('允许');
  expect(s.sessions).toHaveLength(1);
});

it('offers browser speech on engines with no local APIs, without starting it automatically',async()=>{
  const s=setup('browser');
  const main=s.doc.getElementById('hw-intent-voice'),browser=s.doc.getElementById('hw-speech-browser');
  main.click();await settle();
  expect(s.sessions).toHaveLength(0);
  expect(s.doc.getElementById('hw-intent-result').textContent).toContain('不支持本地中文语音');
  expect(browser.hidden).toBe(false);
  browser.click();await settle();
  expect(s.sessions).toHaveLength(1);
  expect(s.sessions[0].processLocally).not.toBe(true);
  await s.sessions[0].onresult(s.result('打开地图'));
  expect(s.nav.state).toMatchObject({screen:'feature',mode:3});
});

it('runs commands inside feature panels and leaves microphone-intensive actions as menu selections',async()=>{
  const s=setup(),top=s.doc.getElementById('hw-toolbar-voice'),input=s.doc.getElementById('hw-intent-text');
  const speak=async text=>{top.click();await settle();await s.sessions.at(-1).onresult(s.result(text));};
  await speak('帮我打开地图');
  expect(s.nav.state).toMatchObject({screen:'feature',mode:3});
  s.doc.getElementById('hw-nav-menu').hidden=true;s.win.LensNavigation.refresh();
  expect(input.disabled).toBe(false);expect(top.disabled).toBe(false);
  await speak('请返回上一页');
  expect(s.nav.state.screen).toBe('actions');
  await speak('切到翻译模式');
  expect(s.nav.state).toMatchObject({screen:'feature',mode:2});
  await speak('放大镜片');
  expect(s.doc.getElementById('hw-view-detail').getAttribute('aria-pressed')).toBe('true');
  expect(input.value).toBe('放大镜片');
  expect(s.status.textContent).toContain('已执行：放大镜片');
  await speak('返回主菜单');
  expect(s.nav.state.screen).toBe('home');
  await speak('打开会议纪要');
  expect(s.nav.state).toMatchObject({screen:'actions',mode:1});
  expect(s.win.fetch).not.toHaveBeenCalled();
});

it('previews interim speech without navigation and pauses translation before acquiring the mic',async()=>{
  const s=setup(),pauseSpeech=vi.fn();s.win.LensFeatures={translation:{pauseSpeech}};
  s.doc.getElementById('hw-toolbar-voice').click();await settle();
  expect(pauseSpeech).toHaveBeenCalledOnce();
  await s.sessions[0].onresult(s.result('向右转',false));
  expect(s.doc.getElementById('hw-intent-result').textContent).toContain('正在听：向右转');
  expect(s.doc.querySelector('.hw-optical-model').style.getPropertyValue('--lens-yaw')).toBe('-18deg');
  await s.sessions[0].onresult(s.result('向右转一点'));
  expect(s.doc.querySelector('.hw-optical-model').style.getPropertyValue('--lens-yaw')).toBe('-6deg');
  expect(s.win.fetch).not.toHaveBeenCalled();
});

it('ignores an old availability rejection and times out a check that never completes',async()=>{
  vi.useFakeTimers();
  const s=setup();let reject;
  s.win.SpeechRecognition.available.mockImplementationOnce(()=>new Promise((_,fail)=>{reject=fail;}));
  s.voice.click();s.voice.click();s.voice.click();await settle();
  reject(new Error('late'));await settle();
  expect(s.sessions[0].abort).not.toHaveBeenCalled();
  expect(s.voice.getAttribute('aria-pressed')).toBe('true');
  s.voice.click();
  s.win.SpeechRecognition.available.mockImplementation(()=>new Promise(()=>{}));
  s.voice.click();vi.advanceTimersByTime(12001);
  expect(s.voice.getAttribute('aria-pressed')).toBe('false');
  expect(s.status.textContent).toContain('聆听已结束');
});

it('does not claim success when the device rejects a command to the already-selected screen',async()=>{
  const s=setup();s.api.act.mockResolvedValue(false);
  s.voice.click();await settle();await s.sessions[0].onresult(s.result('返回主菜单'));
  expect(s.nav.state.screen).toBe('home');
  expect(s.doc.getElementById('hw-command-feedback').textContent).toContain('当前无法执行');
});

it('does not start or fall back to remote speech when the local language is absent',async()=>{
  const s=setup();s.win.SpeechRecognition.available.mockResolvedValue('unavailable');
  s.voice.click();await settle();expect(s.sessions).toHaveLength(0);expect(s.win.fetch).not.toHaveBeenCalled();
  expect(s.status.textContent).toContain('尚无本地中文语音包');
  s.win.SpeechRecognition.available.mockResolvedValue('downloadable');s.voice.click();await settle();
  const install=s.doc.getElementById('hw-speech-install');expect(install.hidden).toBe(false);
  expect(s.win.SpeechRecognition.install).not.toHaveBeenCalled();
  install.click();await settle();expect(s.win.SpeechRecognition.install).toHaveBeenCalledWith({langs:['zh-CN'],processLocally:true});
  expect(s.sessions).toHaveLength(0);
});

it('requires a real local-only ASR capability and cancels pending availability checks',async()=>{
  const s=setup();let resolve;
  s.win.SpeechRecognition.available.mockImplementation(()=>new Promise(done=>{resolve=done;}));
  s.voice.click();s.voice.click();resolve('available');await settle();expect(s.sessions).toHaveLength(0);
});

it('routes typed compound requests once without executing the first local function',async()=>{
  const s=setup(false),form=s.doc.getElementById('hw-intent-form');
  s.doc.getElementById('hw-intent-text').value='先打开地图然后打开翻译';
  form.dispatchEvent(new s.win.Event('submit',{cancelable:true}));
  form.dispatchEvent(new s.win.Event('submit',{cancelable:true}));await settle();
  expect(s.api.act).not.toHaveBeenCalled();expect(s.win.fetch).toHaveBeenCalledOnce();
  const [url,request]=s.win.fetch.mock.calls[0];expect(url).toBe('/api/intent-cloud');
  expect(JSON.parse(request.body)).toMatchObject({text:'先打开地图然后打开翻译',reason:'multiple_tools',context:[]});
  expect(s.doc.getElementById('hw-intent-result').textContent).toContain('尚未配置');
  expect(s.doc.getElementById('hw-intent-route').textContent).toBe('云端 · 未完成');
});

it('keeps typed independent commands local and only sends the latest related task on follow-up',async()=>{
  const s=setup(false),form=s.doc.getElementById('hw-intent-form'),input=s.doc.getElementById('hw-intent-text');
  const send=async text=>{input.value=text;form.dispatchEvent(new s.win.Event('submit',{cancelable:true}));await settle();};
  for(const text of ['地图','翻译','会议纪要','拍照解题','地图'])await send(text);
  expect(s.win.fetch).not.toHaveBeenCalled();expect(s.api.act).toHaveBeenCalledTimes(5);
  await send('把它翻译成英文');expect(s.win.fetch).toHaveBeenCalledOnce();
  const data=JSON.parse(s.win.fetch.mock.calls[0][1].body);expect(data.reason).toBe('context_dependency');
  expect(data.context).toHaveLength(2);expect(data.context[0].content).toBe('地图');
  s.doc.getElementById('hw-intent-reset').click();await send('把它翻译成英文');expect(s.win.fetch).toHaveBeenCalledOnce();
  expect(s.doc.getElementById('hw-intent-result').textContent).toContain('需要前文');
});

it.each(["不要打开地图", "地图和翻译", "今天的会议还没结束", "未知功能"])("does not navigate for unrelated or ambiguous speech: %s", async text => {
  const s = setup(); s.voice.click(); await settle();
  await s.sessions[0].onresult(s.result(text));
  expect(s.api.act).not.toHaveBeenCalled();
  expect(s.status.textContent).not.toContain("已选择");
  expect(s.sessions[0].abort).toHaveBeenCalledOnce();
});

it("selects submenu actions without starting recording, capture or a native picker", async () => {
  const s = setup(); s.nav.commit(s.nav.plan("scene1")); s.win.LensNavigation.refresh();
  s.voice.click(); await settle(); await s.sessions[0].onresult(s.result("选择导入录音"));
  expect(s.api.act).toHaveBeenCalledExactlyOnceWith("focus1");
  expect(s.nav.state.screen).toBe("actions");
  expect(s.nav.state.focus).toBe(1);
});

it("drops cancelled callbacks and never lets an old end event stop a new session", async () => {
  const s = setup(); s.voice.click(); await settle();
  const first = s.sessions[0], lateResult = first.onresult, lateEnd = first.onend;
  s.voice.click(); await settle(); s.voice.click(); await settle();
  await lateResult(s.result("地图")); lateEnd();
  expect(s.api.act).not.toHaveBeenCalled();
  expect(s.sessions[1].abort).not.toHaveBeenCalled();
  expect(s.voice.getAttribute("aria-pressed")).toBe("true");
});

it("cleans up on timeout, permission errors and leaving the menu", async () => {
  vi.useFakeTimers();
  const s = setup(); s.voice.click(); await settle();
  vi.advanceTimersByTime(12001);
  expect(s.sessions[0].abort).toHaveBeenCalledOnce();
  expect(s.status.textContent).toContain("聆听已结束");
  s.voice.click(); await settle(); s.sessions[1].onerror({error: "not-allowed"});
  expect(s.status.textContent).toContain("权限未开启");
  s.voice.click(); await settle(); const late = s.sessions[2].onresult;
  s.unavailable(); s.doc.getElementById("hw-nav-menu").hidden = true; s.win.LensNavigation.refresh();
  await late(s.result("地图"));
  expect(s.sessions[2].abort).toHaveBeenCalledOnce(); expect(s.api.act).not.toHaveBeenCalled();
});

it("aborts speech on hidden pages and page exit", async () => {
  const s = setup(); s.voice.click(); await settle();
  Object.defineProperty(s.doc, "hidden", {configurable: true, value: true});
  s.doc.dispatchEvent(new s.win.Event("visibilitychange"));
  expect(s.sessions[0].abort).toHaveBeenCalledOnce();
  Object.defineProperty(s.doc, "hidden", {configurable: true, value: false});
  s.voice.click(); await settle(); s.win.dispatchEvent(new s.win.Event("pagehide"));
  expect(s.sessions[1].abort).toHaveBeenCalledOnce();
});

it("keeps manual navigation available when speech recognition is unsupported", () => {
  const s = setup(false);
  expect(s.voice.disabled).toBe(true);
  expect(s.status.textContent).toContain("不支持本地语音");
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
