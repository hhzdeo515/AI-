/* One complete glasses assembly; both lenses share a local-only camera stream. */
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
  frame.classList.add("hw-primary-frame");
  const secondFrame = document.createElement("div");
  secondFrame.className = "hw-lens-frame hw-secondary-frame";
  secondFrame.setAttribute("aria-hidden", "true");
  secondFrame.innerHTML = `<div class="hw-secondary-glass"><video id="hw-companion-video" autoplay playsinline muted hidden></video><span class="hw-lens-etch">WAVEGUIDE / R</span></div>`;
  model.append(secondFrame);
  const hardware = document.createElement("div");
  hardware.className = "hw-glasses-hardware";
  hardware.setAttribute("aria-hidden", "true");
  hardware.innerHTML = `<span class="hw-nose-pad hw-nose-pad-left"></span><span class="hw-nose-pad hw-nose-pad-right"></span><span class="hw-hinge-right"></span><span class="hw-frame-camera"></span>
    <div class="hw-temple hw-temple-left"><span class="hw-temple-outer">LENS / AI</span><span class="hw-temple-inner"></span><span class="hw-temple-top"></span><span class="hw-temple-bottom"></span><span class="hw-temple-tip"></span></div>
    <div class="hw-temple hw-temple-right"><span class="hw-temple-outer">LENS / AI</span><span class="hw-temple-inner"></span><span class="hw-temple-top"></span><span class="hw-temple-bottom"></span><span class="hw-temple-tip"></span></div>`;
  model.append(hardware);
  stage.append(model);
  optics.prepend(stage);
  optics.classList.add("has-waveguide", "has-camera-view", "has-full-glasses", "hide-optical-path");
  const toolbar = document.createElement("div");
  toolbar.className = "hw-optical-toolbar";
  toolbar.innerHTML = `<div class="hw-view-source"><span id="hw-view-source">等待摄像头</span><span>眼镜摄像头视角</span></div>
    <div class="hw-optical-actions"><button type="button" id="hw-view-detail" aria-pressed="false">放大镜片</button><select id="hw-view-device" aria-label="选择取景摄像头" hidden><option value="">自动选择摄像头</option></select><button type="button" id="hw-view-camera" aria-pressed="false">开启摄像头</button><button type="button" id="hw-optical-toggle" aria-pressed="false">光路示意</button></div>`;
  optics.before(toolbar);
  const scene = document.createElement("div");
  scene.className = "hw-world-view";
  scene.innerHTML = `<video id="hw-world-video" autoplay playsinline muted hidden aria-label="第一人称摄像头实时画面"></video><div class="hw-camera-empty"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6l1-2h6l1 2h4v14H4V6z"/><circle cx="12" cy="12" r="4"/></svg><strong id="hw-camera-empty-title">等待摄像头画面</strong><span id="hw-camera-empty-note">请允许浏览器访问摄像头</span></div><div class="hw-world-shade"></div>`;
  viewport.prepend(scene);
  const cameraHud = document.createElement("div");
  cameraHud.className = "hw-camera-hud";
  cameraHud.innerHTML = `<div class="hw-camera-top"><span class="hw-camera-source"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6l1-2h6l1 2h4v14H4V6z"/><circle cx="12" cy="12" r="4"/></svg><span id="hw-camera-source-label">摄像头 · 等待连接</span></span><span id="hw-camera-feed-label">未开启摄像头</span></div><div class="hw-camera-focus" aria-hidden="true"><i></i><i></i><i></i><i></i><span>＋</span></div>`;
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
  caption.innerHTML = `<div><span class="hw-optical-eyebrow">SMART GLASSES</span><strong>整副眼镜，自由转动</strong><span id="hw-optical-help">拖动镜框、镜腿或空白处旋转整副眼镜 · 滚轮缩放 · 双击复位</span></div>
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
  status.textContent = "正在连接摄像头。允许访问后，镜片会显示摄像头前方的实时画面。";
  optics.append(status);
  const $ = id => document.getElementById(id);
  const video = $("hw-world-video"), companionVideo = $("hw-companion-video"), cameraButton = $("hw-view-camera");
  const previewVideos = [video, companionVideo];
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  let pitch = -24, yaw = -18, zoom = 1, detail = false, drag = null, suppressClick = false;
  let stream = null, opening = false, generation = 0, captureActive = false, previewWanted = true, pageActive = true;
  const deviceSelect = $("hw-view-device");
  const visible = () => pageActive && location.hash !== "#memory" && !document.hidden;
  // Reading and form controls keep their normal click, selection and scroll behavior.
  const interactive = target => target.closest("button,a,input,textarea,select,label,summary,video,audio,[role=slider],.hw-panel-scroll,#hw-result-body,.hw-question-nav,.hw-map-canvas,#hw-nav-options");
  let realGlasses = null;
  const update = () => {
    const real = optics.classList.contains("has-real-glasses");
    model.style.setProperty("--lens-pitch", `${pitch}deg`);
    model.style.setProperty("--lens-yaw", `${yaw}deg`);
    model.style.setProperty("--lens-zoom", zoom);
    const fit = stage.clientWidth > 0 ? Math.min(1, stage.clientWidth / (detail ? (real ? 620 : 570) : (real ? 1600 : 1240))) : .65;
    model.style.setProperty("--glasses-scale", fit * zoom);
    const offset = detail ? (real ? 340 : 280) : 0;
    model.style.setProperty("--glasses-offset", `${offset}px`);
    realGlasses?.setView({pitch, yaw, scale: fit * zoom, offset});
    model.style.setProperty("--glint-x", `${50 + yaw * .8}%`);
    model.style.setProperty("--world-x", `${-yaw * .45}px`);
    model.style.setProperty("--world-y", `${pitch * .35}px`);
    $("hw-view-scale").textContent = `${Math.round(zoom * 100)}%`;
    $("hw-view-out").disabled = zoom <= .7;
    $("hw-view-in").disabled = zoom >= 1.35;
  };
  function setZoom(value) { zoom = clamp(value, .7, 1.35); update(); }
  function reset() { pitch = detail ? 0 : -24; yaw = detail ? 0 : -18; zoom = 1; update(); }
  $("hw-view-detail").addEventListener("click", event => {
    detail = !detail;
    event.currentTarget.setAttribute("aria-pressed", String(detail));
    event.currentTarget.textContent = detail ? "查看整副眼镜" : "放大镜片";
    optics.classList.toggle("is-lens-detail", detail);
    reset();
  });
  const resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null;
  resizeObserver?.observe(stage);
  window.addEventListener("resize", update);
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
    yaw = clamp(drag.yaw + dx * .22, -75, 75);
    pitch = clamp(drag.pitch - dy * .18, -40, 40);
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
      case "ArrowLeft": yaw = clamp(yaw - 3, -75, 75); break;
      case "ArrowRight": yaw = clamp(yaw + 3, -75, 75); break;
      case "ArrowUp": pitch = clamp(pitch + 3, -40, 40); break;
      case "ArrowDown": pitch = clamp(pitch - 3, -40, 40); break;
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
  function stop(message = "摄像头已暂停。点击“开启摄像头”恢复实时画面。") {
    generation++;
    opening = false;
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    previewVideos.forEach(preview => { preview.pause(); preview.srcObject = null; preview.hidden = true; });
    scene.classList.remove("is-live");
    cameraButton.textContent = "开启摄像头";
    cameraButton.setAttribute("aria-pressed", "false");
    $("hw-view-source").textContent = "摄像头未连接";
    $("hw-camera-source-label").textContent = "摄像头 · 未连接";
    $("hw-camera-feed-label").textContent = "未开启摄像头";
    $("hw-camera-empty-title").textContent = "暂无摄像头画面";
    $("hw-camera-empty-note").textContent = message;
    status.textContent = message;
  }
  async function startCamera() {
    if (captureActive || stream || opening || !visible()) return;
    const current = ++generation;
    opening = true;
    cameraButton.textContent = "取消开启";
    $("hw-view-source").textContent = "正在连接摄像头";
    $("hw-camera-empty-title").textContent = "正在打开摄像头";
    $("hw-camera-empty-note").textContent = "请在浏览器提示中允许摄像头访问";
    status.textContent = "请允许摄像头访问，画面仅在当前浏览器预览。";
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("unsupported");
      const acquired = await navigator.mediaDevices.getUserMedia({audio: false, video: {...(deviceSelect.value ? {deviceId: {exact: deviceSelect.value}} : {facingMode: {ideal: "environment"}}), width: {ideal: 1920}, height: {ideal: 1080}}});
      if (current !== generation) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired;
      acquired.getVideoTracks().forEach(track => track.addEventListener("ended", () => {
        if (stream === acquired) stop("摄像头已断开，请检查设备后重新开启。");
      }));
      previewVideos.forEach(preview => { preview.srcObject = acquired; });
      await Promise.all(previewVideos.map(preview => preview.play()));
      if (current !== generation) return;
      opening = false;
      previewVideos.forEach(preview => { preview.hidden = false; });
      scene.classList.add("is-live");
      cameraButton.textContent = "关闭摄像头";
      cameraButton.setAttribute("aria-pressed", "true");
      $("hw-view-source").textContent = "摄像头实时视野";
      $("hw-camera-source-label").textContent = "摄像头 · 实时画面";
      $("hw-camera-feed-label").textContent = "本地预览";
      status.textContent = "摄像头已开启 · 仅本地预览，不录制、不上传。";
      // Permission has been granted, so browsers may now expose device labels.
      try {
        const devices = (await navigator.mediaDevices.enumerateDevices?.() || []).filter(device => device.kind === "videoinput" && device.deviceId);
        if (current !== generation) return;
        const selected = acquired.getVideoTracks()[0]?.getSettings?.().deviceId || deviceSelect.value;
        deviceSelect.replaceChildren(new Option("自动选择摄像头", ""));
        devices.forEach((device,index) => deviceSelect.add(new Option(device.label || `摄像头 ${index + 1}`, device.deviceId)));
        deviceSelect.value = devices.some(device => device.deviceId === selected) ? selected : "";
        deviceSelect.hidden = devices.length < 2;
      } catch (_) { /* Device selection is optional; keep the working stream. */ }
    } catch (error) {
      if (current !== generation) return;
      const messages = {NotAllowedError: "摄像头权限未开启。请允许浏览器访问，然后点击“开启摄像头”。", NotFoundError: "未找到摄像头，请连接摄像头后重试。", NotReadableError: "摄像头无法读取，可能被其他应用占用，请关闭占用后重试。", OverconstrainedError: "所选摄像头不可用，请切换摄像头后重试。"};
      stop(messages[error.name] || "摄像头暂不可用。请检查设备，并使用支持摄像头的浏览器打开此 HTTPS 页面。");
    }
  }
  cameraButton.addEventListener("click", () => {
    if (captureActive) return;
    if (stream || opening) { previewWanted = false; stop("摄像头已关闭。点击“开启摄像头”恢复实时画面。"); }
    else { previewWanted = true; void startCamera(); }
  });
  deviceSelect.addEventListener("change", () => { previewWanted = true; stop("正在切换摄像头…"); void startCamera(); });
  window.LensOptics = {stop, setCaptureActive(active) {
    const wasActive = captureActive;
    captureActive = active;
    if (active) stop("正在拍照解题，摄像头由拍照窗口使用。结束后恢复实时视野。");
    cameraButton.disabled = active;
    deviceSelect.disabled = active;
    if (wasActive && !active) queueMicrotask(() => { if (previewWanted && visible()) void startCamera(); });
  }};
  const syncVisibility = () => {
    if (!visible()) { if (stream || opening) stop(); }
    else if (previewWanted) void startCamera();
  };
  window.addEventListener("hashchange", syncVisibility);
  document.addEventListener("visibilitychange", syncVisibility);
  window.addEventListener("pagehide", () => { pageActive = false; endDrag(); stop(); });
  window.addEventListener("pageshow", () => { pageActive = true; syncVisibility(); });
  window.addEventListener("blur", () => endDrag());
  // The physical mesh and interactive lens surfaces use one view state.
  realGlasses = window.SmartGlasses?.mount(stage, {
    onReady() {
      optics.classList.add("has-real-glasses");
      caption.querySelector("strong").textContent = "石墨黑 · 智能眼镜";
      update();
    },
    onUnavailable() {
      optics.classList.remove("has-real-glasses");
      caption.querySelector("strong").textContent = "整副眼镜 · 兼容显示";
      update();
    }
  });
  update();
  queueMicrotask(syncVisibility);
})();
