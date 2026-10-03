(() => {
  const $ = id => document.getElementById(id);
  const button = $("meeting-record-toggle"), status = $("meeting-record-status");
  let stream = null, recorder = null, chunks = [], file = null, url = "", token = 0;
  let phase = "off", timer = null, started = 0, size = 0, reason = "";
  function tracksOff() { if (stream) stream.getTracks().forEach(t => t.stop()); stream = null; }
  function display() {
    window.AssistantModel?.setBusy(phase !== "off", "recording");
    button.textContent = phase === "opening" ? "取消开启" : phase === "recording" ? "关闭录音" : phase === "stopping" ? "正在保存…" : "开启录音";
    button.disabled = phase === "stopping";
    $("meeting-record-delete").disabled = phase !== "off" || !file;
    button.setAttribute("aria-pressed", String(phase === "recording"));
    if (phase === "recording") status.textContent = `正在录音 · ${Math.floor((Date.now() - started) / 1000)} 秒 · 点击「关闭录音」停止`;
  }
  function stop(message = "录音已关闭，麦克风已释放。") {
    token++; reason = message; clearInterval(timer); timer = null;
    if (recorder && recorder.state !== "inactive") {
      phase = "stopping"; recorder.stop(); tracksOff(); display();
    } else { tracksOff(); phase = "off"; status.textContent = message; display(); }
  }
  async function start() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      status.textContent = "当前浏览器不支持录音，请使用本机 localhost 或 HTTPS，或上传已有录音。"; return;
    }
    const ticket = ++token; phase = "opening"; display(); status.textContent = "等待麦克风权限；可点击取消开启。";
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({audio:true,video:false});
      if (ticket !== token) { acquired.getTracks().forEach(t => t.stop()); return; }
      stream = acquired;
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find(t => MediaRecorder.isTypeSupported(t));
      if (!mime) throw new Error("没有支持的录音格式");
      recorder = new MediaRecorder(stream, {mimeType:mime, audioBitsPerSecond:64000});
      chunks = []; size = 0; reason = "";
      recorder.addEventListener("dataavailable", event => {
        if (event.data.size) { chunks.push(event.data); size += event.data.size; }
        if (size >= 28 * 1024 * 1024 && phase === "recording") stop("已达到单段录音上限，录音已关闭。请提交或下载后再录下一段。");
      });
      recorder.addEventListener("stop", () => {
        clearInterval(timer); timer = null; tracksOff();
        if (chunks.length) {
          const ext = mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "webm";
          file = new File(chunks, `meeting-${Date.now()}.${ext}`, {type:mime});
          if (url) URL.revokeObjectURL(url);
          url = URL.createObjectURL(file);
          $("meeting-record-player").src = url;
          $("meeting-record-download").href = url;
          $("meeting-record-download").download = file.name;
          $("meeting-record-preview").hidden = false;
        }
        chunks = []; recorder = null; phase = "off";
        status.textContent = (reason || "录音已关闭，麦克风已释放。") + (file ? " 可试听后提交转写，尚未上传。" : " 未采集到录音，请重试。"); display();
      });
      recorder.addEventListener("error", () => stop("录音发生异常，已关闭麦克风。"));
      stream.getAudioTracks().forEach(track => track.addEventListener("ended", () => { if (phase === "recording") stop("麦克风已断开，录音已关闭。"); }));
      recorder.start(1000); phase = "recording"; started = Date.now(); display();
      timer = setInterval(() => { if (Date.now() - started >= 60 * 60 * 1000) stop("已录满60分钟，自动关闭。请保存本段后继续。"); else display(); }, 1000);
    } catch (_) {
      if (ticket !== token) return;
      tracksOff(); phase = "off"; display(); status.textContent = "无法开启麦克风，请检查权限和设备，或上传已有录音。";
    }
  }
  button.addEventListener("click", () => phase === "off" ? start() : stop());
  $("meeting-record-use").addEventListener("click", () => {
    if (phase !== "off") { status.textContent = "请先关闭录音，再提交。"; return; }
    if (file) document.dispatchEvent(new CustomEvent("meeting-recorded", {detail:file}));
  });
  $("meeting-record-delete").addEventListener("click", () => {
    if (phase !== "off" || !file) return;
    if (!window.confirm("删除当前页面的录音？删除后无法恢复，已下载或已提交的副本不受影响。")) return;
    const player = $("meeting-record-player");
    player.pause();
    player.removeAttribute("src");
    player.load();
    if (url) URL.revokeObjectURL(url);
    url = ""; file = null; chunks = []; size = 0;
    $("meeting-record-download").removeAttribute("href");
    $("meeting-record-download").removeAttribute("download");
    $("meeting-record-preview").hidden = true;
    status.textContent = "本页录音已删除，可以重新开启录音。";
    display();
    button.focus();
  });
  window.addEventListener("hashchange", () => { if (location.hash !== "#meeting" && phase !== "off") stop("已离开会议页面，录音已关闭。"); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && phase !== "off") stop("页面已进入后台，录音已关闭。"); });
  window.addEventListener("pagehide", () => { stop(); if (url) URL.revokeObjectURL(url); });
  window.MeetingRecorder = {stop, isActive:() => phase !== "off"};
})();
