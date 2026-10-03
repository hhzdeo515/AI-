const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../lg_assistant/web/static/model-switch.js'),'utf8');
function fixture({href='http://localhost:8802/?keep=1#vision',saved='',storageDenied=false}={}) {
  const buttons=['original','jev'].map(id=>({dataset:{model:id},attrs:{},handlers:{},
    setAttribute(k,v){this.attrs[k]=v;},addEventListener(type,fn){this.handlers[type]=fn;}}));
  const status={},document={title:'',querySelectorAll:()=>buttons,getElementById:()=>status};
  const writes=[],changes=[],location={href};
  const draft={text:'仍然保留的问题',files:['question.png'],documentIds:['shared-doc-7']};
  const localStorage={getItem(){if(storageDenied)throw Error('blocked');return saved;},setItem(k,v){if(storageDenied)throw Error('blocked');writes.push([k,v]);}};
  const window={};
  vm.runInNewContext(source,{window,document,localStorage,location,URL,history:{replaceState(_,__,url){changes.push(url);location.href=new URL(url,location.href).href;}},draft});
  return {buttons,status,document,writes,changes,draft,model:window.AssistantModel,click:id=>buttons.find(b=>b.dataset.model===id).handlers.click()};
}
let f=fixture({saved:'jev'});assert.equal(f.model.id,'jev');assert.equal(f.buttons[1].attrs['aria-pressed'],'true');
f=fixture({href:'http://localhost:8802/?model=original#hardware',saved:'jev'});assert.equal(f.model.id,'original','shared link overrides stored choice');
f=fixture({href:'http://localhost:8802/?model=unknown#memory',saved:'unknown'});assert.equal(f.model.id,'original','unrecognized model cannot route a request');
f=fixture();const draftBefore=JSON.stringify(f.draft);f.click('jev');
assert.equal(f.model.id,'jev');assert.equal(f.document.title,'智能助手 · JEV');
assert.equal(f.changes[0],'/?keep=1&model=jev#vision','switch preserves the current page and other query parameters');
assert.equal(JSON.stringify(f.draft),draftBefore,'switch never edits or reloads page drafts, attachments or selected documents');
assert.deepEqual(f.writes,[['assistant-exam-model','jev']]);
const captured=f.model.id;f.model.setBusy(true);f.click('original');assert.equal(f.model.id,captured,'pending request cannot change its model');assert.ok(f.buttons.every(b=>b.disabled));
f.model.setBusy(true,'recording');f.model.setBusy(false);assert.ok(f.buttons.every(b=>b.disabled),'independent locks are retained');
f.model.setBusy(false,'recording');f.click('original');assert.equal(f.model.id,'original');assert.equal(captured,'jev','previous request selection remains unchanged');
assert.ok(f.buttons.every(b=>!b.disabled));f.model.setReady(false);f.click('jev');assert.equal(f.status.textContent,'JEV 尚未配置');f.model.setReady(true);assert.equal(f.status.hidden,true);
f=fixture({storageDenied:true});f.click('jev');assert.equal(f.model.id,'jev','blocked browser storage cannot prevent model switching');
assert.equal(f.changes.length,1);
console.log('Model switch: shared page/data, request capture, URL/preferences, busy locks and unavailable model states passed.');
