import {AccessError,authorizeAccess,originAllowed} from '../lib/server-access.mjs';
import {IntentCloudError,dispatchIntent} from '../lib/intent-cloud-service.mjs';

async function readBody(req) {
  const limit=16384;
  if(Number(req.headers['content-length'])>limit) throw new IntentCloudError('指令正文过大。',413,'payload_too_large');
  if(req.body!==undefined) {
    const raw=typeof req.body==='string'||Buffer.isBuffer(req.body)?String(req.body):JSON.stringify(req.body);
    if(Buffer.byteLength(raw)>limit) throw new IntentCloudError('指令正文过大。',413,'payload_too_large');
    return JSON.parse(raw);
  }
  const chunks=[];let size=0;
  for await(const chunk of req){const data=Buffer.from(chunk);size+=data.length;if(size>limit)throw new IntentCloudError('指令正文过大。',413,'payload_too_large');chunks.push(data);}
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
export default async function handler(req,res) {
  res.setHeader('Content-Type','application/json; charset=utf-8');res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
  try {
    if(req.method!=='POST'){res.setHeader('Allow','POST');throw new IntentCloudError('请使用 POST 请求。',405,'method_not_allowed');}
    if(!originAllowed(req)) throw new IntentCloudError('请求来源不受支持。',403,'invalid_request_origin');
    authorizeAccess(req);
    if(String(req.headers['content-type']||'').split(';')[0].trim().toLowerCase()!=='application/json')throw new IntentCloudError('请使用 JSON 正文。',415,'unsupported_media_type');
    let payload;
    try{payload=await readBody(req);}catch(error){if(error instanceof IntentCloudError)throw error;throw new IntentCloudError('无效的 JSON 正文。',400,'invalid_input');}
    const result=await dispatchIntent(payload);
    res.statusCode=200;res.end(JSON.stringify(result));
  }catch(error){
    const failure=error instanceof IntentCloudError||error instanceof AccessError?error:new IntentCloudError('云端服务暂时不可用。',502,'cloud_unavailable');
    res.statusCode=failure.status;res.end(JSON.stringify({error:failure.message,code:failure.code}));
  }
}
