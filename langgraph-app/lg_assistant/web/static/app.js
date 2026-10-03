/* ═══════════════════════════════════════════════════════════════════════
   assistant-lite · Glasses Companion — Web UI
   只调用真实存在的后端接口，不做前端伪造状态。
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  /* ── 常量 ───────────────────────────────────────────────────────── */
  const IMAGE_EXT = /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i;
  const AUDIO_EXT = /\.(wav|mp3|m4a|aac|flac|ogg|amr|wma|webm)$/i;
  const SCENE_LABEL = { meeting: "会议纪要", exam: "题解", general: "资料" };

  const VIEW_META = {
    home: "首页",
    meeting: "会议纪要",
    vision: "拍照解题",
    memory: "资料库",
    hardware: "眼镜视野",
  };

  /* ── 状态 ───────────────────────────────────────────────────────── */
  const S = {
    view: "hardware",
    owner: "local",
    session: "web",
    attached: [],
    busy: false,
    health: null,
    meeting: { status: "idle", transcript: "", id: "" },
    meetingStage: "",           // "" | "structuring" | "done"
    meetingHadAudio: false,
    meetingElapsed: 0,          // 已结算的秒数（暂停时累加）
    meetingStartedAt: null,     // 当前计时片段起点；暂停/结束时置空
    memoryFilter: "",
  };

  /* ── DOM 小工具 ─────────────────────────────────────────────────── */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* ── 极简 Markdown 渲染（后端返回的是 markdown-ish 文本） ─────────── */
  function inlineMd(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  }

  function renderMd(text) {
    const lines = String(text || "").split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const raw = lines[i];
      const line = raw.trim();
      i++;

      if (!line) continue;
      if (/^---+$/.test(line)) { out.push("<hr>"); continue; }

      // 表格
      if (line.startsWith("|") && i < lines.length && /^\|?[\s:|-]+\|?$/.test(lines[i].trim())) {
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        let html = "<table><thead><tr>" +
          cells(line).map((c) => "<th>" + inlineMd(c) + "</th>").join("") + "</tr></thead><tbody>";
        i++;
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          html += "<tr>" + cells(lines[i]).map((c) => "<td>" + inlineMd(c) + "</td>").join("") + "</tr>";
          i++;
        }
        out.push(html + "</tbody></table>");
        continue;
      }

      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { out.push("<h" + Math.min(h[1].length + 1, 4) + ">" + inlineMd(h[2]) + "</h" + Math.min(h[1].length + 1, 4) + ">"); continue; }

      if (/^[-*+]\s+/.test(line)) {
        const items = [];
        while (i <= lines.length) {
          const cur = i <= lines.length ? (lines[i - 1] || "").trim() : "";
          if (!/^[-*+]\s+/.test(cur)) break;
          items.push("<li>" + inlineMd(cur.replace(/^[-*+]\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ul>" + items.join("") + "</ul>");
        continue;
      }

      if (/^\d+[.)]\s+/.test(line)) {
        const items = [];
        while (i <= lines.length) {
          const cur = (lines[i - 1] || "").trim();
          if (!/^\d+[.)]\s+/.test(cur)) break;
          items.push("<li>" + inlineMd(cur.replace(/^\d+[.)]\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ol>" + items.join("") + "</ol>");
        continue;
      }

      if (line.startsWith(">")) { out.push("<blockquote>" + inlineMd(line.replace(/^>\s?/, "")) + "</blockquote>"); continue; }

      out.push("<p>" + inlineMd(line) + "</p>");
    }
    return out.join("");
  }

  function speechHtml(spoken, text) {
    // 播报块已从界面移除（用户要求）。
    //
    // 原因不只是「用不上」：屏幕上出现一条被单独高亮的「播报」，会让人把它
    // 当成经过核实的结论。实测出现过模型把「二十届三中全会尚未召开」这种
    // 明显过期的判断放进播报语——屏幕上多一个强调框，就等于多一次把错误
    // 信息说得很确定的机会。语音播报是眼镜端的事，网页不需要预览。
    //
    // Reply.speech 字段仍保留并返回（设备端要用），只是不再渲染。
    return "";
  }

  /* ── 语音合成播放（眼镜端由设备自己播，这里是网页预览） ─────────── */
  let speechAudio = null;
  let speechBtn = null;

  function stopSpeech() {
    if (speechAudio) { speechAudio.pause(); speechAudio = null; }
    if (speechBtn) { speechBtn.textContent = "播报"; speechBtn = null; }
  }

  async function toggleSpeech(text, btn) {
    if (speechBtn === btn && speechAudio) { stopSpeech(); return; }
    stopSpeech();
    const url =
      "/api/speak?owner=" + encodeURIComponent(S.owner) +
      "&session_id=" + encodeURIComponent(S.session) +
      "&text=" + encodeURIComponent(text);
    speechAudio = new Audio(url);
    speechBtn = btn;
    btn.textContent = "停止播报";
    speechAudio.addEventListener("ended", stopSpeech);
    speechAudio.addEventListener("error", () => {
      stopSpeech();
      toast("语音合成失败");
    });
    try {
      await speechAudio.play();
    } catch (e) {
      stopSpeech();
      toast("播放失败：" + e.message);
    }
  }

  function resultHtml(text, extra, spoken) {
    return speechHtml(spoken, text) +
      '<div class="md">' + renderMd(text) + "</div>" + (extra || "");
  }

  /* ── API ────────────────────────────────────────────────────────── */
  async function apiJson(path, opts) {
    const requestOptions = Object.assign({}, opts);
    // Only the synchronous model request may legitimately hold a connection
    // open for minutes. Submission, polling and state reads must settle so
    // their caller can release the UI lock even if the service stops replying.
    const timeoutMs = requestOptions.timeoutMs ?? (path === "/api/chat" ? 0 : 15000);
    delete requestOptions.timeoutMs;
    const controller = timeoutMs > 0 ? new AbortController() : null;
    if (controller) requestOptions.signal = controller.signal;
    const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    try {
      const res = await fetch(path, requestOptions);
      let data = null;
      try { data = await res.json(); }
      catch (e) { if (controller?.signal.aborted) throw e; }
      if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
      if (data === null) throw new Error("服务未返回有效数据，请稍后重试");
      return data;
    } catch (e) {
      if (controller?.signal.aborted) {
        throw new Error("本地服务响应超时；任务可能仍在后台处理，请先查看资料库中的结果");
      }
      // fetch 自己对网络中断只会说 "Failed to fetch"，用户看不懂。
      // 本地服务被重启时就会这样（异步任务表在内存里，重启即丢）。
      if (e instanceof TypeError) throw new Error("连不上本地服务（可能刚重启或已退出），请重试");
      throw e;
    } finally {
      if (timer !== null) clearTimeout(timer);
    }
  }

  function form(opts) {
    const fd = new FormData();
    fd.append("owner", S.owner);
    fd.append("session_id", opts.session_id || S.session);
    if (opts.text != null) fd.append("text", opts.text);
    if (opts.scene) fd.append("scene", opts.scene);
    fd.append("exam_backend", opts.exam_backend || window.AssistantModel.id);
    const event = Object.assign({}, opts.event || {});
    if (opts.scene === "exam" && window.Exam) event.exam = window.Exam.options();
    if (Object.keys(event).length) fd.append("event", JSON.stringify(event));
    // request_id 必须回传：否则进度记录挂在后端自己生成的 id 上，
    // 前端拿着另一个 id 去轮询 /api/progress，永远只能拿到空进度。
    if (opts.request_id) fd.append("request_id", opts.request_id);
    (opts.files || []).forEach((f) => fd.append("files", f, f.name));
    return fd;
  }

  function send(opts) {
    return apiJson("/api/chat", { method: "POST", body: form(opts) });
  }

  // 长耗时场景用：提交后立刻拿到 task_id，再轮询结果。
  // 一次拍题要 30–60 秒（四次模型调用），同步请求会把界面挂住——
  // 用户看到的就是「点了发送没反应」。所以带附件一律走异步 + 轮询。
  async function sendAsync(opts) {
    const started = await apiJson("/api/chat/async", { method: "POST", body: form(opts) });
    const tid = started.task_id;
    const rid = opts.request_id || started.request_id || "";
    let stopPoll = null;
    if (opts.onProgress && rid) stopPoll = pollProgress(rid, opts.onProgress);
    try {
      // A photograph may contain many questions. Keep waiting for this same task
      // while the server reports that it is pending/running; never resubmit it.
      while (true) {
        await sleep(600);
        const rec = await apiJson("/api/task?task_id=" + encodeURIComponent(tid) + "&owner=" + encodeURIComponent(S.owner));
        if (rec.status === "done") return rec.result;
        if (rec.status === "error") throw new Error(rec.error || "任务失败");
        if (rec.status !== "pending" && rec.status !== "running") throw new Error("任务状态无法确认，请查看资料库中的结果或稍后重试");
      }
    } finally {
      if (stopPoll) stopPoll();
    }
  }

  /* ── Toast ──────────────────────────────────────────────────────── */
  let toastTimer = null;
  function toast(msg, ms) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, ms || 2600);
  }

  /* ── 视图路由 ───────────────────────────────────────────────────── */
  function urlView() {
    return location.hash === "#memory" ? "memory" : "hardware";
  }

  function go(view, fromUrl) {
    view = view === "memory" ? "memory" : "hardware";
    if (view !== "hardware" && window.MeetingRecorder?.isActive()) window.MeetingRecorder.stop("已离开眼镜视野，录音已关闭。");
    S.view = view;
    const target = location.pathname + location.search + "#" + view;
    if (location.hash !== "#" + view) {
      if (fromUrl) history.replaceState(null, "", target);
      else location.hash = view;
    }
    $$(".view").forEach((v) => v.classList.toggle("is-on", v.dataset.view === view));
    $$(".topbar [data-go]").forEach((b) => {
      const active = b.dataset.go === view;
      b.classList.toggle("is-on", active);
      b.setAttribute("aria-current", active ? "page" : "false");
    });
    $("#crumb").textContent = VIEW_META[view];
    $("#crumb").hidden = view === "hardware";
    $("#stage").scrollTop = 0;
    $("#live-host").innerHTML = "";
    // 解题页的输入框就是「拍题框」，提示语跟着场景走
    $("#input").placeholder = view === "vision"
      ? "补充要求（可选）"
      : "输入内容";
    showVeil(false);
    stopSpeech();

    if (view === "vision" && window.Exam) window.Exam.refresh(S.owner);
    if (view === "memory") loadMemory();
    if (view === "meeting") renderMeeting();
    // 切到带状态卡片的视图时补拉一次会话状态。
    // 不拉的话卡片会停在初始值（WORKOUT 恒显示 IDLE），
    // 只有在本页提交过请求才会更新——刷新或重新进入就看不到了。
    if (view === "meeting") refreshState();
    renderContext();
  }

  /* ── 状态指示 ───────────────────────────────────────────────────── */
  function applyHealth(h) {
    S.health = h;
    window.AssistantModel.setReady(h.jev_ready);
    const on = !!h.api_key_configured;
    const chips = {
      vision: true,
      audio: true,
      assistant: on,
    };
    $$("#chips .chip").forEach((c) => {
      const ok = chips[c.dataset.chip];
      c.classList.toggle("is-off", !ok);
    });

  }

  /* ── Pipeline ───────────────────────────────────────────────────── */
  function renderPipeline(host, steps, opts) {
    if (!host) return;
    if (!steps || !steps.length) { host.hidden = true; host.innerHTML = ""; return; }
    host.hidden = false;

    const showNum = !!(opts && opts.numbered);
    const active = steps.some((s) => s.state === "active");

    host.innerHTML = steps.map((s, i) => {
      const mark = s.state === "done"
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5.5 5.5L20 7"/></svg>'
        : "";
      const num = showNum ? '<span class="step-num">' + String(i + 1).padStart(2, "0") + "</span>" : "";
      return '<div class="step is-' + s.state + '">' + num +
        '<span class="step-mark">' + mark + "</span>" +
        '<span class="step-label">' + esc(s.label) + "</span></div>";
    }).join("") + (active ? '<div class="scanline"><i></i></div>' : "");
  }

  function pollProgress(rid, onUpdate) {
    let stopped = false;
    (async function tick() {
      if (stopped) return;
      try {
        const p = await apiJson("/api/progress?request_id=" + encodeURIComponent(rid));
        if (p && (p.batch || p.steps && p.steps.length)) onUpdate(p);
      } catch (e) { /* 轮询失败不打断主流程 */ }
      if (!stopped) setTimeout(tick, 520);
    })();
    return function stop() { stopped = true; };
  }

  /* ── Meeting ────────────────────────────────────────────────────── */
  function meetingSteps() {
    const hasText = !!(S.meeting.transcript || "").length;
    const stage = S.meetingStage;
    const capturedLabel = S.meetingHadAudio ? "已收录音" : "已收记录";

    const steps = [
      { id: "captured", label: capturedLabel },
      { id: "transcript", label: "转写完成" },
      { id: "structuring", label: "整理纪要" },
      { id: "summary", label: "纪要完成" },
    ];

    let level = 0;                       // 0..4，已完成的步数
    if (stage === "done") level = 4;
    else if (stage === "structuring") level = 2;
    else if (hasText) level = 1;
    else if (S.meeting.status === "collecting") level = 0;

    return steps.map((s, i) => ({
      id: s.id,
      label: s.label,
      state: i < level ? "done" : (i === level ? (level === 4 ? "done" : "active") : "pending"),
    }));
  }

  function renderMeeting() {
    const m = S.meeting;
    const collecting = m.status === "collecting";
    const paused = m.status === "paused";
    const live = collecting || paused;   // 暂停仍属于"会话还在"，面板不收起
    const panel = $("#meeting-session");
    const badge = $("#meeting-badge");

    $("#meeting-start").hidden = live;
    $("#meeting-stop").hidden = !live;
    $("#meeting-pause").hidden = !live;
    $("#meeting-pause").textContent = paused ? "恢复文字记录" : "暂停文字记录";
    panel.hidden = !live;

    if (live) {
      badge.textContent = paused ? "记录已暂停" : "文字记录中";
      badge.className = "badge " + (paused ? "is-paused" : "is-live");
      const ta = $("#meeting-transcript");
      if (document.activeElement !== ta) ta.value = m.transcript || "";
      updateMeetingCount();
      if (collecting && !S.meetingStartedAt) S.meetingStartedAt = Date.now();
      if (paused && S.meetingStartedAt) {
        // 进入暂停：把这一段时长结算掉，之后不再增长
        S.meetingElapsed += (Date.now() - S.meetingStartedAt) / 1000;
        S.meetingStartedAt = null;
      }
    } else {
      S.meetingStartedAt = null;
      S.meetingElapsed = 0;
      if (m.status === "ended") {
        badge.textContent = "已结束";
        badge.className = "badge is-idle";
      }
    }

    const monitor = $("#meeting-monitor");
    monitor.hidden = true; // Text collection is not microphone recording.
    monitor.classList.toggle("is-paused", paused);
    $("#mono-state").textContent = paused ? "记录已暂停" : "文字记录";
    stopMeetingTimer();
    if (paused) paintMeetingTime();

    const hasAny = live || S.meetingStage;
    renderPipeline($("#meeting-pipeline"), hasAny ? meetingSteps() : null);
  }

  function updateMeetingCount() {
    const v = $("#meeting-transcript").value;
    $("#meeting-count").textContent = v.length + " 字";
  }

  let meetingTimer = null;

  // 已计时秒数 = 之前片段累加 + 当前片段；暂停后 meetingStartedAt 为空，数字不再增长
  function meetingSeconds() {
    const running = S.meetingStartedAt ? (Date.now() - S.meetingStartedAt) / 1000 : 0;
    return Math.floor(S.meetingElapsed + running);
  }

  function paintMeetingTime() {
    const total = meetingSeconds();
    $("#mono-time").textContent =
      String(Math.floor(total / 60)).padStart(2, "0") + ":" +
      String(total % 60).padStart(2, "0");
  }

  function startMeetingTimer() {
    if (meetingTimer) return;
    paintMeetingTime();
    meetingTimer = setInterval(paintMeetingTime, 1000);
  }

  function stopMeetingTimer() {
    if (meetingTimer) { clearInterval(meetingTimer); meetingTimer = null; }
  }

  /* 资料库 */
  async function loadMemory() {
    const host = $("#memory-list");
    host.innerHTML = '<div class="empty">读取中…</div>';
    let rows = [];
    try {
      const q = S.memoryFilter ? "&scene=" + encodeURIComponent(S.memoryFilter) : "";
      const data = await apiJson("/api/resources?owner=" + encodeURIComponent(S.owner) + q);
      rows = data.resources || [];
    } catch (e) {
      host.innerHTML = '<div class="empty">读取失败：' + esc(e.message) + "</div>";
      return;
    }

    if (!rows.length) {
      host.innerHTML = '<div class="empty">还没有归档资料。<br>生成会议纪要或题解后会自动出现在这里。</div>';
      return;
    }

    const groups = {};
    rows.forEach((r) => {
      const day = (r.created || "").slice(0, 10);
      (groups[day] = groups[day] || []).push(r);
    });

    host.innerHTML = Object.keys(groups).sort().reverse().map((day) =>
      '<p class="mem-day">' + esc(day) + "</p>" +
      groups[day].map((r) =>
        '<button class="mem-row" data-id="' + esc(r.id) + '">' +
        iconFor(r.scene) +
        '<span class="mem-main"><span class="mem-title">' + esc(r.title) + "</span>" +
        '<span class="mem-meta">' + esc(SCENE_LABEL[r.scene] || r.scene) + " · " + r.size + " 字</span></span>" +
        '<span class="mem-id">' + esc(r.id.slice(0, 8)) + "</span></button>"
      ).join("")
    ).join("");

    $$(".mem-row", host).forEach((b) => {
      b.addEventListener("click", () => openResource(b.dataset.id));
    });
  }

  function iconFor(scene) {
    const paths = {
      meeting: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
      exam: '<path d="M4 8V5.6A1.6 1.6 0 0 1 5.6 4H8"/><path d="M16 4h2.4A1.6 1.6 0 0 1 20 5.6V8"/><path d="M20 16v2.4a1.6 1.6 0 0 1-1.6 1.6H16"/><path d="M8 20H5.6A1.6 1.6 0 0 1 4 18.4V16"/><circle cx="12" cy="12" r="3"/>',
    };
    return '<svg class="mem-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round">' + (paths[scene] || paths.general || paths.meeting) + "</svg>";
  }

  async function openResource(id) {
    const host = $("#memory-detail");
    host.hidden = false;
    host.innerHTML = '<div class="empty">读取中…</div>';
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const r = await apiJson("/api/resource?owner=" + encodeURIComponent(S.owner) + "&id=" + encodeURIComponent(id));
      const split = r.has_transcript ? splitMeetingBody(r.content || "") : null;
      host.innerHTML = split
        ? '<div class="md">' + renderMd(split.head) + "</div>" +
          '<div class="tr-panel" id="tr-host" data-rid="' + esc(r.id) + '"></div>' +
          exportBar(r.id, r.scene)
        : resultHtml(r.content, exportBar(r.id, r.scene));
      bindExport(host, r.id);
      const trHost = $("#tr-host", host);
      if (trHost) mountTranscriptEditor(trHost, r.id);
    } catch (e) {
      host.innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
    }
  }

  function exportBar(id, scene) {
    const fmts = ["docx", "pdf", "md", "txt"];
    return '<div class="export-bar"><span class="label">导出</span>' +
      fmts.map((f) => '<button class="btn-mini" data-fmt="' + f + '">' + ({docx:"文字文档", pdf:"版式文档", md:"排版文本", txt:"纯文本"})[f] + "</button>").join("") +
      "</div>";
  }

  function bindExport(root, id) {
    $$("[data-fmt]", root).forEach((b) => {
      b.addEventListener("click", () => {
        const fmt = b.dataset.fmt;
        window.location.href = "/api/export?owner=" + encodeURIComponent(S.owner) +
          "&id=" + encodeURIComponent(id) + "&format=" + fmt;
        toast("正在导出");
      });
    });
  }

  /* ── Context Panel ──────────────────────────────────────────────── */
  function renderContext() {
    const body = $("#ctx-body");
    const secs = [];

    if (S.view === "meeting") {
      secs.push(["会议记录", [
        ["状态", ({idle:"未开始", collecting:"记录中", paused:"已暂停", ended:"已结束"})[S.meeting.status] || "未开始"],
        ["会议编号", S.meeting.id ? S.meeting.id.slice(0, 8) : "—"],
        ["转写", (S.meeting.transcript || "").length + " / 48000 字"],
      ]]);
    } else if (S.view === "vision") {
      secs.push(["附件", [
        ["待发送附件", S.attached.length ? S.attached.map((f) => f.name).join("、") : "—"],
        ["发送方式", "拖入 / Ctrl+V 粘贴 / 点 + 选择"],
      ]]);
    } else if (S.view === "memory") {
      secs.push(["资料库", [
        ["筛选", ({meeting:"会议纪要", exam:"题解"})[S.memoryFilter] || "全部"],
        ["数据目录", S.health ? S.health.data_dir : "—"],
      ]]);
    } else {
      secs.push(["助手", [
        ["文本模型", S.health ? S.health.model_text : "—"],
        ["视觉模型", S.health ? S.health.model_vision : "—"],
        ["语音模型", S.health ? S.health.model_asr : "—"],
      ]]);
    }

    body.innerHTML = secs.map((s) =>
      '<div class="ctx-sec"><h4>' + esc(s[0]) + "</h4>" +
      s[1].map((kv) =>
        '<dl class="kv"><dt>' + esc(kv[0]) + "</dt><dd>" + esc(kv[1]) + "</dd></dl>"
      ).join("") + "</div>"
    ).join("") || '<p class="ctx-empty">暂无上下文</p>';
  }

  /* ── 附件 ───────────────────────────────────────────────────────── */
  // 图片/录音统一挂在底部输入框上：拖入、Ctrl+V 粘贴、点 + 选择，
  // 与拍照结果都汇到 addFiles()，统一确认后发送。
  let fileCallback = null;

  function pickFiles(accept, cb) {
    const input = $("#file-input");
    input.value = "";
    input.accept = accept || "";
    fileCallback = cb || null;
    input.click();
  }

  /** 剪贴板/拖拽给的文件可能没有名字，补一个带正确扩展名的名字 */
  function normalizeFile(f) {
    if (!f) return f;
    if (IMAGE_EXT.test(f.name || "") || AUDIO_EXT.test(f.name || "")) return f;
    const type = f.type || "";
    let ext = "";
    if (type.indexOf("image/") === 0) ext = (type.split("/")[1] || "png").replace("jpeg", "jpg");
    else if (type.indexOf("audio/") === 0) ext = type.split("/")[1] || "mp3";
    if (!ext) return f;
    const base = String(f.name || "clipboard").replace(/\.[^.]*$/, "") || "clipboard";
    try {
      return new File([f], base + "." + ext, { type: type });
    } catch (e) {
      return f;
    }
  }

  function addFiles(files) {
    if (S.view === "hardware" && window.HardwareLens) {
      const media = Array.from(files || []).map(normalizeFile);
      for (const file of media) {
        if (IMAGE_EXT.test(file.name)) window.HardwareLens.acceptImage(file);
        else if (AUDIO_EXT.test(file.name)) window.HardwareLens.acceptAudio(file);
        else toast("不支持的格式：" + (file.name || "未知文件"));
      }
      return media.length;
    }
    if (S.view === "memory") { toast("返回眼镜视野后再导入图片或录音"); return 0; }
    let added = 0;
    Array.prototype.slice.call(files || []).forEach((raw) => {
      const f = normalizeFile(raw);
      if (!IMAGE_EXT.test(f.name) && !AUDIO_EXT.test(f.name)) {
        toast("不支持的格式：" + (f.name || "未知文件"));
        return;
      }
      if (!S.attached.some((x) => x.name === f.name && x.size === f.size)) {
        S.attached.push(f);
        added++;
      }
    });
    renderAttached();
    if (added) {
      toast(S.attached.length + " 个附件已放进输入框 · 按 Enter 或点 → 发送");
      $("#input").focus();
    }
    return added;
  }

  let attachUrls = [];
  let msgUrls = [];

  function renderAttached() {
    const strip = $("#attach-strip");
    const note = $("#attached");
    // 缩略图用的 objectURL 每次重绘都要回收，否则选十张图就漏十份内存
    attachUrls.forEach((u) => URL.revokeObjectURL(u));
    attachUrls = [];

    if (!S.attached.length) {
      strip.hidden = true;
      strip.innerHTML = "";
      note.textContent = "";
      return;
    }

    strip.hidden = false;
    strip.innerHTML = S.attached.map((f, i) => {
      const isImg = IMAGE_EXT.test(f.name);
      let thumb;
      if (isImg) {
        const url = URL.createObjectURL(f);
        attachUrls.push(url);
        thumb = '<img src="' + url + '" alt="">';
      } else {
        thumb = '<span class="attach-file">录音</span>';
      }
      return '<span class="attach-chip">' + thumb +
        '<span class="attach-name">' + esc(f.name) + "</span>" +
        '<button class="attach-x" type="button" data-remove="' + i + '" aria-label="移除">×</button></span>';
    }).join("");

    $$("[data-remove]", strip).forEach((b) => {
      b.addEventListener("click", () => {
        S.attached.splice(Number(b.dataset.remove), 1);
        renderAttached();
      });
    });

    note.textContent = "已就绪 " + S.attached.length + " 个附件 · 按 Enter 或点 → 发送";
  }

  /* ── 拖拽 / 粘贴：落点永远是输入框 ──────────────────────────────── */
  function dtHasFiles(e) {
    const dt = e.dataTransfer;
    if (!dt || !dt.types) return false;
    return Array.prototype.indexOf.call(dt.types, "Files") >= 0;
  }

  function filesFrom(dt) {
    const out = [];
    if (!dt) return out;
    if (dt.files && dt.files.length) {
      Array.prototype.push.apply(out, Array.prototype.slice.call(dt.files));
    } else if (dt.items) {
      Array.prototype.slice.call(dt.items).forEach((it) => {
        if (it.kind === "file") {
          const f = it.getAsFile();
          if (f) out.push(f);
        }
      });
    }
    return out.map(normalizeFile);
  }

  function showVeil(on) {
    const veil = $("#drop-veil");
    if (veil) veil.hidden = !on;
    const box = $("#composer");
    if (box) box.classList.toggle("is-drop", !!on);
  }

  /* ── 结果渲染 ───────────────────────────────────────────────────── */
  function pushLive(html, bindFn) {
    const host = $("#live-host");
    const card = el("div", "result");
    card.innerHTML = html;
    host.innerHTML = "";
    host.appendChild(card);
    if (bindFn) bindFn(card);
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  /* ── 对话流（解题页）：输入框在上、消息在上方依次出现 ─────────────
     为什么要一条流：用户的原话是「点发送没反应，把图拖进聊天框里，
     然后上边给我出现答案解析」。所以发出去的消息、等待态、答案解析
     全都在输入框上方按顺序出现，而不是散落在页面各处。            */
  const VISION_EMPTY = '<div id="vision-empty"></div>';

  function threadHost() {
    return S.view === "vision" ? $("#vision-thread") : null;
  }

  /** 往当前场景的对话流里追加一条消息；非解题页沿用原来的单卡结果区 */
  function pushMsg(html, cls) {
    const host = threadHost();
    if (!host) {
      pushLive(html);
      return $("#live-host .result");
    }
    const empty = $("#vision-empty");
    if (empty) empty.remove();
    const clearBtn = $("#vision-clear");
    if (clearBtn) clearBtn.hidden = false;
    const node = el("div", "msg" + (cls ? " " + cls : ""));
    node.innerHTML = html;
    host.appendChild(node);
    node.scrollIntoView({ behavior: "smooth", block: "end" });
    return node;
  }

  function resetThread() {
    const host = $("#vision-thread");
    if (!host) return;
    msgUrls.forEach((u) => URL.revokeObjectURL(u));
    msgUrls = [];
    host.innerHTML = VISION_EMPTY;
    const clearBtn = $("#vision-clear");
    if (clearBtn) clearBtn.hidden = true;
    bindVisionPick();
  }

  function bindVisionPick() {
    const b = $("#vision-pick");
    if (b) b.addEventListener("click", () => window.ExamCamera.open());
  }

  function userMsgHtml(text, files) {
    const imgs = (files || []).filter((f) => IMAGE_EXT.test(f.name));
    const others = (files || []).filter((f) => !IMAGE_EXT.test(f.name));
    let html = "";
    if (imgs.length) {
      html += '<div class="msg-thumbs">' + imgs.map((f) => {
        // 消息里的缩略图单独记一份 URL：renderAttached() 会把附件条的 URL 回收掉，
        // 两条消息共用一份的话，发出去的一瞬间气泡里的图就白了。
        const url = URL.createObjectURL(f);
        msgUrls.push(url);
        return '<img src="' + url + '" alt="">';
      }).join("") + "</div>";
    }
    if (text) html += '<p class="msg-text">' + esc(text) + "</p>";
    if (others.length) {
      html += '<p class="msg-file">' + esc(others.map((f) => f.name).join("、")) + "</p>";
    }
    return html;
  }

  // 等待态：点下去立刻有反应——转圈 + 已用时间（真实步骤由后端上报后填进来）
  const EXAM_STEP_CN = { capture: "读图", recognize: "识别题干", solve: "求解", verify: "回看原图复核" };

  function pendingHtml(hasImage) {
    return '<div class="think"><span class="spinner" aria-hidden="true"></span>' +
      '<span class="think-label">' + (hasImage ? "正在解题…" : "正在思考…") + "</span>" +
      '<span class="think-time">0.0 秒</span></div>' +
      '<div class="pipeline" data-role="steps" hidden></div>';
  }

  function startTimer(node) {
    const t = $(".think-time", node);
    const t0 = Date.now();
    const show = () => { if (t) t.textContent = ((Date.now() - t0) / 1000).toFixed(1) + " 秒"; };
    show();
    const id = setInterval(show, 200);
    return () => { clearInterval(id); show(); };
  }

  /** Keep photographed questions together: each answer is followed by its explanation. */
  function examResultHtml(r, options = {}) {
    const practice=(r.artifacts||[]).find(a=>a.kind==="practice"&&["essay","interview"].includes(a.agent)&&Array.isArray(a.stages));
    if(practice)return practiceResultHtml(practice);
    const batch = (r.artifacts || []).find(a => a.kind === "exam_batch" && Array.isArray(a.questions));
    const modelLabel = r.exam_backend === "jev" ? "JEV" : r.exam_backend === "original" ? "原版" : "";
    const model = modelLabel ? '<span class="answer-model">' + modelLabel + '解题</span>' : "";
    if (batch) {
      const questions = batch.questions;
      const statusOf = q => q.status === "answered" && String(q.answer || "").trim() ? "answered" : q.status === "needs_photo" ? "needs_photo" : q.status === "error" ? "error" : "unresolved";
      const answered = questions.filter(q => statusOf(q) === "answered").length;
      const missing = questions.filter(q => statusOf(q) === "needs_photo").length;
      const pending = questions.length - answered - missing;
      const summary = '<p class="exam-batch-summary" role="status">识别 ' + questions.length + ' 道题 · 已解 ' + answered + ' 道' +
        (missing ? ' · 需补拍 ' + missing + ' 道' : '') + (pending ? ' · 待核验 ' + pending + ' 道' : '') + '</p>';
      const selected = Number.isInteger(options.questionIndex) && options.questionIndex >= 0 && options.questionIndex < questions.length;
      const shownQuestions = selected ? [questions[options.questionIndex]] : questions;
      const cards = shownQuestions.map((q, offset) => {
        const i = selected ? options.questionIndex : offset;
        const status = statusOf(q), label = String(q.label || '第 ' + (i + 1) + ' 题');
        const page = Number.isInteger(q.page) && q.page > 0 ? '<span class="exam-question-page">第 ' + q.page + ' 张照片</span>' : '';
        const answer = status === "answered" ? String(q.answer).trim() : status === "needs_photo" ? "需补拍" : status === "error" ? "处理未完成" : "暂无法确定";
        let detail = q.explanation ? renderMd(q.explanation) : status === "answered" ? '<p>本次未返回详细解析。</p>' : '';
        if (q.needed) detail += '<div class="exam-question-needed"><strong>' + (status === "needs_photo" ? '补拍提示' : status === "error" ? '处理失败' : '未确定原因') + '</strong>' + renderMd(q.needed) + '</div>';
        else if (status === "needs_photo") detail += '<p class="exam-question-needed">请将这道题的题干、图形和全部选项一起拍完整，再次提交。</p>';
        else if (status === "error" && !detail) detail = '<p>本题处理未完成，请稍后重试。</p>';
        else if (status !== "answered" && !detail) detail = '<p>本次未得到通过复核的答案。</p>';
        if (q.review_notes && !String(q.explanation || '').includes(String(q.review_notes))) detail += '<div class="exam-question-review"><strong>核对说明</strong>' + renderMd(q.review_notes) + '</div>';
        return '<article class="exam-question is-' + status + '"><header class="exam-question-heading"><h3>' + esc(label) + '</h3>' + page + '</header>' +
          '<div class="answer-block"><span class="answer-kicker">答案</span><span class="answer-value">' + esc(answer) + '</span></div>' +
          '<section class="exam-question-explanation"><h4>解析</h4><div class="md">' + detail + '</div></section></article>';
      }).join('');
      const notes = Array.isArray(batch.notes) ? batch.notes.filter(n => typeof n === 'string' && n.trim()) : [];
      let notices = batch.error ? renderMd(String(batch.error)) : '';
      if (notes.length) notices += '<ul>' + notes.map(n => '<li>' + inlineMd(n) + '</li>').join('') + '</ul>';
      if (!questions.length && !notices) notices = renderMd(r.text || '未识别到可分题的题目，请补拍清晰完整的题面。');
      return '<div class="exam-inline-results">' + summary + model + cards + (notices ? '<aside class="exam-photo-notices" aria-label="照片识别提示"><h4>照片提示</h4><div class="md">' + notices + '</div></aside>' : '') + '</div>';
    }
    const text = String(r.text || "");
    const answer = text.match(/(?:^|\n)\s*\*\*答案[：:]\s*([\s\S]*?)\*\*(?=\s*(?:\n|$))/) || text.match(/(?:^|\n)\s*(?:#{1,6}\s*)?答案[：:]\s*([^\n]+)/);
    if (answer) {
      const rest = text.replace(answer[0], "").replace(/(?:^|\n)\s*\*\*解析\*\*\s*(?:\n|$)/, "\n").trim();
      return '<div class="exam-inline-results"><article class="exam-question is-answered"><div class="answer-block"><span class="answer-kicker">答案</span>' +
        '<span class="answer-value">' + esc(answer[1]) + '</span>' + model + '</div><section class="exam-question-explanation"><h4>解析</h4>' +
        '<div class="md">' + renderMd(rest || "本次未返回详细解析。") + '</div></section></article></div>';
    }
    const vision = (r.artifacts || []).find(a => a.kind === "vision");
    if (vision && vision.answerable === false) {
      return '<div class="exam-inline-results"><article class="exam-question is-unresolved"><div class="answer-block"><span class="answer-kicker">答案</span>' +
        '<span class="answer-value">暂无法确定</span>' + model + '</div><section class="exam-question-explanation"><h4>解析与补充提示</h4><div class="md">' +
        renderMd(text) + '</div></section></article></div>';
    }
    return '<div class="md">' + renderMd(text) + '</div>' + model;
  }

  function practiceResultHtml(practice) {
    const names={ability:"职业能力测试",essay:"策论",interview:"面试"};
    const decisionLabel={user:"手动题型",previous:"沿用题型",rule:"题型判断规则"}[practice.decision_model]||(practice.decision_model?"题型判断 "+practice.decision_model:"");
    const decision=decisionLabel?'<span>'+esc(decisionLabel)+'</span>':"";
    const generationAction=practice.agent==="essay"?"写作":practice.stages.some(stage=>stage.id==="feedback")?"点评":practice.stages.some(stage=>stage.id==="follow_up")?"追问":"出题";
    const generation=practice.generation_model?'<span>'+generationAction+' '+esc(practice.generation_model)+'</span>':"";
    return '<div class="hw-practice-reading"><p class="hw-practice-source">'+(practice.question_source==="provided"?'<span>题面原文</span>':"")+decision+generation+'</p>'+
      (practice.question?'<section class="hw-practice-question"><h3>'+esc(names[practice.agent]||"学习题目")+'</h3>'+renderMd(practice.question)+'</section>':"")+
      (practice.stages||[]).map(stage=>'<section class="hw-practice-stage"><h3>'+esc(stage.label||stage.id||"练习内容")+'</h3>'+renderMd(stage.text||"")+'</section>').join("")+'</div>';
  }

  /** 助手回答：解题结果把答案单独拎出来，解析与核对说明跟在后面 */
  function answerHtml(r) {
    const exp = r.artifacts && r.artifacts.length ? exportBar(r.artifacts[0].id, r.scene) : "";
    const modelLabel = r.exam_backend === "jev" ? "JEV" : r.exam_backend === "original" ? "原版" : "";
    const model = modelLabel ? '<span class="answer-model">' + modelLabel + '解题</span>' : "";
    if (r.status === "error") {
      return '<div class="msg-error">' + esc(r.text || "请求失败") + "</div>";
    }
    if (r.scene === "exam" || (r.artifacts || []).some(a => a.kind === "vision" || a.kind === "exam_batch")) return examResultHtml(r) + exp;
    return meetingResultHtml(r, exp) + model;
  }

  /* ── 会议产出：正文里的文字记录换成可改判的逐段视图 ──────────────
     说话人聚类的错（把两个人的话算成一个人、把一个人拆成两个）自动修不了，
     只能人来定：拆分、合并、改名。正文里那份纯文本留着没用（同一份内容
     有两处、改了一处另一处没变才是灾难），所以正文只留纪要，文字记录
     交给下面的可编辑视图，改完顺带把归档正文一起重写。            */
  function splitMeetingBody(text) {
    const marker = "\n\n---\n\n## 会议文字记录";
    const i = String(text || "").indexOf(marker);
    if (i < 0) return null;
    const head = text.slice(0, i);
    const tail = text.slice(i + marker.length);
    const nl = tail.indexOf("\n\n");
    return {
      head: head,
      report: nl < 0 ? "" : tail.slice(0, nl).trim(),
      transcript: nl < 0 ? "" : tail.slice(nl + 2),
    };
  }

  function meetingResultHtml(r, extra) {
    const art = (r.artifacts || []).find((a) => a.kind === "transcript" && a.editable);
    const split = art ? splitMeetingBody(r.text || "") : null;
    if (!art || !split) return resultHtml(r.text, extra, r.speech);
    return '<div class="md">' + renderMd(split.head) + "</div>" +
      '<div class="tr-panel" id="tr-host" data-rid="' + esc(art.id) + '"></div>' +
      (extra || "");
  }

  function fmtClock(ms) {
    const total = Math.max(0, Math.round(Number(ms || 0) / 1000));
    return String(Math.floor(total / 60)).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
  }

  async function mountTranscriptEditor(host, rid) {
    if (!host || !rid) return;
    host.innerHTML = '<div class="empty">读取转写记录…</div>';
    let rec = null;
    try {
      rec = await apiJson("/api/transcript?owner=" + encodeURIComponent(S.owner) +
        "&id=" + encodeURIComponent(rid));
    } catch (e) {
      host.innerHTML = "";
      return;
    }
    renderTranscriptEditor(host, rec);
  }

  function transcriptOptions(speakers, selected, withNew) {
    return speakers.map((s) =>
      '<option value="' + esc(s.id) + '"' +
      (String(s.id) === String(selected) ? " selected" : "") + ">" + esc(s.label) + "</option>"
    ).join("") + (withNew ? '<option value="new">＋ 拆成新发言人</option>' : "");
  }

  function renderTranscriptEditor(host, rec) {
    const speakers = rec.speakers || [];
    const names = rec.names || {};
    const utts = rec.utterances || [];
    host.dataset.rid = rec.id;

    host.innerHTML =
      '<div class="panel-head"><span class="panel-title">发言人</span>' +
      '<span class="badge">' + speakers.length + " 人 · " + utts.length + " 段</span></div>" +
      '<p class="tr-report">' + esc(rec.report || "") + "</p>" +
      '<div class="spk-list">' + speakers.map((s) =>
        '<div class="spk-row">' +
        '<span class="spk-label">' + esc(s.label) + "</span>" +
        '<input class="spk-name" data-spk="' + esc(s.id) + '" maxlength="24"' +
        ' placeholder="填真名，如 王浩" value="' + esc(names[String(s.id)] || "") + '">' +
        '<span class="spk-meta">' + s.turns + " 段 · " + s.seconds + " 秒</span>" +
        (speakers.length > 1
          ? '<select class="spk-merge" data-src="' + esc(s.id) + '">' +
            '<option value="">合并到…</option>' + transcriptOptions(speakers, null, false) + "</select>"
          : "") +
        "</div>").join("") + "</div>" +
      '<div class="tr-lines">' + utts.map((u, i) =>
        '<div class="tr-line' + (rec.fragile && rec.fragile[i] ? " is-fragile" : "") + '">' +
        '<span class="tr-time">' + fmtClock(u.begin_ms) + "</span>" +
        '<select class="tr-spk" data-index="' + i + '">' + transcriptOptions(speakers, u.speaker, true) + "</select>" +
        '<span class="tr-text">' + esc(u.text) + "</span></div>").join("") + "</div>" +
      '<div class="tr-actions">' +
      '<button class="btn btn-primary" id="tr-resummarize">按新归属重算纪要</button>' +
      '<button class="btn btn-ghost" id="tr-reset">恢复原始分离</button>' +
      '<span class="tr-status" id="tr-status"></span></div>';

    const status = (msg, ms) => {
      const el2 = $("#tr-status", host);
      if (!el2) return;
      el2.textContent = msg || "";
      if (ms) setTimeout(() => { if (el2.textContent === msg) el2.textContent = ""; }, ms);
    };

    const apply = async (params, note) => {
      status("保存中…");
      try {
        const next = await apiJson("/api/transcript", {
          method: "POST",
          body: (() => {
            const fd = new FormData();
            fd.append("owner", S.owner);
            fd.append("id", host.dataset.rid);
            Object.keys(params).forEach((k) => fd.append(k, params[k]));
            return fd;
          })(),
        });
        renderTranscriptEditor(host, next);
        const el2 = $("#tr-status", host);
        if (el2 && note) el2.textContent = note;
      } catch (e) {
        status("失败：" + e.message);
        toast("改判失败：" + e.message, 3600);
      }
    };

    $$(".spk-name", host).forEach((inp) => {
      inp.addEventListener("change", () => {
        apply({ action: "rename", speaker: inp.dataset.spk, name: inp.value.trim() }, "已改名");
      });
    });
    $$(".spk-merge", host).forEach((sel) => {
      sel.addEventListener("change", () => {
        if (!sel.value) return;
        const target = sel.value;
        const label = (speakers.find((s) => String(s.id) === String(sel.dataset.src)) || {}).label || "";
        const into = (speakers.find((s) => String(s.id) === String(target)) || {}).label || "";
        if (!window.confirm("把「" + label + "」的所有发言并进「" + into + "」？")) { sel.value = ""; return; }
        apply({ action: "merge", source: sel.dataset.src, target: target }, "已合并");
      });
    });
    $$(".tr-spk", host).forEach((sel) => {
      sel.addEventListener("change", () => {
        apply({ action: "reassign", index: sel.dataset.index, speaker: sel.value }, "已改这一段");
      });
    });
    const rz = $("#tr-resummarize", host);
    if (rz) {
      rz.addEventListener("click", async () => {
        rz.disabled = true;
        status("正在按新归属重算纪要…（约 20 秒）");
        try {
          const next = await apiJson("/api/transcript", {
            method: "POST",
            body: (() => {
              const fd = new FormData();
              fd.append("owner", S.owner);
              fd.append("id", host.dataset.rid);
              fd.append("action", "resummarize");
              return fd;
            })(),
          });
          // 纪要变了：整张卡片按新内容重画，再挂回改判面板
          const card = host.closest(".result, .msg");
          if (card) {
            const split = splitMeetingBody(next.content || "");
            const md = $(".md", card);
            if (md && split) md.innerHTML = renderMd(split.head);
          }
          renderTranscriptEditor(host, next);
          const el2 = $("#tr-status", host);
          if (el2) el2.textContent = "纪要已按新归属重算";
        } catch (e) {
          status("重算失败：" + e.message);
        } finally {
          rz.disabled = false;
        }
      });
    }
    const rs = $("#tr-reset", host);
    if (rs) {
      rs.addEventListener("click", () => {
        if (!window.confirm("恢复到算法最初的分离结果？（改名也会一起清掉）")) return;
        apply({ action: "reset" }, "已恢复原始分离");
      });
    }
  }

  /* ── Composer：统一的发送入口 ───────────────────────────────────── */
  let lastError = "";

  const VIEW_SCENE = { vision: "exam" };

  function composerScene() {
    return VIEW_SCENE[S.view] || "";
  }

  function setSendBusy(on) {
    window.AssistantModel.setBusy(on);
    const b = $("#send");
    if (!b) return;
    b.disabled = !!on;
    b.classList.toggle("is-busy", !!on);
  }

  async function runComposer(opts) {
    if (S.busy) { toast("上一条还在处理中，稍等一下"); return null; }
    opts=Object.assign({},opts,{exam_backend:window.AssistantModel.id});
    S.busy = true;
    setSendBusy(true);
    lastError = "";

    const rid = opts.request_id || Math.random().toString(16).slice(2, 14);
    const hasImage = (opts.files || []).some((f) => IMAGE_EXT.test(f.name));
    const hasAudio = (opts.files || []).some((f) => AUDIO_EXT.test(f.name));

    try {
      if (opts.scene === "meeting" && opts.text === "生成会议纪要") {
        S.meetingStage = "structuring";
        renderMeeting();
      }

      let data;
      try {
        // 带附件的一律异步：拍题要 30–60 秒（四次模型调用），录音转写更久，
        // 走同步接口就是界面假死——用户看到的正是「点了发送没反应」。
        const useAsync = opts.async || hasImage || hasAudio;
        data = useAsync
          ? await sendAsync(Object.assign({}, opts, { request_id: rid }))
          : await send({
              text: opts.text, scene: opts.scene, files: opts.files,
              event: opts.event, request_id: rid, exam_backend: opts.exam_backend,
            });
      } catch (e) {
        S.meetingStage = "";
        lastError = e.message || String(e);
        toast("请求失败：" + lastError, 4200);
        return null;
      }

      if (hasAudio) S.meetingHadAudio = true;
      // The lens has its own session and already has the completed response.
      // Refreshing the unrelated composer session must not delay its answer.
      if (!opts.session_id || opts.session_id === S.session) await refreshState();
      return data;
    } finally {
      // 无论成功、请求失败还是渲染异常，都必须释放锁。
      // 曾经 refreshState 抛 TypeError 导致 busy 永远为 true，整个 UI 卡死。
      S.busy = false;
      setSendBusy(false);
    }
  }

  // Hardware UI reuses the real upload, polling and rendering path; sessions are isolated.
  window.HardwareAssistant = {
    async run(opts) {
      const result = await runComposer(Object.assign({}, opts, {async: true}));
      if (!result) throw new Error(lastError || "请求未执行，请稍后重试");
      return result;
    },
    render: renderMd,
    renderExam: examResultHtml,
    renderPractice: practiceResultHtml,
    speak: toggleSpeech,
    stopSpeech,
  };

  async function refreshState() {
    try {
      const st = await apiJson("/api/state?owner=" + encodeURIComponent(S.owner) +
        "&session_id=" + encodeURIComponent(S.session));
      S.meeting = st.meeting || S.meeting;
    } catch (e) { /* 忽略 */ }
    renderMeeting();
    renderContext();
  }

  /* ── 事件绑定 ───────────────────────────────────────────────────── */
  function bind() {
    document.addEventListener("meeting-recorded", async (event) => {
      if (S.view === "hardware" && window.HardwareLens) { window.HardwareLens.acceptAudio(event.detail); return; }
      if (S.busy) { toast("上一条还在处理中，稍后再提交录音"); return; }
      if (S.meeting.status === "paused") { toast("请先恢复会议记录，再提交录音"); return; }
      S.meetingHadAudio = true;
      const r = await runComposer({text: "请转写会议录音", scene: "meeting", files: [event.detail]});
      if (r) { toast("录音已转写"); const host = $("#meeting-result"); host.hidden = false; host.innerHTML = resultHtml(r.text, "", r.speech); }
    });
    document.addEventListener("exam-photo", (event) => addFiles([event.detail]));
    $("#exam-album").addEventListener("click", () => pickFiles("image/*"));
    // 播报播放：事件委托，避免每次渲染都重新绑定
    document.addEventListener("click", (e) => {
      const btn = e.target.closest && e.target.closest(".speech-play");
      if (!btn) return;
      const host = btn.parentElement.querySelector(".speech-text");
      if (host && host.textContent) toggleSpeech(host.textContent, btn);
    });

    // 导航
    $$("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
    // 浏览器前进/后退与手改 hash 都要生效
    window.addEventListener("hashchange", () => {
      if (urlView() !== S.view || location.hash !== "#" + urlView()) go(urlView(), true);
    });
    window.addEventListener("popstate", () => {
      if (urlView() !== S.view || location.hash !== "#" + urlView()) go(urlView(), true);
    });

    $("#ctx-toggle").addEventListener("click", () => {
      const app = $(".app");
      const open = app.classList.toggle("ctx-open");
      $("#ctx-toggle").setAttribute("aria-expanded", String(open));
      $("#ctx").setAttribute("aria-hidden", String(!open));
      renderContext();
    });
    $("#ctx-close").addEventListener("click", () => $("#ctx-toggle").click());

    // Composer
    const input = $("#input");
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 132) + "px";
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendComposer(); }
    });
    $("#send").addEventListener("click", sendComposer);

    const plus = $("#plus");
    plus.addEventListener("click", (e) => {
      e.stopPropagation();
      const menu = $("#attach-menu");
      const open = menu.hidden;
      menu.hidden = !open;
      plus.setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", () => {
      $("#attach-menu").hidden = true;
      $("#plus").setAttribute("aria-expanded", "false");
    });
    $$("#attach-menu button").forEach((b) => {
      b.addEventListener("click", () => {
        const kind = b.dataset.attach;
        pickFiles(kind === "image" ? "image/*" : kind === "audio" ? "audio/*" : "");
      });
    });
    $("#file-input").addEventListener("change", (e) => {
      const files = Array.prototype.slice.call(e.target.files || []);
      const cb = fileCallback;
      fileCallback = null;
      if (cb) cb(files);
      else addFiles(files);
    });

    // 拖拽：拖到窗口任意位置都算——用户是「从别的地方拖入」，
    // 不该要求他瞄准某个框；落点统一是输入框，拖拽期间给一层提示。
    let dragDepth = 0;
    window.addEventListener("dragenter", (e) => {
      if (!dtHasFiles(e)) return;
      e.preventDefault();
      dragDepth++;
      showVeil(true);
    });
    window.addEventListener("dragover", (e) => {
      if (!dtHasFiles(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
      showVeil(true);
    });
    window.addEventListener("dragleave", (e) => {
      if (!dtHasFiles(e)) return;
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) showVeil(false);
    });
    window.addEventListener("drop", (e) => {
      if (!dtHasFiles(e)) return;
      e.preventDefault();
      dragDepth = 0;
      showVeil(false);
      const files = filesFrom(e.dataTransfer);
      if (!files.length) { toast("没有读到文件"); return; }
      addFiles(files);
    });

    // 粘贴：截图工具/网页里复制的图片直接进输入框
    document.addEventListener("paste", (e) => {
      const files = filesFrom(e.clipboardData);
      if (!files.length) return;      // 纯文本粘贴保留浏览器默认行为
      e.preventDefault();
      addFiles(files);
    });

    // Meeting
    $("#meeting-start").addEventListener("click", async () => {
      S.meetingHadAudio = false;
      S.meetingStage = "";
      S.meetingElapsed = 0;
      S.meetingStartedAt = null;
      $("#meeting-result").hidden = true;
      await runComposer({ text: "开始会议记录", scene: "meeting" });
      $("#meeting-transcript").focus();
    });
    $("#meeting-pause").addEventListener("click", async () => {
      const paused = S.meeting.status === "paused";
      const r = await runComposer({ text: paused ? "继续会议" : "暂停会议", scene: "meeting" });
      if (r) toast(paused ? "已恢复会议记录" : "已暂停会议记录");
    });
    $("#meeting-audio").addEventListener("click", () => {
      if (S.meeting.status === "paused") { toast("会议记录已暂停，先点「恢复文字记录」继续", 3200); return; }
      pickFiles("audio/*", async (files) => {
        if (!files.length) return;
        if (!AUDIO_EXT.test(files[0].name)) { toast("请选择音频文件"); return; }
        if (S.meeting.status !== "collecting") {
          const started = await runComposer({ text: "开始会议记录", scene: "meeting" });
          if (!started) return;
        }
        S.meetingHadAudio = true;
        const r = await runComposer({ text: "", scene: "meeting", files: files });
        if (r && r.status !== "error") toast("录音已转写并追加");
      });
    });
    $("#meeting-append").addEventListener("click", async () => {
      if (S.meeting.status === "paused") { toast("会议记录已暂停，先点「恢复文字记录」继续", 3200); return; }
      const v = $("#meeting-transcript").value.trim();
      if (!v) { toast("先粘贴会议转写内容"); return; }
      await runComposer({ text: v, scene: "meeting" });
      $("#meeting-transcript").value = "";
      updateMeetingCount();
      toast("已追加");
    });
    $("#meeting-transcript").addEventListener("input", updateMeetingCount);
    $("#meeting-summarize").addEventListener("click", async () => {
      const r = await runComposer({ text: "生成会议纪要", scene: "meeting" });
      if (!r) { S.meetingStage = ""; renderMeeting(); return; }
      if (r.status === "need_input") { S.meetingStage = ""; renderMeeting(); toast(r.text, 3600); return; }
      S.meetingStage = "done";
      renderMeeting();
      const host = $("#meeting-result");
      host.hidden = false;
      host.innerHTML = resultHtml(r.text, r.artifacts && r.artifacts.length
        ? exportBar(r.artifacts[0].id, r.scene) : "", r.speech);
      if (r.artifacts && r.artifacts.length) bindExport(host, r.artifacts[0].id);
      host.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    $("#meeting-stop").addEventListener("click", async () => {
      window.MeetingRecorder?.stop();
      await runComposer({ text: "结束会议", scene: "meeting" });
      toast("已结束会议记录");
    });

    // Vision：页面里不再有取景框/解题按钮——图片走输入框，结果落进对话流
    bindVisionPick();
    $("#vision-clear").addEventListener("click", () => {
      resetThread();
      toast("已清空对话");
    });

    // Memory 筛选
    $$("#memory-filters .filter").forEach((b) => {
      b.addEventListener("click", () => {
        $$("#memory-filters .filter").forEach((x) => x.classList.remove("is-on"));
        b.classList.add("is-on");
        S.memoryFilter = b.dataset.filter;
        $("#memory-detail").hidden = true;
        loadMemory();
        renderContext();
      });
    });
  }

  async function sendComposer() {
    const input = $("#input");
    let text = input.value.trim();
    const files = S.attached.slice();
    const hasImage = files.some((f) => IMAGE_EXT.test(f.name));

    if (!text && !files.length) { toast("先输入问题，或把题目图片拖进输入框"); input.focus(); return; }
    if (S.busy) { toast("上一条还在处理中，稍等一下"); return; }
    // 只丢图片不写字：补一句默认请求。实测「只有图片、没有文字」会被路由成
    // general，模型回一句「你好，需要什么帮助」——图片白传了。
    if (!text && hasImage) text = "解这道题";

    input.value = "";
    input.style.height = "auto";
    S.attached = [];
    renderAttached();

    // 1) 先把「我」这条放进对话流：点下去必须立刻有东西出现
    pushMsg(userMsgHtml(text, files), "is-user");

    // 2) 再放等待卡片：转圈 + 已用时间 +（后端上报的）真实步骤
    const card = pushMsg(pendingHtml(hasImage), "is-ai is-pending");
    const stopTimer = startTimer(card);
    const rid = Math.random().toString(16).slice(2, 14);

    const r = await runComposer({
      text: text,
      files: files,
      scene: composerScene(),
      request_id: rid,
      onProgress: (p) => {
        const steps = $('[data-role="steps"]', card);
        if (steps && p.steps && p.steps.length) renderPipeline(steps, p.steps, { numbered: true });
        const label = $(".think-label", card);
        const cur = (p.steps || []).find((s) => s.state === "active");
        if (label && p.batch && Number.isInteger(p.batch.total) && p.batch.total > 0) label.textContent = "已处理 " + Math.min(p.batch.total, Math.max(0, Number(p.batch.done) || 0)) + " / " + p.batch.total + " 道题…";
        else if (label && cur && EXAM_STEP_CN[cur.id]) label.textContent = "正在" + EXAM_STEP_CN[cur.id] + "…";
      },
    });

    stopTimer();
    card.classList.remove("is-pending");

    if (!r) {
      card.innerHTML = '<div class="msg-error">' + esc(lastError || "请求失败") + "</div>" +
        '<p class="think-hint">可以直接再发一次；图片和文字还在对话里，不必重新上传。</p>';
      return;
    }

    // 3) 答案解析落在同一个位置，替换掉等待卡片
    card.innerHTML = answerHtml(r);
    if (r.artifacts && r.artifacts.length) bindExport(card, r.artifacts[0].id);
    // 会议产出：文字记录挂上「人工改判说话人」面板
    const trHost = $("#tr-host", card);
    if (trHost) mountTranscriptEditor(trHost, trHost.dataset.rid);
    // 对齐卡片**顶部**而不是底部：答案往往比一屏高，对齐底部会把
    // 「ANSWER」那一行顶出可视区，用户得先往上滚才能看到答案。
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    renderContext();
  }

  /* ── 启动 ───────────────────────────────────────────────────────── */
  async function boot() {
    const host = el("div");
    host.id = "live-host";
    $("#stage").appendChild(host);

    bind();
    resetThread();

    // 问候语按本地时间
    const h = new Date().getHours();

    try { applyHealth(await apiJson("/health")); } catch (e) { /* 忽略 */ }
    await refreshState();
    go(urlView());
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
