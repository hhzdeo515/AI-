// Real request/composer functions with controllable network promises; no AI calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../lg_assistant/web/static/app.js'), 'utf8');
const api = source.slice(source.indexOf('  async function apiJson(path, opts)'), source.indexOf('  function form(opts)'));
const composer = source.slice(source.indexOf('  async function runComposer(opts)'), source.indexOf('  // Hardware UI reuses'));
const polling = source.slice(source.indexOf('  async function sendAsync(opts)'), source.indexOf('  /* ── Toast'));

function network(fetch) {
  const timers = new Map(); let next = 0;
  const context = {fetch, AbortController, TypeError, setTimeout(fn, ms) { timers.set(++next, {fn, ms}); return next; }, clearTimeout(id) { timers.delete(id); }};
  vm.runInNewContext(api + '\nthis.request = apiJson;', context);
  return {request:context.request,timers};
}

async function checkNetwork() {
  let signal;
  const headers = network((_path, opts) => new Promise((_resolve, reject) => {
    signal = opts.signal;
    signal.addEventListener('abort', () => reject(new Error('aborted')));
  }));
  const pending = headers.request('/api/task?task_id=existing');
  assert.equal(headers.timers.size, 1);
  assert.equal([...headers.timers.values()][0].ms, 15000);
  headers.timers.values().next().value.fn();
  await assert.rejects(pending, /响应超时.*后台处理.*资料库/);
  assert.ok(signal.aborted);
  assert.equal(headers.timers.size, 0);

  let bodySignal;
  const body = network(async (_path, opts) => {
    bodySignal = opts.signal;
    return {ok:true,json:() => new Promise((_resolve,reject) => {
      bodySignal.addEventListener('abort', () => reject(new Error('body aborted')));
    })};
  });
  const blocked = body.request('/api/state');
  await new Promise(setImmediate);
  body.timers.values().next().value.fn();
  await assert.rejects(blocked, /响应超时/);
  assert.equal(body.timers.size, 0, 'deadline also covers reading the response body');

  const healthy = network(async (_path, opts) => {
    assert.equal(opts.timeoutMs, undefined, 'timeout option is not passed to fetch');
    return {ok:true,json:async () => ({status:'running'})};
  });
  assert.equal((await healthy.request('/api/task', {timeoutMs:2000})).status, 'running');
  assert.equal(healthy.timers.size, 0);
  const httpError = network(async () => ({ok:false,status:404,json:async () => ({error:'找不到该任务'})}));
  await assert.rejects(httpError.request('/api/task'), /找不到该任务/);
  assert.equal(httpError.timers.size, 0);
  const unavailable = network(async () => {throw new TypeError('Failed to fetch');});
  await assert.rejects(unavailable.request('/api/chat/async', {method:'POST'}), /连不上本地服务/);
  const sync = network(async (_path, opts) => {
    assert.equal(opts.signal, undefined, 'long synchronous model requests keep their existing behavior');
    assert.equal(sync.timers.size, 0);
    return {ok:true,json:async () => ({text:'answer'})};
  });
  assert.equal((await sync.request('/api/chat', {method:'POST'})).text, 'answer');
}

async function checkComposer() {
  const locks = [], replies = [], context = {
    S:{owner:'local',session:'composer',busy:false}, lastError:'',
    window:{AssistantModel:{id:'original'}},
    IMAGE_EXT:/\.png$/, AUDIO_EXT:/\.wav$/,
    toast:message => replies.push(message), renderMeeting(){},
    setSendBusy:busy => locks.push(busy),
    refreshState:async () => {throw new Error('unrelated state read must not run');},
    sendAsync:async () => ({text:'completed camera answer'}),
  };
  vm.runInNewContext(composer + '\nthis.run = runComposer;', context);
  const reply = await context.run({scene:'exam',session_id:'hardware-session',files:[{name:'camera.png'}]});
  assert.equal(reply.text, 'completed camera answer');
  assert.equal(context.S.busy, false);
  assert.deepEqual(locks, [true,false], 'completion releases send/model locks');

  context.sendAsync = async () => {throw new Error('本地服务响应超时');};
  assert.equal(await context.run({scene:'exam',async:true,session_id:'hardware-session'}), null);
  assert.equal(context.S.busy, false);
  assert.match(context.lastError, /响应超时/);
  assert.deepEqual(locks, [true,false,true,false], 'failure also releases send/model locks');
}

async function checkRestartedTask() {
  const calls = [], locks = [], notices = [];
  let polls = 0, stopped = 0;
  const interruption = '找不到该任务。服务可能已重启，本次处理已中断；如已上传照片，可重新提交。';
  const context = {
    S:{owner:'local',session:'composer',busy:false}, lastError:'',
    window:{AssistantModel:{id:'jev'}}, IMAGE_EXT:/\.png$/, AUDIO_EXT:/\.wav$/,
    AbortController, TypeError, setTimeout, clearTimeout,
    toast:message => notices.push(message), renderMeeting(){},
    setSendBusy:busy => locks.push(busy),
    refreshState:async () => {throw new Error('a lost task must not refresh unrelated state');},
    form:()=>({}), sleep:async()=>{},
    pollProgress:rid => {
      assert.equal(rid, 'interrupted-request');
      return () => {stopped++;};
    },
    fetch:async(path, options) => {
      calls.push({path,method:options.method || 'GET'});
      if (path === '/api/chat/async') {
        return {ok:true,status:202,json:async()=>({task_id:'interrupted-task',request_id:'interrupted-request'})};
      }
      assert.equal(path, '/api/task?task_id=interrupted-task&owner=local');
      polls++;
      return polls === 1
        ? {ok:true,status:200,json:async()=>({status:'running'})}
        : {ok:false,status:404,json:async()=>({error:interruption})};
    },
  };
  vm.runInNewContext(api + polling + composer + '\nthis.run = runComposer;', context);
  const reply = await context.run({scene:'exam',session_id:'hardware-session',
                                  request_id:'interrupted-request',files:[{name:'camera.png'}],onProgress(){}});
  assert.equal(reply, null);
  assert.equal(polls, 2, 'running then missing task terminates result polling');
  assert.equal(stopped, 1, 'a lost task also stops progress polling');
  assert.equal(context.S.busy, false);
  assert.deepEqual(locks, [true,false], 'restart interruption releases send/model locks');
  assert.match(context.lastError, /服务可能已重启.*本次处理已中断.*重新提交/);
  assert.ok(notices.some(message => message.includes(interruption)));
  assert.equal(calls.filter(call => call.method === 'POST').length, 1,
               'interruption must not automatically resubmit the photo');
}

(async () => {
  await checkNetwork();
  await checkComposer();
  await checkRestartedTask();
  console.log('Request lifecycle: hung headers/body time out, task status survives, camera answers display without unrelated state reads, UI locks release.');
})().catch(error => {console.error(error);process.exitCode=1;});
