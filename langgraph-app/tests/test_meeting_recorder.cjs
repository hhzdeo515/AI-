const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../lg_assistant/web/static/meeting-recorder.js'), 'utf8');
class Element {
  constructor() { this.listeners = {}; this.attrs = {}; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  async fire(name, event = {}) { for (const fn of this.listeners[name] || []) await fn(event); }
  setAttribute(name, value) { this.attrs[name] = value; }
  removeAttribute(name) { delete this[name]; delete this.attrs[name]; }
  pause() { this.paused = true; }
  load() { this.reloaded = true; }
  focus() { this.focused = true; }
}
function fixture({delayed = false, denied = false} = {}) {
  const nodes = {}, events = [], calls = [], window = new Element(), document = new Element();
  let stopped = 0, resolvePermission;
  const revoked = [];
  window.confirm = () => true;
  const track = new Element(); track.stop = () => stopped++;
  const stream = {getTracks: () => [track], getAudioTracks: () => [track]};
  const waiting = delayed ? new Promise(resolve => resolvePermission = resolve) : Promise.resolve(stream);
  class Recorder extends Element {
    static isTypeSupported(t) { return t.startsWith('audio/webm'); }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; queueMicrotask(async () => { await this.fire('dataavailable', {data:{size:512}}); await this.fire('stop'); }); }
  }
  window.MediaRecorder = Recorder;
  document.getElementById = id => nodes[id] ||= new Element();
  document.dispatchEvent = event => events.push(event);
  vm.runInNewContext(source, {document,window,MediaRecorder:Recorder,navigator:{mediaDevices:{getUserMedia:opts => {
    calls.push(opts); return denied ? Promise.reject(new Error('denied')) : waiting;
  }}}, location:{hash:'#meeting'}, Date, setInterval:()=>1,clearInterval(){}, URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:url=>revoked.push(url)},
  File:class {constructor(chunks,name,opts){this.name=name;this.type=opts.type;}},CustomEvent:class {constructor(type,opts){this.type=type;this.detail=opts.detail;}}});
  return {nodes,window,document,events,calls,revoked, stopped:()=>stopped, resolve:()=>resolvePermission(stream)};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  let f = fixture(); assert.equal(f.calls.length,0,'no microphone access on load');
  await f.nodes['meeting-record-toggle'].fire('click');
  assert.equal(f.calls[0].video,false); assert.equal(f.nodes['meeting-record-toggle'].textContent,'关闭录音');
  await f.nodes['meeting-record-toggle'].fire('click'); await settle();
  assert.equal(f.stopped(),1); assert.equal(f.nodes['meeting-record-toggle'].textContent,'开启录音');
  assert.equal(f.events.length,0,'stopping must not automatically upload');
  await f.nodes['meeting-record-use'].fire('click'); assert.equal(f.events[0].type,'meeting-recorded');
  assert.match(f.events[0].detail.name,/\.webm$/);

  f.window.confirm = () => false;
  await f.nodes['meeting-record-delete'].fire('click');
  assert.equal(f.nodes['meeting-record-preview'].hidden, false, 'cancel keeps recording');
  f.window.confirm = () => true;
  await f.nodes['meeting-record-delete'].fire('click');
  assert.equal(f.nodes['meeting-record-preview'].hidden, true);
  assert.equal(f.nodes['meeting-record-player'].src, undefined);
  assert.equal(f.nodes['meeting-record-player'].paused, true);
  assert.deepEqual(f.revoked, ['blob:test']);
  await f.nodes['meeting-record-use'].fire('click');
  assert.equal(f.events.length, 1, 'deleted recording cannot be submitted again');
  await f.nodes['meeting-record-toggle'].fire('click');
  assert.equal(f.nodes['meeting-record-delete'].disabled, true);
  await f.nodes['meeting-record-toggle'].fire('click'); await settle();
  assert.equal(f.nodes['meeting-record-preview'].hidden, false, 'new recording works after deletion');

  f = fixture({delayed:true}); const opening = f.nodes['meeting-record-toggle'].fire('click');
  await f.nodes['meeting-record-toggle'].fire('click'); f.resolve(); await opening;
  assert.equal(f.stopped(),1,'cancelled pending permission releases late stream');
  assert.equal(f.window.MeetingRecorder.isActive(),false);

  f = fixture({denied:true}); await f.nodes['meeting-record-toggle'].fire('click');
  assert.match(f.nodes['meeting-record-status'].textContent,/无法开启/);
  assert.equal(f.nodes['meeting-record-toggle'].textContent,'开启录音');

  f = fixture(); await f.nodes['meeting-record-toggle'].fire('click');
  f.document.hidden = true; await f.document.fire('visibilitychange'); await settle();
  assert.equal(f.stopped(),1); assert.equal(f.window.MeetingRecorder.isActive(),false);
  assert.match(f.nodes['meeting-record-status'].textContent,/后台/);
  console.log('8 recording lifecycle and deletion scenarios passed');
})().catch(error => {console.error(error);process.exitCode=1;});
