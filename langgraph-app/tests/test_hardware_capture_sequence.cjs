const captureFs=require('node:fs');
const captureVm=require('node:vm');

// Reuse the lens DOM/media harness, without running its existing test sequence.
const harnessSource=captureFs.readFileSync(require.resolve('./test_hardware_lens.cjs'),'utf8').replace(/\r\n/g,'\n');
const sequenceStart=harnessSource.indexOf('\n(async()=>{');
if(sequenceStart<0)throw new Error('The lens test harness boundary was not found.');
const harness=harnessSource.slice(0,sequenceStart);
const sequence=String.raw`
(async()=>{
  const video=node('hw-camera-video');
  video.videoWidth=640;video.videoHeight=480;
  let currentFrame=1,delayBlob=false,pendingBlob;
  const drawnFrames=[];
  document.createElement=tag=>{
    assert.equal(tag,'canvas');
    let drawnFrame;
    return {
      getContext(kind){
        assert.equal(kind,'2d');
        return {drawImage(source,x,y){
          assert.equal(source,video);assert.equal(x,0);assert.equal(y,0);
          drawnFrame=currentFrame;drawnFrames.push(drawnFrame);
        }};
      },
      toBlob(callback,type,quality){
        assert.equal(type,'image/jpeg');assert.equal(quality,.95);
        const deliver=()=>callback(new Blob(['frame-'+drawnFrame],{type}));
        if(delayBlob)pendingBlob=deliver;else deliver();
      }
    };
  };
  async function openCurrentCamera(){
    captureResolve=undefined;
    const beforeRequests=requests,beforeCameras=cameraAcquisitions;
    const opening=operate('press');await new Promise(setImmediate);
    assert.equal(typeof captureResolve,'function','capture action requests a new camera stream');
    assert.equal(cameraAcquisitions,beforeCameras+1);
    assert.equal(requests,beforeRequests,'opening the camera never resubmits a retained photo');
    captureResolve(stream);await opening;
    assert.equal(node('hw-camera-panel').hidden,false);
    assert.equal(video.srcObject,stream);assert.equal(node('hw-camera-shoot').disabled,false);
  }
  async function shootCurrentFrame(){
    decodeResolve=undefined;
    const beforeRequests=requests;
    const taking=node('hw-camera-shoot').fire('click');
    // A second click while the first snapshot is being decoded must be ignored.
    await node('hw-camera-shoot').fire('click');
    await new Promise(setImmediate);
    assert.equal(typeof decodeResolve,'function','a new captured file is decoded before upload');
    assert.equal(requests,beforeRequests);
    decodeResolve();await taking;
    assert.equal(requests,beforeRequests+1,'each completed snapshot is uploaded exactly once');
    assert.equal(node('hw-lens-result').hidden,false);
    assert.equal(video.srcObject,null,'the camera is released after accepting a snapshot');
    return practiceRequests.at(-1).files[0];
  }
  function frameReply(frame){return {text:'answer for frame '+frame,artifacts:[{kind:'exam_batch',questions:[
    {id:'q1',label:'第1题',status:'answered',answer:frame===1?'A':'B',explanation:'frame '+frame+' explanation'}
  ]}]};}
  try {
    await operate('scene0');await openCurrentCamera();
    reply=frameReply(1);const firstFile=await shootCurrentFrame();
    assert.ok(firstFile instanceof File);assert.equal(firstFile.type,'image/jpeg');
    assert.match(firstFile.name,/^question-\d+\.jpg$/);assert.equal(await firstFile.text(),'frame-1');

    await operate('back');await operate('focus0');
    currentFrame=2;await openCurrentCamera();
    reply=frameReply(2);const secondReply=reply,secondFile=await shootCurrentFrame();
    assert.notEqual(secondFile,firstFile,'the second photograph is a newly created File');
    assert.equal(await secondFile.text(),'frame-2','the second upload contains the current camera frame');
    assert.equal(await firstFile.text(),'frame-1','the original File is never overwritten or reused');
    assert.equal(practiceRequests[0].files.length,1);assert.equal(practiceRequests[1].files.length,1);
    assert.equal(practiceRequests[0].files[0],firstFile);assert.equal(practiceRequests[1].files[0],secondFile);
    assert.deepEqual(drawnFrames,[1,2],'the double click does not generate another snapshot');

    const retainedPhoto=node('hw-scene-image').src,body=node('hw-result-body');
    body.scrollHeight=1000;body.clientHeight=200;body.scrollTop=137;body.fire('scroll');
    await operate('back');await operate('focus0');currentFrame=3;await openCurrentCamera();
    delayBlob=true;pendingBlob=undefined;decodeResolve=undefined;
    const canceledBlob=node('hw-camera-shoot').fire('click');
    assert.equal(typeof pendingBlob,'function');
    await operate('back');assert.equal(video.srcObject,null);
    pendingBlob();await canceledBlob;delayBlob=false;
    assert.equal(requests,2,'a snapshot blob arriving after cancellation is never uploaded');
    assert.equal(decodeResolve,undefined,'a canceled blob is not even accepted for image decoding');
    assert.equal(node('hw-scene-image').src,retainedPhoto);
    await operate('focus2');await operate('press');
    assert.equal(renderedReply,secondReply);assert.equal(body.scrollTop,137);
    assert.equal(requests,2,'reopening the previous result after cancel is a local reading action');

    await operate('back');await operate('focus0');currentFrame=4;await openCurrentCamera();
    decodeResolve=undefined;const canceledDecode=node('hw-camera-shoot').fire('click');
    await new Promise(setImmediate);assert.equal(typeof decodeResolve,'function');
    await operate('back');decodeResolve();await canceledDecode;
    assert.equal(requests,2,'an image decode completing after cancellation never starts solving');
    assert.equal(node('hw-scene-image').src,retainedPhoto);
    await operate('focus2');await operate('press');
    assert.equal(renderedReply,secondReply);assert.equal(body.scrollTop,137);
    assert.equal(modelLocks.has('hardware-lens-request'),false);
    console.log('Camera sequence: fresh frames/files, no retained-photo resubmit, double-click guard, late blob/decode cancellation and previous reading retention passed.');
  } finally {window.HardwareLens.stop();}
})();
`;
Promise.resolve(captureVm.runInNewContext(harness+sequence,{
  require,console,Blob,File,URL,setInterval,clearInterval,setTimeout,clearTimeout,queueMicrotask,setImmediate
},{filename:__filename})).catch(error=>{console.error(error);process.exitCode=1;});
