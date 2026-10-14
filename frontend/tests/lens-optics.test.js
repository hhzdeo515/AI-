// @vitest-environment node
import {readFileSync} from "node:fs";
import {JSDOM} from "jsdom";
import {afterEach, beforeEach, expect, it, vi} from "vitest";

const source = file => readFileSync(new URL(`../../langgraph-app/lg_assistant/web/static/${file}`, import.meta.url), "utf8");
const markup = source("hardware.js").match(/host\.innerHTML = `([\s\S]+?)`;/)[1];
let dom, win, doc, camera, stage, model, media;
const flush = async () => { await new Promise(setImmediate); };
function feed() {
  const track = new win.EventTarget();
  track.stop = vi.fn();
  return {track, getTracks: () => [track], getVideoTracks: () => [track]};
}
function pointer(type, target, x, y, id = 1) {
  const event = new win.Event(type, {bubbles: true, cancelable: true});
  Object.assign(event, {button: 0, pointerId: id, clientX: x, clientY: y});
  target.dispatchEvent(event);
  return event;
}
beforeEach(() => {
  dom = new JSDOM(`<div id="hardware-demo">${markup}</div>`, {url: "https://assistant.example/#hardware", runScripts: "outside-only", pretendToBeVisual: true});
  win = dom.window; doc = win.document;
  win.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue();
  win.HTMLMediaElement.prototype.pause = vi.fn();
  media = vi.fn().mockRejectedValue(new win.DOMException("missing", "NotFoundError"));
  Object.defineProperty(win.navigator, "mediaDevices", {value: {getUserMedia: media}});
});
function boot() {
  win.eval(source("lens-optics.js"));
  stage = doc.getElementById("hw-optical-stage");
  model = doc.querySelector(".hw-optical-model");
  camera = doc.getElementById("hw-view-camera");
}
afterEach(() => { win.LensOptics.stop(); dom.window.close(); });

it("automatically opens the actual camera on entry and releases it on manual stop", async () => {
  const stream = feed(); media.mockResolvedValue(stream);
  boot();
  expect(media).not.toHaveBeenCalled();
  expect(doc.querySelector(".hw-world-demo")).toBeNull();
  expect(doc.getElementById("hw-camera-feed-label").textContent).toBe("未开启摄像头");
  await flush();
  expect(media.mock.calls[0][0].audio).toBe(false);
  expect(media.mock.calls[0][0].video.facingMode.ideal).toBe("environment");
  expect(doc.getElementById("hw-world-video").srcObject).toBe(stream);
  expect(doc.getElementById("hw-companion-video").srcObject).toBe(stream);
  expect(doc.getElementById("hw-companion-video").hidden).toBe(false);
  expect(media).toHaveBeenCalledTimes(1);
  expect(doc.getElementById("hw-world-video").hidden).toBe(false);
  expect(camera.getAttribute("aria-pressed")).toBe("true");
  expect(doc.getElementById("hw-camera-source-label").textContent).toBe("摄像头 · 实时画面");
  camera.click();
  expect(stream.track.stop).toHaveBeenCalledOnce();
  expect(doc.getElementById("hw-world-video").srcObject).toBeNull();
  expect(doc.getElementById("hw-companion-video").srcObject).toBeNull();
  expect(doc.getElementById("hw-companion-video").hidden).toBe(true);
  expect(doc.getElementById("hw-world-video").hidden).toBe(true);
  expect(doc.getElementById("hw-camera-source-label").textContent).toContain("未连接");
  doc.dispatchEvent(new win.Event("visibilitychange")); await flush();
  expect(media).toHaveBeenCalledTimes(1);
});

it("releases late permission streams without replacing a newer preview", async () => {
  let firstResolve;
  const first = feed(), second = feed();
  media.mockImplementationOnce(() => new Promise(resolve => { firstResolve = resolve; })).mockResolvedValueOnce(second);
  boot();
  camera.click(); camera.click(); camera.click(); await flush();
  firstResolve(first); await flush();
  expect(first.track.stop).toHaveBeenCalledOnce();
  expect(second.track.stop).not.toHaveBeenCalled();
  expect(doc.getElementById("hw-world-video").srcObject).toBe(second);
});

