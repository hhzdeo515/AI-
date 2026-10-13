/* Horizontal menu gestures and opt-in, one-shot spoken navigation. */
(() => {
  "use strict";
  const api = window.HardwareLens?.menu;
  const menu = document.getElementById("hw-nav-options");
  if (!api || !menu) return;
  const host = document.getElementById("hardware-demo");
  const navigation = document.getElementById("hw-nav-menu");
  const controls = document.createElement("div");
  controls.className = "hw-menu-controls";
  controls.innerHTML = `<div class="hw-menu-paging"><button type="button" id="hw-menu-prev" aria-label="选择上一个功能">‹</button><span id="hw-menu-position" aria-live="polite"></span><button type="button" id="hw-menu-next" aria-label="选择下一个功能">›</button></div><button type="button" id="hw-menu-voice" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M6 10v2a6 6 0 0 0 12 0v-2M12 18v3M8 21h8"/></svg><span>语音选择</span></button>`;
  menu.after(controls);
  const hint = document.createElement("p");
  hint.className = "hw-menu-hint";
  hint.textContent = "左右滑动选择 · 点击卡片确认";
  controls.before(hint);
  const status = document.createElement("p");
  status.id = "hw-menu-voice-status";
  status.setAttribute("role", "status");
  status.textContent = "可说：拍照解题、会议纪要、实时翻译、地图。";
  const privacy = document.createElement("p");
  privacy.className = "hw-menu-voice-note";
  privacy.textContent = "点击后听一次；语音由浏览器识别，可能使用厂商服务。";
  navigation.append(status, privacy);
  menu.setAttribute("aria-label", "左右滑动选择功能，点击卡片确认");
  const $ = id => document.getElementById(id);
  const voice = $("hw-menu-voice");
  const voiceLabel = voice.querySelector("span");
  const recognizer = () => window.SpeechRecognition || window.webkitSpeechRecognition;
  const supported = !!recognizer() && window.isSecureContext !== false;
  let drag = null, suppressClick = false, recognition = null, ticket = 0, timer = null;

  function stopVoice(message) {
    ticket++;
    clearTimeout(timer); timer = null;
    const previous = recognition; recognition = null;
    if (previous) {
      previous.onresult = previous.onerror = previous.onend = previous.onstart = null;
      try { previous.abort(); } catch (_) { try { previous.stop(); } catch (_) {} }
    }
    voice.setAttribute("aria-pressed", "false");
    voiceLabel.textContent = "语音选择";
    if (message) status.textContent = message;
    else if (previous) status.textContent = "语音选择已停止。点击按钮可重新开始。";
  }
  function refresh() {
    const state = api.state();
    const canSelect = state.available && !navigation.hidden;
    $("hw-menu-position").textContent = `${state.focus + 1} / ${state.options.length}`;
    $("hw-menu-prev").disabled = $("hw-menu-next").disabled = !canSelect || state.options.length < 2;
    voice.disabled = !supported || !canSelect;
    if (!supported) status.textContent = "当前浏览器不支持语音选择，请左右滑动或点击。";
    if (recognition && !canSelect) stopVoice("语音选择已停止。点击按钮可重新开始。");
  }
  function run(action) { return api.act(action); }
  $("hw-menu-prev").addEventListener("click", () => run("previous"));
  $("hw-menu-next").addEventListener("click", () => run("next"));

  // A horizontal drag belongs to the menu, while the lens frame still rotates.
  menu.addEventListener("pointerdown", event => {
    if (event.button !== 0 || drag || !api.state().available) return;
    suppressClick = false;
    stopVoice();
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, moved: false, total: 0, width: Math.max(130, menu.getBoundingClientRect().width * .52)};
    event.stopPropagation();
  });
  menu.addEventListener("pointermove", event => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (drag.ignore) return;
    if (!drag.moved) {
      if (Math.abs(dy) > 8 && Math.abs(dy) > Math.abs(dx)) { drag.ignore = true; return; }
      if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy)) return;
      if (!api.state().available) { drag = null; return; }
      drag.moved = true;
      menu.setPointerCapture?.(drag.id);
      host.classList.add("is-lens-swiping");
      api.slide({phase: "start", total: 0});
    }
    drag.total = -dx / drag.width * 44;
    api.slide({phase: "move", total: drag.total});
    event.preventDefault(); event.stopPropagation();
  });
  async function finish(event, cancelled = false) {
    if (!drag || event && event.pointerId !== drag.id) return;
    const ended = drag; drag = null;
    suppressClick = (ended.moved || ended.ignore) && !cancelled;
    host.classList.remove("is-lens-swiping");
    if (menu.hasPointerCapture?.(ended.id)) menu.releasePointerCapture(ended.id);
    if (ended.moved) {
      // Snap to the nearest card, including a short deliberate swipe.
      const total = Math.round(ended.total / 44) * 44;
      if (!cancelled) api.slide({phase: "move", total});
      await api.slide({phase: cancelled ? "cancel" : "end", total});
    }
    refresh();
  }
  menu.addEventListener("pointerup", event => { void finish(event); });
  ["pointercancel", "lostpointercapture"].forEach(type => menu.addEventListener(type, event => { void finish(event, true); }));
  window.addEventListener("pointerup", event => { if (drag && !drag.moved) void finish(event, true); });
  menu.addEventListener("click", event => {
    if (suppressClick) { suppressClick = false; event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  menu.addEventListener("dragstart", event => event.preventDefault());

  function command(text, state) {
    const normalized = String(text).trim().replace(/[\s，。！？、,.!?]/g, "").replace(/^(?:请)?(?:帮我)?(?:选择|打开|进入|切换到|切换)?/, "");
    const scenes = [["拍照解题", "拍照", "解题"], ["会议纪要", "会议"], ["实时翻译", "翻译"], ["地图", "导航"]];
    const scene = scenes.findIndex(names => names.includes(normalized));
    if (scene >= 0) return {action: `scene${scene}`, label: scenes[scene][0], matches: next => next.mode === scene && next.screen === "actions"};
    const moves = {"上一个": "previous", "上一个功能": "previous", "下一个": "next", "下一个功能": "next", "返回主菜单": "home", "主菜单": "home"};
    if (moves[normalized]) {
      const action = moves[normalized];
      const focus = (state.focus + (action === "previous" ? -1 : 1) + state.options.length) % state.options.length;
      return {action, label: normalized, matches: next => action === "home" ? next.screen === "home" : next.screen === state.screen && next.focus === focus};
    }
    if (state.screen === "actions") {
      const index = state.options.indexOf(normalized);
      if (index >= 0) return {action: `focus${index}`, label: normalized, matches: next => next.mode === state.mode && next.screen === "actions" && next.focus === index};
    }
    return null;
  }
  voice.addEventListener("click", () => {
    if (recognition) { stopVoice("语音选择已取消。"); return; }
    if (!supported || !api.state().available) return;
    const current = ++ticket;
    try {
      const SpeechRecognition = recognizer();
      const speech = recognition = new SpeechRecognition();
      speech.lang = "zh-CN"; speech.continuous = false; speech.interimResults = true; speech.maxAlternatives = 1;
      voice.setAttribute("aria-pressed", "true"); voiceLabel.textContent = "停止聆听";
      status.textContent = "请说功能名称，例如“实时翻译”。";
      speech.onresult = async event => {
        if (current !== ticket || recognition !== speech || !api.state().available) return;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i], text = result[0]?.transcript || "";
          if (!result.isFinal) { status.textContent = `正在听：${text}`; continue; }
          const selected = command(text, api.state());
          stopVoice(selected ? `识别到“${selected.label}”，正在选择…` : `未匹配“${text}”。请说拍照解题、会议纪要、实时翻译或地图。`);
          if (selected) {
            try {
              await run(selected.action);
              status.textContent = selected.matches(api.state()) ? `已选择：${selected.label}。点击卡片确认具体操作。` : "当前无法切换，请稍后重试。";
            } catch (_) { status.textContent = "选择未完成，请重试。"; }
          }
          return;
        }
      };
      speech.onerror = event => {
        if (current !== ticket) return;
        const messages = {"not-allowed": "麦克风权限未开启，请允许访问后重试。", "service-not-allowed": "语音识别服务不可用，请使用滑动选择。", "audio-capture": "未找到可用麦克风，请检查设备。", "network": "语音识别网络异常，请重试或滑动选择。", "no-speech": "没有听清，请点击语音选择后再说一次。"};
        stopVoice(messages[event.error] || "语音识别已停止，请重试或滑动选择。");
      };
      speech.onend = () => { if (current === ticket) stopVoice("未听到完整指令，请点击后再试一次。"); };
      speech.start();
      timer = setTimeout(() => { if (current === ticket) stopVoice("聆听已结束，请点击按钮再说一次。"); }, 12000);
    } catch (_) { stopVoice("无法启动语音识别，请检查麦克风权限或使用滑动选择。"); }
  });
  const stop = () => { stopVoice("语音选择已停止。"); void finish(null, true); };
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });
  window.addEventListener("pagehide", stop);
  window.addEventListener("hashchange", () => { if (location.hash === "#memory") stop(); });
  window.addEventListener("blur", () => { void finish(null, true); });
  window.LensNavigation = {refresh, stopVoice, stop};
  refresh();
})();
