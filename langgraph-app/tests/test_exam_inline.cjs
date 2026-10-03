const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../lg_assistant/web/static/app.js'), 'utf8');

// Exercise the production renderer and polling function without browser or model calls.
const markdown = source.slice(source.indexOf('  function esc(s)'), source.indexOf('  function speechHtml('));
const exam = source.slice(source.indexOf('  function examResultHtml('), source.indexOf('  /** 助手回答：'));
const context = {};
vm.runInNewContext(markdown + exam + '\nthis.renderExam = examResultHtml;', context);
const render = context.renderExam;
const batchReply={exam_backend:'jev', text:'不要显示这份旧正文', artifacts:[{kind:'exam_batch', questions:[
  {id:'one', label:'第 12 题', page:1, status:'answered', answer:'B', explanation:'因为 **包含关系** 成立。'},
  {id:'two', label:'第 13 题', page:1, status:'needs_photo', needed:'右侧 D 选项没有拍完整。'},
  {id:'three', label:'第 14 题', page:2, status:'unresolved', needed:'两种规律均符合现有条件。'},
  {id:'four', label:'第 15 题', page:2, status:'error', needed:'服务暂未返回结果。'},
],notes:['页面底部可能还有题目，请补拍。'],error:'第二张照片部分文字不清晰。'}]};
const html = render(batchReply);
const abilityPractice=render({...batchReply,artifacts:[...batchReply.artifacts,{kind:'practice',agent:'ability',stages:[{id:'answer',label:'答案',text:'概要'}]}]},{questionIndex:1});
assert.match(abilityPractice,/第 13 题/);assert.match(abilityPractice,/右侧 D 选项没有拍完整/,'the ability practice metadata never replaces the complete question and retake UI');
const essayPractice=render({artifacts:[{kind:'practice',agent:'essay',question:'<script>unsafe</script>',decision_model:'user',generation_model:'text-model',stages:[{id:'draft',label:'参考范文',text:'真实生成正文'}]}]});
assert.match(essayPractice,/手动题型/);assert.match(essayPractice,/写作 text-model/);assert.match(essayPractice,/参考范文/);assert.match(essayPractice,/真实生成正文/);assert.ok(!essayPractice.includes('<script>'),'practice questions use the same safe renderer as other AI text');
const providedInterview=render({artifacts:[{kind:'practice',agent:'interview',question_source:'provided',question:'真实原题',decision_model:'previous',generation_model:'',stages:[{id:'question',label:'原题',text:'保留问题'}]}]});
assert.match(providedInterview,/题面原文/);assert.match(providedInterview,/沿用题型/);assert.ok(!providedInterview.includes('点评 '),'an original interview question must not imply text-model generation');
assert.match(html,/识别 4 道题 · 已解 1 道 · 需补拍 1 道 · 待核验 2 道/);
assert.equal((html.match(/<article /g)||[]).length,4,'every detected question has an inline card');
assert.ok(!html.includes('不要显示这份旧正文'));
assert.match(html,/JEV解题/);
for (const card of html.matchAll(/<article[\s\S]*?<\/article>/g)) {
  assert.ok(card[0].indexOf('answer-value') < card[0].indexOf('exam-question-explanation'),'answer is above explanation for every question');
}
assert.match(html,/需补拍/);assert.match(html,/右侧 D 选项没有拍完整/);
assert.match(html,/第 2 张照片/);assert.ok(!html.includes('<dialog'));
assert.match(html,/页面底部可能还有题目/);assert.match(html,/第二张照片部分文字不清晰/);
const selected=render(batchReply,{questionIndex:2});
assert.equal((selected.match(/<article /g)||[]).length,1,'the lens renders only its selected question');
assert.match(selected,/第 14 题/);assert.match(selected,/两种规律均符合现有条件/);
assert.ok(!selected.includes('第 12 题')&&!selected.includes('第 13 题')&&!selected.includes('第 15 题'),'inactive sibling cards are omitted');
assert.match(selected,/识别 4 道题 · 已解 1 道 · 需补拍 1 道 · 待核验 2 道/,'summary still counts the complete batch');
assert.match(selected,/页面底部可能还有题目/);assert.match(selected,/第二张照片部分文字不清晰/);
const selectedFirst=render(batchReply,{questionIndex:0});
assert.equal((selectedFirst.match(/<article /g)||[]).length,1,'zero selects the first question instead of falling back to all');
assert.match(selectedFirst,/第 12 题/);assert.match(selectedFirst,/包含关系/);assert.ok(!selectedFirst.includes('第 14 题'));
const selectedPartial=render(batchReply,{questionIndex:1});
assert.equal((selectedPartial.match(/<article /g)||[]).length,1,'an incomplete question remains individually selectable');
assert.match(selectedPartial,/第 13 题/);assert.match(selectedPartial,/右侧 D 选项没有拍完整/);
const unresolvedReason=render({artifacts:[{kind:'exam_batch',questions:[{status:'unresolved',failure_reason:'reasoning_unresolved',needed:'本次尚未得到通过核验的唯一规律。'}]}]});
assert.match(unresolvedReason,/<strong>未确定原因<\/strong>/);
assert.match(unresolvedReason,/本次尚未得到通过核验的唯一规律/);
assert.ok(!unresolvedReason.includes('补拍提示')&&!unresolvedReason.includes('需要补充或核对'),'reasoning failure is described as an unresolved answer, not a photo-quality finding');
const serviceFailure=render({artifacts:[{kind:'exam_batch',questions:[{status:'error',needed:'解题服务连接中断。'}]}]});
assert.match(serviceFailure,/<strong>处理失败<\/strong>/);assert.match(serviceFailure,/解题服务连接中断/);
assert.ok(!serviceFailure.includes('补拍提示')&&!serviceFailure.includes('需要补充或核对'));
for(const status of ['unresolved','error']) {
  const fallback=render({artifacts:[{kind:'exam_batch',questions:[{status,answer:'',needed:'',explanation:''}]}]});
  assert.ok(!/清晰|模糊|补拍|重新拍|重拍|拍完整/.test(fallback),'an unspecified reasoning or service failure does not blame the photograph');
  assert.match(fallback,status==='error'?/本题处理未完成，请稍后重试/:/本次未得到通过复核的答案/);
}
const photoFailure=render({artifacts:[{kind:'exam_batch',questions:[{status:'needs_photo',failure_reason:'image_incomplete',needed:'右侧 D 选项没有拍完整。'}]}]});
assert.match(photoFailure,/<strong>补拍提示<\/strong>/);assert.match(photoFailure,/右侧 D 选项没有拍完整/);
const photoFallback=render({artifacts:[{kind:'exam_batch',questions:[{status:'needs_photo',needed:''}]}]});
assert.match(photoFallback,/题干、图形和全部选项一起拍完整/,'a real missing-photo result still gives the capture guidance');
const emptyBatch=render({text:'旧正文',artifacts:[{kind:'exam_batch',questions:[],notes:['题面过于模糊。'],error:'识别失败，请补拍。'}]});
assert.match(emptyBatch,/识别失败，请补拍/);assert.match(emptyBatch,/题面过于模糊/);assert.ok(!emptyBatch.includes('旧正文'));
const verified=render({artifacts:[{kind:'exam_batch',questions:[{status:'answered',answer:'A',explanation:'解析正文\n\n**程序计算校验**\n2 + 2 = 4\n\n**核对说明**\n已经检查原图。',review_notes:'已经检查原图。'}]}]});
assert.equal((verified.match(/已经检查原图/g)||[]).length,1,'review notes already in the rich explanation are not duplicated');
assert.match(verified,/程序计算校验/);
const unsafe = render({artifacts:[{kind:'exam_batch',questions:[{label:'<script>alert(1)</script>',status:'answered',answer:'<img src=x onerror=alert(1)>',explanation:'<script>bad()</script>'}]}]});
assert.ok(!unsafe.includes('<script>') && !unsafe.includes('<img '),'question labels, answers and explanations cannot inject markup');
assert.match(unsafe,/&lt;script&gt;/);
const blankAnswer=render({artifacts:[{kind:'exam_batch',questions:[{status:'answered',answer:'',needed:'答案还未核验'}]}]});
assert.match(blankAnswer,/已解 0 道/);assert.match(blankAnswer,/暂无法确定/);
const legacy=render({exam_backend:'original',text:'> 沿用上一轮的图片\n\n**答案：C**\n分类：类比推理\n\n**解析**\n这是同一关系。',artifacts:[{kind:'vision',answerable:true}]});
assert.match(legacy,/<span class="answer-value">C<\/span>/);
assert.equal((legacy.match(/<h4>解析<\/h4>/g)||[]).length,1);
assert.ok(!legacy.includes('<strong>解析</strong>'),'legacy explanation heading is not duplicated');
assert.match(legacy,/沿用上一轮的图片/);
assert.match(render({text:'答案：D\n理由：条件成立。'}),/<span class="answer-value">D<\/span>/);
assert.match(render({text:'**需要补充**\n请补拍图形。',artifacts:[{kind:'vision',answerable:false}]}),/解析与补充提示/);
assert.match(render({text:'模型返回了其他格式。'}),/模型返回了其他格式/);

