// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import configHandler from "../api/map-config.js";
import proxyHandler from "../api/amap-proxy.js";
import { fetchMap } from "../lib/amap-service.mjs";

const access = "test-only-access-token-not-production";
const key = "test-js-map-key", security = "test-private-map-security";
beforeEach(() => {
  vi.stubEnv("VERCEL", "1"); vi.stubEnv("TRANSLATION_ACCESS_TOKEN", access);
  vi.stubEnv("AMAP_JS_API_KEY", key); vi.stubEnv("AMAP_SECURITY_JS_CODE", security);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
function request(url, headers = {}) { return { method: "GET", url, headers: { host: "assistant.example", "x-forwarded-proto": "https", ...headers } }; }
function response() { return { headers: {}, statusCode: 200, setHeader(name,value) { this.headers[name.toLowerCase()] = value; }, end(body) { this.body = body; } }; }
async function invoke(handler, req) { const res = response(); await handler(req,res); return res; }

it("requires configuration and production authentication without exposing either secret", async () => {
  let res = await invoke(configHandler, request("/api/map-config"));
  expect(res.statusCode).toBe(401); expect(JSON.parse(res.body).code).toBe("authentication_required");
  vi.stubEnv("AMAP_SECURITY_JS_CODE", "");
  expect((await invoke(configHandler, request("/api/map-config"))).statusCode).toBe(503);
  res = await invoke(configHandler, request("/api/map-config", {authorization:"Bearer " + access}));
  expect(res.statusCode).toBe(503); expect(JSON.parse(res.body).code).toBe("map_not_configured");
  expect(res.body).not.toContain(key); expect(res.body).not.toContain(security);
  vi.stubEnv("AMAP_SECURITY_JS_CODE", security); vi.stubEnv("TRANSLATION_ACCESS_TOKEN", "");
  expect((await invoke(configHandler, request("/api/map-config"))).statusCode).toBe(503);
});

it("returns only the JS key and grants a short-lived signed HttpOnly map cookie", async () => {
  const res = await invoke(configHandler, request("/api/map-config", {authorization:"Bearer " + access}));
  expect(res.statusCode).toBe(200);
  expect(JSON.parse(res.body)).toEqual({key,serviceHost:"/_AMapService"});
  expect(res.body).not.toContain(security);
  const cookie = res.headers["set-cookie"];
  expect(cookie).toContain("HttpOnly"); expect(cookie).toContain("Secure"); expect(cookie).toContain("SameSite=Strict"); expect(cookie).toContain("Path=/_AMapService"); expect(cookie).toContain("Max-Age=900");
  expect(cookie).not.toContain(access); expect(cookie).not.toContain(security);
  const sdkCookie = cookie.split(";",1)[0];
  vi.stubGlobal("fetch", async url => {
    const target = new URL(url);
    expect(target.origin).toBe("https://restapi.amap.com");
    expect(target.pathname).toBe("/v3/place/text");
    expect(target.searchParams.get("key")).toBe(key);
    expect(target.searchParams.get("jscode")).toBe(security);
    expect(target.searchParams.has("path")).toBe(false);
    return new Response('{"status":"1","pois":[]}',{headers:{"Content-Type":"application/json"}});
  });
  const proxied = await invoke(proxyHandler, request("/api/amap-proxy?path=v3/place/text&keywords=上海&key=forged&jscode=forged",{cookie:sdkCookie}));
  expect(proxied.statusCode).toBe(200); expect(String(proxied.body)).toContain('"status":"1"');
  expect((await invoke(proxyHandler, request("/api/amap-proxy?path=v3/place/text",{cookie:sdkCookie+"tampered"}))).statusCode).toBe(401);
  vi.spyOn(Date,"now").mockReturnValue(Date.now()+901000);
  expect((await invoke(proxyHandler, request("/api/amap-proxy?path=v3/place/text",{cookie:sdkCookie}))).statusCode).toBe(401);
  vi.restoreAllMocks();
});

it("limits upstream paths and JSONP callbacks instead of accepting arbitrary targets", async () => {
  vi.stubGlobal("fetch", () => { throw new Error("Invalid requests must not reach a provider"); });
  for (const path of ["https://evil.example/v3/place/text","../v3/place/text","v3/cloud/search","v3/place/text/extra","v3/place/%2e%2e/text"]) {
    const res = await invoke(proxyHandler, request("/api/amap-proxy?path="+encodeURIComponent(path),{authorization:"Bearer "+access}));
    expect(res.statusCode).toBe(400);
  }
  expect((await invoke(proxyHandler, request("/api/amap-proxy?path=v3/place/text&callback=alert(1)",{authorization:"Bearer "+access}))).statusCode).toBe(400);
});

it("routes documented styles and vector maps to fixed provider hosts and does not follow redirects", async () => {
  for (const [path,origin] of [["v4/map/styles","https://webapi.amap.com"],["v3/vectormap","https://fmap01.amap.com"]]) {
    vi.stubGlobal("fetch", async (url,options) => { expect(new URL(url).origin).toBe(origin); expect(options.redirect).toBe("error"); return new Response("safe map bytes",{headers:{"Content-Type":"application/octet-stream"}}); });
    expect((await invoke(proxyHandler,request("/_AMapService/"+path,{authorization:"Bearer "+access}))).statusCode).toBe(200);
  }
});

it("rejects cross-origin requests, upstream errors, and responses reflecting the private security code", async () => {
  expect((await invoke(configHandler, request("/api/map-config",{authorization:"Bearer "+access,origin:"https://other.example"}))).statusCode).toBe(403);
  for (const upstream of [new Response("not allowed",{status:403}),new Response("<html>error</html>",{headers:{"Content-Type":"text/html"}}),new Response(security,{headers:{"Content-Type":"text/plain"}})]) {
    vi.stubGlobal("fetch", async()=>upstream);
    const res=await invoke(proxyHandler,request("/api/amap-proxy?path=v3/assistant/coordinate/convert&locations=116,39",{authorization:"Bearer "+access}));
    expect(res.statusCode).toBe(502); expect(res.body).not.toContain(security);
  }
});

it("bounds map request duration and response size without returning provider details", async () => {
  const target = new URL("https://restapi.amap.com/v3/place/text");
  await expect(fetchMap(target,{key,security},{fetchImpl:()=>new Promise(()=>{}),timeoutMs:10})).rejects.toMatchObject({status:502,code:"map_upstream_unavailable"});
  const largeResponse = new Response(new Uint8Array(5 * 1024 * 1024 + 1),{headers:{"Content-Type":"application/octet-stream"}});
  await expect(fetchMap(target,{key,security},{fetchImpl:async()=>largeResponse})).rejects.toMatchObject({status:502});
});

it("returns a client error for malformed escaped paths", async () => {
  const res=await invoke(proxyHandler,request("/_AMapService/v3/%ZZ",{authorization:"Bearer "+access}));
  expect(res.statusCode).toBe(400);
  expect(JSON.parse(res.body).code).toBe("invalid_map_request");
});

it("blocks URL-encoded private security code in provider responses", async () => {
  const privateCode="test private+map/code";
  const target=new URL("https://restapi.amap.com/v3/place/text");
  for (const encoded of [encodeURIComponent(privateCode),new URLSearchParams({jscode:privateCode}).toString().slice(7)]) {
    await expect(fetchMap(target,{key,security:privateCode},{fetchImpl:async()=>new Response(encoded,{headers:{"Content-Type":"text/plain"}})})).rejects.toMatchObject({status:502});
  }
});
