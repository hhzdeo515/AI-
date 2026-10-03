const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {HardwareNavigation,HardwareSimulator,HardwareMediaSession}=require('../lg_assistant/web/static/hardware-engine.js');

// Exercise real lens callbacks without browser permissions or an AI request.
const nodes=new Map(),docEvents=new Map();let operate,blockTransport=false,transportResume,captureResolve,decodeResolve,stopped=0,requests=0,cameraAcquisitions=0,reply={text:'answer'},renderedReply,renderedOptions,requestFailure=false;
const renderedCalls=[],practiceRequests=[],modelLocks=new Set();let delayMicrophone=false,microphoneResolve;
const spoken=[];
function node(id){
  if(nodes.has(id))return nodes.get(id);
  const classes=new Set(),listeners=new Map(),attributes=new Map();
  const value={id,hidden:false,disabled:false,value:'',checked:true,textContent:'',lastChild:{textContent:''},dataset:{},scrollTop:0,scrollHeight:200,clientHeight:200,
    classList:{add(...names){names.forEach(name=>classes.add(name));},remove(...names){names.forEach(name=>classes.delete(name));},contains:name=>classes.has(name),toggle(name,force){const enabled=force===undefined?!classes.has(name):!!force;enabled?classes.add(name):classes.delete(name);return enabled;}},
    style:{setProperty(){}},addEventListener(name,fn){listeners.set(name,fn);},fire(name,extra={}){return listeners.get(name)?.({target:value,...extra});},
    setAttribute(name,content){attributes.set(name,String(content));},removeAttribute(name){attributes.delete(name);},getAttribute(name){return attributes.has(name)?attributes.get(name):null;},pause(){},load(){},play:async()=>{},focus(){},scrollIntoView(){},scrollBy(){},
    closest(){return value.hidden?value:null;},getClientRects(){return value.hidden?[]:[{}];},matches(){return false;},
    querySelector(selector){return node(id+selector);},querySelectorAll(selector){
      if(id==='hw-nav-options')return value.buttons||[];
      if(id==='hw-record-panel')return ['hw-record-toggle','hw-record-import','hw-record-use','hw-record-download','hw-record-delete'].map(node);
      return [];
    }};
  Object.defineProperty(value,'innerHTML',{get:()=>value.html||'',set(html){value.html=html;if(id==='hw-nav-options')value.buttons=[...html.matchAll(/data-hw-option="(\d+)"/g)].map(match=>{const b=node('option'+match[1]);b.dataset.hwOption=match[1];return b;});
    if(id==='hw-question-select'){
      value.options=[...html.matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)].map((match,index)=>{
        const option=node(id+'-option-'+index);option.textContent=match[2];
        option.removeAttribute('value');option.removeAttribute('selected');
        for(const attribute of match[1].matchAll(/([\w-]+)(?:="([^"]*)")?/g))option.setAttribute(attribute[1],attribute[2]??'');
        option.value=option.getAttribute('value')??String(index);option.selected=option.getAttribute('selected')!==null;return option;
      });
      value.value=(value.options.find(option=>option.selected)||value.options[0])?.value||'';
    }
  }});
  nodes.set(id,value);return value;
}
const host=node('hardware-demo');
host.querySelector=selector=>node(selector);
host.querySelectorAll=()=>[];
const audioTrack={stop(){stopped++;},addEventListener(){}};
const stream={getTracks:()=>[audioTrack],getAudioTracks:()=>[audioTrack],getVideoTracks:()=>[audioTrack]};
class Recorder{
  static isTypeSupported(){return true;}
  constructor(){this.listeners={};this.state='inactive';}
  addEventListener(type,fn){this.listeners[type]=fn;}
  start(){this.state='recording';}
  stop(){this.state='inactive';queueMicrotask(()=>{this.listeners.dataavailable({data:new Blob(['voice'],{type:'audio/webm'})});this.listeners.stop();});}
}
class ImageProbe{constructor(){this.naturalWidth=1500;this.naturalHeight=1200;}decode(){return new Promise(resolve=>decodeResolve=resolve);}}
const window={HardwareNavigation,HardwareMediaSession,
  HardwareSimulator:class extends HardwareSimulator{constructor(options){super({...options,wait:()=>blockTransport?new Promise(resolve=>transportResume=resolve):Promise.resolve()});}},
  SmartRing:{mount:()=>null,bindInput:(_element,options)=>{operate=options.action;return {cancel(){}};}},
  HardwareAssistant:{run:async options=>{
    practiceRequests.push(options);
    requests++;
    options.onProgress?.({steps:[{id:'recognize',state:'active'}],timings:{total_ms:12000}});
    assert.equal(node('hw-processing').lastChild.textContent,'正在识别照片中的题目（已用 12 秒）');
    options.onProgress?.({steps:[{id:'verify',state:'active'}],timings:{total_ms:125000}});
    assert.equal(node('hw-processing').lastChild.textContent,'正在复核答案（已用 125 秒）');
    options.onProgress?.({steps:[{id:'solve',state:'active'}],batch:{total:3,done:1}});
    assert.equal(node('hw-processing').lastChild.textContent,'正在解题与复核 · 已处理 1 / 3 道题');
    if(requestFailure)throw new Error('本地服务响应超时');
    options.onProgress?.({finished:true,batch:{total:3,done:3}});
    assert.equal(node('hw-processing').lastChild.textContent,'处理完成，正在显示结果 · 已处理 3 / 3 道题');
    return reply;
  },render:text=>text,renderExam:(response,options={})=>{renderedReply=response;renderedOptions=options;renderedCalls.push({response,questionIndex:options.questionIndex});return 'inline batch';},speak:text=>spoken.push(text),stopSpeech(){}},
  AssistantModel:{setBusy(on,reason){on?modelLocks.add(reason):modelLocks.delete(reason);}},MediaRecorder:Recorder,addEventListener(){}};
