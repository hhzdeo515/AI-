/* ═══════════════════════════════════════════════════════════════════════
   assistant-lite · Glasses Companion — Web UI
   只调用真实存在的后端接口，不做前端伪造状态。
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  /* ── 常量 ───────────────────────────────────────────────────────── */
  const IMAGE_EXT = /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i;
  const AUDIO_EXT = /\.(wav|mp3|m4a|aac|flac|ogg|amr|wma)$/i;
  const SCENE_LABEL = { meeting: "会议纪要", exam: "题解", fitness: "训练总结", general: "资料" };

  const VIEW_META = {
    home: "Home",
    meeting: "Meeting · 会议纪要",
    vision: "Vision · 拍照解题",
    fitness: "Fitness · 锻炼指导",
    memory: "Memory · 资料库",
  };

  /* ── 状态 ───────────────────────────────────────────────────────── */
  const S = {
    view: "home",
    owner: "local",
    session: "web",
    attached: [],
    busy: false,
    health: null,
    meeting: { status: "idle", transcript: "", id: "" },
    meetingStage: "",           // "" | "structuring" | "done"
    meetingHadAudio: false,
    fitness: { workout: { status: "idle" }, awaiting: "" },
    profile: { profile: {}, summary: "", risk: "", bmi: null },
    memoryFilter: "",
  };

  /* ── DOM 小工具 ─────────────────────────────────────────────────── */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

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

  function resultHtml(text, extra) {
    return '<div class="md">' + renderMd(text) + "</div>" + (extra || "");
  }

  /* ── API ────────────────────────────────────────────────────────── */
  async function apiJson(path, opts) {
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
    return data;
  }

  function form(opts) {
    const fd = new FormData();
    fd.append("owner", S.owner);
    fd.append("session_id", S.session);
    if (opts.text != null) fd.append("text", opts.text);
    if (opts.scene) fd.append("scene", opts.scene);
    if (opts.event) fd.append("event", JSON.stringify(opts.event));
    (opts.files || []).forEach((f) => fd.append("files", f, f.name));
    return fd;
  }

  function send(opts) {
    return apiJson("/api/chat", { method: "POST", body: form(opts) });
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
    return (location.hash || "").replace("#", "") || "home";
  }

  function go(view, fromUrl) {
    if (!VIEW_META[view]) view = "home";
    S.view = view;

    if (!fromUrl) {
      const want = view === "home" ? "" : view;
      if ((location.hash || "").replace("#", "") !== want) {
        if (view === "home") history.pushState(null, "", location.pathname);
        else location.hash = view;
      }
    }

    $$(".view").forEach((v) => v.classList.toggle("is-on", v.dataset.view === view));
    $$(".nav-item").forEach((b) => b.classList.toggle("is-on", b.dataset.go === view));
    $$(".tabbar button").forEach((b) => b.classList.toggle("is-on", b.dataset.go === view));
    $("#crumb").textContent = VIEW_META[view];
    $("#nav").classList.remove("is-open");
    $("#stage").scrollTop = 0;
    $("#live-host").innerHTML = "";

    if (view === "memory") loadMemory();
    if (view === "fitness") loadProfile();
    if (view === "meeting") renderMeeting();
    renderContext();
  }

  /* ── 状态指示 ───────────────────────────────────────────────────── */
  function applyHealth(h) {
    S.health = h;
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
    $("#mode-note").textContent = on ? "模型已连接 · 未接眼镜硬件" : "未配置 API Key";
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
        if (p && p.steps && p.steps.length) onUpdate(p);
      } catch (e) { /* 轮询失败不打断主流程 */ }
      if (!stopped) setTimeout(tick, 520);
    })();
    return function stop() { stopped = true; };
  }

  /* ── Meeting ────────────────────────────────────────────────────── */
  function meetingSteps() {
    const hasText = !!(S.meeting.transcript || "").length;
    const stage = S.meetingStage;
    const capturedLabel = S.meetingHadAudio ? "Audio captured" : "Content captured";

    const steps = [
      { id: "captured", label: capturedLabel },
      { id: "transcript", label: "Transcript ready" },
      { id: "structuring", label: "Structuring meeting" },
      { id: "summary", label: "Summary ready" },
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
    const panel = $("#meeting-session");
    const badge = $("#meeting-badge");

    $("#meeting-start").hidden = collecting;
    $("#meeting-stop").hidden = !collecting;
    panel.hidden = !collecting;

    if (collecting) {
      badge.textContent = "LISTENING";
      badge.className = "badge is-live";
      const ta = $("#meeting-transcript");
      if (document.activeElement !== ta) ta.value = m.transcript || "";
      updateMeetingCount();
      if (!S.meetingStartedAt) S.meetingStartedAt = Date.now();
    } else {
      S.meetingStartedAt = null;
      if (m.status === "ended") {
        badge.textContent = "ENDED";
        badge.className = "badge is-idle";
      }
    }

    $("#meeting-monitor").hidden = !collecting;
    if (collecting) startMeetingTimer(); else stopMeetingTimer();

    const hasAny = collecting || S.meetingStage;
    renderPipeline($("#meeting-pipeline"), hasAny ? meetingSteps() : null);
  }

  function updateMeetingCount() {
    const v = $("#meeting-transcript").value;
    $("#meeting-count").textContent = v.length + " 字";
  }

  let meetingTimer = null;

  function startMeetingTimer() {
    if (meetingTimer) return;
    const tick = () => {
      if (!S.meetingStartedAt) return;
      const total = Math.floor((Date.now() - S.meetingStartedAt) / 1000);
      $("#mono-time").textContent =
        String(Math.floor(total / 60)).padStart(2, "0") + ":" +
        String(total % 60).padStart(2, "0");
    };
    tick();
    meetingTimer = setInterval(tick, 1000);
  }

  function stopMeetingTimer() {
    if (meetingTimer) { clearInterval(meetingTimer); meetingTimer = null; }
  }

  /* ── Fitness ────────────────────────────────────────────────────── */
  async function loadProfile() {
    try {
      S.profile = await apiJson("/api/profile?owner=" + encodeURIComponent(S.owner));
    } catch (e) {
      S.profile = { profile: {}, summary: "", risk: "", bmi: null };
    }
    renderFitness();
  }

  function renderFitness() {
    const host = $("#fit-profile");
    const p = S.profile.profile || {};
    const has = Object.keys(p).length > 0;

    if (!has) {
      host.innerHTML =
        '<div class="panel-head"><span class="panel-title">Health Profile</span>' +
        '<span class="badge is-idle">NOT SET</span></div>' +
        '<p class="frame-hint" style="margin:0 0 14px;text-align:left">' +
        "建档后，器械指导与训练计划都会结合你的年龄、目标、伤病与慢性病。" +
        "</p>";
      const btn = el("button", "btn btn-primary", "建立健康档案");
      btn.addEventListener("click", () => {
        go("fitness");
        runComposer({ text: "建立健康档案", scene: "fitness" });
      });
      host.appendChild(btn);
    } else {
      const fields = [
        ["年龄", p.age], ["身高", p.height_cm ? p.height_cm + " cm" : ""],
        ["体重", p.weight_kg ? p.weight_kg + " kg" : ""],
        ["BMI", S.profile.bmi != null ? S.profile.bmi : ""],
        ["目标", p.goal], ["运动基础", p.level],
        ["每周训练", p.days_per_week != null ? p.days_per_week + " 天" : ""],
        ["伤病", (p.injuries || []).join("、")],
        ["慢性病", (p.conditions || []).join("、")],
        ["可用器械", (p.equipment || []).join("、")],
      ].filter((f) => f[1] !== "" && f[1] != null);

      host.innerHTML =
        '<div class="panel-head"><span class="panel-title">Health Profile</span>' +
        '<span class="badge">SET</span></div>' +
        '<dl class="profile-grid">' +
        fields.map((f) =>
          '<div class="pfield"><dt>' + esc(f[0]) + "</dt><dd>" + esc(f[1]) + "</dd></div>"
        ).join("") + "</dl>" +
        (S.profile.risk
          ? '<p class="frame-hint" style="margin:0;text-align:left">注意：' + esc(S.profile.risk) +
            "。涉及这些情况的训练请先咨询医生或线下教练。</p>"
          : "");
    }

    // Workout 区
    const w = (S.fitness.workout || {});
    const active = w.status === "active" || w.status === "paused";
    const session = $("#fit-session");

    if (active) {
      session.hidden = false;
      $("#fit-badge").textContent = w.status === "paused" ? "PAUSED" : "ACTIVE";
      $("#fit-badge").className = "badge " + (w.status === "paused" ? "is-warn" : "is-live");
      $("#fit-sets").textContent = String(w.total_sets || 0);
      $("#fit-current").textContent = w.current || "—";
      $("#fit-state").textContent = (w.status || "idle").toUpperCase();
      $("#fit-rest").hidden = w.status !== "active";
    } else {
      session.hidden = false;
      session.innerHTML =
        '<div class="panel-head"><span class="panel-title">Workout</span>' +
        '<span class="badge is-idle">IDLE</span></div>' +
        '<p class="frame-hint" style="margin:0 0 14px;text-align:left">' +
        "输入要练的动作开始记录，例如「深蹲」「卧推 3组10次 60公斤」。</p>";
      const row = el("div", "actions");
      row.style.marginBottom = "0";
      const input = el("input");
      input.type = "text";
      input.placeholder = "深蹲";
      input.style.cssText =
        "flex:1;min-width:140px;padding:9px 13px;border-radius:9px;" +
        "border:1px solid var(--line-2);background:rgba(255,255,255,.72);outline:none";
      const btn = el("button", "btn btn-primary", "Start Workout");
      const start = () => {
        const v = (input.value || "").trim() || "深蹲";
        runComposer({ text: "开始" + v, scene: "fitness", event: { semantic_action: "start_exercise" } });
      };
      btn.addEventListener("click", start);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });
      row.appendChild(input);
      row.appendChild(btn);
      session.appendChild(row);
    }
  }

  /* ── Memory ─────────────────────────────────────────────────────── */
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
      host.innerHTML = '<div class="empty">还没有归档资料。<br>生成会议纪要、题解或训练总结后会自动出现在这里。</div>';
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
      fitness: '<path d="M3 12.5h3.4l2-5.2 3 10.4 2.4-7.2 1.8 4h4.9"/>',
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
      host.innerHTML = resultHtml(r.content, exportBar(r.id, r.scene));
      bindExport(host, r.id);
    } catch (e) {
      host.innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
    }
  }

  function exportBar(id, scene) {
    const fmts = ["docx", "pdf", "md", "txt"];
    return '<div class="export-bar"><span class="label">Export</span>' +
      fmts.map((f) => '<button class="btn-mini" data-fmt="' + f + '">' + f.toUpperCase() + "</button>").join("") +
      "</div>";
  }

  function bindExport(root, id) {
    $$("[data-fmt]", root).forEach((b) => {
      b.addEventListener("click", () => {
        const fmt = b.dataset.fmt;
        window.location.href = "/api/export?owner=" + encodeURIComponent(S.owner) +
          "&id=" + encodeURIComponent(id) + "&format=" + fmt;
        toast("正在导出 " + fmt.toUpperCase());
      });
    });
  }

  /* ── Context Panel ──────────────────────────────────────────────── */
  function renderContext() {
    const body = $("#ctx-body");
    const secs = [];

    if (S.view === "meeting") {
      secs.push(["Session", [
        ["状态", S.meeting.status || "idle"],
        ["会议 ID", S.meeting.id ? S.meeting.id.slice(0, 8) : "—"],
        ["转写", (S.meeting.transcript || "").length + " / 48000 字"],
      ]]);
    } else if (S.view === "vision") {
      secs.push(["Image", [["已选", S.attached.length ? S.attached[0].name : "—"]]]);
      secs.push(["Verification", [
        ["计算校验", "由 /api/exam-check 执行"],
        ["黑白格", "等尺寸黑白格题逐格运算"],
      ]]);
    } else if (S.view === "fitness") {
      const p = S.profile.profile || {};
      const w = S.fitness.workout || {};
      secs.push(["Profile", [
        ["目标", p.goal || "—"],
        ["基础", p.level || "—"],
        ["伤病", (p.injuries || []).join("、") || "—"],
      ]]);
      secs.push(["Workout", [
        ["状态", (w.status || "idle").toUpperCase()],
        ["当前动作", w.current || "—"],
        ["累计组数", String(w.total_sets || 0)],
      ]]);
    } else if (S.view === "memory") {
      secs.push(["Library", [
        ["筛选", S.memoryFilter || "All"],
        ["数据目录", S.health ? S.health.data_dir : "—"],
      ]]);
    } else {
      secs.push(["Assistant", [
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
  let fileCallback = null;

  function pickFiles(accept, cb) {
    const input = $("#file-input");
    input.value = "";
    input.accept = accept || "";
    fileCallback = cb || null;
    input.click();
  }

  function addFiles(files) {
    Array.prototype.slice.call(files || []).forEach((f) => {
      const ok = IMAGE_EXT.test(f.name) || AUDIO_EXT.test(f.name);
      if (!ok) { toast("不支持的格式：" + f.name); return; }
      if (!S.attached.some((x) => x.name === f.name && x.size === f.size)) S.attached.push(f);
    });
    renderAttached();
    // 在 Vision 页选了图片就直接进取景框预览
    if (S.view === "vision" && S.attached.some((f) => IMAGE_EXT.test(f.name))) showVisionPreview();
  }

  function renderAttached() {
    const host = $("#attached");
    host.textContent = S.attached.length
      ? "附件：" + S.attached.map((f) => f.name).join("、")
      : "";
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

  /* ── Composer：统一的发送入口 ───────────────────────────────────── */
  async function runComposer(opts) {
    if (S.busy) return null;
    S.busy = true;
    $("#send").disabled = true;

    const rid = Math.random().toString(16).slice(2, 14);
    const hasImage = (opts.files || []).some((f) => IMAGE_EXT.test(f.name));
    const hasAudio = (opts.files || []).some((f) => AUDIO_EXT.test(f.name));

    let stopPoll = null;
    if (opts.scene === "exam" && hasImage) {
      renderPipeline($("#vision-pipeline"), [
        { id: "capture", label: "Capture", state: "done" },
        { id: "recognize", label: "Recognize", state: "active" },
        { id: "solve", label: "Solve", state: "pending" },
        { id: "verify", label: "Verify", state: "pending" },
      ], { numbered: true });
      stopPoll = pollProgress(rid, (p) => renderPipeline($("#vision-pipeline"), p.steps, { numbered: true }));
    }
    if (opts.scene === "meeting" && opts.text === "生成会议纪要") {
      S.meetingStage = "structuring";
      renderMeeting();
    }

    let data = null;
    try {
      data = await send({
        text: opts.text,
        scene: opts.scene,
        files: opts.files,
        event: opts.event,
      });
    } catch (e) {
      if (stopPoll) stopPoll();
      S.meetingStage = "";
      toast("请求失败：" + e.message, 4200);
      S.busy = false;
      $("#send").disabled = false;
      return null;
    }
    if (stopPoll) stopPoll();

    if (hasAudio) S.meetingHadAudio = true;
    await refreshState();
    S.busy = false;
    $("#send").disabled = false;
    return data;
  }

  async function refreshState() {
    try {
      const st = await apiJson("/api/state?owner=" + encodeURIComponent(S.owner) +
        "&session_id=" + encodeURIComponent(S.session));
      S.meeting = st.meeting || S.meeting;
      S.fitness = st.fitness || S.fitness;
    } catch (e) { /* 忽略 */ }
    renderMeeting();
    if (S.view === "fitness") renderFitness();
    renderContext();
  }

  /* ── 事件绑定 ───────────────────────────────────────────────────── */
  function bind() {
    // 导航
    $$("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
    // 浏览器前进/后退与手改 hash 都要生效
    window.addEventListener("hashchange", () => {
      if (urlView() !== S.view) go(urlView(), true);
    });
    window.addEventListener("popstate", () => {
      if (urlView() !== S.view) go(urlView(), true);
    });
    $("#nav-toggle").addEventListener("click", () => $("#nav").classList.toggle("is-open"));
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

    // Meeting
    $("#meeting-start").addEventListener("click", async () => {
      S.meetingHadAudio = false;
      S.meetingStage = "";
      $("#meeting-result").hidden = true;
      await runComposer({ text: "开始会议记录", scene: "meeting" });
      $("#meeting-transcript").focus();
    });
    $("#meeting-audio").addEventListener("click", () => {
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
        ? exportBar(r.artifacts[0].id, r.scene) : "");
      if (r.artifacts && r.artifacts.length) bindExport(host, r.artifacts[0].id);
      host.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    $("#meeting-stop").addEventListener("click", async () => {
      await runComposer({ text: "结束会议", scene: "meeting" });
      toast("已结束会议记录");
    });

    // Vision
    const frame = $("#vision-frame");
    $("#vision-pick").addEventListener("click", () => pickFiles("image/*"));
    frame.addEventListener("click", (e) => { if (e.target === frame) pickFiles("image/*"); });
    ["dragenter", "dragover"].forEach((ev) =>
      frame.addEventListener(ev, (e) => { e.preventDefault(); frame.classList.add("is-drag"); }));
    ["dragleave", "drop"].forEach((ev) =>
      frame.addEventListener(ev, (e) => { e.preventDefault(); frame.classList.remove("is-drag"); }));
    frame.addEventListener("drop", (e) => {
      const imgs = Array.prototype.slice.call(e.dataTransfer.files || []).filter((f) => IMAGE_EXT.test(f.name));
      if (!imgs.length) { toast("请拖入图片文件"); return; }
      S.attached = imgs.slice(0, 1);
      renderAttached();
      showVisionPreview();
    });

    $("#vision-solve").addEventListener("click", async () => {
      const imgs = S.attached.filter((f) => IMAGE_EXT.test(f.name));
      if (!imgs.length) { toast("先选择一张题目图片"); return; }
      const btn = $("#vision-solve");
      btn.disabled = true;
      btn.textContent = "Solving…";
      const r = await runComposer({ text: "解这道题", scene: "exam", files: imgs });
      btn.disabled = false;
      btn.textContent = "Solve";
      if (!r) return;
      const host = $("#vision-result");
      host.hidden = false;
      if (r.status === "error") {
        host.innerHTML = resultHtml(r.text);
      } else {
        const m = r.text.match(/^\*\*答案：(.+?)\*\*/);
        const answer = m ? m[1] : "";
        const rest = m ? r.text.replace(m[0], "").trim() : r.text;
        host.innerHTML = resultHtml(rest, "");
        if (answer) {
          host.innerHTML =
            '<div class="answer-block"><span class="answer-kicker">ANSWER</span>' +
            '<span class="answer-value">' + esc(answer) + "</span></div>" +
            '<p class="answer-note">已回看原图复核，并用程序工具校验计算</p>' +
            '<div class="md">' + renderMd(rest) + "</div>" +
            (r.artifacts && r.artifacts.length ? exportBar(r.artifacts[0].id, r.scene) : "");
        }
        if (r.artifacts && r.artifacts.length) bindExport(host, r.artifacts[0].id);
      }
      host.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    $("#vision-clear").addEventListener("click", () => {
      S.attached = [];
      renderAttached();
      resetVision();
    });

    // Fitness
    $("#fit-setdone").addEventListener("click", () =>
      runComposer({ text: "做完一组", scene: "fitness", event: { semantic_action: "set_done" } }));
    $("#fit-pain").addEventListener("click", async () => {
      const where = window.prompt("哪个部位不适？例如：膝盖 / 腰 / 肩", "膝盖");
      if (!where) return;
      await runComposer({
        text: where + "有点疼", scene: "fitness",
        event: { semantic_action: "pain_report" },
      });
    });
    $("#fit-end").addEventListener("click", async () => {
      const r = await runComposer({ text: "结束训练", scene: "fitness", event: { semantic_action: "end_workout" } });
      if (!r) return;
      const host = $("#fitness-result");
      host.hidden = false;
      host.innerHTML = resultHtml(r.text, r.artifacts && r.artifacts.length ? exportBar(r.artifacts[0].id, r.scene) : "");
      if (r.artifacts && r.artifacts.length) bindExport(host, r.artifacts[0].id);
      host.scrollIntoView({ behavior: "smooth", block: "nearest" });
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

  function showVisionPreview() {
    const img = S.attached.find((f) => IMAGE_EXT.test(f.name));
    const body = $("#vision-body");
    const frame = $("#vision-frame");
    if (!img) { resetVision(); return; }

    const url = URL.createObjectURL(img);
    frame.classList.add("is-focus");
    body.innerHTML = '<img src="' + url + '" alt="题目图片">' +
      '<span class="frame-tag">Image captured</span>';
    $("#vision-solve").disabled = false;
    $("#vision-clear").hidden = false;
    $("#vision-result").hidden = true;
  }

  function resetVision() {
    const frame = $("#vision-frame");
    frame.classList.remove("is-focus");
    $("#vision-body").innerHTML =
      '<svg class="frame-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" ' +
      'stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M4 8V5.6A1.6 1.6 0 0 1 5.6 4H8"/><path d="M16 4h2.4A1.6 1.6 0 0 1 20 5.6V8"/>' +
      '<path d="M20 16v2.4a1.6 1.6 0 0 1-1.6 1.6H16"/><path d="M8 20H5.6A1.6 1.6 0 0 1 4 18.4V16"/>' +
      '<circle cx="12" cy="12" r="3"/></svg>' +
      '<p class="frame-hint">选择或拖入一张题目图片</p>' +
      '<button class="btn btn-primary" id="vision-pick">Choose Image</button>';
    $("#vision-pick").addEventListener("click", () => pickFiles("image/*"));
    $("#vision-solve").disabled = true;
    $("#vision-clear").hidden = true;
    $("#vision-pipeline").hidden = true;
    $("#vision-result").hidden = true;
  }

  async function sendComposer() {
    const input = $("#input");
    const text = input.value.trim();
    if (!text && !S.attached.length) return;

    const files = S.attached.slice();
    input.value = "";
    input.style.height = "auto";
    S.attached = [];
    renderAttached();

    const r = await runComposer({ text: text, files: files });
    if (!r) return;

    // 附件是图片 → 同时更新 Vision 预览
    if (files.some((f) => IMAGE_EXT.test(f.name)) && S.view === "vision") showVisionPreview();

    if (r.status === "need_input") { pushLive(resultHtml(r.text)); return; }

    pushLive(resultHtml(r.text, r.artifacts && r.artifacts.length
      ? exportBar(r.artifacts[0].id, r.scene) : ""), (card) => {
      if (r.artifacts && r.artifacts.length) bindExport(card, r.artifacts[0].id);
    });
  }

  /* ── 启动 ───────────────────────────────────────────────────────── */
  async function boot() {
    const host = el("div");
    host.id = "live-host";
    $("#stage").appendChild(host);

    bind();
    resetVision();

    // 问候语按本地时间
    const h = new Date().getHours();
    $("#greeting").textContent = h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";

    try { applyHealth(await apiJson("/health")); } catch (e) { /* 忽略 */ }
    await refreshState();
    await loadProfile();
    go(urlView());
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
