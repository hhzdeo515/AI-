/* Hardware is simulated; imported media is submitted to the existing real AI backend. */
(() => {
  "use strict";
  const host = document.getElementById("hardware-demo");
  if (!host) return;
  host.innerHTML = `
    <div class="hw-layout">
      <section class="hw-glasses-panel" aria-label="眼镜镜片视图">
        <div class="hw-device-label"><h2>眼镜视野 <small>Lens display</small></h2><span id="hw-mode-label">拍照解题</span></div>
        <div class="hw-optics"><span class="hw-hinge" aria-hidden="true"></span><span class="hw-bridge" aria-hidden="true"></span><div class="hw-lens-frame"><div class="hw-viewport">
          <img id="hw-scene-image" alt="导入的眼前画面" hidden>
          <span class="hw-source-tag">拍摄原图</span>
          <div class="hw-nav-menu" id="hw-nav-menu" aria-label="镜片操作菜单"><div class="hw-nav-heading"><strong id="hw-nav-title">选择一个场景</strong><span class="hw-nav-back-hint" id="hw-nav-back-hint" hidden>长按返回上一页</span></div><div id="hw-nav-options" role="group" aria-label="滑动选择，单击确认"></div><p id="hw-nav-description"></p></div>
          <div id="hw-empty-scene" class="hw-scene-empty"><div class="hw-reticle" aria-hidden="true"><i></i><i></i><i></i><i></i><svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M24 15v18M15 24h18"/></svg></div><strong id="hw-empty-title">把题目放到眼前</strong><p id="hw-empty-copy">同一张照片可包含多道题，题干和选项请拍完整。</p></div>
          <div class="hw-hud"><div class="hw-hud-top"><span class="hw-hud-brand"><i></i>AI 视野</span><span id="hw-lens-status">等待导入</span></div><span class="hw-hud-bottom" id="hw-lens-hint">单击拍照 · 滑动切换场景</span></div>
          <div id="hw-lens-result" class="hw-lens-result" hidden><div class="hw-result-header"><strong id="hw-result-title">解题结果</strong><div><button type="button" id="hw-practice-open" hidden>继续练习</button><button type="button" id="hw-result-speak">播报</button><a id="hw-result-download" download>下载</a><button type="button" id="hw-hide-result">收起</button></div></div><nav id="hw-question-nav" class="hw-question-nav" aria-label="切换照片中的题目" hidden><button type="button" id="hw-question-prev" aria-label="上一题">上一题</button><select id="hw-question-select" aria-label="选择题目"></select><button type="button" id="hw-question-next" aria-label="下一题">下一题</button><span id="hw-question-position" aria-live="polite"></span></nav><div id="hw-result-body" class="md" tabindex="0" aria-label="镜片内的 AI 结果，可滚动阅读"></div><div class="hw-result-footer">AI 回显<span>滚动阅读完整内容</span></div></div>
          <section class="hw-lens-panel" id="hw-camera-panel" hidden aria-label="镜片内拍照"><header><strong>拍照解题 · 摄像头</strong><button type="button" data-hw-back>收起</button></header><video id="hw-camera-video" autoplay playsinline muted aria-label="摄像头画面"></video><p id="hw-camera-status" role="status">正在打开摄像头…</p><div class="hw-panel-actions"><button type="button" class="hw-primary" id="hw-camera-shoot" disabled>拍照并解题</button><button type="button" id="hw-camera-import">导入图片</button><label><input type="checkbox" id="hw-camera-silent" checked>拍照静音</label></div></section>
          <section class="hw-lens-panel" id="hw-record-panel" hidden aria-label="镜片内会议录音"><header><strong>会议纪要 · 采集内容</strong><button type="button" data-hw-back>收起</button></header><div class="hw-panel-scroll"><p id="hw-record-status" role="status">录音尚未开启</p><button type="button" class="hw-primary" id="hw-record-toggle">开启录音</button><audio id="hw-audio-player" controls hidden></audio><p id="hw-audio-name" class="hw-file-name">可录制会议，也可导入已有音频。</p><div class="hw-panel-actions"><button type="button" id="hw-record-import">导入录音</button><button type="button" id="hw-record-use" disabled>生成纪要</button><a id="hw-record-download" hidden>下载录音</a><button type="button" id="hw-record-delete" disabled>删除录音</button></div><details class="hw-meeting-notes"><summary>会议文字</summary><textarea id="hw-meeting-text" rows="3" placeholder="输入会议内容，单独生成文字纪要"></textarea><button type="button" id="hw-meeting-summarize">生成文字纪要</button></details></div></section>
          <section class="hw-lens-panel" id="hw-settings-panel" hidden aria-label="镜片内解题设置"><header><strong>拍照解题 · 设置与资料</strong><button type="button" data-hw-back>收起</button></header><div class="hw-panel-scroll" id="hw-settings-content"><section class="hw-learning-settings"><h3>学习模式</h3><div id="hw-practice-modes" class="hw-practice-modes" role="group" aria-label="选择学习模式"><button type="button" data-practice-agent="auto" aria-pressed="true">自动分类</button><button type="button" data-practice-agent="ability" aria-pressed="false">职业能力测试</button><button type="button" data-practice-agent="essay" aria-pressed="false">策论</button><button type="button" data-practice-agent="interview" aria-pressed="false">面试</button></div><p id="hw-practice-mode-note">拍照后先识别题面，再进入对应学习流程。</p><button type="button" id="hw-practice-resume" class="hw-primary" hidden>按所选模式继续</button><label id="hw-practice-topic-wrap" hidden>练习题目或主题<textarea id="hw-practice-topic" rows="3" maxlength="4000" placeholder="输入策论题目或面试主题，也可以返回后拍照导入题面"></textarea></label><button type="button" id="hw-practice-start-text" class="hw-primary" hidden>从文字开始练习</button></section></div></section>
          <section class="hw-lens-panel hw-practice-panel" id="hw-practice-panel" hidden aria-label="镜片内学习练习"><header><strong id="hw-practice-title">学习练习</strong><button type="button" data-hw-back>返回结果</button></header><div class="hw-panel-scroll"><p id="hw-practice-source" class="hw-practice-source"></p><p id="hw-practice-question"></p><div id="hw-practice-stages" class="hw-practice-stages" role="tablist" aria-label="学习步骤"></div><section id="hw-practice-stage-body" class="md" role="tabpanel" tabindex="0" aria-label="当前学习步骤"></section><div id="hw-practice-answer-wrap" hidden><label>我的作答<textarea id="hw-practice-answer" rows="4" maxlength="20000" placeholder="写下你的真实回答，或先录音作答"></textarea></label><div class="hw-panel-actions"><button type="button" id="hw-practice-record">开启录音作答</button><button type="button" id="hw-practice-audio-clear" hidden>删除本轮录音</button></div><p id="hw-practice-record-status" role="status">录音或文字作答均可，提交后显示点评与追问。</p><audio id="hw-practice-audio" controls hidden></audio></div><label id="hw-practice-draft-wrap" hidden>我的草稿<textarea id="hw-practice-draft" rows="5" maxlength="40000" placeholder="填写你自己写的策论草稿，再提交批改；上方范文仅供参考"></textarea></label><details id="hw-practice-transcript-wrap" hidden><summary>上一轮作答</summary><p id="hw-practice-transcript"></p></details><div id="hw-practice-actions" class="hw-panel-actions"></div><p class="hw-practice-back">长按返回完整结果，再长按返回选择操作。</p></div></section>
          <input type="file" id="hw-image-file" accept="image/jpeg,image/png,image/webp" hidden><input type="file" id="hw-audio-file" accept=".wav,.mp3,.m4a,.aac,.flac,.ogg,.amr,.wma,.webm" hidden>
          <div class="hw-processing" id="hw-processing" role="status" aria-live="polite" hidden><span></span>正在识别照片中的题目</div>
          <div class="hw-notice" role="status" aria-live="polite" id="hw-notice" hidden></div>
          <div class="hw-flash" id="hw-flash"></div>
        </div></div><div class="hw-optics-caption"><span>单镜片 · 第一人称预览</span><span>画面与答案，同屏呈现</span></div></div>
      </section>
      <aside class="hw-ring-panel" aria-label="戒指操控窗口"><div class="hw-device-label"><h2>智能戒指</h2><span class="hw-status" id="hw-connection">模拟已连接</span></div>
        <div class="hw-ring-stage"><button type="button" class="hw-ring" id="hw-ring" aria-label="上下滑动操控眼镜，轻点确认，长按返回上一页" aria-describedby="hw-gesture-guide"><span class="hw-ring-inner"></span><span class="hw-ring-touch"></span><canvas id="hw-ring-canvas" aria-hidden="true"></canvas><span class="hw-flow-rail" aria-hidden="true"><svg class="hw-flow-up" viewBox="0 0 16 16"><path d="m4 10 4-4 4 4"/></svg><span class="hw-flow-track"><span class="hw-flow-ticks"></span><span class="hw-flow-thumb"></span></span><svg class="hw-flow-down" viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg></span><span class="hw-flow-caption" aria-hidden="true">上下滑动 · 轻点确认</span></button></div>
        <div class="hw-ring-tools"><span id="hw-ring-state" role="status" aria-live="polite">等待操作</span><button type="button" id="hw-ring-inspect" aria-pressed="false">仅看旋转</button></div>
        <p class="hw-instruction">轻按戒指，开始解题<br><kbd>Space</kbd> 或鼠标单击</p>
        <p class="hw-gesture-guide" id="hw-gesture-guide">在戒指上滑动选择<br>长按 0.7 秒后松开，返回上一页</p>
        <p class="hw-small" id="hw-ring-purpose">当前按压：拍照并解题</p>
      </aside>
    </div>`;
  const $ = id => document.getElementById(id);
  let imageFile = null, audioFile = null, imageUrl = "", audioUrl = "", working = false, workingProgress = "";
  let photoPending = false;
  let resultText = ["", ""], resultModel = ["", ""], resultReply = [null, null], importGeneration = 0;
  let sourcePhoto = null, questionIndex = 0, questionScroll = [], questionPhotos = new Map();
  let cameraTaking=false, recorder=null, recordPhase="off", recordChunks=[], recordSize=0, recordTimer=null, recordStarted=0, recordReason="", controlFocus=0, resultUrl="", shutterAudio=null;
  const practiceNames={auto:"自动分类",ability:"职业能力测试",essay:"策论",interview:"面试"}, practiceActions=new Set(["run","analyze","outline","draft","critique","start","answer","follow_up"]);
  let learningAgent="auto", practiceStage=0, practiceRecorder=null, practiceRecordPhase="off", practiceChunks=[], practiceRecordSize=0, practiceRecordTimer=null, practiceRecordStarted=0, practiceAudioFile=null, practiceAudioUrl="", practiceLastAnswer="";
  const captureMedia=constraints=>navigator.mediaDevices?.getUserMedia ? navigator.mediaDevices.getUserMedia(constraints) : Promise.reject(new Error("当前浏览器不支持采集，请使用 localhost 或 HTTPS，或导入已有素材。"));
  const camera = new window.HardwareMediaSession(captureMedia), microphone = new window.HardwareMediaSession(captureMedia);
  const practiceMicrophone=new window.HardwareMediaSession(captureMedia);
  let session = `hardware-${Date.now()}`;
  const modeNames = ["拍照解题", "会议纪要"];
  const navigation = new window.HardwareNavigation();
  let ringView = null, inspecting = false, ringInput = null, flowDrag = null;
  const engine = new window.HardwareSimulator({onChange: render});
  ringView = window.SmartRing.mount($("hw-ring-canvas"), {onUnavailable:()=>{
    ringView = null;
    setInspect(false);
    note("三维显示暂不可用，仍可轻点戒指操控眼镜。刷新页面可重新启用旋转查看。");
  }});
  const lensVisible=()=>location.hash!=="#memory";
  ringView?.setVisible(lensVisible());
  render(engine.state);
  function render(s) {
    if(s.error) note(s.feedback);
    $("hw-connection").textContent = s.connected ? "模拟已连接" : "模拟已断开";
    $("hw-connection").classList.toggle("offline", !s.connected);
    const nav = flowDrag?.previewFocus!=null ? {...navigation.state,focus:flowDrag.previewFocus} : navigation.state, home = nav.screen === "home", reading = nav.screen === "result", menuScreen=home||nav.screen==="actions";
    const selected = home ? modeNames[nav.focus] : navigation.options()[nav.focus];
    $("hw-mode-label").textContent = home ? "场景选择" : modeNames[s.mode];
    $("hw-ring-purpose").textContent = reading ? "当前单击：阅读下一段" : !menuScreen ? "滑动选控件 · 单击确认" : `当前选中：${selected}`;
    const ringState = s.error ? "指令失败" : !s.connected ? "连接已断开" : working ? "AI 正在处理" : s.busy ? "指令发送中" : s.phase === 3 ? "设备已回执" : "等待操作";
    $("hw-ring-state").textContent = inspecting ? "旋转查看中" : ringState;
    const panel = host.querySelector(".hw-ring-panel");
    panel.classList.toggle("is-offline", !s.connected); panel.classList.toggle("is-error", s.error); panel.classList.toggle("is-busy", s.busy || working);
    ringView?.setState({connected:s.connected,busy:s.busy||working,error:s.error});
    $("hw-ring-inspect").disabled = !ringView || working || s.busy;
    host.querySelector(".hw-instruction").innerHTML = inspecting ? "拖动，查看戒指的每一面<br>此模式只旋转，不操控菜单" : reading ? "上下滑动，滚动答案<br>轻点阅读下一段" : !menuScreen ? "滑动选择镜片内的控件<br>轻点确认，长按返回" : `${home ? "上下拖动选场景" : "上下拖动选操作"}<br>轻点戒指，确认选择`;
    $("hw-gesture-guide").textContent = inspecting ? "拖动或方向键查看造型。返回操控后，上下滑动选择或阅读。" : "在戒指或右侧滑带上拖动，左右方向也可操作。长按 0.7 秒后松开，返回上一页。";
    host.querySelector(".hw-flow-caption").textContent = inspecting ? "拖动旋转 · 查看造型" : "上下滑动 · 轻点确认";
    panel.classList.toggle("is-reading",reading);
    $("hw-processing").hidden = !working;
    $("hw-processing").lastChild.textContent = s.mode === 0 ? workingProgress || "正在识别照片中的题目" : "正在转写与整理纪要";
    $("hw-lens-hint").textContent = reading ? "滑动阅读 · 长按返回上一页" : "滑动选择 · 单击确认";
    $("hw-audio-player").hidden = !audioFile;
    $("hw-scene-image").hidden = home || s.mode !== 0 || !imageFile || nav.screen === "camera";
    if (!reading || s.mode !== 0) {
      $("hw-scene-image").src = imageUrl;
      $("hw-scene-image").alt = "本次拍摄的原照片";
    }
    $("hw-empty-scene").hidden = true;
    $("hw-lens-result").hidden = !reading;
    $("hw-question-nav").hidden = !reading || s.mode !== 0 || photoQuestions().length < 2;
    $("hw-nav-menu").hidden = !menuScreen || working;
    $("hw-camera-panel").hidden = nav.screen!=="camera" || working;
    $("hw-record-panel").hidden = nav.screen!=="recording" || working;
    $("hw-settings-panel").hidden = nav.screen!=="settings" || working;
    $("hw-practice-panel").hidden = nav.screen!=="practice";
    host.querySelector(".hw-viewport").classList.toggle("has-menu",menuScreen);
    host.querySelector(".hw-viewport").classList.toggle("has-panel",!menuScreen&&!reading);
    host.querySelector(".hw-viewport").classList.toggle("is-exam-reading",reading&&s.mode===0);
    $("hw-hide-result").textContent=s.mode===0?"返回操作":"收起";
    $("hw-nav-title").textContent = home ? "选择一个场景" : `${modeNames[s.mode]} · 选择操作`;
    $("hw-nav-back-hint").hidden = home;
    const options = home ? modeNames : navigation.options();
    const photoHint = imageFile && photoPending ? `当前画面：${imageFile.name}。轻点开始解题。` : "打开摄像头，拍摄新的题目后解答。" + (resultText[0] ? "上次答案仍可查看。" : "");
    const descriptions = home ? ["拍照或导入题目，在镜片中阅读答案。", "录制或导入会议，在镜片中阅读纪要。"] : s.mode===0 ? [photoHint,"导入一张 JPG、PNG 或 WebP 图片，最大 20 MB。","继续阅读本场景上一次的处理结果。","打开摄像头，拍摄新的题目画面。","选择考试场景、知识范围与参考资料。",imageFile ? `重新解答保留的图片：${imageFile.name}。` : "请先拍照或导入题目图片。"] : ["在镜片内开启麦克风；轻点停止并保存录音。","导入已有会议录音，最大 32 MB。",audioFile ? `当前录音：${audioFile.name}。轻点生成会议纪要。` : "先录制会议或导入录音，也可输入会议文字。","继续阅读本场景上一次的处理结果。"];
    const menu=$("hw-nav-options"), menuKey=home?"home":`actions-${s.mode}`;
    menu.classList.toggle("is-home",home); menu.classList.toggle("is-carousel",!home);
    if(menu.dataset.menu!==menuKey) {
      menu.dataset.menu=menuKey;
      delete menu.dataset.position;
      menu.innerHTML=options.map((label,i)=>`<button type="button" data-hw-option="${i}"><span>${label}</span><small></small></button>`).join("");
    }
    menu.querySelectorAll("[data-hw-option]").forEach((b,i)=>{b.dataset.description=descriptions[i];});
    menu.querySelectorAll("[data-hw-option]").forEach(b=>{b.disabled=working||s.busy;});
    selectOption(nav.focus,flowDrag?.position??nav.focus,flowDrag?.position!=null);
    $("hw-empty-title").textContent = s.mode === 0 ? "把题目放到眼前" : audioFile ? "会议录音已就绪" : "让会议内容进入镜片";
    $("hw-empty-copy").textContent = s.mode === 0 ? "拍照或导入图片，用戒指开始解题。" : audioFile ? audioFile.name : "录制或导入会议，用戒指生成会议纪要。";
    $("hw-lens-status").textContent = working ? "AI 处理中" : home ? `${nav.focus+1} / 2` : reading ? "正在阅读" : nav.screen==="practice"?`${practiceNames[practiceArtifact()?.agent]||"学习"}练习`:recordPhase!=="off" ? "会议录音中" : nav.screen==="camera" ? "摄像头画面" : nav.screen==="settings" ? "设置与资料" : (s.mode === 0 ? imageFile : audioFile) ? "素材已就绪" : "等待采集";
    $("hw-ring").disabled=working||s.busy||recordPhase==="stopping"||practiceRecordPhase==="stopping";
    host.querySelectorAll("[data-hw-back]").forEach(b=>{b.disabled=working||s.busy||recordPhase==="stopping";});
    host.querySelectorAll("#hw-camera-import,#hw-record-import,#hw-meeting-summarize").forEach(b=>{b.disabled=working||s.busy||recordPhase!=="off";});
    updatePracticeControls(s);
    if(!working)updateRecording();
  }
  function selectOption(focus,position=focus,dragging=false) {
    const menu=$("hw-nav-options"), buttons=menu.querySelectorAll("[data-hw-option]"), count=buttons.length;
    if(!count)return;
    const carousel=menu.classList.contains("is-carousel"), reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    if(reduced)position=focus;
    const previous=Number(menu.dataset.position??position);
    let delta=position-previous;
    if(!dragging)delta-=Math.round(delta/count)*count;
    menu.classList.toggle("is-dragging",carousel&&dragging&&!reduced);
    const recycled=[], leaving=[];
    const place=(button,slot)=>{
      const distance=Math.abs(slot), emphasis=Math.max(0,1-distance);
      button.style.setProperty("--option-slot",slot);
      button.style.setProperty("--option-scale",.88+.12*emphasis);
      button.style.setProperty("--option-opacity",(.4+.6*emphasis)*Math.max(0,Math.min(1,2-distance)));
      button.style.setProperty("--option-blur",`${Math.min(distance,1)}px`);
    };
    buttons.forEach((b,i)=>{
      const selected=i===focus, slot=(((i-position+count/2)%count)+count)%count-count/2;
      // Recycle only across the loop boundary; multi-step selections still slide.
      const oldSlot=Number(b.dataset.slot);
      const wrapping=carousel&&!dragging&&!reduced&&Math.abs(delta)>.001&&b.dataset.slot!=null&&Math.abs(slot-oldSlot+delta)>count/2;
      if(wrapping&&Math.abs(oldSlot)<2){
        // A visible outgoing card must finish its slide while its peer enters.
        const ghost=b.cloneNode(true), visual=getComputedStyle(b);
        ghost.removeAttribute("data-hw-option");ghost.removeAttribute("id");
        ghost.setAttribute("aria-hidden","true");ghost.tabIndex=-1;ghost.disabled=true;
        ghost.classList.add("hw-option-ghost");ghost.classList.remove("is-wrapping");
        Object.assign(ghost.style,{transition:"none",transform:visual.transform,opacity:visual.opacity,filter:visual.filter});
        menu.appendChild(ghost);leaving.push([ghost,oldSlot-delta]);
      }
      b.classList.toggle("is-wrapping",wrapping);
      if(wrapping){place(b,slot+delta);recycled.push([b,slot]);}else place(b,slot);
      b.dataset.slot=slot;
      b.setAttribute("aria-pressed",String(selected));
      b.querySelector("small").textContent=selected?"单击确认":"滑动选择";
      if(selected)$("hw-nav-description").textContent=b.dataset.description;
    });
    if(recycled.length){
      // One layout flush places the recycled cards outside the visible track.
      void menu.offsetWidth;
      recycled.forEach(([button,slot])=>{button.classList.remove("is-wrapping");place(button,slot);});
      leaving.forEach(([ghost,slot])=>{
        Object.assign(ghost.style,{transition:"",transform:`translateX(calc(-50% + ${slot}*85%)) scale(.88)`,opacity:"0",filter:"blur(1px)"});
        setTimeout(()=>ghost.remove(),430);
      });
    }
    menu.dataset.position=position;
  }
  function note(text) { $("hw-notice").textContent = text; $("hw-notice").hidden = !text; }
  function photoQuestions() {
    const batch = (resultReply[0]?.artifacts || []).find(a => a.kind === "exam_batch" && Array.isArray(a.questions));
    return batch?.questions || [];
  }
  function questionLabel(question, index) { return String(question?.label || `第${index + 1}题`); }
  function escapeQuestionLabel(text) { return text.replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character])); }
  function rememberQuestionScroll() {
    if (engine.state.mode === 0 && navigation.state.screen === "result") questionScroll[questionIndex] = $("hw-result-body").scrollTop;
  }
  function questionPhoto(question) {
    // The original is already decoded. Local drawing cannot complete late and
    // overwrite the next selected question or a newly captured photo.
    if (questionPhotos.has(questionIndex)) return questionPhotos.get(questionIndex);
    const width = sourcePhoto?.naturalWidth, height = sourcePhoto?.naturalHeight;
    const valid = region => region?.page === 1 && Array.isArray(region.bbox) && region.bbox.length === 4 &&
      region.bbox.every(Number.isFinite) && region.bbox[0] >= 0 && region.bbox[1] >= 0 &&
      region.bbox[2] <= 1000 && region.bbox[3] <= 1000 && region.bbox[0] < region.bbox[2] && region.bbox[1] < region.bbox[3];
    const own = Array.isArray(question?.regions) ? question.regions.filter(valid) : [];
    if (!width || !height || !own.length) return imageUrl;
    const context = Array.isArray(question.context_regions) ? question.context_regions.filter(valid) : [];
    try {
      const pieces = [...own, ...context].map(region => {
        const [left, top, right, bottom] = region.bbox;
        const x = Math.max(0, Math.floor((left - 4) * width / 1000));
        const y = Math.max(0, Math.floor((top - 4) * height / 1000));
        return {x, y, width:Math.min(width, Math.ceil((right + 4) * width / 1000)) - x,
          height:Math.min(height, Math.ceil((bottom + 4) * height / 1000)) - y};
      });
      // Stack material separately; a union rectangle would include neighbors.
      const fullWidth = Math.max(...pieces.map(piece => piece.width));
      const fullHeight = pieces.reduce((sum, piece) => sum + piece.height, 0) + 16 * (pieces.length - 1);
      const scale = Math.min(1, 1600 / fullWidth, 1600 / fullHeight);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.min(1600, Math.ceil(fullWidth * scale)));
      canvas.height = Math.max(1, Math.min(1600, Math.ceil(fullHeight * scale)));
      const painter = canvas.getContext("2d");
      painter.fillStyle = "white"; painter.fillRect(0, 0, canvas.width, canvas.height);
      let y = 0;
      for (const piece of pieces) {
        painter.drawImage(sourcePhoto, piece.x, piece.y, piece.width, piece.height,
          (fullWidth - piece.width) * scale / 2, y * scale, piece.width * scale, piece.height * scale);
        y += piece.height + 16;
      }
      const url = canvas.toDataURL("image/png"); questionPhotos.set(questionIndex, url); return url;
    } catch (_) { return imageUrl; }
  }
  function renderQuestionNavigation() {
    const questions = photoQuestions(), count = questions.length;
    questionIndex = Math.max(0, Math.min(questionIndex, Math.max(0, count - 1)));
    $("hw-question-select").innerHTML = questions.map((q, index) => `<option value="${index}">${escapeQuestionLabel(questionLabel(q, index))}${q.status === "needs_photo" ? " · 需补拍" : q.status !== "answered" ? " · 待核验" : ""}</option>`).join("");
    $("hw-question-select").value = String(questionIndex);
    $("hw-question-prev").disabled = count < 2 || questionIndex === 0;
    $("hw-question-next").disabled = count < 2 || questionIndex === count - 1;
    $("hw-question-position").textContent = count ? `${questionIndex + 1} / ${count}` : "";
    const question = questions[questionIndex];
    $("hw-scene-image").src = question ? questionPhoto(question) : imageUrl;
    $("hw-scene-image").alt = question ? `${questionLabel(question, questionIndex)}的题目画面` : "本次拍摄的原照片";
  }
  function selectQuestion(index) {
    const count = photoQuestions().length;
    if (working || engine.state.busy || engine.state.mode !== 0 || navigation.state.screen !== "result" ||
        !Number.isInteger(index) || index < 0 || index >= count) return;
    rememberQuestionScroll(); window.HardwareAssistant.stopSpeech?.(); questionIndex = index; showResult();
  }
  function practiceArtifact() {return (resultReply[0]?.artifacts||[]).find(a=>a.kind==="practice"&&["ability","essay","interview"].includes(a.agent)&&Array.isArray(a.stages));}
  function uncertainRoute() {return (resultReply[0]?.artifacts||[]).find(a=>a.kind==="practice_route"&&a.agent==="unknown");}
  function practiceSource(artifact) {
    const decision={user:"手动题型",previous:"沿用题型",rule:"题型判断规则"}[artifact?.decision_model]||(artifact?.decision_model?`题型判断 ${artifact.decision_model}`:"");
    const generationAction=artifact?.agent==="essay"?"写作":artifact?.stages?.some(stage=>stage.id==="feedback")?"点评":artifact?.stages?.some(stage=>stage.id==="follow_up")?"追问":"出题";
    const generation=artifact?.generation_model?`${generationAction} ${artifact.generation_model}`:"";
    return [artifact?.question_source==="provided"?"题面原文":"",decision,generation].filter(Boolean).join(" · ");
  }
  const htmlEscape=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  function renderPracticePanel() {
    const artifact=practiceArtifact();if(!artifact)return;
    const stages=artifact.stages;practiceStage=Math.max(0,Math.min(practiceStage,stages.length-1));
    $("hw-practice-title").textContent=`${practiceNames[artifact.agent]} · 练习`;
    $("hw-practice-source").textContent=practiceSource(artifact);
    $("hw-practice-question").textContent=artifact.question||"";
    $("hw-practice-stages").innerHTML=stages.map((stage,index)=>`<button type="button" role="tab" data-practice-stage="${index}" aria-selected="${index===practiceStage}">${htmlEscape(stage.label||stage.id||"练习内容")}</button>`).join("");
    $("hw-practice-stage-body").innerHTML=window.HardwareAssistant.render(stages[practiceStage]?.text||"");
    $("hw-practice-answer-wrap").hidden=artifact.agent!=="interview";
    $("hw-practice-draft-wrap").hidden=artifact.agent!=="essay";
    $("hw-practice-transcript-wrap").hidden=!practiceLastAnswer;
    $("hw-practice-transcript").textContent=practiceLastAnswer;
    const actions=(artifact.next_actions||[]).filter(action=>practiceActions.has(action.id));
    $("hw-practice-actions").innerHTML=actions.map(action=>`<button type="button" data-practice-action="${action.id}"${["answer","critique"].includes(action.id)?' class="hw-primary"':""}>${htmlEscape(action.label||action.id)}</button>`).join("");
    updatePracticeControls();
  }
  function updatePracticeControls(simulatorState=engine.state) {
    const locked=working||simulatorState.busy, recording=practiceRecordPhase!=="off";
    $("hw-practice-modes").querySelectorAll("[data-practice-agent]").forEach(button=>{button.setAttribute("aria-pressed",String(button.dataset.practiceAgent===learningAgent));button.disabled=locked||recording;});
    const subjective=["essay","interview"].includes(learningAgent), uncertain=!!uncertainRoute();
    $("hw-practice-topic-wrap").hidden=!subjective;$("hw-practice-start-text").hidden=!subjective;
    $("hw-practice-mode-note").textContent=uncertain?"题型暂未确定。选择学习模式后，可继续使用已识别题面。":learningAgent==="auto"?"拍照后先识别题面，再进入对应学习流程。":learningAgent==="ability"?"保留照片中的全部题目，逐题显示答案与解析。":learningAgent==="essay"?"审题、框架、参考范文与自己的草稿批改都在镜片内完成。":"先看题目，再用文字或录音作答，获取点评与追问。";
    $("hw-practice-resume").hidden=!uncertain;
    ["hw-practice-topic","hw-practice-start-text","hw-practice-resume","hw-practice-answer","hw-practice-draft"].forEach(id=>$(id).disabled=locked||recording);
    $("hw-practice-panel").querySelectorAll("[data-practice-stage],[data-practice-action]").forEach(button=>button.disabled=locked||recording);
    $("hw-practice-record").disabled=locked||practiceRecordPhase==="stopping"||recordPhase!=="off";
    $("hw-practice-record").textContent=practiceRecordPhase==="opening"?"取消开启":practiceRecordPhase==="recording"?"结束并保留录音":practiceRecordPhase==="stopping"?"正在保存录音…":"开启录音作答";
    $("hw-practice-audio").hidden=!practiceAudioFile;$("hw-practice-audio-clear").hidden=!practiceAudioFile;
    $("hw-practice-audio-clear").disabled=locked||recording;
    if(practiceRecordPhase==="recording")$("hw-practice-record-status").textContent=`正在录音 · ${Math.floor((Date.now()-practiceRecordStarted)/1000)} 秒。结束录音后提交作答。`;
    window.AssistantModel?.setBusy(recording,"hardware-practice-recording");
  }
  function openPractice() {
    if(working||engine.state.busy||recordPhase!=="off"||!practiceArtifact())return;
    navigation.state={screen:"practice",mode:0,focus:0};engine.state.mode=0;controlFocus=0;
    renderPracticePanel();render(engine.state);markControl();
  }
  function showResult(resetScroll=false,interactive=false) {
    const mode = engine.state.mode;
    if (!resultText[mode]) return;
    if (mode === 0) {
      if (resetScroll) { questionIndex = 0; questionScroll = []; questionPhotos.clear(); }
      renderQuestionNavigation();
    }
    const artifact=mode===0?practiceArtifact():null;
    $("hw-result-title").textContent = artifact&&artifact.agent!=="ability"?`${practiceNames[artifact.agent]} · 学习结果`:mode === 0 ? `解题结果${resultModel[mode]?" · "+resultModel[mode]:""}` : "会议纪要";
    $("hw-result-body").innerHTML = mode === 0 && window.HardwareAssistant.renderExam
      ? window.HardwareAssistant.renderExam(resultReply[mode] || {text:resultText[mode]}, {questionIndex})
      : window.HardwareAssistant.render(resultText[mode]);
    if(resultUrl)URL.revokeObjectURL(resultUrl);
    resultUrl=URL.createObjectURL(new Blob([resultText[mode]],{type:"text/plain;charset=utf-8"}));
    $("hw-result-download").href=resultUrl;$("hw-result-download").download=`${modeNames[mode]}-${Date.now()}.txt`;
    $("hw-result-download").textContent = mode === 0 && photoQuestions().length > 1 ? "下载全部" : "下载";
    $("hw-result-speak").hidden=!window.HardwareAssistant.speak;
    $("hw-practice-open").hidden=mode!==0||!artifact&& !uncertainRoute()||artifact?.agent==="ability";
    $("hw-practice-open").textContent=uncertainRoute()?"选择学习模式":"继续练习";
    $("hw-lens-result").hidden = false;
    navigation.state.screen = "result";
    render(engine.state);
    $("hw-result-body").scrollTop = resetScroll ? 0 : mode === 0 ? questionScroll[questionIndex] || 0 : $("hw-result-body").scrollTop;
    updateReading();
    if(interactive&&artifact&&["essay","interview"].includes(artifact.agent)){navigation.state.screen="practice";controlFocus=0;renderPracticePanel();render(engine.state);}
  }
  function updateReading() {
    const body=$("hw-result-body"), max=Math.max(0,body.scrollHeight-body.clientHeight);
    host.querySelector(".hw-result-footer span").textContent = max<2 ? "已显示全部内容" : body.scrollTop>=max-2 ? "已到末尾 · 长按返回" : `阅读进度 ${Math.round(body.scrollTop/max*100)}%`;
  }
  $("hw-result-body").addEventListener("scroll",()=>{rememberQuestionScroll();updateReading();},{passive:true});
  $("hw-question-prev").addEventListener("click",()=>selectQuestion(questionIndex - 1));
  $("hw-question-next").addEventListener("click",()=>selectQuestion(questionIndex + 1));
  $("hw-question-select").addEventListener("change",event=>selectQuestion(Number(event.target.value)));
  async function operate(action) {
    if (working || engine.state.busy || inspecting || flowDrag) return;
    rememberQuestionScroll();
    note("");
    const mode = engine.state.mode, file = mode === 0 ? imageFile : audioFile;
    const plan = navigation.plan(action,{hasFile:!!file||mode===1&&!!$("hw-meeting-text").value.trim(),hasResult:!!resultText[mode],hasPendingPhoto:photoPending});
    // Open the native picker within the input event, before asynchronous transport.
    if(plan.effect==="import" && engine.state.connected && engine.state.fault==="none") {
      (mode===0?$("hw-image-file"):$("hw-audio-file")).click(); return;
    }
    const success = await engine.send("navigate",{label:action,execute:()=>{
      navigation.commit(plan); engine.state.mode=plan.next.mode;
      return plan.message || "操作已完成";
    }});
    if (!success) return;
    if(plan.next.screen!=="camera"&&camera.active)closeCamera();
    if(plan.next.screen!=="recording"&&recordPhase!=="off")stopRecording();
    if(plan.next.screen!=="practice"&&practiceRecordPhase!=="off")stopPracticeRecording();
    if(action==="back"||action==="home") {
      importGeneration++;closeCamera();stopRecording("录音已关闭，麦克风已释放。");stopPracticeRecording();window.HardwareAssistant.stopSpeech?.();
    }
    if(plan.effect==="camera") {await openCamera();return;}
    if(plan.effect==="capture") {await activateControl();return;}
    if(plan.effect==="record") {await startRecording();return;}
    if(plan.effect==="recordToggle") {await activateControl();return;}
    if(plan.effect==="settings") {openSettings();return;}
    if(["controlPrevious","controlNext"].includes(plan.effect)) {focusControl(plan.effect==="controlPrevious"?-1:1);return;}
    if(plan.effect==="settingsSelect"||plan.effect==="practiceSelect") {await activateControl();return;}
    if(plan.effect==="scrollUp" || plan.effect==="scrollDown") {
      const body=$("hw-result-body"), amount=Math.max(80,body.clientHeight*.72)*(plan.effect==="scrollUp"?-1:1);
      body.scrollBy({top:amount,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'}); updateReading();return;
    }
    if(plan.effect==="read") {showResult();return;}
    if(plan.effect!=="run") { if(plan.effect==="missing")note(plan.message);render(engine.state);return; }
    await runCurrent();
  }
  function closeCamera() {
    camera.stop();cameraTaking=false;$("hw-camera-video").srcObject=null;$("hw-camera-shoot").disabled=true;
  }
  async function openCamera() {
    if(working)return;
    navigation.state.screen="camera";controlFocus=0;note("");render(engine.state);
    $("hw-camera-status").textContent="正在打开摄像头…";
    try {
      const stream=await camera.start({audio:false,video:{facingMode:{ideal:"environment"},width:{ideal:1920},height:{ideal:1440}}});
      if(!stream)return;
      const video=$("hw-camera-video");video.srcObject=stream;await video.play();
      if(camera.stream!==stream||navigation.state.screen!=="camera")return;
      stream.getVideoTracks().forEach(track=>track.addEventListener("ended",()=>{if(camera.stream!==stream)return;closeCamera();$("hw-camera-status").textContent="摄像头已断开。请返回后重试，或导入图片。";}));
      $("hw-camera-shoot").disabled=!video.videoWidth;
      $("hw-camera-status").textContent="让题目尽量铺满画面，拍全题干、图形和选项；确认小字与细线清楚后，再轻点「拍照并解题」。";
      markControl();
    } catch(error) {
      closeCamera();$("hw-camera-status").textContent=error.name==="NotAllowedError"?"摄像头权限未开启。请在浏览器中允许访问，或导入图片。":error.message?.includes("localhost")?error.message:"摄像头不可用。请检查设备，返回后重试，或导入图片。";
    }
  }
  function shutterSound() {
    if($("hw-camera-silent").checked)return;
    try {
      const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;
      shutterAudio?.close().catch(()=>{});const context=shutterAudio=new Audio(),oscillator=context.createOscillator(),gain=context.createGain();
      oscillator.frequency.value=880;gain.gain.value=.035;oscillator.connect(gain);gain.connect(context.destination);oscillator.start();oscillator.stop(context.currentTime+.08);
      oscillator.onended=()=>{context.close().catch(()=>{});if(shutterAudio===context)shutterAudio=null;};
    } catch(_) {}
  }
  async function takePhoto() {
    const video=$("hw-camera-video");
    if(!camera.stream||!video.videoWidth||cameraTaking||working)return;
    const generation=camera.generation;cameraTaking=true;$("hw-camera-shoot").disabled=true;
    try {
      const canvas=document.createElement("canvas");canvas.width=video.videoWidth;canvas.height=video.videoHeight;canvas.getContext("2d").drawImage(video,0,0);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",.95));
      if(generation!==camera.generation||navigation.state.screen!=="camera")return;
      if(!blob)throw new Error("照片生成失败，请重新拍摄。");
      shutterSound();const accepted=await acceptImage(new File([blob],`question-${Date.now()}.jpg`,{type:"image/jpeg"}));
      if(accepted&&lensVisible()&&!document.hidden)await runCurrent();
    } catch(error) {$("hw-camera-status").textContent=error.message||"照片生成失败，请重新拍摄。";}
    finally {cameraTaking=false;$("hw-camera-shoot").disabled=!camera.stream||!video.videoWidth;}
  }
  function updateRecording() {
    window.AssistantModel?.setBusy(recordPhase!=="off","hardware-recording");
    const active=recordPhase!=="off";
    $("hw-record-toggle").textContent=recordPhase==="opening"?"取消开启":recordPhase==="recording"?"停止并保存录音":recordPhase==="stopping"?"正在保存…":"开启录音";
    $("hw-record-toggle").disabled=recordPhase==="stopping"||working;
    $("hw-record-toggle").setAttribute("aria-pressed",String(recordPhase==="recording"));
    $("hw-record-use").disabled=active||working||!audioFile;
    $("hw-record-delete").disabled=active||working||!audioFile;
    $("hw-record-import").disabled=active||working;
    $("hw-meeting-text").disabled=active||working;
    $("hw-meeting-summarize").disabled=active||working;
    if(recordPhase==="recording")$("hw-record-status").textContent=`正在录音 · ${Math.floor((Date.now()-recordStarted)/1000)} 秒。轻点停止，长按返回也会停止并保存。`;
    markControl();
  }
  function stopRecording(message="录音已关闭，麦克风已释放。") {
    recordReason=message;clearInterval(recordTimer);recordTimer=null;
    if(recorder&&recorder.state!=="inactive") {recordPhase="stopping";recorder.stop();microphone.stop();}
    else {microphone.stop();recordPhase="off";$("hw-record-status").textContent=message;}
    updateRecording();
  }
  async function startRecording() {
    if(recordPhase!=="off"||working)return;
    navigation.state.screen="recording";controlFocus=0;recordPhase="opening";render(engine.state);
    $("hw-record-status").textContent="等待麦克风权限；轻点可取消开启。";
    if(!window.MediaRecorder){recordPhase="off";$("hw-record-status").textContent="当前浏览器不支持录音，请导入已有音频。";updateRecording();return;}
    try {
      const stream=await microphone.start({audio:true,video:false});if(!stream)return;
      const mime=["audio/webm;codecs=opus","audio/mp4","audio/ogg;codecs=opus"].find(type=>MediaRecorder.isTypeSupported(type));
      if(!mime)throw new Error("当前浏览器没有可用的录音格式，请导入已有音频。");
      const current=recorder=new MediaRecorder(stream,{mimeType:mime,audioBitsPerSecond:64000});recordChunks=[];recordSize=0;recordReason="";
      current.addEventListener("dataavailable",event=>{if(event.data.size){recordChunks.push(event.data);recordSize+=event.data.size;}if(recordSize>=28*1024*1024&&recordPhase==="recording")stopRecording("单段录音已达到上限，已停止并保存。请保存本段后继续。");});
      current.addEventListener("stop",()=>{
        if(recorder!==current)return;
        clearInterval(recordTimer);recordTimer=null;microphone.stop();recordPhase="off";recorder=null;
        let saved=false;
        if(recordChunks.length){const ext=mime.includes("mp4")?"m4a":mime.includes("ogg")?"ogg":"webm";saved=acceptAudio(new File(recordChunks,`meeting-${Date.now()}.${ext}`,{type:mime}),{stay:true});}
        recordChunks=[];$("hw-record-status").textContent=(recordReason||"录音已关闭，麦克风已释放。")+(saved?" 本次录音已保留，可试听后生成纪要，尚未提交。":" 本次录音未保存，请重试。");render(engine.state);
      });
      current.addEventListener("error",()=>stopRecording("录音发生异常，麦克风已关闭。请检查后重试。"));
      stream.getAudioTracks().forEach(track=>track.addEventListener("ended",()=>{if(recordPhase==="recording")stopRecording("麦克风已断开，录音已关闭。");}));
      current.start(1000);recordPhase="recording";recordStarted=Date.now();render(engine.state);
      recordTimer=setInterval(()=>Date.now()-recordStarted>=60*60*1000?stopRecording("已录满 60 分钟，自动停止并保存。请保存本段后继续。"):updateRecording(),1000);
    } catch(error) {microphone.stop();recorder=null;recordPhase="off";$("hw-record-status").textContent=error.name==="NotAllowedError"?"麦克风权限未开启。请在浏览器中允许访问，或导入录音。":error.message?.includes("localhost")?error.message:"麦克风不可用，请检查权限和设备，或导入录音。";render(engine.state);}
  }
  function panelControls() {
    const id=navigation.state.screen==="camera"?"hw-camera-panel":navigation.state.screen==="recording"?"hw-record-panel":navigation.state.screen==="settings"?"hw-settings-panel":navigation.state.screen==="practice"?"hw-practice-panel":null;
    if(!id)return [];
    return [...$(id).querySelectorAll("button:not([data-hw-back]),a[href],input:not([type=file]),select,textarea,summary")].filter(control=>!control.disabled&&control.getClientRects().length&&!control.closest("[hidden]"));
  }
  function markControl() {
    host.querySelectorAll(".is-ring-focus").forEach(control=>control.classList.remove("is-ring-focus"));
    const controls=panelControls();controlFocus=Math.min(controlFocus,Math.max(0,controls.length-1));const control=controls[controlFocus];
    if(control){
      control.classList.add("is-ring-focus");const label=control.labels?.[0]?.cloneNode(true);label?.querySelectorAll("input,select,textarea,button").forEach(node=>node.remove());
      const name=label?.textContent.trim()||control.textContent.trim()||control.placeholder||control.getAttribute("aria-label")||"编辑设置";
      $("hw-ring-purpose").textContent=`当前选中：${name}${control.matches("select")?" · "+control.selectedOptions[0]?.textContent.trim():""}`;
    }
  }
  function focusControl(direction) {
    const controls=panelControls();if(!controls.length)return;
    controlFocus=(controlFocus+direction+controls.length)%controls.length;markControl();controls[controlFocus].scrollIntoView({block:"nearest",behavior:"instant"});
  }
  async function activateControl() {
    const control=panelControls()[controlFocus];if(!control)return;
    if(control.id==="hw-camera-shoot"){await takePhoto();return;}
    if(control.id==="hw-record-toggle"){recordPhase==="off"?await startRecording():stopRecording();return;}
    if(control.id==="hw-practice-record"){practiceRecordPhase==="off"?await startPracticeRecording():stopPracticeRecording();return;}
    if(control.matches("select")){try{control.showPicker();}catch(_){control.focus();}}
    else if(control.matches("input:not([type=checkbox]),textarea"))control.focus();else control.click();
  }
  function openSettings(returnScreen) {
    if(working||engine.state.busy||recordPhase!=="off"||practiceRecordPhase!=="off")return;
    closeCamera();navigation.state={screen:"settings",mode:0,focus:0,...(returnScreen==="result"?{returnScreen}: {})};engine.state.mode=0;controlFocus=0;note("");render(engine.state);window.Exam?.refresh("local");markControl();
  }
  async function runCurrent(textOverride,practiceRequest) {
    if(working||engine.state.busy||recordPhase!=="off"||practiceRecordPhase!=="off"||!lensVisible()||document.hidden)return;
    const mode=engine.state.mode,file=mode===0?imageFile:audioFile,notes=$("hw-meeting-text").value.trim();
    if(!file&&!textOverride&&!(mode===1&&notes)){note(mode===0?"请先拍照或导入题目图片。":"请先录制或导入会议，也可输入会议文字。");return;}
    // Retrying a submitted picture is explicit, including after a failed solve.
    if(mode===0&&file&&(!practiceRequest||practiceRequest.files?.includes(file)))photoPending=false;
    closeCamera();
    const initialProgress=practiceRequest?({run:"正在审题与整理学习内容",start:"正在准备面试题目",analyze:"正在审题",outline:"正在整理写作框架",draft:"正在生成参考范文",critique:"正在批改草稿",answer:practiceRequest.files?.length?"正在转写与点评作答":"正在点评作答",follow_up:"正在准备追问"}[practiceRequest.practice.action]||"正在继续学习练习"):"正在识别照片中的题目";
    working = true; workingProgress = mode===0?initialProgress:"";window.AssistantModel?.setBusy(true,"hardware-lens-request");render(engine.state);
    if (mode === 0) { $("hw-flash").classList.remove("is-flashing"); void $("hw-flash").offsetWidth; $("hw-flash").classList.add("is-flashing"); }
    try {
      const response = await window.HardwareAssistant.run({
        scene: mode === 0 ? "exam" : "meeting", session_id: `${session}-${mode}${mode===1&&(textOverride||!file)?"-text-"+Date.now():""}`,
        text: textOverride || (mode === 0 ? "请识别照片中的完整题面并按学习模式处理；职业能力测试保留全部题目并逐题给出答案与解析，拍摄不完整时说明需要补拍的部分。" : file ? "请转写这段会议录音并生成会议纪要。" : `请根据以下会议内容生成会议纪要。\n${notes}`),
        files: practiceRequest?practiceRequest.files||[]:file&&!textOverride ? [file] : [],
        ...(mode===0?{event:{practice:practiceRequest?.practice||{agent:learningAgent,action:"run"}}}:{}),
        onProgress: p => {
          if (mode !== 0 || !working) return;
          const active = (p.steps || []).find(step => step.state === "active");
          const phases = {capture:"正在上传照片",recognize:"正在识别照片中的题目",solve:"正在解题与复核",verify:"正在复核答案"};
          workingProgress = p.error ? "处理失败，正在获取提示" : p.finished ? "处理完成，正在显示结果" : phases[active?.id] || initialProgress;
          if (p.batch && Number.isInteger(p.batch.total) && p.batch.total > 0) {
            const done = Math.min(p.batch.total, Math.max(0, Number(p.batch.done) || 0));
            workingProgress += ` · 已处理 ${done} / ${p.batch.total} 道题`;
          }
          if (Number.isFinite(p.timings?.total_ms) && p.timings.total_ms > 0) {
            workingProgress += `（已用 ${Math.floor(p.timings.total_ms / 1000)} 秒）`;
          }
          $("hw-processing").lastChild.textContent = workingProgress;
        }
      });
      if (response.status === "error" || response.status === "failed") throw new Error(response.text || "服务处理失败");
      resultText[mode] = response.text || "服务未返回正文，请重试。";
      resultModel[mode] = response.exam_backend === "jev" ? "JEV" : response.exam_backend === "original" ? "原版" : "";
      resultReply[mode] = response;
      if(mode===0){
        const artifact=practiceArtifact();practiceStage=Math.max(0,(artifact?.stages.length||1)-1);
        if(practiceRequest?.practice.topic){
          practiceLastAnswer="";$("hw-practice-answer").value="";$("hw-practice-draft").value="";clearPracticeAudio();
          if(imageUrl)URL.revokeObjectURL(imageUrl);imageUrl="";imageFile=null;sourcePhoto=null;photoPending=false;$("hw-scene-image").removeAttribute("src");
        }
        if(practiceRequest?.practice.action==="answer"){
          practiceLastAnswer=artifact?.transcript||practiceRequest.practice.answer||"";
          $("hw-practice-answer").value="";clearPracticeAudio();
        }
      }
      note("");
      showResult(true,mode===0);
    } catch(e) { note(`AI 处理未完成：${e.message}。素材已保留，作答与草稿也已保留，可重试。`); }
    finally { working = false;window.AssistantModel?.setBusy(false,"hardware-lens-request");render(engine.state); }
  }
  async function runPractice(action,{fromTopic=false,reuse=false}={}) {
    if(working||engine.state.busy||practiceRecordPhase!=="off"||recordPhase!=="off"||!practiceActions.has(action))return;
    const artifact=practiceArtifact(), agent=fromTopic||reuse?learningAgent:artifact?.agent;
    if(!agent||reuse&&agent==="auto"){note("请先选择具体学习模式。题面仍保留，无需重新拍照。");return;}
    if(!fromTopic&&!reuse&&!(artifact?.next_actions||[]).some(next=>next.id===action)){note("当前步骤暂不能执行此操作。");return;}
    const practice={agent,action}, files=[];
    if(fromTopic){const topic=$("hw-practice-topic").value.trim();if(!["essay","interview"].includes(agent)||!topic){note("请先输入策论题目或面试主题。");return;}if(topic.length>4000){note("练习主题最多 4000 字，请缩短后提交。");return;}practice.topic=topic;}
    if(action==="critique"){const draft=$("hw-practice-draft").value.trim();if(!draft){note("请先填写你自己的草稿，再提交批改。");$("hw-practice-draft").focus();return;}if(draft.length>40000){note("草稿最多 40000 字，请缩短后提交。");return;}practice.draft=draft;}
    if(action==="answer"){
      const answer=$("hw-practice-answer").value.trim();if(answer.length>20000){note("作答最多 20000 字，请缩短后提交。");return;}
      if(answer)practice.answer=answer;else if(practiceAudioFile)files.push(practiceAudioFile);else{note("请先填写或录制你自己的回答，再提交点评。");$("hw-practice-answer").focus();return;}
    }
    const text=fromTopic?practice.topic:reuse?"请按所选学习模式继续处理已识别题面。":({analyze:"请重新审题。",outline:"请调整写作框架。",draft:"请生成明确标注的参考范文。",critique:"请批改我提交的草稿。",answer:"请点评我的真实作答并继续对练。",follow_up:"请根据上一轮回答继续追问。",start:"请开始新一轮面试出题。"}[action]||"请继续当前学习练习。");
    await runCurrent(text,{practice,files});
  }
  function clearPracticeAudio() {
    $("hw-practice-audio").pause();$("hw-practice-audio").removeAttribute("src");$("hw-practice-audio").load();
    if(practiceAudioUrl)URL.revokeObjectURL(practiceAudioUrl);practiceAudioUrl="";practiceAudioFile=null;
  }
  async function startPracticeRecording() {
    if(working||engine.state.busy||recordPhase!=="off"||practiceRecordPhase!=="off"||navigation.state.screen!=="practice"||practiceArtifact()?.agent!=="interview")return;
    if(!window.MediaRecorder){$("hw-practice-record-status").textContent="当前浏览器不支持录音，请使用文字作答。";return;}
    practiceRecordPhase="opening";$("hw-practice-record-status").textContent="正在打开麦克风…";updatePracticeControls();
    try {
      const stream=await practiceMicrophone.start({audio:true});if(!stream)return;
      if(navigation.state.screen!=="practice"){practiceMicrophone.stop();practiceRecordPhase="off";updatePracticeControls();return;}
      const mime=["audio/webm;codecs=opus","audio/webm","audio/mp4","audio/ogg;codecs=opus"].find(type=>MediaRecorder.isTypeSupported(type))||"";
      const current=practiceRecorder=new MediaRecorder(stream,mime?{mimeType:mime}:{});practiceChunks=[];practiceRecordSize=0;
      current.addEventListener("dataavailable",event=>{if(event.data.size){practiceChunks.push(event.data);practiceRecordSize+=event.data.size;}if(practiceRecordSize>=28*1024*1024&&practiceRecordPhase==="recording")stopPracticeRecording();});
      current.addEventListener("stop",()=>{
        if(practiceRecorder!==current)return;clearInterval(practiceRecordTimer);practiceRecordTimer=null;practiceMicrophone.stop();practiceRecorder=null;practiceRecordPhase="off";
        if(practiceChunks.length){clearPracticeAudio();const ext=mime.includes("mp4")?"m4a":mime.includes("ogg")?"ogg":"webm";practiceAudioFile=new File(practiceChunks,`interview-answer-${Date.now()}.${ext}`,{type:mime||"audio/webm"});practiceAudioUrl=URL.createObjectURL(practiceAudioFile);$("hw-practice-audio").src=practiceAudioUrl;$("hw-practice-record-status").textContent="本轮录音已保留。可试听后提交；返回会关闭麦克风。";}else $("hw-practice-record-status").textContent="未收到录音内容，可重试或使用文字作答。";
        practiceChunks=[];render(engine.state);
      });
      current.addEventListener("error",()=>{stopPracticeRecording();$("hw-practice-record-status").textContent="录音发生异常，麦克风已关闭。可重试或使用文字作答。";});
      stream.getAudioTracks().forEach(track=>track.addEventListener("ended",()=>{if(practiceRecordPhase==="recording")stopPracticeRecording();}));
      current.start(1000);practiceRecordPhase="recording";practiceRecordStarted=Date.now();updatePracticeControls();
      practiceRecordTimer=setInterval(()=>Date.now()-practiceRecordStarted>=60*60*1000?stopPracticeRecording():updatePracticeControls(),1000);
    } catch(error){practiceMicrophone.stop();practiceRecorder=null;practiceRecordPhase="off";$("hw-practice-record-status").textContent=error.name==="NotAllowedError"?"麦克风权限未开启。可允许访问后重试，或使用文字作答。":"麦克风不可用，可重试或使用文字作答。";updatePracticeControls();}
  }
  function stopPracticeRecording() {
    clearInterval(practiceRecordTimer);practiceRecordTimer=null;
    if(practiceRecordPhase==="opening"){practiceMicrophone.stop();practiceRecordPhase="off";$("hw-practice-record-status").textContent="麦克风开启已取消，可使用文字作答。";updatePracticeControls();return;}
    if(practiceRecordPhase==="recording"&&practiceRecorder?.state!=="inactive"){practiceRecordPhase="stopping";practiceRecorder.stop();practiceMicrophone.stop();updatePracticeControls();}
  }
  function clearFlowVisual() {
    $("hw-ring").classList.remove("is-flowing");
    $("hw-ring").style.setProperty("--flow-thumb","0px");
    $("hw-ring").style.setProperty("--flow-ticks","0px");
  }
  async function followFlow(event) {
    const body=$("hw-result-body"), ring=$("hw-ring");
    const announce=text=>{if($("hw-ring-state").textContent!==text)$("hw-ring-state").textContent=text;};
    if(event.phase==="start") {
      flowDrag={screen:navigation.state.screen,startTop:body.scrollTop,maxTop:Math.max(0,body.scrollHeight-body.clientHeight),previewFocus:["home","actions"].includes(navigation.state.screen)?navigation.state.focus:null,controlStart:controlFocus,controlPreview:controlFocus,allowed:engine.state.connected&&!engine.state.busy&&!working};
      ring.classList.add("is-flowing");return;
    }
    const drag=flowDrag;if(!drag)return;
    if(event.phase==="move") {
      ring.style.setProperty("--flow-thumb",`${Math.max(-42,Math.min(42,event.total*.55))}px`);
      ring.style.setProperty("--flow-ticks",`${event.total%16}px`);
      if(!drag.allowed){announce("连接已断开，暂不能操控");return;}
      if(!["home","actions","result"].includes(drag.screen)) {
        const controls=panelControls();if(controls.length){controlFocus=((drag.controlStart+Math.trunc(event.total/44))%controls.length+controls.length)%controls.length;drag.controlPreview=controlFocus;markControl();controls[controlFocus].scrollIntoView({block:"nearest",behavior:"instant"});}return;
      }
      const preview=navigation.drag(event.total,drag.startTop,drag.maxTop);
      if(drag.screen==="result") {body.scrollTop=preview.scrollTop;updateReading();announce("跟随拖动阅读");}
      else {
        drag.previewFocus=preview.focus;
        drag.position=navigation.state.focus+event.total/44;
        selectOption(preview.focus,drag.position,true);
        const label=drag.screen==="home"?modeNames[preview.focus]:navigation.options()[preview.focus];
        $("hw-ring-purpose").textContent=`当前选中：${label}`;
        announce(`已滑到 · ${label}`);
        if(drag.screen==="home")$("hw-lens-status").textContent=`${preview.focus+1} / 2`;
      }
      return;
    }
    clearFlowVisual();
    drag.position=null;
    if(event.phase==="cancel") {
      if(drag.screen==="result"){body.scrollTop=drag.startTop;updateReading();}
      else if(!["home","actions"].includes(drag.screen)){controlFocus=drag.controlStart;markControl();}
      flowDrag=null;render(engine.state);return;
    }
    if(!["home","actions","result"].includes(drag.screen)){flowDrag=null;render(engine.state);return;}
    const changed=drag.screen==="result"?Math.abs(body.scrollTop-drag.startTop)>1:drag.previewFocus!==navigation.state.focus;
    if(!drag.allowed||!changed){flowDrag=null;render(engine.state);return;}
    const plan=navigation.plan(drag.screen==="result"?"scrollPosition":`focus${drag.previewFocus}`);
    const ok=await engine.send("navigate",{label:"拖动戒指",execute:()=>{navigation.commit(plan);engine.state.mode=plan.next.mode;return plan.message;}});
    if(!ok&&drag.screen==="result"){body.scrollTop=drag.startTop;updateReading();}
    flowDrag=null;render(engine.state);
  }
  ringInput = window.SmartRing.bindInput($("hw-ring"), {action:operate,flow:true,onFlow:followFlow,vertical:()=>true,inspect:()=>inspecting,rotate:(dx,dy)=>ringView?.rotate(dx,dy),feedback:text=>{$("hw-ring-state").textContent=text;}});
  function setInspect(value) {
    inspecting = value; ringInput?.cancel();
    $("hw-ring-inspect").setAttribute("aria-pressed",String(inspecting));
    $("hw-ring-inspect").textContent = inspecting ? "返回操控" : "仅看旋转";
    $("hw-ring").setAttribute("aria-label", inspecting ? "旋转查看智能戒指，拖动或使用方向键" : "上下滑动操控眼镜，轻点确认，长按返回上一页");
    $("hw-gesture-guide").textContent = inspecting ? "拖动戒指或使用方向键旋转。返回操控后可继续控制眼镜。" : "在戒指上滑动选择；长按 0.7 秒后松开，返回上一页。";
    host.querySelector(".hw-ring-panel").classList.toggle("is-inspecting",inspecting);
    if(!inspecting) ringView?.reset();
    render(engine.state);
  }
  $("hw-ring-inspect").addEventListener("click", () => setInspect(!inspecting));
  $("hw-nav-options").addEventListener("click",e=>{
    const option=e.target.closest("[data-hw-option]");if(!option || working || engine.state.busy || inspecting)return;
    const index=Number(option.dataset.hwOption);
    if(navigation.state.screen==="home")operate(`scene${index}`);
    else operate(index===navigation.state.focus?"press":`focus${index}`);
  });
  $("hw-hide-result").addEventListener("click", () => operate("back"));
  async function acceptImage(file) {
    if (!file || working || engine.state.busy || recordPhase!=="off"||practiceRecordPhase!=="off") return false;
    if (!/\.(jpe?g|png|webp)$/i.test(file.name) || file.size > 20 * 1024 * 1024) { note("请选择 20 MB 以内的 JPG、PNG 或 WebP 图片。"); return false; }
    const ticket = ++importGeneration, url = URL.createObjectURL(file), probe = new Image(); probe.src = url;
    try { await probe.decode(); if (ticket !== importGeneration || working || engine.state.busy || recordPhase!=="off") { URL.revokeObjectURL(url); return false; } }
    catch (_) { URL.revokeObjectURL(url); note("图片无法读取，请换一张图片。"); return false; }
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    closeCamera();window.HardwareAssistant.stopSpeech?.();
    imageFile = file; imageUrl = url; sourcePhoto = probe; photoPending = true; resultText[0] = "";resultModel[0]="";resultReply[0]=null;
    questionIndex = 0; questionScroll = []; questionPhotos.clear();
    practiceStage=0;practiceLastAnswer="";["hw-practice-answer","hw-practice-draft","hw-practice-topic"].forEach(id=>$(id).value="");clearPracticeAudio();
    $("hw-scene-image").src = url;
    navigation.state={screen:"actions",mode:0,focus:0};engine.state.mode=0;
    note(""); render(engine.state);return true;
  }
  function acceptAudio(file,{stay=false}={}) {
    if (!file || working || !stay&&engine.state.busy || recordPhase!=="off") return false;
    if (!/\.(wav|mp3|m4a|aac|flac|ogg|amr|wma|webm)$/i.test(file.name) || file.size > 32 * 1024 * 1024) { note("请选择支持的音频格式，大小不超过 32 MB。"); return false; }
    $("hw-audio-player").pause(); if (audioUrl) URL.revokeObjectURL(audioUrl);
    audioFile = file; audioUrl = URL.createObjectURL(file); resultText[1] = "";resultReply[1]=null;
    $("hw-audio-player").src = audioUrl; $("hw-audio-name").textContent = file.name;
    $("hw-record-download").href=audioUrl;$("hw-record-download").download=file.name;$("hw-record-download").hidden=false;
    if(!stay){closeCamera();navigation.state={screen:"actions",mode:1,focus:2};engine.state.mode=1;}
    note("");render(engine.state);return true;
  }
  $("hw-image-file").addEventListener("change",event=>{const file=event.target.files[0];event.target.value="";acceptImage(file);});
  $("hw-audio-file").addEventListener("change",event=>{const file=event.target.files[0];event.target.value="";acceptAudio(file);});
  $("hw-camera-import").addEventListener("click",()=>$("hw-image-file").click());
  $("hw-record-import").addEventListener("click",()=>$("hw-audio-file").click());
  $("hw-camera-shoot").addEventListener("click",takePhoto);
  $("hw-camera-video").addEventListener("loadeddata",()=>{if(camera.stream&&!cameraTaking)$("hw-camera-shoot").disabled=!$("hw-camera-video").videoWidth;});
  $("hw-record-toggle").addEventListener("click",()=>recordPhase==="off"?startRecording():stopRecording());
  $("hw-record-use").addEventListener("click",()=>runCurrent());
  $("hw-meeting-summarize").addEventListener("click",()=>{const text=$("hw-meeting-text").value.trim();if(!text){note("请先输入会议文字，再生成纪要。");return;}runCurrent(`请根据以下会议内容生成会议纪要。\n${text}`);});
  $("hw-record-delete").addEventListener("click",()=>{
    if(recordPhase!=="off"||working||!audioFile)return;
    $("hw-audio-player").pause();$("hw-audio-player").removeAttribute("src");$("hw-audio-player").load();URL.revokeObjectURL(audioUrl);audioUrl="";audioFile=null;resultText[1]="";resultReply[1]=null;
    $("hw-record-download").removeAttribute("href");$("hw-record-download").hidden=true;$("hw-audio-name").textContent="本页录音已删除，可重新录制或导入。";render(engine.state);
  });
  host.querySelectorAll("[data-hw-back]").forEach(button=>button.addEventListener("click",()=>operate("back")));
  $("hw-result-speak").addEventListener("click",()=>{
    const question = engine.state.mode === 0 ? photoQuestions()[questionIndex] : null;
    const speech = question ? `${questionLabel(question, questionIndex)}。${question.status === "answered" ? `答案：${question.answer}。解析：${question.explanation || ""}` : question.needed || "这道题暂未确定，请核对题面。"}` : resultText[engine.state.mode];
    window.HardwareAssistant.speak?.(speech,$("hw-result-speak"));
  });
  $("hw-practice-open").addEventListener("click",()=>uncertainRoute()?openSettings("result"):openPractice());
  $("hw-practice-modes").addEventListener("click",event=>{
    const button=event.target.closest("[data-practice-agent]");if(!button||working||engine.state.busy||practiceRecordPhase!=="off"||!Object.hasOwn(practiceNames,button.dataset.practiceAgent))return;
    learningAgent=button.dataset.practiceAgent;updatePracticeControls();markControl();
  });
  $("hw-practice-start-text").addEventListener("click",()=>runPractice(learningAgent==="interview"?"start":"run",{fromTopic:true}));
  $("hw-practice-resume").addEventListener("click",()=>runPractice("run",{reuse:true}));
  $("hw-practice-stages").addEventListener("click",event=>{
    const button=event.target.closest("[data-practice-stage]");if(!button||working||engine.state.busy||practiceRecordPhase!=="off")return;
    practiceStage=Number(button.dataset.practiceStage);renderPracticePanel();markControl();
  });
  $("hw-practice-actions").addEventListener("click",event=>{const button=event.target.closest("[data-practice-action]");if(button)return runPractice(button.dataset.practiceAction);});
  $("hw-practice-record").addEventListener("click",()=>practiceRecordPhase==="off"?startPracticeRecording():stopPracticeRecording());
  $("hw-practice-audio-clear").addEventListener("click",()=>{if(working||engine.state.busy||practiceRecordPhase!=="off")return;clearPracticeAudio();$("hw-practice-record-status").textContent="本轮录音已删除，可重新录制或使用文字作答。";updatePracticeControls();});
  function relocateSettings() {
    const controls=$("exam-controls");if(!controls)return;
    $("hw-settings-content").append(controls);
    const legacy=controls.querySelector(".exam-camera-actions");if(legacy)legacy.hidden=true;
    $("exam-camera-note").hidden=true;
    try{$("hw-camera-silent").checked=localStorage.getItem("assistant.exam.camera.silent")!=="false";}catch(_){}
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",relocateSettings,{once:true});else relocateSettings();
  $("hw-settings-content").addEventListener("change",markControl);
  $("hw-camera-silent").addEventListener("change",()=>{const checked=$("hw-camera-silent").checked;$("exam-camera-silent").checked=checked;try{localStorage.setItem("assistant.exam.camera.silent",String(checked));}catch(_){};});
  window.HardwareLens={acceptImage,acceptAudio,openSettings,closeSettings:()=>operate("back"),stop:()=>{importGeneration++;closeCamera();stopRecording();stopPracticeRecording();$("hw-audio-player").pause();$("hw-practice-audio").pause();}};
  document.addEventListener("keydown", e => {
    if (!lensVisible() || e.repeat || e.ctrlKey || e.altKey || e.metaKey || /INPUT|TEXTAREA|SELECT|AUDIO/.test(e.target.tagName) || e.target.isContentEditable || e.target.closest("#hw-result-body")) return;
    if (e.target.tagName === "BUTTON" && (e.key === " " || e.key === "Enter")) return;
    const actions = {" ": "press", ArrowLeft: "previous", ArrowRight: "next", ArrowUp:"up",ArrowDown:"down", Escape: "back",Home:"home"};
    if (actions[e.key]) { e.preventDefault(); operate(actions[e.key]); }
    else if (e.key.toLowerCase() === "r") operate("read");
  });
  window.addEventListener("hashchange", () => { ringInput.cancel();ringView?.setVisible(lensVisible());if(!lensVisible())window.HardwareLens.stop(); });
  document.addEventListener("visibilitychange",()=>{if(document.hidden)window.HardwareLens.stop();});
  window.addEventListener("pagehide",()=>{window.HardwareLens.stop();importGeneration++;shutterAudio?.close().catch(()=>{});[imageUrl,audioUrl,resultUrl,practiceAudioUrl].filter(Boolean).forEach(url=>URL.revokeObjectURL(url));});
})();
