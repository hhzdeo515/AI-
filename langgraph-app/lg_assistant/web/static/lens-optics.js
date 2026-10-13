/* Interactive optical schematic. Camera preview stays local and never records. */
(() => {
  "use strict";
  const host = document.getElementById("hardware-demo");
  const optics = host?.querySelector(".hw-optics");
  if (!optics) return;
  const viewport = optics.querySelector(".hw-viewport");
  const frame = optics.querySelector(".hw-lens-frame");
  const stage = document.createElement("div");
  stage.id = "hw-optical-stage";
  stage.className = "hw-optical-stage";
  stage.tabIndex = 0;
  stage.setAttribute("role", "group");
  stage.setAttribute("aria-label", "可旋转缩放的眼镜，方向键旋转，加减号缩放，Home 复位");
  stage.setAttribute("aria-describedby", "hw-optical-help");
  const model = document.createElement("div");
  model.className = "hw-optical-model";
  for (const node of [...optics.children]) {
    if (!node.classList.contains("hw-optics-caption")) model.append(node);
  }
  stage.append(model);
  optics.prepend(stage);
  optics.classList.add("has-waveguide", "has-camera-view", "hide-optical-path");
  const toolbar = document.createElement("div");
  toolbar.className = "hw-optical-toolbar";
  toolbar.innerHTML = `<div class="hw-view-source"><span id="hw-view-source">模拟取景</span><span>眼镜摄像头视角</span></div>
    <div class="hw-optical-actions"><button type="button" id="hw-view-camera" aria-pressed="false">开启摄像头</button><button type="button" id="hw-optical-toggle" aria-pressed="false">光路示意</button></div>`;
  optics.before(toolbar);
  const scene = document.createElement("div");
  scene.className = "hw-world-view";
  scene.innerHTML = `<div class="hw-world-demo" aria-hidden="true"></div><video id="hw-world-video" autoplay playsinline muted hidden aria-label="第一人称摄像头实时画面"></video><div class="hw-world-shade"></div>`;
  viewport.prepend(scene);
  const cameraHud = document.createElement("div");
  cameraHud.className = "hw-camera-hud";
  cameraHud.innerHTML = `<div class="hw-camera-top"><span class="hw-camera-source"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6l1-2h6l1 2h4v14H4V6z"/><circle cx="12" cy="12" r="4"/></svg><span id="hw-camera-source-label">模拟取景 · 示例画面</span></span><span id="hw-camera-feed-label">未开启摄像头</span></div><div class="hw-camera-focus" aria-hidden="true"><i></i><i></i><i></i><i></i><span>＋</span></div>`;
  viewport.append(cameraHud);
  const light = document.createElement("div");
  light.className = "hw-waveguide";
  light.setAttribute("aria-hidden", "true");
  light.innerHTML = `<div class="hw-grating hw-grating-in"></div><div class="hw-grating hw-grating-out"></div>
    <svg class="hw-light-path" viewBox="0 0 800 540" preserveAspectRatio="none" fill="none">
      <path class="hw-light-halo" d="M20 170 L76 98 L140 52 L218 102 L296 52 L374 102 L452 52 L530 102 L608 52 L690 108 L739 180 L700 245 L742 310 L699 375 L668 428"/>
      <path class="hw-light-ray" d="M20 170 L76 98 L140 52 L218 102 L296 52 L374 102 L452 52 L530 102 L608 52 L690 108 L739 180 L700 245 L742 310 L699 375 L668 428"/>
      <path class="hw-light-output" d="M708 236 L650 236 M730 270 L670 270 M724 304 L652 304 M712 338 L664 338"/>
      <circle cx="76" cy="98" r="5"/><circle cx="699" cy="375" r="4"/>
    </svg><span class="hw-optical-tag hw-tag-in">入耦</span><span class="hw-optical-tag hw-tag-out">出耦 · 成像</span>`;
  viewport.append(light);
  const emitter = document.createElement("span");
  emitter.className = "hw-projector";
  emitter.setAttribute("aria-hidden", "true");
  frame.append(emitter);
  const caption = optics.querySelector(".hw-optics-caption");
  caption.innerHTML = `<div><span class="hw-optical-eyebrow">CAMERA VIEW</span><strong>眼前画面，随时取景</strong><span id="hw-optical-help">拖动镜框或空白处旋转 · 滚轮缩放 · 双击空白处复位</span></div>
    <div class="hw-view-controls"><button type="button" id="hw-view-out" aria-label="缩小眼镜">−</button><output id="hw-view-scale" aria-label="眼镜缩放比例">100%</output><button type="button" id="hw-view-in" aria-label="放大眼镜">＋</button><button type="button" id="hw-view-reset">复位</button></div>`;
  const legend = document.createElement("div");
  legend.className = "hw-optical-legend";
  legend.hidden = true;
  legend.innerHTML = `<span><i></i>光机入耦</span><span>↗ 波导内反射</span><span>▥ 表面浮雕光栅出耦</span><small>光路与光栅为放大示意</small>`;
  optics.append(legend);
  const status = document.createElement("p");
  status.id = "hw-view-status";
  status.className = "hw-view-status";
  status.setAttribute("role", "status");
  status.textContent = "当前为写实取景示例。开启摄像头后，镜片显示你的实时画面。";
  optics.append(status);
  const $ = id => document.getElementById(id);
  const video = $("hw-world-video"), cameraButton = $("hw-view-camera");
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  let pitch = -3, yaw = -8, zoom = 1, drag = null, suppressClick = false;
  let stream = null, opening = false, generation = 0, captureActive = false;
  // Reading and form controls keep their normal click, selection and scroll behavior.
  const interactive = target => target.closest("button,a,input,textarea,select,label,summary,video,audio,[role=slider],.hw-panel-scroll,#hw-result-body,.hw-question-nav,.hw-map-canvas,#hw-nav-options");
  const update = () => {
    model.style.setProperty("--lens-pitch", `${pitch}deg`);
    model.style.setProperty("--lens-yaw", `${yaw}deg`);
    model.style.setProperty("--lens-zoom", zoom);
    model.style.setProperty("--glint-x", `${50 + yaw * .8}%`);
    model.style.setProperty("--world-x", `${-yaw * .45}px`);
    model.style.setProperty("--world-y", `${pitch * .35}px`);
    $("hw-view-scale").textContent = `${Math.round(zoom * 100)}%`;
    $("hw-view-out").disabled = zoom <= .7;
    $("hw-view-in").disabled = zoom >= 1.35;
  };
  function setZoom(value) { zoom = clamp(value, .7, 1.35); update(); }
  function reset() { pitch = -3; yaw = -8; zoom = 1; update(); }
  function endDrag(event) {
    if (!drag || (event && event.pointerId !== drag.id)) return;
    const id = drag.id;
    suppressClick = drag.moved && event?.type === "pointerup";
    drag = null;
    stage.classList.remove("is-dragging");
    if (stage.hasPointerCapture?.(id)) stage.releasePointerCapture(id);
  }
  stage.addEventListener("pointerdown", event => {
    if (event.button !== 0 || drag) return;
    suppressClick = false;
    if (interactive(event.target)) return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, pitch, yaw, moved: false};
    stage.setPointerCapture?.(event.pointerId);
    stage.classList.add("is-dragging");
    stage.focus({preventScroll: true});
    event.preventDefault();
  });
  stage.addEventListener("pointermove", event => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.hypot(dx, dy) > 4) drag.moved = true;
    yaw = clamp(drag.yaw + dx * .15, -38, 38);
    pitch = clamp(drag.pitch - dy * .12, -25, 25);
    update();
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach(type => stage.addEventListener(type, endDrag));
  stage.addEventListener("click", event => {
    if (suppressClick) { event.preventDefault(); event.stopPropagation(); suppressClick = false; }
  }, true);
  stage.addEventListener("wheel", event => {
    if (event.target.closest("input,textarea,select,video,audio,[role=slider],.hw-panel-scroll,#hw-result-body,.hw-question-nav") || event.ctrlKey) return;
    event.preventDefault();
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1);
    setZoom(zoom * Math.exp(-clamp(delta, -200, 200) * .0015));
  }, {passive: false});
  stage.addEventListener("dblclick", event => { if (!interactive(event.target)) reset(); });
  stage.addEventListener("keydown", event => {
    if (event.target !== stage) return;
    switch (event.key) {
      case "ArrowLeft": yaw = clamp(yaw - 3, -38, 38); break;
      case "ArrowRight": yaw = clamp(yaw + 3, -38, 38); break;
      case "ArrowUp": pitch = clamp(pitch + 3, -25, 25); break;
      case "ArrowDown": pitch = clamp(pitch - 3, -25, 25); break;
      case "+": case "=": setZoom(zoom + .1); break;
      case "-": setZoom(zoom - .1); break;
      case "Home": reset(); break;
      default: return;
    }
    event.preventDefault(); event.stopPropagation(); update();
  });
  $("hw-view-out").addEventListener("click", () => setZoom(zoom - .1));
  $("hw-view-in").addEventListener("click", () => setZoom(zoom + .1));
  $("hw-view-reset").addEventListener("click", reset);
  $("hw-optical-toggle").addEventListener("click", event => {
    const hidden = optics.classList.toggle("hide-optical-path");
    event.currentTarget.setAttribute("aria-pressed", String(!hidden));
    legend.hidden = hidden;
  });
  function stop(message = "摄像头已关闭，当前为模拟取景示例。") {
    generation++;
    opening = false;
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    video.pause(); video.srcObject = null; video.hidden = true;
    scene.classList.remove("is-live");
    cameraButton.textContent = "开启摄像头";
    cameraButton.setAttribute("aria-pressed", "false");
    $("hw-view-source").textContent = "模拟取景";
    $("hw-camera-source-label").textContent = "模拟取景 · 示例画面";
    $("hw-camera-feed-label").textContent = "未开启摄像头";
    status.textContent = message;
  }
  async function toggleCamera() {
    if (captureActive) return;
    if (stream || opening) { stop(); return; }
    const current = ++generation;
    opening = true;
    cameraButton.textContent = "取消开启";
    status.textContent = "请允许摄像头访问，画面仅在当前浏览器预览。";
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("unsupported");
      const acquired = await navigator.mediaDevices.getUserMedia({audio: false, video: {facingMode: {ideal: "environment"}, width: {ideal: 1920}, height: {ideal: 1080}}});
      if (current !== generation) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired;
      acquired.getVideoTracks().forEach(track => track.addEventListener("ended", () => {
        if (stream === acquired) stop("摄像头已断开，已恢复模拟取景示例。可重新开启摄像头。");
      }));
      video.srcObject = acquired;
      await video.play();
      if (current !== generation) return;
      opening = false; video.hidden = false;
      scene.classList.add("is-live");
      cameraButton.textContent = "关闭摄像头";
      cameraButton.setAttribute("aria-pressed", "true");
      $("hw-view-source").textContent = "摄像头实时视野";
      $("hw-camera-source-label").textContent = "摄像头 · 实时画面";
      $("hw-camera-feed-label").textContent = "本地预览";
      status.textContent = "摄像头已开启 · 仅本地预览，不录制、不上传。";
    } catch (error) {
      if (current !== generation) return;
      stop(error.name === "NotAllowedError" ? "摄像头权限未开启，当前为模拟取景示例。允许权限后可重试。" : "摄像头暂不可用，当前为模拟取景示例。检查设备后可重试。");
    }
  }
  cameraButton.addEventListener("click", toggleCamera);
  window.LensOptics = {stop, setCaptureActive(active) {
    captureActive = active;
    if (active) stop("正在拍照解题，摄像头由拍照窗口使用。结束后可重新开启实时视野。");
    cameraButton.disabled = active;
  }};
  window.addEventListener("pagehide", () => { endDrag(); stop(); });
  window.addEventListener("blur", () => endDrag());
  update();
})();
