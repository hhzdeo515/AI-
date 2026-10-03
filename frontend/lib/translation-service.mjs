/** Real, bounded translation requests. No model credentials or browser sample data. */
const LANGUAGES = new Set(["zh-CN", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "hi", "th", "vi", "id"]);
const PROVIDER_URL = "https://api.mymemory.translated.net/get";

export class TranslationError extends Error {
  constructor(message, status = 502, code = "translation_unavailable") {
    super(message); this.status = status; this.code = code;
  }
}

function validate(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload) || typeof payload.text !== "string" || !payload.text.trim()) {
    throw new TranslationError("请输入要翻译的文字。", 400, "invalid_translation_input");
  }
  if (!LANGUAGES.has(payload.source) || !LANGUAGES.has(payload.target)) {
    throw new TranslationError("请选择支持的源语言和目标语言。", 400, "unsupported_translation_language");
  }
  if (Buffer.byteLength(payload.text, "utf8") > 500) {
    throw new TranslationError("每段文字最多 500 个 UTF-8 字节，请分段翻译。", 400, "translation_text_too_long");
  }
  return { text: payload.text, source: payload.source, target: payload.target };
}

function quotaError() {
  return new TranslationError("翻译服务的免费额度已用完，请稍后再试。", 429, "translation_quota_exceeded");
}

export async function translateText(payload, { fetchImpl = globalThis.fetch, timeoutMs = 10000 } = {}) {
  const { text, source, target } = validate(payload);
  const result = translation => ({ translation, source, target, provider: "MyMemory" });
  if (source === target) return result(text);
  const url = new URL(PROVIDER_URL);
  url.searchParams.set("q", text); url.searchParams.set("langpair", `${source}|${target}`);
  const controller = new AbortController();
  let timer;
  try {
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new TranslationError("翻译服务响应超时，请重试。", 502, "translation_timeout"));
      }, timeoutMs);
    });
    const request = (async () => {
      const response = await fetchImpl(url, { signal: controller.signal, headers: { Accept: "application/json" }, redirect: "error" });
      if (response.status === 429) throw quotaError();
      if (!response.ok) throw new TranslationError("翻译服务暂时不可用，请重试。");
      const body = await response.json();
      if (!body || typeof body !== "object") throw new TranslationError("翻译服务返回了无效结果，请重试。");
      if (body.quotaFinished === true || Number(body.responseStatus) === 429 || /used all available free translations|daily (?:quota|limit)|quota exceeded/i.test(String(body.responseDetails || ""))) throw quotaError();
      const translation = body.responseData?.translatedText;
      if (Number(body.responseStatus) !== 200 || typeof translation !== "string" || !translation.trim()) throw new TranslationError("翻译服务未返回有效译文，请重试。");
      return result(translation);
    })();
    return await Promise.race([request, timeout]);
  } catch (error) {
    if (error instanceof TranslationError) throw error;
    if (error?.name === "AbortError" || error?.name === "TimeoutError") throw new TranslationError("翻译服务响应超时，请重试。", 502, "translation_timeout");
    throw new TranslationError("翻译服务暂时不可用，请重试。");
  } finally { clearTimeout(timer); }
}