it("shows permission errors without a fake image and releases video on playback failure", async () => {
  media.mockRejectedValueOnce(new win.DOMException("denied", "NotAllowedError"));
  boot();
  camera.click(); await flush();
  expect(doc.getElementById("hw-view-status").textContent).toContain("权限未开启");
  expect(camera.textContent).toBe("开启摄像头");
  expect(doc.getElementById("hw-camera-empty-note").textContent).toContain("权限未开启");
  expect(doc.querySelector(".hw-world-demo")).toBeNull();
  const stream = feed(); media.mockResolvedValueOnce(stream);
  win.HTMLMediaElement.prototype.play.mockRejectedValueOnce(new Error("play failed"));
  camera.click(); await flush();
  expect(stream.track.stop).toHaveBeenCalledOnce();
  expect(doc.getElementById("hw-world-video").hidden).toBe(true);
});

it("hands the camera to photo capture and cancels pending preview requests on navigation", async () => {
  const stream = feed(); media.mockResolvedValueOnce(stream);
  boot();
  camera.click(); await flush();
  win.LensOptics.setCaptureActive(true);
  expect(stream.track.stop).toHaveBeenCalledOnce();
  expect(camera.disabled).toBe(true);
  camera.click(); expect(media).toHaveBeenCalledTimes(1);
  let resolve;
  media.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  win.LensOptics.setCaptureActive(false);
  expect(camera.disabled).toBe(false);
  await flush();
  win.dispatchEvent(new win.Event("pagehide"));
  const late = feed(); resolve(late); await flush();
  expect(late.track.stop).toHaveBeenCalledOnce();
  expect(doc.getElementById("hw-world-video").hidden).toBe(true);
});

it("shows an empty view when a camera track disconnects", async () => {
  const stream = feed(); media.mockResolvedValue(stream);
  boot();
  camera.click(); await flush();
  stream.track.dispatchEvent(new win.Event("ended"));
  expect(doc.getElementById("hw-view-source").textContent).toBe("摄像头未连接");
  expect(doc.getElementById("hw-view-status").textContent).toContain("已断开");
});

it("switches real video devices and releases the old feed", async () => {
  const first = feed(), second = feed();
  first.track.getSettings = () => ({deviceId: "front"});
  second.track.getSettings = () => ({deviceId: "back"});
  win.navigator.mediaDevices.enumerateDevices = vi.fn().mockResolvedValue([
    {kind: "videoinput", deviceId: "front", label: "前置摄像头"},
    {kind: "videoinput", deviceId: "back", label: "后置摄像头"}
  ]);
  media.mockResolvedValueOnce(first).mockResolvedValueOnce(second);
  boot();
  await flush();
  const select = doc.getElementById("hw-view-device");
  expect(select.hidden).toBe(false);
  expect(select.value).toBe("front");
  select.value = "back";
  select.dispatchEvent(new win.Event("change")); await flush();
  expect(first.track.stop).toHaveBeenCalledOnce();
  expect(media.mock.calls[1][0]).toMatchObject({audio: false, video: {deviceId: {exact: "back"}}});
  expect(doc.getElementById("hw-world-video").srcObject).toBe(second);
  expect(select.value).toBe("back");
});

it("waits for the hardware view, pauses when hidden and reconnects when visible", async () => {
  win.history.replaceState(null, "", "#memory");
  boot();
  await flush();
  expect(media).not.toHaveBeenCalled();
  const first = feed(), second = feed();
  media.mockResolvedValueOnce(first).mockResolvedValueOnce(second);
  win.history.replaceState(null, "", "#hardware");
  win.dispatchEvent(new win.Event("hashchange")); await flush();
  expect(media).toHaveBeenCalledTimes(1);
  Object.defineProperty(doc, "hidden", {value: true, configurable: true});
  doc.dispatchEvent(new win.Event("visibilitychange"));
  expect(first.track.stop).toHaveBeenCalledOnce();
  Object.defineProperty(doc, "hidden", {value: false, configurable: true});
  doc.dispatchEvent(new win.Event("visibilitychange")); await flush();
  expect(doc.getElementById("hw-world-video").srcObject).toBe(second);
});

it("does not reopen the preview when photo capture closes during page exit", async () => {
  boot();
  win.LensOptics.setCaptureActive(true);
  await flush();
  win.LensOptics.setCaptureActive(false);
  win.dispatchEvent(new win.Event("pagehide"));
  await flush();
  expect(media).not.toHaveBeenCalled();
});

