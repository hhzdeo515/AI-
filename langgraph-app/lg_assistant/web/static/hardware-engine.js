/* Device contract simulator and lens navigation. Media capture stays in the browser. */
(function (root) {
  "use strict";
  const modes = ["拍照解题", "会议纪要"];
  const menuOptions = [
    ["拍照解题", "导入图片", "查看上次答案", "重新拍照", "解题设置", "重新解答当前图片"],
    ["开启录音", "导入录音", "生成会议纪要", "查看上次纪要"]
  ];
  const actions = ["press", "next", "previous", "wake", "back", "navigate"];
  class HardwareNavigation {
    constructor() { this.reset(); }
    reset() { this.state = {screen:"home", mode:0, focus:0}; }
    options(mode=this.state.mode) { return menuOptions[mode] || menuOptions[0]; }
    drag(total, scrollTop=0, scrollMax=0) {
      if(this.state.screen==="result")return {scrollTop:Math.max(0,Math.min(scrollMax,scrollTop+total*2))};
      const count=this.state.screen==="home"?modes.length:this.options().length;
      const offset=this.state.focus+Math.trunc(total/44);
      return {focus:((offset%count)+count)%count};
    }
    plan(action, {hasFile=false,hasResult=false,hasPendingPhoto=!hasResult}={}) {
      const next={...this.state}; let effect="none", message="";
      if (action==="home") { next.screen="home"; next.focus=next.mode; message="已回到场景选择"; }
      else if (action==="back" || action==="double") {
        next.screen=next.screen==="practice"||next.screen==="settings"&&next.returnScreen==="result"?"result":next.screen==="home"||next.screen==="actions"?"home":"actions"; delete next.returnScreen;next.focus=next.screen==="home"?next.mode:0; message="已返回上一级";
      } else if (/^scene\d+$/.test(action) && Number(action.slice(5))<modes.length) { next.mode=Number(action.slice(5));next.screen="actions";next.focus=0;message=`进入${modes[next.mode]}`; }
      else if (/^focus\d+$/.test(action) && ["home","actions"].includes(next.screen) && Number(action.slice(5))<(next.screen==="home"?modes.length:this.options(next.mode).length)) { next.focus=Number(action.slice(5));if(next.screen==="home")next.mode=next.focus;message=`选中${next.screen==="home"?modes[next.focus]:this.options(next.mode)[next.focus]}`; }
      else if (action==="scrollPosition" && next.screen==="result") { message="阅读位置已同步"; }
      else if (action==="rerun" && next.mode===0) {
        effect=hasFile?"run":"missing";message=hasFile?"重新解答当前图片":"还没有题目图片，请先拍照或导入图片";
      }
      else if (action==="read" && hasResult) { next.screen="result";effect="read";message="继续阅读上次结果"; }
      else if (["previous","next","up","down"].includes(action)) {
        const direction=action==="previous"||action==="up"?-1:1;
        if(next.screen==="result") { effect=direction<0?"scrollUp":"scrollDown";message=direction<0?"向上阅读":"向下阅读"; }
        else if(["home","actions"].includes(next.screen)) { const length=next.screen==="home"?modes.length:this.options(next.mode).length;next.focus=(next.focus+direction+length)%length;if(next.screen==="home")next.mode=next.focus;message=`选中${next.screen==="home"?modes[next.focus]:this.options(next.mode)[next.focus]}`; }
        else {effect=direction<0?"controlPrevious":"controlNext";message="选择镜片内的控件";}
      } else if (action==="press") {
        if(next.screen==="home") { next.screen="actions";next.focus=0;message=`进入${modes[next.mode]}`; }
        else if(next.screen==="result") { effect="scrollDown";message="阅读下一段"; }
        else if(next.screen==="camera") {effect="capture";message="拍摄眼前画面";}
        else if(next.screen==="recording") {effect="recordToggle";message="控制会议录音";}
        else if(next.screen==="settings") {effect="settingsSelect";message="确认当前设置";}
        else if(next.screen==="practice") {effect="practiceSelect";message="确认当前练习操作";}
        else if((next.mode===0&&next.focus===2)||(next.mode===1&&next.focus===3)) {
          if(hasResult){next.screen="result";effect="read";message="继续阅读上次结果";}else{effect="missing";message="还没有处理结果，请先完成一次处理";}
        }
        else if(next.focus===1) { effect="import";message=next.mode===0?"请选择眼前画面":"请选择会议录音"; }
        else if(next.mode===0&&next.focus===4) {next.screen="settings";effect="settings";message="打开解题设置";}
        else if(next.mode===0&&next.focus===5) {effect=hasFile?"run":"missing";message=hasFile?"重新解答当前图片":"还没有题目图片，请先拍照或导入图片";}
        else if(next.mode===0&&(next.focus===3||!hasFile||hasResult||!hasPendingPhoto)) {next.screen="camera";effect="camera";message="打开眼镜摄像头，拍摄新题目";}
        else if(next.mode===1&&next.focus===0) {next.screen="recording";effect="record";message="打开会议录音";}
        else if(!hasFile) { next.focus=0;effect="missing";message="先开启录音或导入音频，再生成会议纪要"; }
        else { effect="run";message=next.mode===0?"图片处理指令已执行":"录音处理指令已执行"; }
      }
      return {next,effect,message};
    }
    commit(plan) { this.state=plan.next; }
  }
  // Permission prompts can outlive their lens panel. Never retain a late stream.
  class HardwareMediaSession {
    constructor(acquire) {this.acquire=acquire;this.generation=0;this.stream=null;this.opening=false;}
    async start(constraints) {
      this.stop();const ticket=this.generation;this.opening=true;
      try {
        const stream=await this.acquire(constraints);
        if(ticket!==this.generation){stream.getTracks().forEach(track=>track.stop());return null;}
        this.stream=stream;this.opening=false;return stream;
      } catch(error) {if(ticket!==this.generation)return null;this.opening=false;throw error;}
    }
    stop() {this.generation++;this.opening=false;if(this.stream)this.stream.getTracks().forEach(track=>track.stop());this.stream=null;}
    get active() {return this.opening||!!this.stream;}
  }
  class HardwareSimulator {
    constructor({onChange = () => {}, wait = ms => new Promise(r => setTimeout(r, ms))} = {}) {
      this.onChange = onChange; this.wait = wait; this.generation = 0; this.serial = 0;
      this.reset();
    }
    reset() {
      this.generation++;
      this.state = {connected: true, busy: false, mode: 0, assistant: false,
        meeting: false, photos: 0, phase: -1, error: false,
        feedback: "准备好了，按一下戒指试试。", fault: "none", events: [], last: null};
      this.emit();
    }
    emit() { this.onChange(this.state); }
    log(stage, text, id) {
      this.state.events.unshift({stage, text, id, time: new Date().toLocaleTimeString("zh-CN", {hour12: false})});
      this.state.events = this.state.events.slice(0, 24);
    }
    connect(value) {
      this.generation++;
      const s = this.state;
      if (s.busy) this.log("中止", "连接变化，待执行指令已取消", s.last?.id);
      s.connected = value; s.busy = false; s.phase = -1; s.error = false;
      s.feedback = value ? "模拟连接已恢复，请重新操作；旧指令不会自动重放。" : "模拟连接已断开。按压戒指可查看失败反馈。";
      this.emit();
    }
    setFault(value) { this.state.fault = value; this.emit(); }
    async send(action, {execute, label}={}) {
      if (!actions.includes(action)) throw new Error("Unknown ring action");
      const s = this.state;
      if (s.busy) return false;
      const token = this.generation;
      const request = {id: `SIM-${String(++this.serial).padStart(4, "0")}`, source: "virtual-ring", action, simulated: true};
      s.last = request; s.busy = true; s.error = false; s.phase = 0;
      s.feedback = "戒指已接收操作…";
      this.log("输入", label || action, request.id); this.emit();
      const step = async (phase, message) => {
        await this.wait(action === "navigate" ? 45 : 280);
        if (this.generation !== token) return false;
        s.phase = phase; s.feedback = message; this.emit(); return true;
      };
      const fail = message => {
        s.busy = false; s.error = true; s.feedback = message;
        this.log("失败", message, request.id); this.emit(); return false;
      };
      if (!await step(1, "模拟链路正在传输指令…")) return false;
      if (!s.connected) return fail("未送达：连接已断开。请恢复连接后重试。");
      const fault = s.fault; s.fault = "none";
      if (fault === "timeout") {
        await this.wait(600);
        if (this.generation !== token) return false;
        return fail("模拟传输超时：指令未送达，眼镜未执行。请重试。");
      }
      this.log("送达", "虚拟眼镜收到指令", request.id);
      if (!await step(2, "虚拟眼镜正在执行…")) return false;
      if (fault === "reject") return fail("模拟执行失败：设备忙，未改变眼镜状态。请重试。");
      // This delay precedes mutation so a disconnect/reset cannot leave a stale execution.
      if (!await step(2, "正在生成执行结果…")) return false;
      let result;
      if (action === "navigate") {
        result = execute?.() || "操作已完成";
      } else if (action === "next" || action === "previous") {
        s.mode = (s.mode + (action === "next" ? 1 : modes.length - 1)) % modes.length;
        s.assistant = false; result = `已切换到${modes[s.mode]}`;
      } else if (action === "wake") {
        s.assistant = true; result = "助手已唤醒（模拟），等待你的指令";
      } else if (action === "back") {
        s.assistant = false; result = "已返回眼前画面";
      } else if (s.assistant) {
        result = "助手已就绪（模拟）。可打开实际助手继续对话";
      } else if (s.mode === 0) {
        s.photos++; result = `拍照指令已执行，已截取当前导入画面`;
      } else {
        result = "会议指令已执行，准备处理导入音频";
      }
      s.phase = 3; s.busy = false; s.feedback = result;
      this.log("回执", result, request.id); this.emit(); return true;
    }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {HardwareSimulator, HardwareNavigation, HardwareMediaSession, modes};
  else { root.HardwareSimulator = HardwareSimulator; root.HardwareNavigation = HardwareNavigation; root.HardwareMediaSession=HardwareMediaSession; }
})(typeof window !== "undefined" ? window : globalThis);
