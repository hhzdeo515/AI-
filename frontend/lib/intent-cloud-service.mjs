/** A real authenticated gateway, deliberately excluded from demo transport. */
export class IntentCloudError extends Error {
  constructor(message,status,code){super(message);this.status=status;this.code=code;}
}
function validate(payload) {
  const invalid=()=>{throw new IntentCloudError('无效的任务指令。',400,'invalid_input');};
  if(!payload||typeof payload!=='object'||Array.isArray(payload))invalid();
  if(typeof payload.text!=='string'||!payload.text.trim()||payload.text.length>600)invalid();
  if(!['multiple_tools','context_dependency','content_task'].includes(payload.reason))invalid();
  if(typeof payload.requestId!=='string'||!/^[0-9a-f-]{36}$/i.test(payload.requestId))invalid();
  if(!Array.isArray(payload.context)||payload.context.length>8)invalid();
  const context=payload.context.map(item=>{
    if(!item||!['user','assistant'].includes(item.role)||typeof item.content!=='string'||!item.content.trim()||item.content.length>1200)invalid();
    return {role:item.role,content:item.content};
  });
  if(payload.reason==='context_dependency'&&!context.length)invalid();
  return {text:payload.text,reason:payload.reason,context,requestId:payload.requestId};
}
export async function dispatchIntent(payload,{env=process.env,fetchImpl=globalThis.fetch,timeoutMs=20000}={}) {
  const input=validate(payload);
  let url;
  try{url=new URL(env.INTENT_CLOUD_URL);}catch(_){}
  if(!url||url.protocol!=='https:'||url.username||url.password||!env.INTENT_CLOUD_TOKEN) {
    throw new IntentCloudError('已识别为云端任务，但云端编排服务尚未配置；本次没有执行工具。',503,'intent_cloud_not_configured');
  }
  try {
    const response=await fetchImpl(url,{method:'POST',redirect:'error',signal:AbortSignal.timeout(timeoutMs),headers:{'Content-Type':'application/json',Authorization:`Bearer ${env.INTENT_CLOUD_TOKEN}`,'Idempotency-Key':input.requestId},body:JSON.stringify(input)});
    if(!response.ok)throw new IntentCloudError('云端未返回成功结果；执行状态未确认，请勿重复提交。',502,'cloud_failed');
    // Stream cap prevents an upstream response from consuming unbounded memory.
    const reader=response.body?.getReader();
    if(!reader)throw new IntentCloudError('云端返回格式无效。',502,'invalid_cloud_response');
    let bytes=0;const chunks=[];
    while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>32768){await reader.cancel();throw new IntentCloudError('云端返回内容过大。',502,'invalid_cloud_response');}chunks.push(Buffer.from(value));}
    const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if(typeof body.reply!=='string'||!body.reply.trim()||body.reply.length>8000)throw new IntentCloudError('云端没有返回有效回复。',502,'invalid_cloud_response');
    return {reply:body.reply,requestId:input.requestId};
  }catch(error){
    if(error instanceof IntentCloudError)throw error;
    throw new IntentCloudError('云端请求未完成；执行状态未确认，请勿重复提交。',502,'cloud_unavailable');
  }
}
