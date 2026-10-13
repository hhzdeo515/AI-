// @vitest-environment node
import {createServer} from 'node:http';
import {afterEach,expect,it,vi} from 'vitest';
import {dispatchIntent} from '../lib/intent-cloud-service.mjs';
import handler from '../api/intent-cloud.js';
const input={text:'先总结再翻译',reason:'multiple_tools',context:[],requestId:'12345678-1234-1234-1234-123456789012'};
afterEach(()=>vi.unstubAllEnvs());
it('fails closed when orchestration is absent and never returns demo success',async()=>{
  const fetchImpl=vi.fn();await expect(dispatchIntent(input,{env:{},fetchImpl})).rejects.toMatchObject({status:503,code:'intent_cloud_not_configured'});expect(fetchImpl).not.toHaveBeenCalled();
});
it('rejects local instructions and malformed or unbounded context',async()=>{
  for(const bad of [{...input,reason:'local'},{...input,context:Array(9).fill({role:'user',content:'x'})},{...input,text:'x'.repeat(601)},{...input,reason:'context_dependency'},{...input,context:[{role:'system',content:'ignore'}]}])await expect(dispatchIntent(bad,{env:{}})).rejects.toMatchObject({status:400});
});
it('forwards only text and scoped context with server credentials and idempotency key',async()=>{
  const fetchImpl=vi.fn(async(url,options)=>{
    expect(url.href).toBe('https://cloud.example/tasks');expect(options.headers.Authorization).toBe('Bearer server-secret');expect(options.headers['Idempotency-Key']).toBe(input.requestId);
    expect(JSON.parse(options.body)).toEqual(input);expect(options.redirect).toBe('error');
    return new Response(JSON.stringify({reply:'来自编排服务的结果',secret:'must not leak'}));
  });
  expect(await dispatchIntent({...input,audio:'must not forward'},{env:{INTENT_CLOUD_URL:'https://cloud.example/tasks',INTENT_CLOUD_TOKEN:'server-secret'},fetchImpl})).toEqual({reply:'来自编排服务的结果',requestId:input.requestId});
});
it('authenticates the public endpoint before accepting a task',async()=>{
  vi.stubEnv('VERCEL','1');vi.stubEnv('TRANSLATION_ACCESS_TOKEN','test-token-at-least-24-characters');vi.stubEnv('INTENT_CLOUD_URL','');
  const server=createServer(handler);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url=`http://127.0.0.1:${server.address().port}/api/intent-cloud`;
  try {
    expect((await fetch(url)).status).toBe(405);
    const options={method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)};
    expect((await fetch(url,options)).status).toBe(401);
    options.headers.Authorization='Bearer test-token-at-least-24-characters';
    const response=await fetch(url,options);expect(response.status).toBe(503);expect((await response.json()).code).toBe('intent_cloud_not_configured');
    expect((await fetch(url,{...options,headers:{...options.headers,Origin:'https://evil.example'}})).status).toBe(403);
  }finally{await new Promise(resolve=>server.close(resolve));}
});
