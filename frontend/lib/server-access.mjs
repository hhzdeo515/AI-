import { createHash, createHmac, timingSafeEqual } from "node:crypto";

export class AccessError extends Error {
  constructor(message, status, code) { super(message); this.status = status; this.code = code; }
}
function token() {
  const value = (process.env.TRANSLATION_ACCESS_TOKEN || "").trim();
  if (process.env.VERCEL && value.length < 24) throw new AccessError("服务尚未配置访问口令。", 503, "authentication_not_configured");
  return value;
}
const digest = value => createHash("sha256").update(value, "utf8").digest();
const signature = (expiry, secret) => createHmac("sha256", secret).update(expiry, "utf8").digest("base64url");
function validMapCookie(req, secret) {
  const value = String(req.headers.cookie || "").split(";").map(item => item.trim()).find(item => item.startsWith("amap_access="))?.slice("amap_access=".length);
  const match = value?.match(/^(\d{10})\.([A-Za-z0-9_-]{43})$/);
  if (!match) return false;
  const now = Math.floor(Date.now() / 1000), expires = Number(match[1]);
  return expires > now && expires <= now + 900 && timingSafeEqual(digest(match[2]), digest(signature(match[1], secret)));
}
export function authorizeAccess(req, { mapCookie = false } = {}) {
  const expected = token();
  if (!expected) return;
  const submitted = String(req.headers.authorization || "").match(/^Bearer\s+(.+)$/i)?.[1]?.trim() || "";
  if (submitted && timingSafeEqual(digest(submitted), digest(expected))) return;
  if (mapCookie && validMapCookie(req, expected)) return;
  throw new AccessError("请输入访问口令。", 401, "authentication_required");
}
export function requestScheme(req) {
  const forwarded = req.headers["x-forwarded-proto"];
  return forwarded === "http" || forwarded === "https" ? `${forwarded}:` : req.socket?.encrypted || process.env.VERCEL ? "https:" : "http:";
}
export function originAllowed(req) {
  if (req.headers["sec-fetch-site"] === "cross-site") return false;
  const origin = req.headers.origin;
  if (!origin) return true;
  try { const parsed = new URL(origin); return parsed.protocol === requestScheme(req) && parsed.origin === origin && parsed.host === req.headers.host; }
  catch { return false; }
}
export function mapCookie(req) {
  const secret = token();
  if (!secret) return null;
  const expiry = String(Math.floor(Date.now() / 1000) + 900);
  return `amap_access=${expiry}.${signature(expiry, secret)}; Max-Age=900; Path=/_AMapService; HttpOnly; SameSite=Strict${requestScheme(req) === "https:" ? "; Secure" : ""}`;
}
