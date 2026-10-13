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
  dom = new JSDOM(`<div id="hardware-demo">${markup}</div>`, {url: "https://assistant.example/#hardware", runScripts: "outside-only"});
  win = dom.window; doc = win.document;
  win.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue();
  win.HTMLMediaElement.prototype.pause = vi.fn();
  media = vi.fn();
  Object.defineProperty(win.navigator, "mediaDevices", {value: {getUserMedia: media}});
  win.eval(source("lens-optics.js"));
  stage = doc.getElementById("hw-optical-stage");
  model = doc.querySelector(".hw-optical-model");
  camera = doc.getElementById("hw-view-camera");
});
afterEach(() => { win.LensOptics.stop(); dom.window.close(); });

it("does not request camera access until clicked and releases it on stop", async () => {
  expect(media).not.toHaveBeenCalled();
  const stream = feed(); media.mockResolvedValue(stream);
  camera.click(); await flush();
  expect(media.mock.calls[0][0].audio).toBe(false);
  expect(doc.getElementById("hw-world-video").srcObject).toBe(stream);
  expect(doc.getElementById("hw-world-video").hidden).toBe(false);
  expect(camera.getAttribute("aria-pressed")).toBe("true");
  camera.click();
  expect(stream.track.stop).toHaveBeenCalledOnce();
  expect(doc.getElementById("hw-world-video").srcObject).toBeNull();
  expect(doc.getElementById("hw-world-video").hidden).toBe(true);
});

it("releases late permission streams without replacing a newer preview", async () => {
  let firstResolve;
  const first = feed(), second = feed();
  media.mockImplementationOnce(() => new Promise(resolve => { firstResolve = resolve; })).mockResolvedValueOnce(second);
  camera.click(); camera.click(); camera.click(); await flush();
  firstResolve(first); await flush();
  expect(first.track.stop).toHaveBeenCalledOnce();
  expect(second.track.stop).not.toHaveBeenCalled();
  expect(doc.getElementById("hw-world-video").srcObject).toBe(second);
});

it("keeps the fallback usable after permission refusal and releases video on playback failure", async () => {
  media.mockRejectedValueOnce(new win.DOMException("denied", "NotAllowedError"));
  camera.click(); await flush();
  expect(doc.getElementById("hw-view-status").textContent).toContain("权限未开启");
  expect(camera.textContent).toBe("开启摄像头");
  const stream = feed(); media.mockResolvedValueOnce(stream);
  win.HTMLMediaElement.prototype.play.mockRejectedValueOnce(new Error("play failed"));
  camera.click(); await flush();
  expect(stream.track.stop).toHaveBeenCalledOnce();
  expect(doc.getElementById("hw-world-video").hidden).toBe(true);
});

it("hands the camera to photo capture and cancels pending preview requests on navigation", async () => {
  const stream = feed(); media.mockResolvedValueOnce(stream);
  camera.click(); await flush();
  win.LensOptics.setCaptureActive(true);
  expect(stream.track.stop).toHaveBeenCalledOnce();
  expect(camera.disabled).toBe(true);
  camera.click(); expect(media).toHaveBeenCalledTimes(1);
  win.LensOptics.setCaptureActive(false);
  expect(camera.disabled).toBe(false);
  let resolve;
  media.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  camera.click(); win.dispatchEvent(new win.Event("pagehide"));
  const late = feed(); resolve(late); await flush();
  expect(late.track.stop).toHaveBeenCalledOnce();
  expect(doc.getElementById("hw-world-video").hidden).toBe(true);
});

it("falls back when a camera track disconnects", async () => {
  const stream = feed(); media.mockResolvedValue(stream);
  camera.click(); await flush();
  stream.track.dispatchEvent(new win.Event("ended"));
  expect(doc.getElementById("hw-view-source").textContent).toBe("模拟视野");
  expect(doc.getElementById("hw-view-status").textContent).toContain("已断开");
});

it("rotates with bounded drag, cancels cleanly and leaves controls clickable", () => {
  pointer("pointerdown", stage, 100, 100);
  pointer("pointermove", stage, 1000, 1000);
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("38deg");
  expect(model.style.getPropertyValue("--lens-pitch")).toBe("-25deg");
  pointer("pointercancel", stage, 1000, 1000);
  expect(stage.classList.contains("is-dragging")).toBe(false);
  pointer("pointermove", stage, 0, 0);
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("38deg");
  const button = doc.getElementById("hw-camera-shoot");
  pointer("pointerdown", button, 0, 0);
  pointer("pointermove", stage, 100, 100);
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("38deg");
  const click = new win.MouseEvent("click", {bubbles: true, cancelable: true});
  button.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(false);
  doc.getElementById("hw-view-reset").click();
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("-8deg");
});

it("zooms on the lens while preserving answer scrolling, form controls and browser pinch zoom", () => {
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
  stage.dispatchEvent(new win.KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("-5deg");
  stage.dispatchEvent(new win.KeyboardEvent("keydown", {key: "Home", bubbles: true}));
  expect(model.style.getPropertyValue("--lens-yaw")).toBe("-8deg");
  const button = doc.getElementById("hw-optical-toggle"); button.click();
  expect(button.getAttribute("aria-pressed")).toBe("false");
  expect(doc.querySelector(".hw-optics").classList.contains("hide-optical-path")).toBe(true);
});
