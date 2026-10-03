const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const sourcePath = path.join(__dirname, '../lg_assistant/web/static/lens-imu.js');
const tick = () => new Promise(resolve => setImmediate(resolve));

function target() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    fire(type, value = {}) { for (const fn of [...(listeners.get(type) || [])]) fn({ type, ...value }); },
    count(type) { return listeners.get(type)?.size || 0; },
  };
}

function setup(options = {}) {
  const w = target(), orientation = Object.assign(target(), { angle: 0 }), document = Object.assign(target(), { hidden: false });
  const changes = [], timers = new Map();
  let now = 0, timerId = 0, permissionCalls = [];
  w.screen = { orientation }; w.document = document; w.isSecureContext = options.secure !== false;
  w.setTimeout = (fn, delay) => { const id = ++timerId; timers.set(id, { fn, at: now + delay }); return id; };
  w.clearTimeout = id => timers.delete(id);
  if (!options.unsupported) {
    w.DeviceOrientationEvent = function () {};
    if (options.permission) w.DeviceOrientationEvent.requestPermission = absolute => { permissionCalls.push(absolute); return options.permission(); };
  }
  w.window = w;
  if (fs.existsSync(sourcePath)) vm.runInNewContext(fs.readFileSync(sourcePath, 'utf8'), { window: w });
  assert.ok(w.LensIMU?.create, 'register a browser IMU controller without starting sensors automatically');
  const imu = w.LensIMU.create({ onChange: state => changes.push(state) });
  function advance(ms) {
    now += ms;
    for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); }
  }
  return { w, document, orientation, imu, changes, timers, permissionCalls, advance };
}

test('manual simulation is always available, wraps headings, and labels all eight compass directions', () => {
  const s = setup({ unsupported: true });
  assert.equal(s.w.count('deviceorientation'), 0);
  assert.equal(s.timers.size, 0);
  for (const [degrees, heading, label] of [[450, 90, '东'], [-90, 270, '西'], [720, 0, '北'], [45, 45, '东北'], [135, 135, '东南'], [180, 180, '南'], [225, 225, '西南'], [315, 315, '西北']]) {
    s.imu.setHeading(degrees);
    assert.equal(s.imu.status.heading, heading); assert.equal(s.imu.status.label, label);
    assert.equal(s.imu.status.source, 'manual'); assert.equal(s.imu.status.available, false);
    assert.match(s.imu.status.message, /模拟/);
  }
  const count = s.changes.length;
  s.imu.setHeading(NaN); s.imu.setHeading(Infinity); s.imu.setHeading(null);
  assert.equal(s.changes.length, count, 'invalid inputs do not replace a valid heading');
  const snapshot = s.imu.status; snapshot.heading = 123;
  assert.equal(s.imu.status.heading, 315, 'state snapshots cannot mutate the controller');
  s.imu.calibrate();
  assert.equal(s.imu.status.heading, 0); assert.equal(s.imu.status.source, 'manual');
});

test('sensor startup requests explicit permission and projects the device forward with pitch and roll', async () => {
  const s = setup({ permission: async () => 'granted' });
  assert.equal(s.permissionCalls.length, 0);
  const started = s.imu.start();
  assert.deepEqual(s.permissionCalls, [true], 'request magnetometer permission inside the explicit start action');
  await tick();
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 270, beta: 90, gamma: 0 });
  const first = await started;
  assert.ok(Math.abs(first.heading - 90) < 1e-8); assert.equal(first.source, 'sensor'); assert.equal(first.available, true);
  assert.equal(first.pitch, 90); assert.equal(first.roll, 0); assert.equal(first.absolute, true);
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 270, beta: 0, gamma: 90 });
  assert.ok(Math.abs(s.imu.status.heading) < 1e-8, 'rolling the device sideways changes the projected facing direction');
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 0, beta: 90, gamma: 30 });
  assert.ok(Math.abs(s.imu.status.heading - 330) < 1e-8, 'heading is not a raw alpha alias');
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 180, beta: 0, gamma: 0 });
  assert.equal(s.imu.status.available, false); assert.match(s.imu.status.message, /水平朝向|立起/);
  s.imu.stop();
});

test('absolute or WebKit compass data supersedes relative readings without accepting lower-priority late readings', async () => {
  const s = setup(); const started = s.imu.start();
  s.w.fire('deviceorientation', { absolute: false, alpha: 30, beta: 90, gamma: 0 });
  await started;
  assert.equal(s.imu.status.source, 'relative'); assert.equal(s.imu.status.absolute, false);
  assert.equal(s.imu.status.calibrated, false); assert.match(s.imu.status.message, /相对.*未校准/);
  s.imu.calibrate(0);
  assert.equal(s.imu.status.heading, 0); assert.equal(s.imu.status.calibrated, true); assert.equal(s.imu.status.source, 'relative');
  s.w.fire('deviceorientation', { absolute: false, alpha: 60, beta: 90, gamma: 0 });
  assert.ok(Math.abs(s.imu.status.heading - 330) < 1e-8); assert.match(s.imu.status.message, /相对.*参考/);
  s.w.fire('deviceorientation', { alpha: 60, beta: 90, gamma: 0, webkitCompassHeading: 90, webkitCompassAccuracy: 10 });
  assert.equal(s.imu.status.source, 'sensor'); assert.ok(Math.abs(s.imu.status.heading - 90) < 1e-8);
  assert.equal(s.imu.status.calibrated, false, 'an absolute reference replaces a calibration made in a different reference frame');
  s.w.fire('deviceorientation', { absolute: false, alpha: 180, beta: 90, gamma: 0 });
  assert.ok(Math.abs(s.imu.status.heading - 90) < 1e-8);
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 180, beta: 90, gamma: 0 });
  assert.ok(Math.abs(s.imu.status.heading - 180) < 1e-8);
  s.imu.stop();
});

