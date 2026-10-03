const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { JSDOM } = require('../../frontend/node_modules/jsdom');
const sourcePath = path.join(__dirname, '../lg_assistant/web/static/live-translation.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = (translation, status = 200) => ({ ok: status === 200, status, json: async () => status === 200 ? { translation, source: 'zh-CN', target: 'en', provider: 'MyMemory' } : { error: '翻译额度暂时用完，请稍后重试。' } });

function setup(options = {}) {
  const dom = new JSDOM('<section id="hw-translation-panel" class="hw-lens-panel hw-feature-panel" hidden></section>', { url: 'https://assistant.example/#hardware', runScripts: 'outside-only' });
  const w = dom.window, recognizers = [], calls = [], spoken = [];
  let cancelled = 0;
  class Recognition {
    constructor() { recognizers.push(this); this.starts = 0; this.aborts = 0; }
    start() { this.starts++; this.onstart?.(); }
    abort() { this.aborts++; this.onend?.(); }
    result(text, final = true) { const result = [{ transcript: text }]; result.isFinal = final; this.onresult?.({ resultIndex: 0, results: [result] }); }
  }
  if (!options.unsupported) w[options.webkit ? 'webkitSpeechRecognition' : 'SpeechRecognition'] = Recognition;
  w.TextEncoder = TextEncoder;
  w.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  w.speechSynthesis = { cancel() { cancelled++; }, speak(value) { spoken.push(value); } };
  w.fetch = async (url, init) => {
    const call = { url, ...init, body: JSON.parse(init.body) }; calls.push(call);
    return options.fetch ? options.fetch(call) : response(`译文:${call.body.text}`);
  };
  if (fs.existsSync(sourcePath)) w.eval(fs.readFileSync(sourcePath, 'utf8'));
  const feature = w.LensFeatures?.translation;
  assert.ok(feature, 'the independent translation feature must be registered');
  const panel = w.document.getElementById('hw-translation-panel');
  feature.mount(panel); feature.open();
  const get = name => w.document.getElementById(`hw-translation-${name}`);
  return { w, feature, get, panel, recognizers, calls, spoken, get cancelled() { return cancelled; }, cleanup() { feature.close(); dom.window.close(); } };
}

test('provides 15 languages and manual translation when browser speech recognition is unavailable', async () => {
  const s = setup({ unsupported: true });
  try {
    const required = ['zh-CN', 'en', 'ja', 'ko', 'fr', 'de', 'es', 'pt', 'it', 'ru', 'ar', 'hi', 'th', 'vi', 'id'];
    assert.deepEqual([...s.get('source').options].map(option => option.value), required);
    assert.deepEqual([...s.get('target').options].map(option => option.value), required);
    assert.equal(s.get('source').value, 'zh-CN'); assert.equal(s.get('target').value, 'en');
    assert.equal(s.get('start').disabled, true); assert.match(s.get('speech-status').textContent, /不支持.*文字/);
    assert.match(s.panel.textContent, /MyMemory/); assert.match(s.panel.textContent, /浏览器/);
    assert.equal(s.panel.querySelector('[data-hw-back]').textContent, '返回');
    s.get('text').value = '你好'; s.get('translate').click(); await tick();
    assert.equal(s.calls.length, 1); assert.deepEqual(s.calls[0].body, { text: '你好', source: 'zh-CN', target: 'en' });
    assert.equal(s.get('translation').textContent, '译文:你好');
    s.get('swap').click(); assert.equal(s.get('source').value, 'en'); assert.equal(s.get('target').value, 'zh-CN');
    assert.equal(s.get('translation').textContent, '');
  } finally { s.cleanup(); }
});

test('splits multibyte text by Unicode characters into at most 500 UTF-8 bytes and enforces 2000 characters', async () => {
  const s = setup();
  try {
    const text = '你'.repeat(170) + '。' + '😀'.repeat(160) + '!' + 'é'.repeat(100);
    s.get('text').value = text; s.get('translate').click(); await tick();
    assert.ok(s.calls.length > 2);
    assert.equal(s.calls.map(call => call.body.text).join(''), text);
    for (const call of s.calls) {
      assert.ok(Buffer.byteLength(call.body.text, 'utf8') <= 500);
      assert.ok(!/[\ud800-\udbff]$|^[\udc00-\udfff]/.test(call.body.text), 'no surrogate pair is split');
    }
    const before = s.calls.length;
    s.get('text').value = '你'.repeat(2001); s.get('translate').click(); await tick();
    assert.equal(s.calls.length, before); assert.match(s.get('status').textContent, /2000/);
  } finally { s.cleanup(); }
});

test('requests microphone only on explicit start and serializes final sentences without sending interim text', async () => {
  let firstResolve;
  const s = setup({ fetch: call => call.body.text === '第一句。' ? new Promise(resolve => { firstResolve = resolve; }) : response(`译文:${call.body.text}`) });
  try {
    assert.equal(s.recognizers.length, 0);
    s.get('start').click(); const recognition = s.recognizers[0];
    assert.equal(recognition.continuous, true); assert.equal(recognition.interimResults, true); assert.equal(recognition.lang, 'zh-CN');
    recognition.result('正在说话', false); await tick();
    assert.equal(s.calls.length, 0); assert.equal(s.get('interim').textContent, '正在说话'); assert.equal(s.get('text').value, '');
    recognition.result('第一句。'); recognition.result('第二句。'); await tick();
    assert.equal(s.calls.length, 1, 'a second sentence waits until the first finishes');
    firstResolve(response('first')); await tick();
    assert.deepEqual(s.calls.map(call => call.body.text), ['第一句。', '第二句。']);
    assert.equal(s.get('translation').textContent, 'first\n译文:第二句。');
    s.get('copy').click(); assert.equal(s.get('text').value, '第一句。\n第二句。');
    s.get('start').click(); assert.ok(recognition.aborts > 0); assert.equal(s.get('start').getAttribute('aria-pressed'), 'false');
  } finally { s.cleanup(); }
});

test('close aborts pending requests and browser speech, and ignores late results and end callbacks', async () => {
  let resolveFetch;
  const s = setup({ fetch: () => new Promise(resolve => { resolveFetch = resolve; }) });
  try {
    s.get('start').click(); const recognition = s.recognizers[0], lateEnd = recognition.onend, lateResult = recognition.onresult;
    recognition.result('关闭之前'); await tick();
    const signal = s.calls[0].signal;
    s.feature.close(); assert.ok(signal.aborted); assert.ok(recognition.aborts > 0); assert.ok(s.cancelled > 0); assert.equal(s.panel.hidden, true);
    lateEnd(); const result = [{ transcript: '晚到语音' }]; result.isFinal = true; lateResult({ resultIndex: 0, results: [result] });
    resolveFetch(response('late translation')); await tick(); await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(s.recognizers.length, 1); assert.equal(s.calls.length, 1); assert.equal(s.get('translation').textContent, '');
    assert.ok(!s.get('original').textContent.includes('晚到语音'));
    s.feature.open(); assert.equal(s.recognizers.length, 1, 'reopening does not request microphone');
  } finally { s.cleanup(); }
});

test('language changes abort old translations and stop old recognition without committing a late response', async () => {
  let resolveFetch;
  const s = setup({ fetch: () => new Promise(resolve => { resolveFetch = resolve; }) });
  try {
    s.get('start').click(); const recognition = s.recognizers[0]; recognition.result('旧语言'); await tick();
    s.get('target').value = 'ja'; s.get('target').dispatchEvent(new s.w.Event('change'));
    assert.ok(s.calls[0].signal.aborted); assert.ok(recognition.aborts > 0);
    resolveFetch(response('old English')); await tick(); assert.equal(s.get('translation').textContent, '');
    assert.equal(s.get('start').getAttribute('aria-pressed'), 'false');
  } finally { s.cleanup(); }
});

test('speech network errors stop automatic restarts and allow an explicit recovery', async () => {
  const s = setup();
  try {
    s.get('start').click(); const first = s.recognizers[0], lateEnd = first.onend;
    first.onerror({ error: 'network' }); lateEnd(); await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(s.recognizers.length, 1); assert.match(s.get('speech-status').textContent, /网络/);
    s.get('start').click(); assert.equal(s.recognizers.length, 2);
    s.recognizers[1].result('已恢复'); await tick(); assert.equal(s.get('translation').textContent, '译文:已恢复');
    const count = s.recognizers.length; s.recognizers[1].onend();
    await new Promise(resolve => setTimeout(resolve, 300)); assert.equal(s.recognizers.length, count + 1, 'a normal end resumes listening');
  } finally { s.cleanup(); }
});

test('translation quota errors display the real failure and release controls for retry', async () => {
  let fail = true;
  const s = setup({ fetch: call => fail ? response('', 429) : response('<img src=x onerror=alert(1)>') });
  try {
    s.get('text').value = '重试'; s.get('translate').click(); await tick();
    assert.match(s.get('status').textContent, /额度/); assert.equal(s.get('translation').textContent, '');
    assert.equal(s.get('translate').disabled, false);
    fail = false; s.get('translate').click(); await tick();
    assert.equal(s.get('translation').textContent, '<img src=x onerror=alert(1)>'); assert.equal(s.get('translation').children.length, 0);
    s.get('speak').click(); assert.equal(s.spoken[0].text, '<img src=x onerror=alert(1)>');
    s.feature.close(); assert.ok(s.cancelled > 0);
  } finally { s.cleanup(); }
});

test('hiding the panel or the browser page releases speech recognition and translation requests', async () => {
  const s = setup();
  try {
    s.get('start').click(); const first = s.recognizers[0]; s.panel.hidden = true; await tick();
    assert.ok(first.aborts > 0);
    s.feature.open(); s.get('start').click(); const second = s.recognizers[1];
    Object.defineProperty(s.w.document, 'hidden', { configurable: true, value: true });
    s.w.document.dispatchEvent(new s.w.Event('visibilitychange')); assert.ok(second.aborts > 0);
    assert.equal(s.get('start').getAttribute('aria-pressed'), 'false');
  } finally { s.cleanup(); }
});

test('repeated open calls preserve active listening without requesting another microphone', () => {
  const s = setup();
  try {
    s.get('start').click(); s.feature.open();
    assert.equal(s.recognizers.length, 1);
    assert.match(s.get('speech-status').textContent, /正在听取/);
  } finally { s.cleanup(); }
});

test('closing a scheduled speech restart cancels it and silent endings cannot restart indefinitely', async () => {
  const s = setup();
  try {
    s.get('start').click(); s.recognizers[0].onend(); s.feature.close();
    await new Promise(resolve => setTimeout(resolve, 300)); assert.equal(s.recognizers.length, 1);
    s.feature.open(); s.get('start').click();
    s.recognizers[1].onend(); await new Promise(resolve => setTimeout(resolve, 300));
    s.recognizers[2].onend(); await new Promise(resolve => setTimeout(resolve, 300));
    s.recognizers[3].onend(); await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(s.recognizers.length, 4); assert.equal(s.get('start').getAttribute('aria-pressed'), 'false');
    assert.match(s.get('speech-status').textContent, /停止/);
  } finally { s.cleanup(); }
});

test('webkit speech recognition respects selected language and permission errors require a manual restart', async () => {
  const s = setup({ webkit: true });
  try {
    s.get('source').value = 'ar'; s.get('source').dispatchEvent(new s.w.Event('change'));
    s.get('start').click(); const current = s.recognizers[0], lateEnd = current.onend;
    assert.equal(current.lang, 'ar-SA');
    current.onerror({ error: 'not-allowed' }); lateEnd();
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(s.recognizers.length, 1); assert.ok(current.aborts > 0);
    assert.match(s.get('speech-status').textContent, /权限/);
    s.get('start').click(); assert.equal(s.recognizers.length, 2);
  } finally { s.cleanup(); }
});

test('a late response after changing language cannot overwrite a newer translation or release its controls', async () => {
  const resolvers = [];
  const s = setup({ fetch: () => new Promise(resolve => resolvers.push(resolve)) });
  try {
    s.get('text').value = '旧请求'; s.get('translate').click(); await tick();
    s.get('target').value = 'ja'; s.get('target').dispatchEvent(new s.w.Event('change'));
    s.get('text').value = '新请求'; s.get('translate').click(); await tick();
    assert.equal(s.calls[1].body.target, 'ja');
    resolvers[0](response('old')); await tick();
    assert.equal(s.get('translation').textContent, ''); assert.equal(s.get('translate').disabled, true);
    resolvers[1](response('新しい翻訳')); await tick();
    assert.equal(s.get('translation').textContent, '新しい翻訳'); assert.equal(s.get('translate').disabled, false);
  } finally { s.cleanup(); }
});

test('subtitles precede manual input while language and microphone controls remain first', () => {
  const s = setup();
  try {
    const before = (left, right) => !!(left.compareDocumentPosition(right) & s.w.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.ok(before(s.get('source'), s.get('original')));
    assert.ok(before(s.get('start'), s.get('translation')));
    assert.ok(before(s.get('original'), s.get('text')), 'the source subtitle must appear before the manual form');
    assert.ok(before(s.get('translation'), s.get('text')), 'the translation subtitle must appear before the manual form');
    assert.equal(s.get('text').hidden, false);
  } finally { s.cleanup(); }
});

test('a new translation reveals subtitles inside the lens without scrolling the page', async () => {
  const s = setup();
  try {
    const scroller = s.panel.querySelector('.hw-panel-scroll');
    let pageScrolls = 0;
    s.w.scrollTo = s.w.scrollBy = () => { pageScrolls++; };
    s.w.HTMLElement.prototype.scrollIntoView = () => { pageScrolls++; };
    Object.defineProperty(scroller, 'clientHeight', { value: 200 });
    scroller.getBoundingClientRect = () => ({ top: 20, bottom: 220, height: 200 });
    const toolbar = s.panel.querySelector('.hw-translation-toolbar');
    if (toolbar) toolbar.getBoundingClientRect = () => ({ height: 60 });
    s.get('translation').getBoundingClientRect = () => ({ top: 500 - scroller.scrollTop, bottom: 580 - scroller.scrollTop, height: 80 });
    scroller.scrollTop = 200;
    s.get('text').value = '把字幕放在镜片里'; s.get('translate').click(); await tick();
    const subtitle = s.get('translation').getBoundingClientRect();
    assert.ok(subtitle.top >= 88 && subtitle.bottom <= 212, 'the subtitle is visible below microphone controls');
    assert.equal(pageScrolls, 0, 'the browser page and its ancestors must not jump');
  } finally { s.cleanup(); }
});

test('translation authentication reveals a password field only when required and retries using session storage', async () => {
  const token = 'private-test-translation-access';
  const s = setup({ fetch: call => call.headers.Authorization === `Bearer ${token}` ? response('authenticated translation') : { ok: false, status: 401, json: async () => ({ error: '需要翻译访问口令', code: 'authentication_required' }) } });
  try {
    assert.ok(s.get('access-row'), 'the hidden authentication prompt must be mounted');
    assert.equal(s.get('access-row').hidden, true);
    s.get('text').value = '需要口令'; s.get('translate').click(); await tick();
    assert.equal(s.get('access-row').hidden, false); assert.equal(s.get('access').type, 'password');
    assert.equal(s.get('access').getAttribute('aria-label'), '翻译访问口令');
    assert.equal(s.get('original').textContent, '需要口令'); assert.equal(s.get('text').value, '需要口令');
    assert.equal(s.calls.length, 1, 'authentication does not silently retry');
    s.get('access').value = token; s.get('translate').click(); await tick();
    assert.equal(s.w.sessionStorage.getItem('assistant.translation.access'), token);
    assert.equal(s.w.localStorage.length, 0);
    assert.equal(s.calls[1].url, '/api/translate'); assert.equal(s.calls[1].headers.Authorization, `Bearer ${token}`);
    assert.ok(!JSON.stringify(s.calls[1].body).includes(token));
    assert.equal(s.get('translation').textContent, 'authenticated translation');
    s.feature.close(); assert.equal(s.get('access').value, ''); assert.equal(s.get('access-row').hidden, true);
    assert.ok(!s.panel.textContent.includes(token)); assert.ok(!s.panel.outerHTML.includes(token));
  } finally { s.cleanup(); }
});

test('ordinary Flask cookie authentication errors do not request the separate translation password', async () => {
  const s = setup({ fetch: () => ({ ok: false, status: 401, json: async () => ({ error: '未授权：需要访问口令', code: 'unauthorized' }) }) });
  try {
    assert.ok(s.get('access-row'), 'the separate translation prompt must be mounted');
    s.get('text').value = '原文'; s.get('translate').click(); await tick();
    assert.equal(s.get('access-row').hidden, true); assert.match(s.get('status').textContent, /未授权/);
    assert.equal(s.get('original').textContent, '原文');
  } finally { s.cleanup(); }
});

test('a full translation queue stops recognition and retains the rejected final sentence for manual retry', async () => {
  let firstResolve;
  const s = setup({ fetch: call => call.body.text === '第1句' ? new Promise(resolve => { firstResolve = resolve; }) : response(`译文:${call.body.text}`) });
  try {
    s.get('start').click(); const current = s.recognizers[0];
    for (let index = 1; index <= 27; index++) current.result(`第${index}句`);
    await tick(); assert.ok(current.aborts > 0);
    assert.match(s.get('original').textContent, /第27句/);
    assert.match(s.get('speech-status').textContent, /未翻译语句已保留/);
    firstResolve(response('译文:第1句')); await tick();
    assert.equal(s.calls.length, 26); assert.match(s.get('status').textContent, /未翻译语句已保留/);
    s.get('copy').click(); assert.match(s.get('text').value, /第27句/);
  } finally { s.cleanup(); }
});

test('manual speech playback stops recognition to avoid translating its own audio', async () => {
  const s = setup();
  try {
    s.get('start').click(); const current = s.recognizers[0], lateEnd = current.onend;
    current.result('请播报'); await tick(); s.get('speak').click();
    assert.ok(current.aborts > 0); assert.equal(s.spoken.length, 1);
    assert.match(s.get('speech-status').textContent, /手动开始/);
    assert.equal(s.get('auto-speak'), null);
    lateEnd(); await new Promise(resolve => setTimeout(resolve, 300)); assert.equal(s.recognizers.length, 1);
  } finally { s.cleanup(); }
});
