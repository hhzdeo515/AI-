"""访问口令：给局域网/公网暴露加一道门。

为什么必须做：这个应用**没有任何鉴权**，而它持有你的百炼 API Key，
并且会把上传的图片、题解、会议纪要写进本机磁盘。裸奔暴露到局域网或公网，
等于把 Key 和磁盘内容对所有人开放。

设计取舍（刻意保持简单）：

- **口令不是账号体系**：环境变量 `ACCESS_TOKEN` 设一个值，打开页面输一次，
  之后靠 cookie 记住。没有用户表、没有注册、没有找回密码。
- **未设置口令 = 不启用鉴权**：保持本地单机使用的零摩擦，但启动时会打印醒目警告。
- **API 与页面区别对待**：未通过时 API 返回 401 JSON（而不是 HTML 登录页），
  否则前端拿到的是一堆 HTML，错误信息没法看。
- 口令比较用 `secrets.compare_digest`，避免时序侧信道。
"""

from __future__ import annotations

import hmac
import json
import secrets
from typing import Any

from flask import Request, Response, jsonify, make_response, redirect, request

#: cookie 名。带前缀避免与页面上的其它 cookie 撞名。
COOKIE_NAME = "lg_access"

#: 不需要口令的路径。静态资源与登录页本身必须放行，否则登录页都打不开。
EXEMPT_PATHS = frozenset({"/login", "/health", "/favicon.ico"})
EXEMPT_PREFIXES = ("/static/",)


def is_enabled(token: str) -> bool:
    return bool((token or "").strip())


def _is_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)


def _presented_token(req: Request) -> str:
    """从 cookie 或 Authorization: Bearer 取口令（后者便于脚本/设备端调用）。"""
    cookie = req.cookies.get(COOKIE_NAME, "")
    if cookie:
        return cookie
    auth = req.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def token_ok(req: Request, token: str) -> bool:
    presented = _presented_token(req)
    if not presented:
        return False
    return hmac.compare_digest(presented, token)


def wants_json(req: Request) -> bool:
    """API 请求返回 JSON 401，页面请求跳登录页。

    只看路径前缀，不猜 Accept 头——前端有些请求不带 Accept。
    """
    return req.path.startswith("/api/")


LOGIN_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>需要访问口令</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#F5F7F8; font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif; color:#1F2933; }}
  .card {{ background:#fff; border:1px solid #E3E8EC; border-radius:14px; padding:32px 28px;
           width:min(360px,92vw); box-shadow:0 8px 28px rgba(31,41,51,.08); }}
  h1 {{ font-size:18px; margin:0 0 6px; }}
  p.sub {{ margin:0 0 20px; font-size:13px; color:#6B7A88; }}
  input {{ width:100%; box-sizing:border-box; padding:11px 12px; font-size:15px;
           border:1px solid #D5DDE3; border-radius:9px; outline:none; }}
  input:focus {{ border-color:#3FA7A3; box-shadow:0 0 0 3px rgba(63,167,163,.15); }}
  button {{ width:100%; margin-top:14px; padding:11px; font-size:15px; cursor:pointer;
            background:#3FA7A3; color:#fff; border:0; border-radius:9px; }}
  button:hover {{ background:#369592; }}
  .err {{ margin-top:12px; font-size:13px; color:#C05252; }}
  .hint {{ margin-top:16px; font-size:12px; color:#8A97A3; line-height:1.6; }}
  code {{ background:#F1F4F6; padding:1px 5px; border-radius:4px; }}
</style></head><body>
<form class="card" method="post" action="/login">
  <h1>AI 智能助手</h1>
  <p class="sub">请输入访问口令</p>
  <input type="password" name="token" autofocus autocomplete="current-password"
         placeholder="访问口令" aria-label="访问口令">
  <button type="submit">进入</button>
  {error}
  <div class="hint">口令在服务端的 <code>ACCESS_TOKEN</code> 环境变量里设置。
    忘记时到服务端启动日志里看，或把 <code>.env</code> 里的值改掉。</div>
</form></body></html>
"""


def login_page(error: str = "") -> str:
    err_html = f'<div class="err">{error}</div>' if error else ""
    return LOGIN_PAGE.format(error=err_html)


def _set_cookie(resp: Response, token: str, secure: bool) -> None:
    resp.set_cookie(
        COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="Lax",
        secure=secure,
        max_age=60 * 60 * 24 * 30,  # 30 天，省得每次都要输
        path="/",
    )


def install(app: Any, token: str, *, secure_cookie: bool = False) -> None:
    """把鉴权挂到 Flask 应用上。``token`` 为空则完全不启用（本地单机模式）。"""

    @app.before_request
    def _guard():  # type: ignore[unused-ignore]
        if not is_enabled(token):
            return None
        if _is_exempt(request.path):
            return None
        if token_ok(request, token):
            return None

        if wants_json(request):
            resp = jsonify({"error": "未授权：需要访问口令", "code": "unauthorized"})
            resp.status_code = 401
            return resp
        return redirect("/login")

    @app.route("/login", methods=["GET", "POST"])
    def _login():  # type: ignore[unused-ignore]
        if not is_enabled(token):
            return redirect("/")

        if request.method == "GET":
            # 已登录就别停在登录页
            if token_ok(request, token):
                return redirect("/")
            return login_page()

        submitted = (request.form.get("token") or "").strip()
        if not submitted or not hmac.compare_digest(submitted, token):
            return login_page("口令不正确"), 401

        resp = make_response(redirect("/"))
        _set_cookie(resp, token, secure_cookie)
        return resp

    @app.get("/logout")
    def _logout():  # type: ignore[unused-ignore]
        resp = make_response(redirect("/login"))
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp


def generate_token() -> str:
    """没配口令时生成一个，供启动时打印。"""
    return secrets.token_urlsafe(12)


def describe(token: str) -> str:
    return json.dumps({"auth_enabled": is_enabled(token)})
