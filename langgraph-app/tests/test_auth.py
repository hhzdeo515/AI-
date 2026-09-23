"""访问口令测试：不需要 API Key、不联网、不起端口。

为什么这批测试重要：鉴权是**唯一**防止别人白用你 API Key 与读你磁盘数据的东西。
它一旦失效不会报错，只会静默放行——所以必须有测试盯着。
"""

from __future__ import annotations

import io
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lg_assistant import config, graph, llm, nodes, store  # noqa: E402
from lg_assistant.web import auth  # noqa: E402

TOKEN = "test-token-abcdef123456"


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lgauth-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "app.sqlite3"
    config.CHECKPOINT_DB = tmp / "ck.sqlite3"
    config.EXPORT_DIR = tmp / "exports"
    config.UPLOAD_DIR = tmp / "uploads"
    store._local = threading.local()
    store._initialised = False
    return tmp


_ORIGINAL_TOKEN: str | None = None


def _client(token: str | None = TOKEN):
    """启用了鉴权的客户端。token=None 表示不启用鉴权（本地单机模式）。

    **必须在整个请求期间保持 config.ACCESS_TOKEN**，而不只是在建 app 时——
    `health()` 之类的处理函数是在请求时才读这个配置的（实测踩过：
    只在建 app 时设，请求时已被还原，导致 auth_enabled 判断错误）。
    """
    global _ORIGINAL_TOKEN
    from lg_assistant.web.app import create_app

    _ORIGINAL_TOKEN = config.ACCESS_TOKEN
    config.ACCESS_TOKEN = token or ""
    app = create_app(graph.build_graph(None))
    app.config["TESTING"] = True
    return app.test_client()


def _restore_token() -> None:
    global _ORIGINAL_TOKEN
    if _ORIGINAL_TOKEN is not None:
        config.ACCESS_TOKEN = _ORIGINAL_TOKEN


def _login(c, token: str = TOKEN):
    return c.post("/login", data={"token": token})


# --------------------------------------------------------------------------- #
# 纯函数层
# --------------------------------------------------------------------------- #
def test_is_enabled_only_for_non_empty_token() -> None:
    assert auth.is_enabled(TOKEN) is True
    for empty in ("", "   ", None):
        assert auth.is_enabled(empty) is False, repr(empty)


def test_exempt_paths_are_limited() -> None:
    """放行面必须很小：只有登录页、健康检查与静态资源。"""
    for p in ("/login", "/health", "/favicon.ico", "/static/app.js", "/static/app.css"):
        assert auth._is_exempt(p) is True, p
    for p in ("/", "/api/chat", "/api/export", "/api/resources", "/api/routing-stats"):
        assert auth._is_exempt(p) is False, p


def test_compare_uses_constant_time_and_rejects_prefix() -> None:
    """口令比较不能只看前缀是否匹配。"""
    class FakeReq:
        cookies: dict = {}
        headers: dict = {}
        path = "/"

    r = FakeReq()
    assert auth.token_ok(r, TOKEN) is False
    r.cookies = {auth.COOKIE_NAME: TOKEN}
    assert auth.token_ok(r, TOKEN) is True
    r.cookies = {auth.COOKIE_NAME: TOKEN[:-1]}
    assert auth.token_ok(r, TOKEN) is False
    r.cookies = {auth.COOKIE_NAME: TOKEN + "x"}
    assert auth.token_ok(r, TOKEN) is False


def test_bearer_header_accepted_for_device_clients() -> None:
    """设备端/脚本用 Authorization 头比 cookie 方便。"""

    class FakeReq:
        cookies: dict = {}
        headers = {"Authorization": f"Bearer {TOKEN}"}
        path = "/api/chat"

    assert auth.token_ok(FakeReq(), TOKEN) is True


