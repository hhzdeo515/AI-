import { TranslationError, translateText } from "../lib/translation-service.mjs";
import { AccessError, authorizeAccess, originAllowed } from "../lib/server-access.mjs";

async function jsonBody(req) {
  if (Number(req.headers["content-length"]) > 4096) throw new TranslationError("请求正文过大。", 400, "invalid_translation_input");
  if (req.body !== undefined) {
    if (Buffer.byteLength(typeof req.body === "string" || Buffer.isBuffer(req.body) ? String(req.body) : JSON.stringify(req.body), "utf8") > 4096) throw new TranslationError("请求正文过大。", 400, "invalid_translation_input");
    return typeof req.body === "string" || Buffer.isBuffer(req.body) ? JSON.parse(String(req.body)) : req.body;
  }
  const chunks = []; let bytes = 0;
  for await (const chunk of req) {
    const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += data.length;
    if (bytes > 4096) throw new TranslationError("请求正文过大。", 400, "invalid_translation_input");
    chunks.push(data);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

/** Vercel discovers this root /api handler alongside the static `out` export. */
export default async function handler(req, res) {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  try {
    if (req.method !== "POST") { res.setHeader("Allow", "POST"); throw new TranslationError("请使用 POST 请求。", 405, "method_not_allowed"); }
    if (!originAllowed(req)) throw new TranslationError("请求来源不受支持。", 403, "invalid_request_origin");
    authorizeAccess(req);
    if (String(req.headers["content-type"] || "").split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new TranslationError("请使用 JSON 请求正文。", 415, "unsupported_media_type");
    let payload;
    try { payload = await jsonBody(req); }
    catch (error) {
      if (error instanceof TranslationError) throw error;
      throw new TranslationError("请求正文不是有效 JSON。", 400, "invalid_translation_input");
    }
    const result = await translateText(payload);
    res.statusCode = 200; res.end(JSON.stringify(result));
  } catch (error) {
    const failure = error instanceof TranslationError || error instanceof AccessError ? error : new TranslationError("翻译服务暂时不可用，请重试。");
    res.statusCode = failure.status; res.end(JSON.stringify({ error: failure.message, code: failure.code }));
  }
}
