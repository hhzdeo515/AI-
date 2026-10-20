// @vitest-environment node
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {expect,it} from 'vitest';
const sandbox={window:{}};
vm.createContext(sandbox);
for(const name of ['edge-intent-model.js','edge-intent.js']) vm.runInContext(readFileSync(new URL(`../../langgraph-app/lg_assistant/web/static/${name}`,import.meta.url),'utf8'),sandbox);
const api=sandbox.window.EdgeIntent;
const state={screen:'home',options:['拍照解题','会议纪要','实时翻译','地图'],mode:0,focus:0};
it.each([
  ['请帮我打开实时翻译。','scene2'],['帮我切换到地图','scene3'],['我想使用会议纪要功能','scene1'],['我要拍题','scene0'],['我想找路','scene3'],['下一个','next'],['上一个功能','previous'],['返回主菜单','home']
])('recognizes a single standalone instruction locally: %s',(text,action)=>expect(api.decide(text,state)).toMatchObject({route:'local',action}));
it.each(['不要打开地图','地图和翻译哪个好','今天的会议还没结束','未知功能','删除全部文件','翻译不准确'])('clarifies locally without selecting or uploading: %s',text=>expect(api.decide(text,state).route).toBe('clarify'));
it.each([
  ['可以帮我打开地图吗','scene3'],['切到翻译模式','scene2'],['请给我放大镜片','view:detail'],
  ['把眼镜缩小一点','view:zoomOut'],['放大一点','view:zoomIn'],['向右转一点','view:right'],
  ['请往左转一下','view:left'],['往上抬一点','view:up'],['往下转一点','view:down'],
  ['查看整副眼镜','view:whole'],['请恢复默认视角','view:reset'],['帮我返回上一页','back']
])('understands natural device requests without a model service: %s',(text,action)=>expect(api.decide(text,state)).toMatchObject({route:'local',action}));
it.each(['别向右转','不要放大镜片','为什么要放大镜片','放大镜片然后向右转','昨天把眼镜缩小了','把这张照片放大','恢复出厂设置'])('does not turn unrelated phrases into device operations: %s',text=>expect(api.decide(text,state).route).not.toBe('local'));
it.each(['打开地图和翻译','先生成会议纪要然后翻译成英文','先翻译成英文再翻译成法语','搜索餐厅然后导航','总结后导出文件','打开地图，打开翻译','查询天气并搜索餐厅'])('routes a compound request before executing any local part: %s',text=>expect(api.decide(text,state)).toMatchObject({route:'cloud',reason:'multiple_tools'}));
it('does not classify five unrelated turns as a contextual conversation',()=>{
  const session=api.createSession();
  for(const text of ['打开地图','打开翻译','拍照解题','会议纪要','主菜单','打开地图']){
    const decision=session.decide(text,state);expect(decision.route).toBe('local');session.remember(text,decision);
  }
  const followup=session.decide('把它翻译成英文',state);
  expect(followup).toMatchObject({route:'cloud',reason:'context_dependency'});
  expect(followup.context).toHaveLength(2);expect(followup.context[0].content).toBe('打开地图');
});
it('requires antecedents, expires context and supports an explicit new task',()=>{
  let now=100;const session=api.createSession({now:()=>now,ttl:1000});
  expect(session.decide('把它翻译成英文',state).route).toBe('clarify');
  let decision=session.decide('打开地图',state);session.remember('打开地图',decision);
  expect(session.decide('那再详细一点',state).reason).toBe('context_dependency');
  session.reset();expect(session.decide('那再详细一点',state).route).toBe('clarify');
  session.remember('打开地图',decision);now+=1001;
  expect(session.decide('那再详细一点',state).route).toBe('clarify');
});
it('keeps only a coherent task chain and clears unrelated/uncertain history',()=>{
  const session=api.createSession();
  let d=session.decide('先总结再翻译',state);session.remember('先总结再翻译',d,'已生成译文');
  d=session.decide('换成法语',state);expect(d.context).toHaveLength(2);session.remember('换成法语',d,'法语译文');
  expect(session.decide('再简短一点',state).context).toHaveLength(4);
  d=session.decide('今天天气怎么样',state);session.remember('今天天气怎么样',d);
  expect(session.decide('继续',state).route).toBe('clarify');
});