it("rotates with bounded drag, cancels cleanly and leaves controls clickable", () => {
  boot();
  pointer("pointerdown", stage, 100, 100);
  pointer("pointermove", stage, 1000, 1000);
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("75deg");
  expect(model.style.getPropertyValue("--lens-pitch")).toBe("-40deg");
  pointer("pointercancel", stage, 1000, 1000);
  expect(stage.classList.contains("is-dragging")).toBe(false);
  pointer("pointermove", stage, 0, 0);
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("75deg");
  const button = doc.getElementById("hw-camera-shoot");
  pointer("pointerdown", button, 0, 0);
  pointer("pointermove", stage, 100, 100);
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("75deg");
  const click = new win.MouseEvent("click", {bubbles: true, cancelable: true});
  button.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(false);
  doc.getElementById("hw-view-reset").click();
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("-18deg");
});

it("zooms on the lens while preserving answer scrolling, form controls and browser pinch zoom", () => {
  boot();
  const wheel = (target, deltaY, extra = {}) => {
    const event = new win.WheelEvent("wheel", {bubbles: true, cancelable: true, deltaY, ...extra});
    target.dispatchEvent(event); return event;
  };
  expect(wheel(stage, -200).defaultPrevented).toBe(true);
  expect(Number(model.style.getPropertyValue("--lens-zoom"))).toBeGreaterThan(1);
  expect(wheel(doc.getElementById("hw-hide-result"), -5).defaultPrevented).toBe(true);
  const scale = model.style.getPropertyValue("--lens-zoom");
  expect(wheel(doc.getElementById("hw-result-body"), 200).defaultPrevented).toBe(false);
  expect(wheel(doc.getElementById("hw-question-select"), 200).defaultPrevented).toBe(false);
  expect(wheel(stage, 200, {ctrlKey: true}).defaultPrevented).toBe(false);
  expect(model.style.getPropertyValue("--lens-zoom")).toBe(scale);
  for (let i = 0; i < 10; i++) wheel(stage, 200);
  expect(doc.getElementById("hw-view-scale").textContent).toBe("70%");
  expect(doc.getElementById("hw-view-out").disabled).toBe(true);
});

it("provides keyboard rotation, reset and an optical layer toggle", () => {
  boot();
  stage.dispatchEvent(new win.KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("-15deg");
  stage.dispatchEvent(new win.KeyboardEvent("keydown", {key: "Home", bubbles: true}));
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("-18deg");
  const button = doc.getElementById("hw-optical-toggle");
  expect(button.getAttribute("aria-pressed")).toBe("false");
  expect(doc.querySelector(".hw-optics").classList.contains("hide-optical-path")).toBe(true);
  expect(doc.querySelector(".hw-optical-legend").hidden).toBe(true);
  button.click();
  expect(button.getAttribute("aria-pressed")).toBe("true");
  expect(doc.querySelector(".hw-optics").classList.contains("hide-optical-path")).toBe(false);
  expect(doc.querySelector(".hw-optical-legend").hidden).toBe(false);
});

it("keeps the whole glasses assembly together when dragging and changing viewing scale", () => {
  boot();
  const parts = [".hw-primary-frame", ".hw-secondary-frame", ".hw-bridge", ".hw-temple-left", ".hw-temple-right"];
  const nodes = parts.map(selector => doc.querySelector(selector));
  nodes.forEach(node => expect(node.closest(".hw-optical-model")).toBe(model));
  pointer("pointerdown", nodes[4], 100, 100);
  pointer("pointermove", stage, 200, 150);
  pointer("pointerup", stage, 200, 150);
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("4deg");
  const button = doc.getElementById("hw-view-detail");
  button.click();
  expect(button.getAttribute("aria-pressed")).toBe("true");
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("0deg");
  expect(model.style.getPropertyValue("--glasses-offset")).toBe("280px");
  button.click();
  expect(button.getAttribute("aria-pressed")).toBe("false");
  expect(model.style.getPropertyValue("--glasses-offset")).toBe("0px");
  nodes.forEach((node, index) => expect(doc.querySelector(parts[index])).toBe(node));
});