const document={getElementById:node,readyState:'loading',hidden:false,addEventListener(name,fn){docEvents.set(name,fn);}};
const context={window,document,location:{hash:'#hardware'},navigator:{mediaDevices:{getUserMedia:async constraints=>{
  if(constraints.video){cameraAcquisitions++;return await new Promise(resolve=>captureResolve=resolve);}
  return delayMicrophone?await new Promise(resolve=>microphoneResolve=resolve):stream;
}}},
  MediaRecorder:Recorder,Blob,File,URL,Image:ImageProbe,setInterval,clearInterval,setTimeout,clearTimeout,queueMicrotask,matchMedia:()=>({matches:true}),console};
vm.runInNewContext(fs.readFileSync(require.resolve('../lg_assistant/web/static/hardware.js'),'utf8'),context);

(async()=>{
  await operate('scene1');await operate('press');
  assert.equal(node('hw-record-toggle').textContent,'停止并保存录音');
  blockTransport=true;const navigating=operate('next');assert.ok(transportResume,'ring transport is waiting');
  window.HardwareLens.stop();await new Promise(setImmediate);
  assert.match(node('hw-audio-name').textContent,/^meeting-\d+\.webm$/,'recording stopped during a ring command is still retained');
  assert.equal(node('hw-record-download').hidden,false);
  assert.match(node('hw-record-status').textContent,/本次录音已保留/);
  blockTransport=false;transportResume();await navigating;
  await operate('home');await operate('scene0');
  const cameraOpening=operate('press');await new Promise(setImmediate);assert.ok(captureResolve);
  const before=stopped;window.HardwareLens.stop();captureResolve(stream);await cameraOpening;
  assert.equal(stopped,before+1,'camera permission arriving after stop releases every track');
  assert.equal(node('hw-camera-video').srcObject,null);assert.equal(requests,0);
  const importing=window.HardwareLens.acceptImage(new File(['image'],'question.png',{type:'image/png'}));
  window.HardwareLens.stop();decodeResolve();assert.equal(await importing,false,'a decoding photo cannot commit after leaving the lens');
  assert.equal(requests,0,'late photo cancellation never starts AI processing');
  reply={text:'two-question body',exam_backend:'jev',artifacts:[{kind:'exam_batch',count:2,answered_count:1,needs_photo_count:1,questions:[
    {id:'p1-q1',label:'第1题',page:1,status:'answered',answer:'A',explanation:'first explanation'},
    {id:'p1-q2',label:'第2题',page:1,status:'needs_photo',answer:'',needed:'option D clipped',regions:[{page:2,bbox:[0,0,1000,500]}]}]}]};
  const ready=window.HardwareLens.acceptImage(new File(['new image'],'questions.png',{type:'image/png'}));decodeResolve();assert.equal(await ready,true);
  const originalPhotoUrl=node('hw-scene-image').src;
  await operate('press');
  assert.equal(requests,1);assert.equal(renderedReply,reply,'lens preserves structured multi-question artifacts rather than only text');
  assert.equal(node('hw-result-body').innerHTML,'inline batch');assert.equal(node('hw-lens-result').hidden,false);
  assert.equal(node('hw-hide-result').textContent,'返回操作');
  assert.equal(node('hw-result-title').textContent,'解题结果 · JEV');
  assert.equal(node('hw-question-nav').hidden,false,'multi-question results show navigation');
  assert.equal(renderedOptions.questionIndex,0,'a new photo starts with its first question');
  assert.equal(node('hw-question-select').value,'0');assert.match(node('hw-question-position').textContent,/1\s*\/\s*2/);
  assert.equal(node('hw-question-prev').disabled,true);assert.equal(node('hw-question-next').disabled,false);
  assert.equal(node('hw-question-select').options.length,2,'all detected questions remain selectable');
  assert.match(node('hw-question-select').options[1].textContent,/第2题/,'the partial question is in the question selector');
  assert.equal(node('hw-question-select').options[1].getAttribute('value'),'1');
  assert.equal(node('hw-scene-image').src,originalPhotoUrl,'a question without valid regions shows its original photo');
  node('hw-result-speak').fire('click');assert.match(spoken.at(-1),/第1题.*答案：A.*first explanation/);
  assert.ok(!spoken.at(-1).includes('option D clipped'),'speaking the first question omits its sibling');
  const body=node('hw-result-body');body.scrollHeight=2000;body.clientHeight=200;body.scrollTop=137;
  node('hw-question-next').fire('click');
  assert.equal(renderedOptions.questionIndex,1,'next renders only the second question');
  assert.equal(body.scrollTop,0,'a newly opened sibling question starts at its beginning');
  assert.equal(node('hw-question-select').value,'1');assert.match(node('hw-question-position').textContent,/2\s*\/\s*2/);
  assert.equal(node('hw-question-prev').disabled,false);assert.equal(node('hw-question-next').disabled,true);
  assert.equal(node('hw-scene-image').src,originalPhotoUrl,'coordinates from a different page cannot crop the current camera photo');
  node('hw-result-speak').fire('click');assert.match(spoken.at(-1),/第2题.*option D clipped/);
  assert.ok(!spoken.at(-1).includes('first explanation')&&!spoken.at(-1).includes('答案：A'),'speaking a partial question only gives its own retake hint');
  body.scrollTop=59;node('hw-question-prev').fire('click');
  assert.equal(renderedOptions.questionIndex,0);assert.equal(body.scrollTop,137,'previous restores that question\'s scroll');
  node('hw-question-select').value='1';node('hw-question-select').fire('change');
  assert.equal(renderedOptions.questionIndex,1,'select can open a needs-photo question');
  assert.equal(body.scrollTop,59,'select restores the partial question\'s own scroll');
  assert.equal(renderedReply,reply,'switching questions retains the complete structured reply and batch counts');
  assert.equal(requests,1,'prev, next and select render locally without another AI request');
  body.scrollTop=211;body.fire('scroll');
  await operate('back');await operate('focus2');await operate('press');assert.equal(renderedReply,reply,'last-result reading retains all structured questions');
  assert.equal(renderedOptions.questionIndex,1,'returning to operations and reopening keeps the selected question');
  assert.equal(body.scrollTop,211,'reopening the previous result retains its latest reading position');
  assert.equal(requests,1,'reopening an existing answer does not resubmit the photo');
  await operate('back');await operate('focus0');
  const beforeNextPhotoCamera=cameraAcquisitions,beforeNextPhotoStop=stopped;captureResolve=null;
  const nextPhotoOpening=operate('press');await new Promise(setImmediate);
  assert.equal(cameraAcquisitions,beforeNextPhotoCamera+1,'photo solving after an existing result acquires a new camera session');
  assert.ok(captureResolve,'the next photograph is waiting for new camera permission');
  assert.equal(node('hw-camera-panel').hidden,false,'the camera panel opens for a new photograph');
  assert.equal(requests,1,'opening the next camera never silently resubmits the solved image');
  await operate('back');captureResolve(stream);await nextPhotoOpening;
  assert.equal(stopped,beforeNextPhotoStop+1,'canceling the next camera releases a late permission stream');
  assert.equal(node('hw-camera-video').srcObject,null);
  await operate('focus2');await operate('press');
  assert.equal(renderedReply,reply,'canceling a new camera preserves the last solved photo result');
  assert.equal(renderedOptions.questionIndex,1);assert.equal(body.scrollTop,211);
  assert.equal(requests,1,'the retained result can still be read without another AI request');
  await operate('back');await operate('focus5');requestFailure=true;await operate('press');
  assert.equal(requests,2,'only the explicit current-image operation resubmits the old photo');
  assert.equal(practiceRequests.at(-1).files[0].name,'questions.png');
  assert.equal(cameraAcquisitions,beforeNextPhotoCamera+1,'explicit re-solving uses the retained image without opening a camera');
  assert.equal(node('hw-processing').hidden,true,'a failed request clears the processing overlay');
  assert.equal(node('hw-ring').disabled,false,'a failed request releases ring controls');
  assert.match(node('hw-notice').textContent,/本地服务响应超时/);
  assert.match(node('hw-notice').textContent,/素材已保留/);
  requestFailure=false;
  const beforeRerun=requests;await operate('rerun');
  assert.equal(requests,beforeRerun+1,'an explicit rerun action can retry the retained photo');
  assert.equal(practiceRequests.at(-1).files[0].name,'questions.png');
  assert.equal(renderedReply,reply);
  reply={text:'single-question body',exam_backend:'original',artifacts:[{kind:'exam_batch',questions:[{id:'p1-q1',label:'第1题',page:1,status:'answered',answer:'B',explanation:'new first explanation'}]}]};
  const single=window.HardwareLens.acceptImage(new File(['single photo'],'single.png',{type:'image/png'}));decodeResolve();assert.equal(await single,true);
  const beforeFreshRequest=requests,beforeFreshCamera=cameraAcquisitions;
  await operate('press');
  assert.equal(requests,beforeFreshRequest+1,'a new imported image remains directly solvable after an earlier result');
  assert.equal(cameraAcquisitions,beforeFreshCamera,'a fresh imported image does not unnecessarily open the camera');
  assert.equal(practiceRequests.at(-1).files[0].name,'single.png');
  assert.equal(renderedReply,reply);assert.equal(renderedOptions.questionIndex,0,'a different photo resets the selected question');
  assert.equal(body.scrollTop,0,'a different photo resets reading positions');
  assert.equal(node('hw-question-nav').hidden,true,'a single question needs no prev/select/next navigation');
  const neverAnswered=window.HardwareLens.acceptImage(new File(['unanswered photo'],'first-failure.png',{type:'image/png'}));decodeResolve();assert.equal(await neverAnswered,true);
  const beforeInitialFailure=requests;requestFailure=true;await operate('press');
  assert.equal(requests,beforeInitialFailure+1,'a fresh image is submitted once even if its first request fails');
  assert.equal(practiceRequests.at(-1).files[0].name,'first-failure.png');
  assert.equal(node('hw-processing').hidden,true);assert.equal(node('hw-ring').disabled,false);
  requestFailure=false;
  const beforeUnansweredCamera=cameraAcquisitions,beforeUnansweredStop=stopped;captureResolve=null;
  const unansweredCameraOpening=operate('press');await new Promise(setImmediate);
  assert.equal(cameraAcquisitions,beforeUnansweredCamera+1,'an attempted image opens a new camera even without a successful result');
  assert.ok(captureResolve);assert.equal(node('hw-camera-panel').hidden,false);
  assert.equal(requests,beforeInitialFailure+1,'photo solving cannot silently retry an old failed image');
  await operate('back');captureResolve(stream);await unansweredCameraOpening;
  assert.equal(stopped,beforeUnansweredStop+1,'canceling the camera after a failed solve releases late media');
  await operate('focus5');await operate('press');
  assert.equal(requests,beforeInitialFailure+2,'the explicit current-image operation can retry a never-answered image');
  assert.equal(practiceRequests.at(-1).files[0].name,'first-failure.png');
  assert.equal(renderedReply,reply);
  await operate('back');await operate('focus0');
  reply={text:'no questions',exam_backend:'original',artifacts:[{kind:'exam_batch',questions:[],error:'photo unreadable'}]};
  const empty=window.HardwareLens.acceptImage(new File(['empty photo'],'empty.png',{type:'image/png'}));decodeResolve();assert.equal(await empty,true);
  await operate('press');assert.equal(renderedReply,reply);assert.equal(node('hw-question-nav').hidden,true,'an empty inventory hides navigation');
  assert.ok(renderedCalls.filter(call=>call.response===reply).length,'the empty batch notice is still rendered');
  const initialPhotoRequest=practiceRequests.find(request=>request.files.some(file=>file.name==='questions.png'));
  assert.deepEqual(JSON.parse(JSON.stringify(initialPhotoRequest.event.practice)),{agent:'auto',action:'run'},'a fresh photograph classifies before choosing its practice branch');
  const photoSession=initialPhotoRequest.session_id;
  const clickChoice=(id,key,value)=>node(id).fire('click',{target:{dataset:{[key]:value},closest(){return this;}}});
  const essay={kind:'practice',agent:'essay',question:'提升基层公共服务',decision_model:'user',generation_model:'text-model',stages:[{id:'analyze',label:'审题',text:'审题正文'},{id:'outline',label:'框架',text:'框架正文'},{id:'draft',label:'参考范文',text:'AI参考范文'}],next_actions:[{id:'critique',label:'提交草稿批改'},{id:'outline',label:'调整写作框架'}]};
  window.HardwareLens.openSettings();clickChoice('hw-practice-modes','practiceAgent','essay');node('hw-practice-topic').value='提升基层公共服务';
  reply={text:'策论完整结果',artifacts:[essay]};await node('hw-practice-start-text').fire('click');
  const essayStart=practiceRequests.at(-1);assert.equal(essayStart.session_id,photoSession);assert.equal(essayStart.files.length,0,'text topics do not attach a prior photograph');assert.equal(essayStart.event.practice.topic,'提升基层公共服务');
  assert.equal(node('hw-practice-panel').hidden,false);assert.equal(node('hw-practice-draft').value,'','AI reference writing must never become the user draft');assert.match(node('hw-practice-source').textContent,/手动题型.*写作 text-model/);
  const beforeEmptyDraft=practiceRequests.length;await clickChoice('hw-practice-actions','practiceAction','critique');assert.equal(practiceRequests.length,beforeEmptyDraft,'empty drafts are not submitted');
  node('hw-practice-draft').value='这是我的真实草稿';requestFailure=true;await clickChoice('hw-practice-actions','practiceAction','critique');assert.equal(node('hw-practice-draft').value,'这是我的真实草稿','a failed critique retains user writing');assert.equal(node('hw-ring').disabled,false);assert.equal(modelLocks.has('hardware-lens-request'),false);
  requestFailure=false;reply={text:'批改结果',artifacts:[{...essay,stages:[...essay.stages,{id:'critique',label:'批改',text:'真实批改内容'}]}]};await clickChoice('hw-practice-actions','practiceAction','critique');
  const critique=practiceRequests.at(-1);assert.equal(critique.session_id,photoSession);assert.equal(critique.files.length,0);assert.equal(critique.event.practice.draft,'这是我的真实草稿');assert.match(node('hw-practice-stage-body').innerHTML,/真实批改内容/);
  await operate('back');assert.equal(node('hw-lens-result').hidden,false,'long press returns practice to the full result first');await operate('back');assert.equal(node('hw-nav-menu').hidden,false);
  const interview={kind:'practice',agent:'interview',question:'同事误解你时如何沟通？',decision_model:'user',generation_model:'text-model',stages:[{id:'question',label:'面试题目',text:'请给出你的真实回答'}],next_actions:[{id:'answer',label:'提交我的作答'}]};
  window.HardwareLens.openSettings();clickChoice('hw-practice-modes','practiceAgent','interview');node('hw-practice-topic').value='沟通协调';reply={text:'面试题目',artifacts:[interview]};await node('hw-practice-start-text').fire('click');
  assert.equal(practiceRequests.at(-1).event.practice.action,'start');assert.equal(practiceRequests.at(-1).files.length,0);assert.equal(node('hw-practice-answer-wrap').hidden,false);
  node('hw-practice-answer').value='我会先了解分歧，再沟通事实。';reply={text:'服务故障',status:'error',artifacts:[{kind:'practice_route',agent:'unknown'}]};await clickChoice('hw-practice-actions','practiceAction','answer');
  assert.equal(node('hw-practice-answer').value,'我会先了解分歧，再沟通事实。','ASR or service errors keep the current user answer and result');assert.match(node('hw-practice-actions').innerHTML,/提交我的作答/);assert.equal(modelLocks.has('hardware-lens-request'),false);
  reply={text:'点评与追问',artifacts:[{...interview,decision_model:'previous',stages:[{id:'feedback',label:'点评',text:'先澄清事实，再给出行动'},{id:'follow_up',label:'追问',text:'对方仍然不接受怎么办？'}],next_actions:[{id:'answer',label:'回答追问'},{id:'follow_up',label:'继续追问'}]}]};
  await clickChoice('hw-practice-actions','practiceAction','answer');assert.equal(practiceRequests.at(-1).event.practice.answer,'我会先了解分歧，再沟通事实。');assert.equal(practiceRequests.at(-1).session_id,photoSession);assert.equal(node('hw-practice-answer').value,'','the next round must not accidentally resubmit an older answer');
  const beforeEmptyAnswer=practiceRequests.length;await clickChoice('hw-practice-actions','practiceAction','answer');assert.equal(practiceRequests.length,beforeEmptyAnswer);
  await node('hw-practice-record').fire('click');assert.equal(modelLocks.has('hardware-practice-recording'),true);assert.equal(node('hw-practice-answer').disabled,true);
  node('hw-practice-record').fire('click');await new Promise(setImmediate);assert.equal(modelLocks.has('hardware-practice-recording'),false);assert.equal(node('hw-practice-audio').hidden,false);
  reply={text:'语音点评',artifacts:[{...interview,transcript:'真实语音转写',stages:[{id:'feedback',label:'点评',text:'录音点评'}],next_actions:[{id:'answer',label:'回答追问'}]}]};await clickChoice('hw-practice-actions','practiceAction','answer');
  const recordedAnswer=practiceRequests.at(-1);assert.equal(recordedAnswer.scene,'exam');assert.equal(recordedAnswer.session_id,photoSession);assert.match(recordedAnswer.files[0].name,/^interview-answer-/);assert.equal(recordedAnswer.event.practice.action,'answer');assert.equal(recordedAnswer.event.practice.answer,undefined);assert.equal(node('hw-practice-transcript').textContent,'真实语音转写');assert.equal(node('hw-practice-audio').hidden,true);
  delayMicrophone=true;const opening= node('hw-practice-record').fire('click');await new Promise(setImmediate);const microphoneBefore=stopped;window.HardwareLens.stop();microphoneResolve(stream);await opening;delayMicrophone=false;
  assert.equal(stopped,microphoneBefore+1,'late interview microphone permission releases every track after leaving');assert.equal(modelLocks.has('hardware-practice-recording'),false);
  node('hw-practice-answer').value='旧的回答';node('hw-practice-draft').value='旧的草稿';const newPhoto=window.HardwareLens.acceptImage(new File(['fresh'],'new-practice.png',{type:'image/png'}));decodeResolve();assert.equal(await newPhoto,true);
  reply={text:'新的题面',artifacts:[{...essay,decision_model:'rule'}]};await operate('press');const fresh=practiceRequests.at(-1);assert.equal(fresh.files[0].name,'new-practice.png');assert.equal(fresh.event.practice.answer,undefined);assert.equal(fresh.event.practice.draft,undefined);assert.equal(node('hw-practice-answer').value,'');assert.equal(node('hw-practice-draft').value,'');
  console.log('Practice workflows: fixed photo session, fresh-image isolation, true drafts and answers, failure retention, staged results, interview recording, ASR source and late microphone cleanup passed.');
  console.log('Lens lifecycle and per-question reading: camera/recording cleanup, cancellation, next/prev/select, partial questions, independent scroll positions, reopen persistence and new-photo reset passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
