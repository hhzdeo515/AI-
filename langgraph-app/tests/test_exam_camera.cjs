// Offline camera lifecycle tests: no physical camera or microphone is accessed.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../lg_assistant/web/static/exam-camera.js'), 'utf8');
class Element {
  constructor() { this.listeners = {}; this.checked = true; this.open = false; this.disabled = true; this.videoWidth = 1600; this.videoHeight = 1200; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({}); }
  showModal() { this.open = true; }
  close() { this.open = false; this.fire('close'); }
  async play() {}
}
function fixture({preference = null, delayed = false, denied = false, delayedBlob = false} = {}) {
  const nodes = {}, events = [], storage = {}, calls = [];
  const document = new Element(), window = new Element();
  let sounds = 0, stopped = 0, resolveCamera, resolveBlob;
  const track = new Element(); track.stop = () => stopped++;
  const stream = {getTracks: () => [track], getVideoTracks: () => [track]};
  const camera = delayed ? new Promise(resolve => { resolveCamera = resolve; }) : Promise.resolve(stream);
  document.getElementById = id => nodes[id] ||= new Element();
  document.dispatchEvent = event => events.push(event);
  document.createElement = () => ({getContext: () => ({drawImage(){}}), toBlob: fn => { if (delayedBlob) resolveBlob = fn; else fn({}); }});
  window.AudioContext = class {
    constructor() { sounds++; this.currentTime = 0; }
    createOscillator() { return {frequency:{},connect(){},start(){},stop(){}}; }
    createGain() { return {gain:{},connect(){}}; }
    close() { return Promise.resolve(); }
  };
  vm.runInNewContext(source, {document, window, navigator:{mediaDevices:{getUserMedia: opts => {
    calls.push(opts); return denied ? Promise.reject({name:'NotAllowedError'}) : camera;
  }}}, localStorage:{getItem: () => preference, setItem:(k,v) => storage[k] = v},
  File: class {constructor(parts,name,opts){this.name=name;this.type=opts.type;}}, CustomEvent:class {constructor(type,opts){this.type=type;this.detail=opts.detail;}}, Date});
  return {nodes,events,storage,calls,document,window,stream, sounds:()=>sounds, stopped:()=>stopped,
    resolveCamera:()=>resolveCamera(stream), resolveBlob:()=>resolveBlob({})};
}
(async () => {
  let f = fixture();
  assert.equal(f.calls.length, 0, 'camera must not open on page load');
  await f.window.ExamCamera.open();
  assert.equal(f.calls[0].audio, false);
  await f.nodes['exam-camera-shoot'].fire('click');
  assert.equal(f.sounds(), 0, 'default capture must be silent');
  assert.equal(f.events[0].type, 'exam-photo');
  assert.equal(f.events[0].detail.type, 'image/jpeg');
  assert.equal(f.stopped(), 1);

  f = fixture({preference:'false'});
  await f.window.ExamCamera.open();
  await f.nodes['exam-camera-shoot'].fire('click');
  assert.equal(f.sounds(), 1, 'opt-out allows capture sound');
  f.nodes['exam-camera-silent'].checked = true;
  await f.nodes['exam-camera-silent'].fire('change');
  assert.equal(f.storage['assistant.exam.camera.silent'], 'true');

  f = fixture({delayed:true});
  const opening = f.window.ExamCamera.open();
  f.window.ExamCamera.close(); f.resolveCamera(); await opening;
  assert.equal(f.stopped(), 1, 'late permission result must release stream');
  assert.equal(f.events.length, 0);

  f = fixture({denied:true}); await f.window.ExamCamera.open();
  assert.match(f.nodes['exam-camera-status'].textContent, /权限未开启/);
  assert.equal(f.nodes['exam-camera-shoot'].disabled, true);

  f = fixture({delayedBlob:true}); await f.window.ExamCamera.open();
  await f.nodes['exam-camera-shoot'].fire('click');
  f.window.ExamCamera.close(); f.resolveBlob();
  assert.equal(f.events.length, 0, 'cancelled capture must not attach a late image');

  f = fixture(); await f.window.ExamCamera.open(); f.document.hidden = true;
  await f.document.fire('visibilitychange');
  assert.equal(f.stopped(), 1);
  assert.equal(f.nodes['exam-camera-dialog'].open, false);
  console.log('6 camera lifecycle scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
