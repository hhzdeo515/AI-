// @vitest-environment node
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { translateText } from "../lib/translation-service.mjs";
import handler from "../api/translate.js";

const cases = JSON.parse(readFileSync(new URL("fixtures/translation-cases.json", import.meta.url), "utf8"));
const providerResponse = body => new Response(JSON.stringify(body), { status: 200 });
beforeEach(() => { vi.stubEnv("VERCEL", ""); vi.stubEnv("TRANSLATION_ACCESS_TOKEN", ""); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it("sends multilingual Unicode to the real provider boundary and returns its translation", async () => {
  for (const input of cases.inputs) {
    const fetchImpl = async url => {
      const request = new URL(url);
      expect(request.origin + request.pathname).toBe("https://api.mymemory.translated.net/get");
      expect(request.searchParams.get("q")).toBe(input.text);
      expect(request.searchParams.get("langpair")).toBe(`${input.source}|${input.target}`);
      expect(request.searchParams.has("key")).toBe(false);
      return providerResponse({ responseStatus: 200, quotaFinished: false, responseData: { translatedText: input.translation } });
    };
    expect(await translateText(input, { fetchImpl })).toEqual({ translation: input.translation, source: input.source, target: input.target, provider: "MyMemory" });
  }
});

it("accepts all supported languages and skips the upstream for same-language requests", async () => {
  for (const language of cases.languages) {
    expect(await translateText({ text: "保持原文", source: language, target: language }, { fetchImpl: () => { throw new Error("Same-language must stay local"); } })).toEqual({ translation: "保持原文", source: language, target: language, provider: "MyMemory" });
  }
});

it("rejects invalid data and enforces the UTF-8 byte limit rather than character count", async () => {
  for (const invalid of cases.invalid) await expect(translateText(invalid.payload)).rejects.toMatchObject({ status: invalid.status });
  await expect(translateText({ text: "中".repeat(167), source: "zh-CN", target: "en" })).rejects.toMatchObject({ status: 400 });
  expect((await translateText({ text: "中".repeat(166), source: "zh-CN", target: "zh-CN" })).translation).toHaveLength(166);
});

it("returns provider quotas as 429 and rejects upstream errors without fake success", async () => {
  for (const item of cases.upstreamErrors) await expect(translateText({ text: "Hello", source: "en", target: "ja" }, { fetchImpl: async () => providerResponse(item.body) })).rejects.toMatchObject({ status: item.status });
});

it("bounds translation time and maps connection or invalid JSON failures to 502", async () => {
  const input = { text: "Hello", source: "en", target: "ja" };
  await expect(translateText(input, { fetchImpl: () => new Promise(() => {}), timeoutMs: 10 })).rejects.toMatchObject({ status: 502, code: "translation_timeout" });
  await expect(translateText(input, { fetchImpl: async () => { throw new Error("private upstream URL"); } })).rejects.toMatchObject({ status: 502 });
  await expect(translateText(input, { fetchImpl: async () => new Response("not JSON") })).rejects.toMatchObject({ status: 502 });
});

it("validates HTTP method, JSON and browser origin while allowing same-origin translation", async () => {
  const server = createServer(handler);
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    expect((await fetch(`${origin}/api/translate`)).status).toBe(405);
    expect((await fetch(`${origin}/api/translate`, { method: "POST", body: "Hello" })).status).toBe(415);
    expect((await fetch(`${origin}/api/translate`, { method: "POST", headers: { "Content-Type": "application/json", Origin: "https://unrelated.example" }, body: "{}" })).status).toBe(403);
    expect((await fetch(`${origin}/api/translate`, { method: "POST", headers: { "Content-Type": "application/json", Origin: origin.replace("http:", "https:") }, body: "{}" })).status).toBe(403);
    expect((await fetch(`${origin}/api/translate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{" })).status).toBe(400);
    const response = await fetch(`${origin}/api/translate`, { method: "POST", headers: { "Content-Type": "application/json", Origin: origin }, body: JSON.stringify({ text: "你好", source: "zh-CN", target: "zh-CN" }) });
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toEqual({ translation: "你好", source: "zh-CN", target: "zh-CN", provider: "MyMemory" });
  } finally { await new Promise(resolve => server.close(resolve)); }
});

it("caps already parsed Vercel JSON bodies rather than trusting content-length alone", async () => {
  let output;
  const req = { method: "POST", headers: { "content-type": "application/json", host: "example.test" }, body: { text: "Hello", source: "en", target: "en", padding: "x".repeat(5000) } };
  const res = { setHeader() {}, end(value) { output = JSON.parse(value); } };
  await handler(req, res);
  expect(res.statusCode).toBe(400);
  expect(output).toHaveProperty("error");
});

it("fails closed without a production token and accepts only its exact bearer token", async () => {
  vi.stubEnv("VERCEL", "1");
  const server = createServer(handler);
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const endpoint = `http://127.0.0.1:${server.address().port}/api/translate`;
  const post = authorization => fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json", ...(authorization ? { Authorization: authorization } : {}) }, body: JSON.stringify({ text: "Bonjour", source: "fr", target: "fr" }) });
  try {
    const missingConfiguration = await post();
    expect(missingConfiguration.status).toBe(503);
    expect(await missingConfiguration.json()).toHaveProperty("error");
    const testToken = "test-only-translation-token-not-production";
    vi.stubEnv("TRANSLATION_ACCESS_TOKEN", testToken);
    for (const authorization of [undefined, "Bearer incorrect", "Bearer " + testToken.slice(1), "Basic " + testToken]) {
      const unauthorized = await post(authorization);
      expect(unauthorized.status).toBe(401);
      expect(await unauthorized.json()).toMatchObject({ code: "authentication_required" });
    }
    const authorized = await post("Bearer " + testToken);
    expect(authorized.status).toBe(200);
    expect(await authorized.json()).toEqual({ translation: "Bonjour", source: "fr", target: "fr", provider: "MyMemory" });
  } finally { await new Promise(resolve => server.close(resolve)); }
});

it("protects a configured local token while leaving unconfigured local development usable", async () => {
  const token = "test-only-local-translation-token";
  vi.stubEnv("TRANSLATION_ACCESS_TOKEN", token);
  const req = { method: "POST", headers: { "content-type": "application/json", host: "example.test" }, body: { text: "Hello", source: "en", target: "en" } };
  const res = { setHeader() {}, end() {} };
  await handler(req, res);
  expect(res.statusCode).toBe(401);
  req.headers.authorization = "Bearer " + token;
  await handler(req, res);
  expect(res.statusCode).toBe(200);
  vi.stubEnv("TRANSLATION_ACCESS_TOKEN", "");
  delete req.headers.authorization;
  await handler(req, res);
  expect(res.statusCode).toBe(200);
});
