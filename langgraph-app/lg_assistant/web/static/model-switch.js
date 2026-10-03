/* Model selection changes the next request, while the page and its data stay shared. */
(() => {
  "use strict";
  const preference="assistant-exam-model";
  const models={original:"原版",jev:"JEV"};
  let saved="";
  try { saved=localStorage.getItem(preference); } catch (_) {}
  const requested=new URL(location.href).searchParams.get("model");
  let id=Object.hasOwn(models,requested)?requested:Object.hasOwn(models,saved)?saved:"original";
  const buttons=document.querySelectorAll("[data-model]");
  const status=document.getElementById("model-status");
  const locks=new Set();
  let jevReady=null;

  function render() {
    buttons.forEach(button => {
      button.setAttribute("aria-pressed",String(button.dataset.model===id));
      button.disabled=locks.size>0;
    });
    const message=locks.has("recording")?"录音结束后可切换":locks.size?"处理完成后可切换":id==="jev" && jevReady===false?"JEV 尚未配置":"";
    if(status) { status.textContent=message; status.hidden=!message; }
    document.title=`智能助手 · ${models[id]}`;
  }
  function setBusy(busy,reason="processing") {
    if(busy) locks.add(reason); else locks.delete(reason);
    render();
  }
  buttons.forEach(button => button.addEventListener("click",() => {
    const next=button.dataset.model;
    if(locks.size || next===id || !Object.hasOwn(models,next)) return;
    id=next;
    const target=new URL(location.href);
    target.searchParams.set("model",id);
    history.replaceState(null,"",target.pathname+target.search+target.hash);
    try { localStorage.setItem(preference,id); } catch (_) {}
    render();
  }));
  window.AssistantModel=Object.freeze({get id(){return id;},setBusy,setReady:ready=>{jevReady=ready;render();}});
  render();
})();
