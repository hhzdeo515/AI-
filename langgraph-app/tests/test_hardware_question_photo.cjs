/* Actual local question-photo drawing, without a browser or any AI calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../lg_assistant/web/static/hardware.js'), 'utf8');
const start = source.indexOf('  function questionPhoto(');
const end = source.indexOf('  function renderQuestionNavigation(', start);
assert.ok(start >= 0 && end > start, 'extract the production questionPhoto function');
const drawSource = source.slice(start, end) + '\nquestionPhoto;';

function harness(width = 1000, height = 1000, options = {}) {
  const canvases = [];
  const original = {naturalWidth: width, naturalHeight: height};
  const context = {
    sourcePhoto: original,
    imageUrl: 'blob:original-photo',
    questionIndex: 0,
    questionPhotos: new Map(),
    document: {createElement(tag) {
      assert.equal(tag, 'canvas');
      const draws = [], fills = [];
      const painter = {fillRect: (...args) => fills.push(args), drawImage: (...args) => draws.push(args)};
      const canvas = {width: 0, height: 0, draws, fills, exports: 0,
        getContext(kind) {assert.equal(kind, '2d'); return options.noPainter ? null : painter;},
        toDataURL(kind) {
          assert.equal(kind, 'image/png');
          this.exports++;
          if (options.failExport) throw new Error('simulated canvas export failure');
          return 'data:image/png;base64,canvas-' + canvases.indexOf(this);
        }};
      canvases.push(canvas);
      return canvas;
    }},
  };
  return {context, original, canvases, draw: vm.runInNewContext(drawSource, context)};
}

const region = (bbox, page = 1) => ({page, bbox});
const question = (regions, context = []) => ({regions, context_regions: context});

test('selected question uses its own normalized rectangle and matching source photo', () => {
  const h = harness();
  const url = h.draw(question([region([100, 200, 400, 300])]));
  assert.match(url, /^data:image\/png/);
  assert.equal(h.canvases.length, 1);
  const canvas = h.canvases[0];
  assert.equal(canvas.draws.length, 1);
  assert.equal(canvas.draws[0][0], h.original);
  assert.deepEqual(canvas.draws[0].slice(1), [96, 196, 308, 108, 0, 0, 308, 108]);
  assert.deepEqual([canvas.width, canvas.height], [308, 108]);
});

test('question and distant shared material are drawn separately, without their intervening neighbors', () => {
  const h = harness();
  h.draw(question([region([100, 700, 900, 850])], [region([0, 0, 1000, 200])]));
  const canvas = h.canvases[0];
  assert.equal(canvas.draws.length, 2);
  assert.deepEqual(canvas.draws[0].slice(1, 5), [96, 696, 808, 158]);
  assert.deepEqual(canvas.draws[1].slice(1, 5), [0, 0, 1000, 204]);
  assert.deepEqual(canvas.draws[0].slice(5), [96, 0, 808, 158]);
  assert.deepEqual(canvas.draws[1].slice(5), [0, 174, 1000, 204]);
  assert.deepEqual([canvas.width, canvas.height], [1000, 378]);
  assert.deepEqual(canvas.fills, [[0, 0, 1000, 378]], 'the gap and narrow-piece margins have a white background');
});

test('another page cannot be cropped from the currently loaded camera photograph', () => {
  const h = harness();
  assert.equal(h.draw(question([region([0, 0, 1000, 500], 2)])), h.context.imageUrl);
  assert.equal(h.canvases.length, 0);
  assert.equal(h.context.questionPhotos.size, 0);
});

test('foreign-page context is ignored while the valid local question remains visible', () => {
  const h = harness();
  h.draw(question([region([0, 600, 1000, 900])], [region([0, 0, 1000, 200], 2)]));
  assert.equal(h.canvases[0].draws.length, 1);
  assert.deepEqual(h.canvases[0].draws[0].slice(1, 5), [0, 596, 1000, 308]);
});

test('shared material alone cannot substitute for a missing or invalid question rectangle', () => {
  for (const own of [[], null, [region([200, 200, 200, 500])]]) {
    const h = harness();
    assert.equal(h.draw(question(own, [region([0, 0, 1000, 200])])), h.context.imageUrl);
    assert.equal(h.canvases.length, 0);
  }
});

test('invalid, nonfinite and out-of-bounds coordinates fall back to the full photograph', () => {
  const badRegions = [
    region([NaN, 0, 1000, 500]), region([0, 0, Infinity, 500]),
    region([-1, 0, 1000, 500]), region([0, -1, 1000, 500]),
    region([0, 0, 1001, 500]), region([0, 0, 1000, 1001]),
    region([1000, 0, 0, 500]), region([0, 500, 1000, 500]),
    region(['0', 0, 1000, 500]), region([null, 0, 1000, 500]),
    region([0, 0, 1000]), region('not coordinates'), region([0, 0, 1000, 500], '1'), null,
  ];
  for (const item of badRegions) {
    const h = harness();
    assert.equal(h.draw(question([item])), h.context.imageUrl);
    assert.equal(h.canvases.length, 0, JSON.stringify(item));
  }
});

test('undeclared or undecoded source size keeps the original photograph', () => {
  for (const [width, height] of [[0, 1000], [1000, 0]]) {
    const h = harness(width, height);
    assert.equal(h.draw(question([region([0, 0, 1000, 1000])])), h.context.imageUrl);
    assert.equal(h.canvases.length, 0);
  }
  const h = harness();
  h.context.sourcePhoto = null;
  assert.equal(h.draw(question([region([0, 0, 1000, 1000])])), h.context.imageUrl);
});

test('both crop dimensions remain at most 1600, including floating-point rounding at 2120 pixels', () => {
  for (const [width, height] of [[8000, 4000], [4000, 8000], [2120, 1000], [1000, 2120]]) {
    const h = harness(width, height);
    h.draw(question([region([0, 0, 1000, 1000])]));
    const canvas = h.canvases[0];
    assert.ok(canvas.width <= 1600 && canvas.height <= 1600, `${width}x${height} produced ${canvas.width}x${canvas.height}`);
    assert.ok(canvas.width >= 1 && canvas.height >= 1);
    assert.equal(canvas.draws[0][0], h.original);
  }
});

test('combined question and material height also obeys the size limit', () => {
  const h = harness(1000, 3000);
  h.draw(question([region([0, 500, 1000, 1000])], [region([0, 0, 1000, 400])]));
  const canvas = h.canvases[0];
  assert.equal(canvas.draws.length, 2);
  assert.ok(canvas.width <= 1600 && canvas.height <= 1600);
  assert.ok(canvas.draws[1][6] > canvas.draws[0][6], 'material has its own destination rectangle');
});

test('revisiting the same question reuses its cache while siblings have separate pictures', () => {
  const h = harness();
  const first = question([region([0, 0, 1000, 200])]);
  const second = question([region([0, 700, 1000, 1000])]);
  const firstUrl = h.draw(first);
  assert.equal(h.draw(first), firstUrl);
  assert.equal(h.canvases.length, 1);
  assert.equal(h.canvases[0].exports, 1);
  h.context.questionIndex = 1;
  const secondUrl = h.draw(second);
  assert.notEqual(secondUrl, firstUrl);
  assert.equal(h.canvases.length, 2);
  assert.notDeepEqual(h.canvases[0].draws[0].slice(1, 5), h.canvases[1].draws[0].slice(1, 5));
  h.context.questionIndex = 0;
  assert.equal(h.draw(first), firstUrl);
  assert.equal(h.canvases.length, 2);
});

test('canvas failure preserves the full photograph and does not cache a broken preview', () => {
  for (const options of [{noPainter: true}, {failExport: true}]) {
    const h = harness(1000, 1000, options);
    assert.equal(h.draw(question([region([0, 0, 1000, 500])])), h.context.imageUrl);
    assert.equal(h.context.questionPhotos.size, 0);
  }
});
