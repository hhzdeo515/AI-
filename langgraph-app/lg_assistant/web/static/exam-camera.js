/* Camera access starts only from an explicit click. No microphone requested. */
(() => {
  const $ = id => document.getElementById(id);
  const dialog = $("exam-camera-dialog"), video = $("exam-camera-video");
  const shoot = $("exam-camera-shoot"), silent = $("exam-camera-silent");
  const preferenceKey = "assistant.exam.camera.silent";
  let stream = null, generation = 0, audio = null;
  try { silent.checked = localStorage.getItem(preferenceKey) !== "false"; } catch (_) {}
  function note() {
    $("exam-camera-note").textContent = silent.checked
      ? "已关闭应用内拍照提示音。系统相机快门声由设备控制。"
      : "拍照时播放轻提示音。系统相机快门声由设备控制。";
  }
  function stopSound() { if (audio) { audio.close().catch(() => {}); audio = null; } }
  silent.addEventListener("change", () => {
    if (silent.checked) stopSound();
    note();
    try { localStorage.setItem(preferenceKey, String(silent.checked)); }
    catch (_) { $("exam-camera-note").textContent += " 当前浏览器无法保存设置，本次仍然有效。"; }
  });
  function stop() {
    generation++;
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null; video.srcObject = null; shoot.disabled = true;
  }
  function close() { stop(); if (dialog.open) dialog.close(); }
  function sound() {
    if (silent.checked) return;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) return;
      stopSound(); const ctx = audio = new Audio();
      const oscillator = ctx.createOscillator(), gain = ctx.createGain();
      oscillator.frequency.value = 880; gain.gain.value = 0.035;
      oscillator.connect(gain); gain.connect(ctx.destination);
      oscillator.start(); oscillator.stop(ctx.currentTime + 0.08);
      oscillator.onended = () => { ctx.close().catch(() => {}); if (audio === ctx) audio = null; };
    } catch (_) { /* A blocked sound must never prevent taking a photo. */ }
  }
  async function open() {
    if (dialog.open) return;
    stop(); dialog.showModal();
    const ticket = generation;
    $("exam-camera-status").textContent = "正在打开摄像头…";
    if (!navigator.mediaDevices?.getUserMedia) {
      $("exam-camera-status").textContent = "当前浏览器无法打开摄像头。请使用本机 localhost 或 HTTPS，或返回后从相册选择照片。";
      return;
    }
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({audio:false, video:{facingMode:{ideal:"environment"}, width:{ideal:1920}, height:{ideal:1440}}});
      if (ticket !== generation || !dialog.open) { acquired.getTracks().forEach(t => t.stop()); return; }
      stream = acquired; video.srcObject = acquired;
      acquired.getVideoTracks().forEach(track => track.addEventListener("ended", () => {
        if (ticket !== generation) return;
        stop(); $("exam-camera-status").textContent = "摄像头已断开，请关闭后重新拍照。";
      }));
      await video.play();
      if (ticket !== generation) return;
      shoot.disabled = !video.videoWidth;
      $("exam-camera-status").textContent = silent.checked ? "静音拍照已开启。" : "拍照提示音已开启。";
    } catch (error) {
      if (ticket !== generation) return;
      stop();
      $("exam-camera-status").textContent = error.name === "NotAllowedError"
        ? "摄像头权限未开启。请在浏览器中允许访问，或返回后从相册选择照片。"
        : "摄像头不可用，可能未连接或被其他应用占用。请检查后重试，或从相册选择照片。";
    }
  }
  video.addEventListener("loadeddata", () => { if (stream && dialog.open && video.videoWidth) shoot.disabled = false; });
  shoot.addEventListener("click", () => {
    if (!stream || shoot.disabled || !video.videoWidth) return;
    shoot.disabled = true;
    const ticket = generation, canvas = document.createElement("canvas");
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    try {
      canvas.getContext("2d").drawImage(video, 0, 0);
      canvas.toBlob(blob => {
        if (ticket !== generation || !dialog.open) return;
        if (!blob) { shoot.disabled = false; $("exam-camera-status").textContent = "照片生成失败，请重新拍摄。"; return; }
        sound();
        const file = new File([blob], `question-${Date.now()}.jpg`, {type:"image/jpeg"});
        close(); document.dispatchEvent(new CustomEvent("exam-photo", {detail:file}));
      }, "image/jpeg", 0.95);
    } catch (_) { shoot.disabled = false; $("exam-camera-status").textContent = "照片生成失败，请重新拍摄。"; }
  });
  $("exam-camera-open").addEventListener("click", open);
  $("exam-camera-close").addEventListener("click", close);
  dialog.addEventListener("close", stop);
  dialog.addEventListener("cancel", close);
  window.addEventListener("pagehide", () => { close(); stopSound(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { close(); stopSound(); } });
  window.ExamCamera = {open, close};
  note();
})();