// Guard the height/overflow chain which keeps a long paper inside the lens.
// The real browser check also compares scrollHeight with clientHeight.
const css = fs.readFileSync(require.resolve('../lg_assistant/web/static/hardware.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g,'');
function cssRules(selector) {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>m[1].trim()===selector).map(m=>Object.fromEntries(m[2].split(';').filter(v=>v.includes(':')).map(v=>{const split=v.indexOf(':');return [v.slice(0,split).trim(),v.slice(split+1).trim()];})));
}
const lensSizes=cssRules('.hw-viewport.is-exam-reading');
assert.equal(lensSizes.length,2,'desktop and mobile both explicitly bound the photographed-result lens');
for(const sizes of lensSizes) {
  assert.match(sizes.height,/^clamp\([\d.]+px,[\d.]+vh,[\d.]+px\)$/,'lens height cannot grow with explanation content');
  assert.match(sizes['max-height'],/^[\d.]+px$/);
  assert.match(sizes['grid-template-rows'],/minmax\(0,1fr\)/,'result row allows its scroll body to shrink');
}
assert.equal(lensSizes[0].flex,'none','the old flexible auto-height rule cannot stretch the result lens');
assert.equal(lensSizes[1].height,'clamp(420px,74vh,560px)','mobile prioritizes room for the answer and initial explanation lines');
assert.equal(lensSizes[1]['max-height'],'560px');
const resultBox=cssRules('.hw-viewport.is-exam-reading #hw-lens-result')[0];
assert.equal(resultBox.position,'static');assert.equal(resultBox['min-height'],'0');assert.equal(resultBox.overflow,'hidden');
const scrollBox=cssRules('.hw-viewport.is-exam-reading #hw-result-body')[0];
assert.equal(scrollBox['min-height'],'0');assert.equal(scrollBox.flex,'1 1 0');assert.equal(scrollBox.overflow,'auto');

const polling = source.slice(source.indexOf('  async function sendAsync(opts)'), source.indexOf('  /* ── Toast'));
async function runPoll(replies) {
  const calls=[];let polls=0,stopped=0;
  const fixture={sleep:async()=>{},form:()=>({}),pollProgress:()=>()=>{stopped++;},
    apiJson:async(path)=>{calls.push(path);if(path==='/api/chat/async')return {task_id:'same-task'};return replies(polls++);}};
  vm.runInNewContext(polling + '\nthis.request = sendAsync({request_id:"progress-id",onProgress:()=>{}});',fixture);
  try { return {result:await fixture.request,calls,polls,stopped}; }
  catch(error) {return {error,calls,polls,stopped};}
}
(async()=>{
  const long=await runPoll(i=>i<1000?{status:i%2?'running':'pending'}:{status:'done',result:{text:'all questions'}});
  assert.deepEqual(long.result,{text:'all questions'});
  assert.equal(long.polls,1001,'a batch remains attached after the old 900-poll limit');
  assert.equal(long.calls.filter(p=>p==='/api/chat/async').length,1,'waiting never creates a duplicate task');
  assert.ok(long.calls.slice(1).every(p=>p==='/api/task?task_id=same-task'));
  assert.equal(long.stopped,1,'progress polling stops after completion');
  const failed=await runPoll(()=>({status:'error',error:'failed-one'}));
  assert.match(failed.error.message,/failed-one/);assert.equal(failed.stopped,1);
  const invalid=await runPoll(()=>({status:'unknown'}));assert.match(invalid.error.message,/任务状态无法确认/);
  console.log('Inline photo results: all questions or selected question, complete-batch counts, answer before explanation, missing-photo warnings, legacy results, safe text and long-running same-task polling passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
