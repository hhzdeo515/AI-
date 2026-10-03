import { AccessError, authorizeAccess, originAllowed } from "../lib/server-access.mjs";
import { fetchMap, jsonFailure, mapSettings, proxyTarget } from "../lib/amap-service.mjs";

export default async function handler(req,res) {
  res.setHeader("Cache-Control","no-store"); res.setHeader("X-Content-Type-Options","nosniff");
  try {
    if (req.method !== "GET") { res.setHeader("Allow","GET"); throw new AccessError("请使用 GET 请求。",405,"method_not_allowed"); }
    if (!originAllowed(req)) throw new AccessError("请求来源不受支持。",403,"invalid_request_origin");
    const settings=mapSettings();
    authorizeAccess(req,{mapCookie:true});
    const target=proxyTarget(req.url,settings), upstream=await fetchMap(target,settings);
    res.statusCode=200; res.setHeader("Content-Type",upstream.type); res.end(upstream.body);
  } catch(error) { jsonFailure(res,error); }
}
