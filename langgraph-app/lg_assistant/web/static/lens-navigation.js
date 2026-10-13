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
  privacy.textContent = "单功能本地选择；依赖前文或多个任务交给云端。";
  navigation.append(status, privacy);
  menu.setAttribute("aria-label", "左右滑动选择功能，点击卡片确认");
  const $ = id => document.getElementById(id);
  const voice = $("hw-menu-voice");
  const voiceLabel = voice.querySelector("span");
  const recognizer = () => window.SpeechRecognition || window.webkitSpeechRecognition;
  const supported = !!recognizer()?.prototype && 'processLocally' in recognizer().prototype && typeof recognizer().available === 'function' && window.isSecureContext !== false;
  let drag = null, suppressClick = false, recognition = null, ticket = 0, timer = null, preparing = false;
  const session = window.EdgeIntent?.createSession();
  const consolePanel = document.createElement('section');
  consolePanel.className = 'hw-intent-console';
  consolePanel.setAttribute('aria-label','本地语音与任务指令');
  consolePanel.innerHTML = `<header><strong>语音与任务</strong><span id="hw-intent-route">本地就绪</span><button type="button" id="hw-intent-reset">新任务</button></header><form id="hw-intent-form"><input id="hw-intent-text" aria-label="文字指令" maxlength="600" autocomplete="off" placeholder="试试：打开地图 / 先总结再翻译"><button type="submit">执行</button></form><p id="hw-intent-result" role="status">单个功能在设备内识别。相关追问、多个任务会发送文字到云端。</p><button type="button" id="hw-speech-install" hidden>下载本地中文语音包</button><label id="hw-intent-access" hidden>云端访问口令<input type="password" id="hw-intent-token" autocomplete="current-password" maxlength="256"></label>`;
  host.querySelector('.hw-glasses-panel').append(consolePanel);
  const input=$('hw-intent-text'), output=$('hw-intent-result'), routeLabel=$('hw-intent-route'), submit=$('hw-intent-form').querySelector('button');
  let processing=false, request=null, operation=0;
  const report = message => {status.textContent=message;output.textContent=message;};
  const matches = (decision,before,after) => {
    if(decision.action.startsWith('scene')) return after.mode===Number(decision.action.slice(5)) && after.screen==='actions';
    if(decision.action.startsWith('focus')) return after.mode===before.mode && after.focus===Number(decision.action.slice(5)) && after.screen==='actions';
    if(decision.action==='home') return after.screen==='home';
    const focus=(before.focus+(decision.action==='previous'?-1:1)+before.options.length)%before.options.length;
    return after.screen===before.screen && after.focus===focus;
  };
  async function handleText(text) {
    if(processing || !api.state().available || !session) return;
    stopVoice();
    const before=api.state(), decision=session.decide(text,before), current=++operation;
    if(decision.route==='clarify') {
      routeLabel.textContent='本地 · 待补充';report(decision.message);session.remember(text,decision);return;
    }
    processing=true;refresh();
    try {
      if(decision.route==='local') {
        await run(decision.action);
        if(current!==operation) return;
        const success=matches(decision,before,api.state());
        routeLabel.textContent='本地 · 未上传';
        report(success?`已选择：${decision.label}。点击卡片确认具体操作。`:'当前无法切换，请稍后重试。');
        if(success) session.remember(text,decision);
      } else {
        routeLabel.textContent=`云端 · ${decision.label}`;
        report(`正在提交：${decision.label}。仅发送本次文字与相关前文。`);
        let access=$('hw-intent-token').value.trim();
        if(!access) {try{access=sessionStorage.getItem('assistant.translation.access')||'';}catch(_) {}}
        const controller=request=new AbortController();
        const timeout=setTimeout(()=>controller.abort(),25000);
        let response,body;
        try {
          response=await fetch('/api/intent-cloud',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json',...(access?{Authorization:`Bearer ${access}`}:{})},body:JSON.stringify({text,reason:decision.reason,context:decision.context,requestId:crypto.randomUUID()})});
          body=await response.json();
        } finally {clearTimeout(timeout);}
        if(current!==operation) return;
        if(response.status===401) $('hw-intent-access').hidden=false;
        if(!response.ok) throw new Error(body.error||'云端请求失败，请稍后重试。');
        if(typeof body.reply!=='string'||!body.reply.trim()) throw new Error('云端没有返回有效结果。');
        report(body.reply);routeLabel.textContent='云端 · 已回复';session.remember(text,decision,body.reply);
      }
    } catch(error) {
      if(current===operation) {routeLabel.textContent=decision.route==='cloud'?'云端 · 未完成':'本地 · 未完成';report(error.name==='AbortError'?'请求已停止；执行状态未确认，请勿重复提交。':error.message);}
    } finally {if(current===operation){processing=false;request=null;refresh();}}
  }
  function resetTask() {operation++;request?.abort();request=null;processing=false;session?.reset();stopVoice();refresh();}
  $('hw-intent-form').addEventListener('submit',event=>{event.preventDefault();void handleText(input.value);});
  $('hw-intent-reset').addEventListener('click',()=>{resetTask();input.value='';routeLabel.textContent='本地就绪';report('已开始新任务，相关前文已清空。');});

  function stopVoice(message) {
    ticket++;
    preparing = false;
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
    $("hw-menu-prev").disabled = $("hw-menu-next").disabled = !canSelect || processing || state.options.length < 2;
    voice.disabled = !supported || !canSelect || processing;
    submit.disabled = !canSelect || processing;
    input.disabled = !canSelect || processing;
    if (!supported && routeLabel.textContent==='本地就绪') status.textContent = "浏览器不支持本地语音，请在下方输入文字指令或滑动选择。";
    if ((recognition || preparing) && !canSelect) stopVoice("语音选择已停止。点击按钮可重新开始。");
  }
  function run(action) { return api.act(action); }
  $("hw-menu-prev").addEventListener("click", () => run("previous"));
  $("hw-menu-next").addEventListener("click", () => run("next"));

  // A horizontal drag belongs to the menu, while the lens frame still rotates.
  menu.addEventListener("pointerdown", event => {
    if (event.button !== 0 || drag || processing || !api.state().available) return;
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

  voice.addEventListener("click", async () => {
    if (recognition || preparing) { stopVoice("语音选择已取消。"); return; }
    if (!supported || !api.state().available) return;
    const current = ++ticket;
    try {
      const SpeechRecognition = recognizer();
      preparing=true;voice.setAttribute('aria-pressed','true');voiceLabel.textContent='取消';
      status.textContent='正在检查本地中文语音包…';
      const availability=await SpeechRecognition.available({langs:['zh-CN'],processLocally:true});
      if(current!==ticket || !api.state().available) return;
      preparing=false;
      if(availability!=='available') {
        $('hw-speech-install').hidden=availability!=='downloadable';
        stopVoice(availability==='downloadable'?'请先在下方下载本地中文语音包。':availability==='downloading'?'中文语音包正在下载，请稍后重试。':'此浏览器尚无本地中文语音包，请使用文字指令。');return;
      }
      $('hw-speech-install').hidden=true;
      const speech = recognition = new SpeechRecognition();
      speech.processLocally = true;
      speech.lang = "zh-CN"; speech.continuous = false; speech.interimResults = true; speech.maxAlternatives = 1;
      voice.setAttribute("aria-pressed", "true"); voiceLabel.textContent = "停止聆听";
      status.textContent = "请说功能名称，例如“实时翻译”。";
      speech.onresult = async event => {
        if (current !== ticket || recognition !== speech || !api.state().available) return;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i], text = result[0]?.transcript || "";
          if (!result.isFinal) { status.textContent = `正在听：${text}`; continue; }
          stopVoice();input.value=text;await handleText(text);
          return;
        }
      };
      speech.onerror = event => {
        if (current !== ticket) return;
        const messages = {"language-not-supported":"本地中文语音包不可用，请输入文字指令。", "not-allowed": "麦克风权限未开启，请允许访问后重试。", "service-not-allowed": "本地语音识别不可用，请使用文字指令。", "audio-capture": "未找到可用麦克风，请检查设备。", "network": "本地语音识别失败，请输入文字指令。", "no-speech": "没有听清，请点击语音选择后再说一次。"};
        stopVoice(messages[event.error] || "语音识别已停止，请重试或滑动选择。");
      };
      speech.onend = () => { if (current === ticket) stopVoice("未听到完整指令，请点击后再试一次。"); };
      speech.start();
      timer = setTimeout(() => { if (current === ticket) stopVoice("聆听已结束，请点击按钮再说一次。"); }, 12000);
    } catch (_) { stopVoice("无法启动语音识别，请检查麦克风权限或使用滑动选择。"); }
  });
  $('hw-speech-install').addEventListener('click',async()=>{
    const button=$('hw-speech-install');button.disabled=true;
    report('正在下载本地中文语音包。下载只获取模型，不上传声音。');
    try {const ready=await recognizer().install({langs:['zh-CN'],processLocally:true});report(ready?'语音包已就绪，请点击语音选择。':'语音包安装未完成，请重试或输入文字指令。');button.hidden=!!ready;}
    catch(_){report('语音包无法安装，请使用文字指令。');}finally{button.disabled=false;}
  });
  const stop = () => { resetTask();stopVoice("语音选择已停止。"); void finish(null, true); };
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });
  window.addEventListener("pagehide", stop);
  window.addEventListener("hashchange", () => { if (location.hash === "#memory") stop(); });
  window.addEventListener("blur", () => { void finish(null, true); });
  window.LensNavigation = {refresh, stopVoice, stop};
  refresh();
})();
