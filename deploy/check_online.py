"""Check the actual deployed URL; credentials are read only from the environment."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import os
import sys
import time
from urllib.parse import urljoin, urlparse

import requests


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get("src") if tag == "script" else attrs.get("href") if tag == "link" and attrs.get("rel") == "stylesheet" else ""
        if value:
            self.paths.add(value)


def require(condition, label):
    if not condition:
        raise RuntimeError(label)
    print("PASS " + label)


def check(base, token, *, allow_http=False, wait_idle=0):
    parsed = urlparse(base)
    require(parsed.scheme == "https" or allow_http and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"},
            "HTTPS address (or explicit local test)")
    require(bool(token), "access credential provided")
    base = base.rstrip("/") + "/"
    with requests.Session() as session:
        session.trust_env = False
        def call(method, path, **kwargs):
            url = urljoin(base, path)
            follow = kwargs.pop("allow_redirects", True)
            for _ in range(4):
                if urlparse(url).netloc != parsed.netloc:
                    raise RuntimeError("redirect leaves deployment origin")
                response = session.request(method, url, timeout=20, allow_redirects=False, **kwargs)
                if not follow or method != "GET" or response.status_code not in {301, 302, 303, 307, 308}:
                    return response
                url = urljoin(url, response.headers.get("Location", ""))
            raise RuntimeError("too many redirects")
        health = call("GET", "health")
        require(health.status_code == 200 and health.json().get("ok") and health.json().get("auth_enabled"),
                "health and authentication enabled")
        deadline = time.monotonic() + wait_idle
        while wait_idle and health.json().get("active_tasks", 0):
            if time.monotonic() >= deadline:
                raise RuntimeError("active tasks remain; do not restart")
            time.sleep(3)
            health = call("GET", "health")
            require(health.status_code == 200, "health available while waiting")
        if wait_idle:
            require(health.json().get("active_tasks") == 0, "no active tasks")
        require(call("GET", "api/resources", allow_redirects=False).status_code == 401,
                "anonymous business API rejected")
        require(call("GET", "api/tasks", allow_redirects=False).status_code == 401,
                "anonymous tasks rejected")
        require(call("POST", "api/chat/async", allow_redirects=False).status_code == 401,
                "anonymous task submission rejected")
        login_page = call("GET", "login")
        require(login_page.status_code == 200 and "口令" in login_page.text and 'action="/login"' in login_page.text,
                "login page available")
        login = call("POST", "api/login", json={"token": token})
        require(login.status_code == 200 and login.json().get("authenticated"), "valid login succeeds")
        cookie = login.headers.get("Set-Cookie", "")
        require("HttpOnly" in cookie and "SameSite=Lax" in cookie, "cookie protections enabled")
        if parsed.scheme == "https":
            require("Secure" in cookie, "HTTPS cookie is Secure")
        page = call("GET", "")
        require(page.status_code == 200 and 'id="hardware-demo"' in page.text,
                "original glasses workbench served")
        assets = Assets()
        assets.feed(page.text)
        required_assets = {"/static/hardware.css", "/static/hardware.js", "/static/hardware-engine.js", "/static/smart-ring.js", "/static/live-translation.js", "/static/live-translation.css", "/static/lens-imu.js", "/static/lens-map.js", "/static/lens-map.css"}
        require(required_assets <= {urlparse(path).path for path in assets.paths},
                "glasses and ring static assets referenced")
        for path in sorted(assets.paths):
            if urlparse(urljoin(base, path)).netloc != parsed.netloc:
                raise RuntimeError("unexpected external script or stylesheet")
            asset = call("GET", path)
            require(asset.status_code == 200, "static asset available")
            if urlparse(path).path == "/static/hardware.js":
                require("眼镜镜片视图" in asset.text and "戒指操控窗口" in asset.text,
                        "glasses and ring interface available")
        require(call("GET", "api/resources").status_code == 200, "authenticated business API available")
        require(call("POST", "api/logout").status_code == 200, "logout succeeds")
        require(call("GET", "api/resources").status_code == 401, "logout revokes browser access")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-env", default="ACCESS_TOKEN")
    parser.add_argument("--allow-local-http", action="store_true")
    parser.add_argument("--wait-idle", type=int, default=0, metavar="SECONDS")
    args = parser.parse_args()
    try:
        check(args.url, os.getenv(args.token_env, ""), allow_http=args.allow_local_http, wait_idle=args.wait_idle)
    except (RuntimeError, requests.RequestException, ValueError) as exc:
        # Do not print request headers, response bodies, or credential values.
        print("FAIL " + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        raise SystemExit(1)