# --------------------------------------------------------------------------- #
# 鉴权关闭时：保持本地零摩擦
# --------------------------------------------------------------------------- #
def test_no_token_means_open_access() -> None:
    _fresh()
    c = _client(token=None)
    assert c.get("/").status_code == 200
    r = c.post("/api/chat", data={"text": "计算 1+1", "owner": "u"})
    assert r.status_code == 200, r.status_code
    _restore_token()


def test_health_reports_auth_state_without_leaking_token() -> None:
    _fresh()
    for tok, expect in ((None, False), (TOKEN, True)):
        c = _client(token=tok)
        body = c.get("/health").get_json()
        assert body["auth_enabled"] is expect, (tok, body)
        assert TOKEN not in str(body), "健康检查不得泄露口令"
    _restore_token()


# --------------------------------------------------------------------------- #
# 鉴权开启时：门必须是关的
# --------------------------------------------------------------------------- #
def test_page_redirects_to_login_when_unauthenticated() -> None:
    _fresh()
    c = _client()
    r = c.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_api_returns_json_401_not_html_redirect() -> None:
    """API 必须返回 JSON 401——返回 HTML 登录页会让前端错误信息没法看。"""
    _fresh()
    c = _client()
    for path in ("/api/routing-stats", "/api/resources", "/api/export", "/api/state"):
        r = c.get(path)
        assert r.status_code == 401, (path, r.status_code)
        body = r.get_json()
        assert body and body.get("code") == "unauthorized", (path, body)


def test_chat_api_blocked_when_unauthenticated() -> None:
    """最关键的一条：没口令就不能花钱调模型。"""
    _fresh()
    c = _client()
    r = c.post("/api/chat", data={"text": "你好", "owner": "u"})
    assert r.status_code == 401, r.status_code


def test_static_and_login_page_stay_open() -> None:
    """否则登录页自己都打不开（没有 CSS，还会重定向死循环）。"""
    _fresh()
    c = _client()
    assert c.get("/login").status_code == 200
    assert c.get("/health").status_code == 200
    assert c.get("/static/app.css").status_code == 200
    assert c.get("/static/app.js").status_code == 200


# --------------------------------------------------------------------------- #
# 登录流程
# --------------------------------------------------------------------------- #
def test_wrong_token_rejected_with_401() -> None:
    _fresh()
    c = _client()
    for bad in ("wrong", "", "test-token-abcdef12345", "test-token-abcdef1234567"):
        r = _login(c, bad)
        assert r.status_code == 401, (bad, r.status_code)


def test_correct_token_sets_cookie_and_grants_access() -> None:
    _fresh()
    c = _client()
    r = _login(c)
    assert r.status_code == 302
    # cookie 必须设上，且带 httponly
    set_cookie = r.headers.get("Set-Cookie", "")
    assert auth.COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie, set_cookie
    assert "SameSite=Lax" in set_cookie, set_cookie

    # 之后页面与 API 都应放行
    assert c.get("/").status_code == 200
    assert c.get("/api/routing-stats").status_code == 200


def test_logged_in_user_is_not_stuck_on_login_page() -> None:
    _fresh()
    c = _client()
    _login(c)
    r = c.get("/login")
    assert r.status_code == 302
    assert r.headers.get("Location", "").endswith("/")


def test_logout_clears_cookie_and_blocks_again() -> None:
    _fresh()
    c = _client()
    _login(c)
    assert c.get("/").status_code == 200

    c.get("/logout")
    r = c.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_cookie_from_other_token_rejected() -> None:
    """换口令后旧 cookie 必须失效。"""
    _fresh()
    c = _client()
    c.set_cookie(auth.COOKIE_NAME, "some-other-token", domain="localhost")
    assert c.get("/").status_code == 302


# --------------------------------------------------------------------------- #
def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
        finally:
            # 每个用例后都还原：本文件会把 config.ACCESS_TOKEN 改成测试值，
            # 不还原会污染同进程内其它测试文件（实测踩过：test_web 掉到 2/27）。
            _restore_token()
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    raise SystemExit(_run_all())
