import { AccessError, authorizeAccess, mapCookie, originAllowed } from "../lib/server-access.mjs";
import { jsonFailure, mapSettings } from "../lib/amap-service.mjs";

export default async function handler(req,res) {
  res.setHeader("Cache-Control","no-store"); res.setHeader("X-Content-Type-Options","nosniff");
  try {
    if (req.method !== "GET") { res.setHeader("Allow","GET"); throw new AccessError("请使用 GET 请求。",405,"method_not_allowed"); }
    if (!originAllowed(req)) throw new AccessError("请求来源不受支持。",403,"invalid_request_origin");
    const settings=mapSettings();
    authorizeAccess(req);
    const cookie=mapCookie(req); if (cookie) res.setHeader("Set-Cookie",cookie);
    res.setHeader("Content-Type","application/json; charset=utf-8");
    res.statusCode=200; res.end(JSON.stringify({key:settings.key,serviceHost:"/_AMapService"}));
  } catch(error) { jsonFailure(res,error); }
}
