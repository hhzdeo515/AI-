const fs = require('node:fs');
const vm = require('node:vm');

// Reuse the actual lens DOM/media harness and supply only the two external feature boundaries.
const source = fs.readFileSync(require.resolve('./test_hardware_lens.cjs'), 'utf8').replace(/\r\n/g, '\n');
const boundary = source.indexOf('\n(async()=>{');
if (boundary < 0) throw new Error('Lens harness boundary not found');
const setup = String.raw`
const windowEvents = new Map(), featureEvents = [], featureClicks = [];
window.addEventListener = (name, callback) => windowEvents.set(name, callback);
window.LensFeatures = Object.fromEntries(['translation','map'].map(name => [name, {
  mount(panel) {
    featureEvents.push(name + ':mount:' + panel.id);
    const controls = [node(name + '-start'), node(name + '-stop')];
    controls.forEach(control => { control.textContent = control.id; control.click = () => featureClicks.push(control.id); });
    panel.querySelectorAll = () => controls;
  },
  open() { featureEvents.push(name + ':open'); },
  close() { featureEvents.push(name + ':close'); }
}]));
`;
const harness = source.slice(0, boundary).replace('vm.runInNewContext(', setup + '\nvm.runInNewContext(');
const sequence = String.raw`
(async()=>{
  assert.ok(featureEvents.includes('translation:mount:hw-translation-panel'));
  assert.ok(featureEvents.includes('map:mount:hw-map-panel'));
  assert.equal(node('hw-lens-status').textContent,'1 / 4','the home HUD counts all available scenes');
  assert.equal(node('hw-nav-options').buttons.length,4,'all four scenes are selectable from the original ring menu');
  await operate('scene2');await operate('press');
  assert.equal(node('hw-translation-panel').hidden,false);assert.equal(node('hw-map-panel').hidden,true);
  assert.equal(featureEvents.filter(event=>event==='translation:open').length,1);
  assert.equal(node('hw-mode-label').textContent,'实时翻译');
  await operate('press');assert.deepEqual(featureClicks,['translation-start'],'ring confirmation reaches the feature control');
  await operate('next');await operate('press');assert.deepEqual(featureClicks,['translation-start','translation-stop'],'ring navigation reaches the next feature control');
  await operate('back');assert.equal(node('hw-translation-panel').hidden,true);
  assert.ok(featureEvents.includes('translation:close'),'leaving the feature releases its lifecycle');
  await operate('press');const beforeSwitch=featureEvents.filter(event=>event==='translation:close').length;
  await operate('scene3');assert.ok(featureEvents.filter(event=>event==='translation:close').length>beforeSwitch,'switching scenes closes translation');
  await operate('press');assert.equal(node('hw-map-panel').hidden,false);
  assert.ok(featureEvents.includes('map:open'));assert.equal(requests,0,'features never submit requests to photo or meeting processing');
  const beforeImport=featureEvents.filter(event=>event==='map:close').length;
  assert.equal(window.HardwareLens.acceptAudio(new File(['voice'],'meeting.webm',{type:'audio/webm'})),true);
  assert.ok(featureEvents.filter(event=>event==='map:close').length>beforeImport,'a direct media import closes the prior feature when it changes scenes');
  await operate('scene3');await operate('press');assert.equal(node('hw-map-panel').hidden,false);
  const beforeHidden=featureEvents.filter(event=>event==='map:close').length;
  document.hidden=true;docEvents.get('visibilitychange')();
  assert.ok(featureEvents.filter(event=>event==='map:close').length>beforeHidden,'hidden pages close the map and its requests');
  assert.equal(node('hw-map-panel').hidden,true);
  document.hidden=false;await operate('press');
  context.location.hash='#memory';windowEvents.get('hashchange')();assert.equal(node('hw-map-panel').hidden,true,'opening the archive leaves no active feature panel');
  context.location.hash='#hardware';windowEvents.get('hashchange')();
  await operate('press');await operate('home');assert.equal(node('hw-map-panel').hidden,true,'returning home closes the feature');
  await operate('scene2');blockTransport=true;
  const opensBefore=featureEvents.filter(event=>event==='translation:open').length;
  const delayedOpen=operate('press');await new Promise(setImmediate);assert.equal(typeof transportResume,'function');
  window.HardwareLens.stop();blockTransport=false;transportResume();await delayedOpen;
  assert.equal(featureEvents.filter(event=>event==='translation:open').length,opensBefore,'stopping while ring transport is pending cannot reopen translation later');
  assert.equal(node('hw-ring').disabled,false,'canceling a pending feature command releases the ring');
  await operate('press');const beforePageHide=featureEvents.filter(event=>event==='translation:close').length;
  windowEvents.get('pagehide')();assert.ok(featureEvents.filter(event=>event==='translation:close').length>beforePageHide);
  assert.equal(requests,0);assert.equal(cameraAcquisitions,0,'opening features does not open the exam camera');
  console.log('Feature menu: four scenes, plugin mounting, ring controls, back/home/switch, hidden/archive/pagehide cleanup and late-command cancellation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
`;
vm.runInNewContext(harness + sequence, {
  require, console, process, Blob, File, URL, setInterval, clearInterval, setTimeout, clearTimeout, queueMicrotask, setImmediate,
}, { filename: __filename });
