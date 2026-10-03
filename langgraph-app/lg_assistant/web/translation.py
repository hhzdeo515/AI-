"""MyMemory translation behind the application's existing access guard."""
from __future__ import annotations

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, request

LANGUAGES = frozenset({"zh-CN", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "hi", "th", "vi", "id"})
PROVIDER_URL = "https://api.mymemory.translated.net/get"
blueprint = Blueprint("translation", __name__)


class TranslationError(Exception):
    def __init__(self, message: str, status: int = 502, code: str = "translation_unavailable"):
        super().__init__(message)
        self.status, self.code = status, code


def _quota_error():
    return TranslationError("翻译服务的免费额度已用完，请稍后再试。", 429, "translation_quota_exceeded")


def translate_text(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or not payload["text"].strip():
        raise TranslationError("请输入要翻译的文字。", 400, "invalid_translation_input")
    text, source, target = payload["text"], payload.get("source"), payload.get("target")
    if not isinstance(source, str) or not isinstance(target, str) or source not in LANGUAGES or target not in LANGUAGES:
        raise TranslationError("请选择支持的源语言和目标语言。", 400, "unsupported_translation_language")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise TranslationError("请输入有效的 UTF-8 文字。", 400, "invalid_translation_input") from None
    if size > 500:
        raise TranslationError("每段文字最多 500 个 UTF-8 字节，请分段翻译。", 400, "translation_text_too_long")
    result = {"translation": text, "source": source, "target": target, "provider": "MyMemory"}
    if source == target:
        return result
    upstream_request = Request(PROVIDER_URL + "?" + urlencode({"q": text, "langpair": source + "|" + target}), headers={"Accept": "application/json"})
    try:
        with urlopen(upstream_request, timeout=10) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise TranslationError("翻译服务返回了无效结果，请重试。")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise TranslationError("翻译服务返回了无效结果，请重试。")
        details = str(body.get("responseDetails", "")).lower()
        if body.get("quotaFinished") is True or str(body.get("responseStatus")) == "429" or "used all available free translations" in details or "daily quota" in details or "daily limit" in details or "quota exceeded" in details:
            raise _quota_error()
        data = body.get("responseData")
        translated = data.get("translatedText") if isinstance(data, dict) else None
        if str(body.get("responseStatus")) != "200" or not isinstance(translated, str) or not translated.strip():
            raise TranslationError("翻译服务未返回有效译文，请重试。")
        result["translation"] = translated
        return result
    except TranslationError:
        raise
    except HTTPError as error:
        if error.code == 429:
            raise _quota_error() from None
        raise TranslationError("翻译服务暂时不可用，请重试。") from None
    except (TimeoutError, socket.timeout):
        raise TranslationError("翻译服务响应超时，请重试。", 502, "translation_timeout") from None
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise TranslationError("翻译服务响应超时，请重试。", 502, "translation_timeout") from None
        raise TranslationError("翻译服务暂时不可用，请重试。") from None
    except (ValueError, OSError):
        raise TranslationError("翻译服务暂时不可用，请重试。") from None


@blueprint.route("/api/translate", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def translate_route():
    try:
        if request.method != "POST":
            raise TranslationError("请使用 POST 请求。", 405, "method_not_allowed")
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise TranslationError("请求来源不受支持。", 403, "invalid_request_origin")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                raise TranslationError("请求来源不受支持。", 403, "invalid_request_origin") from None
            if parsed.scheme != request.scheme or parsed.netloc != request.host or origin != parsed.scheme + "://" + parsed.netloc:
                raise TranslationError("请求来源不受支持。", 403, "invalid_request_origin")
        if request.mimetype != "application/json":
            raise TranslationError("请使用 JSON 请求正文。", 415, "unsupported_media_type")
        if request.content_length and request.content_length > 4096:
            raise TranslationError("请求正文过大。", 400, "invalid_translation_input")
        if len(request.get_data(cache=True)) > 4096:
            raise TranslationError("请求正文过大。", 400, "invalid_translation_input")
        response = jsonify(translate_text(request.get_json(silent=True)))
    except TranslationError as error:
        response = jsonify({"error": str(error), "code": error.code})
        response.status_code = error.status
        if error.status == 405:
            response.headers["Allow"] = "POST"
    response.headers["Cache-Control"] = "no-store"
    return response
