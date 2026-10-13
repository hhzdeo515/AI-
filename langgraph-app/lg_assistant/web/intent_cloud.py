"""Task gateway for the full application; auth is enforced by app.before_request."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from flask import Blueprint, jsonify, request

blueprint = Blueprint("intent_cloud", __name__)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dispatch(payload):
    invalid = ({"error": "无效的任务指令。", "code": "invalid_input"}, 400)
    if not isinstance(payload, dict):
        return invalid
    text, reason, context, request_id = (payload.get(k) for k in ("text", "reason", "context", "requestId"))
    if not isinstance(text, str) or not text.strip() or len(text) > 600:
        return invalid
    if reason not in ("multiple_tools", "context_dependency", "content_task") or not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", request_id):
        return invalid
    if not isinstance(context, list) or len(context) > 8 or (reason == "context_dependency" and not context):
        return invalid
    for item in context:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant") or not isinstance(item.get("content"), str) or not item["content"].strip() or len(item["content"]) > 1200:
            return invalid
    url, token = os.environ.get("INTENT_CLOUD_URL", ""), os.environ.get("INTENT_CLOUD_TOKEN", "")
    try:
        parsed = urlsplit(url)
        configured = parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and token
    except ValueError:
        configured = False
    if not configured:
        return {"error": "已识别为云端任务，但云端编排服务尚未配置；本次没有执行工具。", "code": "intent_cloud_not_configured"}, 503
    data = {"text": text, "reason": reason, "context": [{"role": i["role"], "content": i["content"]} for i in context], "requestId": request_id}
    try:
        outgoing = Request(url, data=json.dumps(data, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": "Bearer " + token, "Idempotency-Key": request_id}, method="POST")
        with build_opener(NoRedirect).open(outgoing, timeout=20) as response:
            raw = response.read(32769)
        if len(raw) > 32768:
            raise ValueError("response too large")
        body = json.loads(raw)
        if not isinstance(body, dict) or not isinstance(body.get("reply"), str) or not body["reply"].strip() or len(body["reply"]) > 8000:
            raise ValueError("invalid response")
        return {"reply": body["reply"], "requestId": request_id}, 200
    except Exception:
        return {"error": "云端请求未完成；执行状态未确认，请勿重复提交。", "code": "cloud_unavailable"}, 502


@blueprint.route("/api/intent-cloud", methods=["POST"])
def route():
    origin = request.headers.get("Origin")
    if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and origin != request.host_url.rstrip("/")):
        body, status = {"error": "请求来源不受支持。", "code": "invalid_request_origin"}, 403
    elif request.mimetype != "application/json":
        body, status = {"error": "请使用 JSON 正文。", "code": "unsupported_media_type"}, 415
    elif (request.content_length or 0) > 16384 or len(request.get_data(cache=True)) > 16384:
        body, status = {"error": "指令正文过大。", "code": "payload_too_large"}, 413
    else:
        body, status = dispatch(request.get_json(silent=True))
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response