test('screen rotation updates screen coordinates without adding a false yaw to the physical viewing direction', async () => {
  const s = setup(); const started = s.imu.start();
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 270, beta: 90, gamma: 0 });
  await started;
  s.orientation.angle = 90; s.orientation.fire('change');
  assert.equal(s.imu.status.screenAngle, 90); assert.ok(Math.abs(s.imu.status.heading - 90) < 1e-8);
  s.orientation.angle = -90; s.orientation.fire('change');
  assert.equal(s.imu.status.screenAngle, 270); assert.ok(Math.abs(s.imu.status.heading - 90) < 1e-8);
  s.imu.stop(); assert.equal(s.orientation.count('change'), 0);
});

test('unavailable, insecure, and denied sensors fall back to truthful manual simulation', async () => {
  for (const options of [{ unsupported: true }, { secure: false }, { permission: async () => 'denied' }, { permission: async () => { throw new Error('blocked'); } }]) {
    const s = setup(options); s.imu.setHeading(225);
    const state = await s.imu.start();
    assert.equal(state.source, 'manual'); assert.equal(state.available, false); assert.equal(state.heading, 225);
    assert.match(state.message, /手动模拟/); assert.equal(s.w.count('deviceorientation'), 0); assert.equal(s.timers.size, 0);
    s.imu.setHeading(45); assert.equal(s.imu.status.heading, 45);
  }
});

test('a computer without usable direction events times out and releases sensor subscriptions', async () => {
  const s = setup(); s.imu.setHeading(135); const started = s.imu.start();
  assert.ok(s.w.count('deviceorientation') > 0);
  s.w.fire('deviceorientation', { alpha: null, beta: null, gamma: null });
  s.advance(3000);
  const state = await started;
  assert.equal(state.source, 'manual'); assert.equal(state.available, false); assert.equal(state.heading, 135);
  assert.match(state.message, /未收到|不支持/); assert.match(state.message, /手动模拟/);
  assert.equal(s.w.count('deviceorientation'), 0); assert.equal(s.w.count('deviceorientationabsolute'), 0);
  assert.equal(s.timers.size, 0);
});

test('stop and manual input cancel late permissions and stale event callbacks, while restart is explicit', async () => {
  let grant;
  const s = setup({ permission: () => new Promise(resolve => { grant = resolve; }) });
  const started = s.imu.start(); s.imu.stop(); await started;
  grant('granted'); await tick();
  assert.equal(s.w.count('deviceorientation'), 0); assert.equal(s.timers.size, 0);
  const restarted = s.imu.start(); grant('granted'); await tick();
  const oldListener = [...s.w.listeners.get('deviceorientationabsolute')][0];
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 0, beta: 90, gamma: 0 }); await restarted;
  s.imu.setHeading(123); const count = s.changes.length;
  oldListener({ type: 'deviceorientationabsolute', absolute: true, alpha: 180, beta: 90, gamma: 0 });
  assert.equal(s.imu.status.heading, 123); assert.equal(s.imu.status.source, 'manual'); assert.equal(s.changes.length, count);
  assert.equal(s.w.count('deviceorientationabsolute'), 0);
  const pending = s.imu.start(); s.imu.setHeading(270); await pending; grant('granted'); await tick();
  assert.equal(s.imu.status.heading, 270); assert.equal(s.w.count('deviceorientation'), 0);
});

test('calibration keeps live sensors subscribed and page lifecycle stops all sensor resources', async () => {
  const s = setup(); const started = s.imu.start();
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 270, beta: 90, gamma: 0 }); await started;
  s.imu.calibrate(180); assert.ok(Math.abs(s.imu.status.heading - 180) < 1e-8);
  assert.equal(s.imu.status.calibrated, true); assert.equal(s.w.count('deviceorientationabsolute'), 1);
  s.w.fire('deviceorientationabsolute', { absolute: true, alpha: 260, beta: 90, gamma: 0 });
  assert.ok(Math.abs(s.imu.status.heading - 190) < 1e-8);
  s.document.hidden = true; s.document.fire('visibilitychange');
  assert.equal(s.imu.status.available, false); assert.equal(s.w.count('deviceorientation'), 0);
  assert.equal(s.w.count('pagehide'), 0); assert.equal(s.document.count('visibilitychange'), 0);
  s.document.hidden = false; const again = s.imu.start();
  s.w.fire('pagehide'); await again;
  assert.equal(s.w.count('deviceorientationabsolute'), 0); assert.equal(s.timers.size, 0);
});
