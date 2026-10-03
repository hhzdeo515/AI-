/* Browser speech recognition feeds confirmed sentences to the real translation API. */
(() => {
  "use strict";
  const languages = [
    ["zh-CN", "中文", "zh-CN"], ["en", "英语", "en-US"], ["ja", "日语", "ja-JP"],
    ["ko", "韩语", "ko-KR"], ["fr", "法语", "fr-FR"], ["de", "德语", "de-DE"],
    ["es", "西班牙语", "es-ES"], ["pt", "葡萄牙语", "pt-PT"], ["it", "意大利语", "it-IT"],
    ["ru", "俄语", "ru-RU"], ["ar", "阿拉伯语", "ar-SA"], ["hi", "印地语", "hi-IN"],
    ["th", "泰语", "th-TH"], ["vi", "越南语", "vi-VN"], ["id", "印度尼西亚语", "id-ID"],
  ];
  const encoder = new TextEncoder();
  const accessKey = "assistant.translation.access";
  let panel = null, nodes = {}, visible = false, generation = 0, worker = null, pending = null;
  let queue = [], originals = [], translations = [], interim = "", queuedDropped = false;
  let listening = false, recognition = null, speechGeneration = 0, restartTimer = null, idleEnds = 0;
  let observer = null;
  const recognizerType = () => window.SpeechRecognition || window.webkitSpeechRecognition;
  const locale = value => languages.find(language => language[0] === value)?.[2] || value;
  const setStatus = text => { if (nodes.status) nodes.status.textContent = text; };
  const speechStatus = text => { if (nodes["speech-status"]) nodes["speech-status"].textContent = text; };
  const speechSupported = () => !!recognizerType() && window.isSecureContext !== false;
  const synthesisSupported = () => !!window.speechSynthesis && !!window.SpeechSynthesisUtterance;

  function readAccess() {
    try { return window.sessionStorage.getItem(accessKey) || ""; } catch (_) { return ""; }
  }

  function saveAccess() {
    const value = nodes.access.value.trim();
    if (!value) return true;
    try { window.sessionStorage.setItem(accessKey, value); return true; }
    catch (_) { setStatus("浏览器无法保存会话口令，请允许此网站的会话存储后重试。"); return false; }
  }

  function updateControls() {
    if (!panel) return;
    nodes.start.disabled = !visible || !speechSupported();
    nodes.start.textContent = listening ? "停止实时语音" : "开始实时语音";
    nodes.start.setAttribute("aria-pressed", String(listening));
    nodes.translate.disabled = !visible || worker !== null || queue.length > 0;
    nodes.speak.disabled = !visible || !translations.length || !synthesisSupported();
    nodes.copy.disabled = !originals.length && !interim;
    nodes.count.textContent = `${Array.from(nodes.text.value).length} / 2000 字`;
  }

  function stopSpeech() {
    try { window.speechSynthesis?.cancel(); } catch (_) {}
  }

  function stopRecognition(message) {
    speechGeneration++; listening = false;
    clearTimeout(restartTimer); restartTimer = null;
    const previous = recognition; recognition = null;
    if (previous) {
      previous.onstart = previous.onresult = previous.onerror = previous.onend = null;
      try { previous.abort(); } catch (_) { try { previous.stop(); } catch (_) {} }
    }
    if (message) speechStatus(message);
    updateControls();
  }

  function resetWork(clear = false) {
    generation++; queue = []; worker = null; queuedDropped = false;
    pending?.abort(); pending = null; stopSpeech();
    if (clear) {
      originals = []; translations = []; interim = "";
      nodes.original.textContent = ""; nodes.translation.textContent = ""; nodes.interim.textContent = "";
    }
    updateControls();
  }

  // for...of keeps UTF-16 surrogate pairs together; each request is capped in UTF-8 bytes.
  function segments(text) {
    const result = [], chars = Array.from(text);
    let part = "", bytes = 0;
    const flush = () => { if (part.trim()) result.push(part); part = ""; bytes = 0; };
    chars.forEach((char, index) => {
      const size = encoder.encode(char).length;
      if (bytes + size > 500) flush();
      part += char; bytes += size;
      if (/[。！？!?\n]/u.test(char) || char === "." && (index === chars.length - 1 || /\s/u.test(chars[index + 1]))) flush();
    });
    flush(); return result;
  }

  function speak(text) {
    if (!visible || !text || !synthesisSupported()) return;
    stopRecognition("实时语音已暂停以避免识别播报声音；需要时请手动开始。");
    try {
      stopSpeech();
      const utterance = new window.SpeechSynthesisUtterance(text);
      utterance.lang = locale(nodes.target.value);
      window.speechSynthesis.speak(utterance);
    } catch (_) { setStatus("当前浏览器无法播报译文，仍可阅读或复制文字。"); }
  }

  async function translatePart(text, source, target, ticket) {
    const controller = new AbortController(); pending = controller;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 20000);
    try {
      const headers = { "Content-Type": "application/json" }, access = readAccess();
      if (access) headers.Authorization = `Bearer ${access}`;
      const response = await fetch("/api/translate", {
        method: "POST", headers,
        body: JSON.stringify({ text, source, target }), signal: controller.signal,
      });
      const data = await response.json().catch(() => null);
      if (ticket !== generation || !visible) return null;
      if (response.status === 401 && data?.code === "authentication_required") {
        nodes["access-row"].hidden = false; nodes.access.value = "";
        try { window.sessionStorage.removeItem(accessKey); } catch (_) {}
        throw new Error("需要有效的翻译访问口令；填写后点击翻译文字或开始实时语音重试。");
      }
      if (!response.ok) throw new Error(data?.error || (response.status === 429 ? "翻译额度暂时用完，请稍后重试。" : `翻译服务暂时不可用（${response.status}），请稍后重试。`));
      if (typeof data?.translation !== "string" || !data.translation.trim() || data.provider !== "MyMemory") throw new Error("翻译服务未返回有效结果，请重试。");
      nodes["access-row"].hidden = true; nodes.access.value = "";
      return data.translation;
    } catch (error) {
      if (ticket !== generation || !visible) return null;
      if (timedOut) throw new Error("翻译请求超时，请稍后重试。");
      if (error.name === "AbortError") throw error;
      if (error instanceof TypeError) throw new Error("翻译网络连接失败，请检查网络后重试。");
      throw error;
    } finally {
      clearTimeout(timer);
      if (pending === controller) pending = null;
    }
  }

  function revealTranslation() {
    const scroller = panel?.querySelector(".hw-translation-content");
    if (!scroller || scroller.clientHeight <= 0) return;
    const viewport = scroller.getBoundingClientRect(), subtitle = nodes.translation.getBoundingClientRect();
    const toolbar = scroller.querySelector(".hw-translation-toolbar");
    const top = viewport.top + (toolbar?.getBoundingClientRect().height || 0) + 8, bottom = viewport.bottom - 8;
    if (bottom <= top) return;
    let offset = 0;
    if (subtitle.bottom > bottom) offset = subtitle.height > bottom - top ? subtitle.bottom - bottom : subtitle.top - top;
    else if (subtitle.top < top) offset = subtitle.top - top;
    // Never use scrollIntoView: it can also move the stage and browser page.
    scroller.scrollTop = Math.max(0, scroller.scrollTop + offset);
  }

  async function drain() {
    const ticket = generation;
    if (worker === ticket || !visible) return;
    worker = ticket; updateControls();
    try {
      while (queue.length && ticket === generation && visible) {
        const item = queue.shift(), chunks = segments(item.text), output = [];
        for (let index = 0; index < chunks.length; index++) {
          setStatus(`正在翻译${chunks.length > 1 ? ` · 第 ${index + 1} / ${chunks.length} 段` : ""}…`);
          const result = await translatePart(chunks[index], item.source, item.target, ticket);
          if (ticket !== generation || !visible || result === null) return;
          output.push(result);
        }
        translations.push(output.join("\n")); translations = translations.slice(-30);
        nodes.translation.textContent = translations.join("\n");
        revealTranslation();
      }
      if (ticket === generation && visible) setStatus(queuedDropped ? "已翻译接收的语句；未翻译语句已保留，可放入输入框重试。" : "翻译完成 · MyMemory");
    } catch (error) {
      if (ticket !== generation || !visible) return;
      queue = [];
      stopRecognition("实时语音已暂停，可检查网络后重新开始。");
      setStatus(error.message || "翻译未完成，请重试。");
    } finally {
      if (worker === ticket) { worker = null; updateControls(); }
    }
  }

  function enqueue(text) {
    text = text.trim();
    if (!visible || !text) return;
    originals.push(text); originals = originals.slice(-30);
    nodes.original.textContent = originals.join("\n");
    if (queue.length >= 25) {
      queuedDropped = true;
      const message = "等待翻译的语句较多，已暂停录音；未翻译语句已保留，可放入输入框重试。";
      stopRecognition(message); setStatus(message); updateControls();
      return;
    }
    queue.push({ text, source: nodes.source.value, target: nodes.target.value });
    updateControls(); void drain();
  }

  function startRecognition() {
    if (!visible || !listening || !speechSupported()) return;
    const ticket = ++speechGeneration;
    const current = new (recognizerType())(); recognition = current;
    current.continuous = true; current.interimResults = true; current.lang = locale(nodes.source.value);
    const active = () => visible && listening && ticket === speechGeneration && recognition === current;
    current.onstart = () => { if (active()) speechStatus("正在听取语音 · 说完一句后自动翻译"); };
    current.onresult = event => {
      if (!active()) return;
      const preview = [];
      for (let index = event.resultIndex || 0; index < event.results.length; index++) {
        const result = event.results[index], text = result[0]?.transcript || "";
        if (result.isFinal) { idleEnds = 0; enqueue(text); }
        else preview.push(text);
      }
      interim = preview.join(""); nodes.interim.textContent = interim; updateControls();
    };
    current.onerror = event => {
      if (!active()) return;
      const messages = {
        "not-allowed": "麦克风权限未获允许，请检查浏览器权限后手动开始，或使用文字翻译。",
        "service-not-allowed": "浏览器语音服务未获允许，可使用文字翻译。",
        "audio-capture": "未找到可用麦克风，请检查设备或使用文字翻译。",
        network: "浏览器语音识别网络异常，已停止录音；恢复网络后可手动开始。",
        "no-speech": "暂未识别到语音，已停止录音；可重新开始或使用文字翻译。",
        "language-not-supported": "当前浏览器不支持此语言的语音识别，可使用文字翻译。",
      };
      stopRecognition(messages[event.error] || "语音识别已停止，请手动重试或使用文字翻译。");
    };
    current.onend = () => {
      if (!active()) return;
      recognition = null;
      current.onstart = current.onresult = current.onerror = current.onend = null;
      if (++idleEnds > 2) { stopRecognition("暂未收到新的语音，已停止录音；需要时可重新开始。"); return; }
      speechStatus("继续听取语音…");
      restartTimer = setTimeout(() => {
        restartTimer = null;
        if (visible && listening && ticket === speechGeneration) startRecognition();
      }, 250);
    };
    try { current.start(); }
    catch (_) { stopRecognition("无法开启浏览器语音识别，请检查权限后重试，或使用文字翻译。"); }
    updateControls();
  }

  function changeLanguages() {
    stopRecognition("语言已切换，需要实时语音时请重新开始。");
    resetWork(true); setStatus("语言已切换，旧译文已清空。");
  }

  function mount(element) {
    if (!element) throw new Error("需要实时翻译面板。");
    if (panel === element) return panel;
    if (panel) close();
    observer?.disconnect(); panel = element;
    panel.innerHTML = `<header><strong>实时翻译</strong><button type="button" data-hw-back>返回</button></header>
      <div class="hw-panel-scroll hw-translation-content">
        <div class="hw-translation-toolbar"><div class="hw-translation-languages"><label>原文语言<select id="hw-translation-source" aria-label="原文语言"></select></label><button type="button" id="hw-translation-swap" aria-label="交换原文与译文语言" title="交换语言">⇄</button><label>译文语言<select id="hw-translation-target" aria-label="译文语言"></select></label></div>
          <div class="hw-panel-actions"><button type="button" class="hw-primary" id="hw-translation-start" aria-pressed="false">开始实时语音</button><button type="button" id="hw-translation-copy" disabled>原文放入输入框</button></div>
          <p id="hw-translation-speech-status" role="status" aria-live="polite"></p>
          <label class="hw-translation-access" id="hw-translation-access-row" hidden>翻译访问口令<input type="password" id="hw-translation-access" aria-label="翻译访问口令" autocomplete="current-password" maxlength="256" placeholder="填写后点击翻译或开始"></label></div>
        <div class="hw-translation-outputs"><section class="hw-translation-output"><h3>原文</h3><p id="hw-translation-original" dir="auto"></p><p id="hw-translation-interim" dir="auto" aria-label="正在识别，尚未发送翻译"></p></section>
          <section class="hw-translation-output"><h3>译文</h3><p id="hw-translation-translation" dir="auto" aria-live="polite"></p><div class="hw-panel-actions"><button type="button" id="hw-translation-speak" disabled>播报译文</button></div></section></div>
        <p id="hw-translation-status" role="status" aria-live="polite"></p>
        <label class="hw-translation-text-label">文字翻译<textarea id="hw-translation-text" rows="3" maxlength="4000" placeholder="输入文字后点击翻译，最多 2000 字"></textarea></label>
        <div class="hw-translation-submit"><span id="hw-translation-count">0 / 2000 字</span><button type="button" class="hw-primary" id="hw-translation-translate">翻译文字</button></div>
        <p class="hw-translation-note">语音识别由浏览器提供，可能使用浏览器厂商服务。确认的语句和手动提交的文字发送至 MyMemory 翻译服务，免费额度有限。麦克风只在点击开始后开启。</p>
      </div>`;
    nodes = {};
    panel.querySelectorAll('[id^="hw-translation-"]').forEach(node => { nodes[node.id.slice("hw-translation-".length)] = node; });
    for (const key of ["source", "target"]) languages.forEach(([value, label]) => {
      const option = document.createElement("option"); option.value = value; option.textContent = label; nodes[key].append(option);
    });
    nodes.source.value = "zh-CN"; nodes.target.value = "en";
    nodes.source.addEventListener("change", changeLanguages); nodes.target.addEventListener("change", changeLanguages);
    nodes.swap.addEventListener("click", () => { const value = nodes.source.value; nodes.source.value = nodes.target.value; nodes.target.value = value; changeLanguages(); });
    nodes.text.addEventListener("input", updateControls);
    nodes.translate.addEventListener("click", () => {
      const text = nodes.text.value.trim();
      if (!text) { setStatus("请先输入要翻译的文字。"); nodes.text.focus(); return; }
      if (Array.from(text).length > 2000) { setStatus("每次文字翻译最多 2000 字，请拆分后提交。"); return; }
      if (!saveAccess()) return;
      stopRecognition("实时语音已暂停，正在翻译输入文字。"); resetWork(true); enqueue(text);
    });
    nodes.start.addEventListener("click", () => {
      if (listening) { stopRecognition("实时语音已停止，已确认的语句会继续翻译。"); return; }
      if (!speechSupported()) return;
      if (!saveAccess()) return;
      stopSpeech();
      listening = true; idleEnds = 0; speechStatus("正在开启浏览器语音识别，请允许麦克风权限。"); startRecognition();
    });
    nodes.copy.addEventListener("click", () => {
      const text = [...originals, interim].filter(Boolean).join("\n"), chars = Array.from(text);
      nodes.text.value = chars.slice(0, 2000).join(""); updateControls();
      setStatus(chars.length > 2000 ? "已复制前 2000 字；点击翻译文字后才会发送。" : "原文已放入输入框；点击翻译文字后才会发送。");
    });
    nodes.speak.addEventListener("click", () => speak(translations.join("\n")));
    observer = new MutationObserver(() => { if (visible && panel.hidden) close(); });
    observer.observe(panel, { attributes: true, attributeFilter: ["hidden"] });
    resetWork(true); updateControls(); return panel;
  }

  function open() {
    if (!panel) return;
    if (visible && !panel.hidden) { updateControls(); return; }
    visible = true; panel.hidden = false;
    if (!speechSupported()) speechStatus("当前浏览器不支持实时语音识别，请使用 HTTPS 下的兼容浏览器，或直接输入文字翻译。");
    else speechStatus("实时语音尚未开启；点击开始才会使用麦克风。");
    if (!translations.length) setStatus("输入文字，或开始实时语音。");
    updateControls();
  }

  function close() {
    visible = false; stopRecognition("实时语音已停止，麦克风已释放。"); resetWork();
    if (nodes.access) { nodes.access.value = ""; nodes["access-row"].hidden = true; }
    if (panel) panel.hidden = true;
  }

  document.addEventListener("visibilitychange", () => { if (document.hidden) close(); });
  window.addEventListener("pagehide", close);
  window.addEventListener("hashchange", () => { if (location.hash !== "#hardware") close(); });
  window.LensFeatures = window.LensFeatures || {};
  window.LensFeatures.translation = Object.freeze({ mount, open, close });
})();
