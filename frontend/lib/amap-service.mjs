import { AccessError } from "./server-access.mjs";

const UPSTREAMS = new Map([
  ["v4/map/styles", "https://webapi.amap.com/v4/map/styles"],
  ["v3/vectormap", "https://fmap01.amap.com/v3/vectormap"],
  ...["v3/place/text", "v3/place/around", "v3/place/detail", "v3/assistant/inputtips", "v3/assistant/coordinate/convert", "v3/geocode/geo", "v3/geocode/regeo", "v3/config/district"].map(path => [path, `https://restapi.amap.com/${path}`]),
]);
export function mapSettings() {
  const key = (process.env.AMAP_JS_API_KEY || "").trim(), security = (process.env.AMAP_SECURITY_JS_CODE || "").trim();
  if (!key || !security) throw new AccessError("高德地图尚未配置，IMU 模拟仍可使用。", 503, "map_not_configured");
  return { key, security };
}
export function proxyTarget(requestUrl, settings) {
  const incoming = new URL(requestUrl, "http://localhost");
  let path;
  try { path = incoming.pathname.startsWith("/_AMapService/") ? decodeURIComponent(incoming.pathname.slice("/_AMapService/".length)) : incoming.searchParams.get("path"); }
  catch { throw new AccessError("不支持该地图服务请求。", 400, "invalid_map_request"); }
  const upstream = UPSTREAMS.get(path);
  if (!upstream || incoming.search.length > 8192) throw new AccessError("不支持该地图服务请求。", 400, "invalid_map_request");
  const target = new URL(upstream);
  for (const [name,value] of incoming.searchParams) {
    if (["path", "key", "jscode"].includes(name)) continue;
    if (["callback", "jsonp"].includes(name) && !/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$/.test(value)) throw new AccessError("地图请求回调无效。", 400, "invalid_map_request");
    target.searchParams.append(name,value);
  }
  target.searchParams.set("key",settings.key); target.searchParams.set("jscode",settings.security);
  return target;
}
export async function fetchMap(target, settings, {fetchImpl=fetch,timeoutMs=12000}={}) {
  const controller = new AbortController(); let timer;
  try {
    const operation = async () => {
    const upstream = await fetchImpl(target, {signal:controller.signal,redirect:"error",headers:{Accept:"application/json, application/javascript, application/octet-stream"}});
    if (!upstream.ok) throw new Error("Upstream unavailable");
    const type = upstream.headers.get("content-type") || "application/octet-stream";
    if (!/^(?:application\/(?:json|javascript|octet-stream|x-protobuf)|text\/(?:plain|javascript))\b/i.test(type)) throw new Error("Invalid upstream type");
    const chunks = []; let size = 0;
    for await (const chunk of upstream.body) {
      size += chunk.length;
      if (size > 5 * 1024 * 1024) { controller.abort(); throw new Error("Upstream too large"); }
      chunks.push(Buffer.from(chunk));
    }
    const body = Buffer.concat(chunks);
    const encodedSecurity = new URLSearchParams({jscode:settings.security}).toString().slice("jscode=".length);
    if ([settings.security,encodeURIComponent(settings.security),encodedSecurity].some(value=>body.includes(Buffer.from(value)))) throw new Error("Unsafe upstream response");
    return { body, type };
    };
    const deadline = new Promise((_,reject) => {
      timer = setTimeout(() => { controller.abort(); reject(new Error("Upstream timeout")); },timeoutMs);
    });
    return await Promise.race([operation(),deadline]);
  } catch { throw new AccessError("高德地图服务暂时不可用，请稍后重试。", 502, "map_upstream_unavailable"); }
  finally { clearTimeout(timer); }
}
export function jsonFailure(res,error) {
  const failure = error instanceof AccessError ? error : new AccessError("高德地图服务暂时不可用，请稍后重试。",502,"map_upstream_unavailable");
  res.setHeader("Content-Type","application/json; charset=utf-8"); res.statusCode=failure.status;
  res.end(JSON.stringify({error:failure.message,code:failure.code}));
}
