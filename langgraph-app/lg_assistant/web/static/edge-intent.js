/* Entirely local inference and task-scoped context. No network or storage API. */
(() => {
  'use strict';
  const normalize = text => String(text).toLowerCase().replace(/[\s\p{P}]/gu, '');
  const labels = {scene0:'拍照解题',scene1:'会议纪要',scene2:'实时翻译',scene3:'地图',previous:'上一个功能',next:'下一个功能',home:'主菜单'};
  const sceneTerms = [/拍照|拍题|解题|做题|题目识别/,/会议|纪要|录音|开会/,/翻译|译成|译为/,/地图|导航|路线|找路/];
  function predict(text) {
    const model = window.EdgeIntentWeights;
    if (!model) return null;
    const s = '^'+normalize(text)+'$', vector = {};
    let total = 0;
    for (const n of [2,3]) for (let i=0;i<=s.length-n;i++) {
      const term = s.slice(i,i+n);
      if (vector[term]) continue;
      const value = model.idf[term] || 4; // Unknown terms lower similarity.
      vector[term] = value; total += value*value;
    }
    const norm = Math.sqrt(total)||1, scores = {};
    for (const sample of model.samples) {
      let score = 0;
      for (const [term,value] of Object.entries(sample.vector)) score += value*(vector[term]||0)/norm;
      scores[sample.label] = Math.max(scores[sample.label]||0,score);
    }
    const ranked = Object.entries(scores).sort((a,b)=>b[1]-a[1]);
    const [action,score] = ranked[0];
    return action !== 'reject' && score >= model.threshold && score-(ranked[1]?.[1]||0)>=model.margin ? {action,label:labels[action],score,model:model.version} : null;
  }
  const clarify = message => ({route:'clarify',message});
  function decide(text,state,context=[]) {
    const raw=String(text).trim(), s=normalize(raw);
    if (!s || raw.length>600) return clarify('请用 600 字以内描述指令。');
    if (/不要|别再|别打开|不需要|不想|不用|取消/.test(s)) return clarify('未执行。请直接说需要选择的功能。');
    // Questions about capabilities are not requests to execute those capabilities.
    if (/区别|哪个好|是什么|有哪些|有什么用|支持哪些|好用吗|能不能/.test(s)) return clarify('这是功能咨询，尚未执行操作。可直接说“打开地图”等功能指令。');
    const control={上一个:'previous',上一个功能:'previous',下一个:'next',下一个功能:'next',返回主菜单:'home',主菜单:'home'}[s];
    if(control) return {route:'local',action:control,label:labels[control],model:'menu-exact'};
    const dependency = /(?:它|上述|上面|刚才|之前|上一(?:条|句|个|次|步)|这个结果|这份(?:内容|纪要|记录)|同样|接着|继续|再详细|再简短|换成|改成|那(?:个|条|份|再)|为什么|怎么得出|第[一二三四五六七八九十0-9]+个呢|还有呢)/.test(s);
    const scenes=sceneTerms.filter(pattern=>pattern.test(s)).length;
    const tasks=raw.split(/然后|接着|并且|同时|以及|和|并|[，,；;]|再(?=翻译|导航|打开|生成|总结|导出|搜索|查找|查询|拍照|记录)/).filter(Boolean);
    const verb=/翻译|译成|导航|打开|生成|总结|导出|搜索|查找|查询|拍照|记录|解题|发送|保存/;
    const multiple=scenes>1 && /和|与|及|并|再|然后|同时|先|接着|[，,、；;]/.test(raw) || tasks.filter(t=>verb.test(t)).length>1 || /(?:总结|生成纪要|翻译|解题).*(?:导出|发送|保存)/.test(s);
    if (dependency && !context.length && !multiple) return clarify('这条指令需要前文。请先说明要处理的内容，或开始一个新任务。');
    if (multiple) return {route:'cloud',reason:'multiple_tools',label:'多个功能 / 工具',context:dependency?context:[]};
    if (dependency) return {route:'cloud',reason:'context_dependency',label:'需要前文',context};
    const clean=s.replace(/^(?:请)?(?:帮我)?(?:选择|打开|进入|切换到|切换)?/,'');
    if (state.screen==='actions') {
      const index=state.options.findIndex(option=>normalize(option)===clean);
      if(index>=0) return {route:'local',action:`focus${index}`,label:state.options[index],model:'menu-exact'};
    }
    const selected=predict(clean)||predict(raw);
    // Content-bearing tasks must never be reduced to opening a menu.
    if (/翻译.{1,}(?:成|为)|翻译[：:]|导航到|去往|计算|总结[：:]|解答[：:]/.test(raw)) return {route:'cloud',reason:'content_task',label:'内容处理',context:[]};
    if (selected) return {route:'local',...selected};
    return clarify('没有确定你的意思。请说功能名称，或补充要处理的内容。');
  }
  function createSession({now=()=>Date.now(),ttl=300000}={}) {
    let history=[],updated=0;
    const reset=()=>{history=[];updated=0;};
    return {
      reset,
      decide(text,state) {
        if(now()-updated>ttl) reset();
        return decide(text,state,history.map(item=>({...item})));
      },
      remember(text,decision,reply) {
        if(decision.route==='clarify'){reset();return;}
        if(decision.reason!=='context_dependency' && !decision.context?.length) history=[];
        history.push({role:'user',content:text});
        history.push({role:'assistant',content:(reply || `已选择${decision.label}`).slice(0,1200)});
        history=history.slice(-8);updated=now();
      }
    };
  }
  window.EdgeIntent={predict,decide,createSession};
})();
