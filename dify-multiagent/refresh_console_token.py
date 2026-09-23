"""Refresh Dify console credentials into the api container.

The console access token expires after 60 minutes. This script exchanges the
account refresh token held in Redis for a fresh access token, writes the rotated
refresh token back to Redis (so the browser session in use stays valid), and
copies the token files into the api container for `deploy.ps1`.

Run from the repo root; requires `docker` on PATH.

Usage: python refresh_console_token.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# 本机环境相关的值一律从环境变量读，**不硬编码**。
#
# 这里的账号 ID 与 Redis 密码原本是写死的常量，上线前扫描时发现 Redis 密码
# 会被提交进公开仓库——本机部署的凭据不该出现在版本库里。
# 需要时在 .env 或 shell 里设置：
#   DIFY_ACCOUNT_ID        控制台账号 ID（见 dify 的 accounts 表）
#   DIFY_REDIS_PASSWORD    docker-redis-1 的 requirepass
#   DIFY_REDIS_CONTAINER   默认 docker-redis-1
#   DIFY_API_CONTAINER     默认 docker-api-1
# --------------------------------------------------------------------------- #
ACCOUNT_ID = os.environ.get("DIFY_ACCOUNT_ID", "").strip()
REDIS_PW = os.environ.get("DIFY_REDIS_PASSWORD", "").strip()
REDIS = os.environ.get("DIFY_REDIS_CONTAINER", "docker-redis-1").strip()
API = os.environ.get("DIFY_API_CONTAINER", "docker-api-1").strip()
TOK_DIR = "/tmp/tok"
HERE = Path(__file__).resolve().parent

if not ACCOUNT_ID or not REDIS_PW:
    print(
        "[配置缺失] 需要设置环境变量 DIFY_ACCOUNT_ID 与 DIFY_REDIS_PASSWORD。\n"
        "  账号 ID：docker exec docker-db_postgres-1 psql -U postgres -d dify"
        " -t -A -c \"select id,email from accounts;\"\n"
        "  Redis 密码：见 F:\\dify\\docker\\.env 的 REDIS_PASSWORD",
        file=sys.stderr,
    )
    raise SystemExit(2)

ACCOUNT_REFRESH_KEY = f"account_refresh_token:{ACCOUNT_ID}"

EXCHANGE = '''
import json, os, urllib.error, urllib.request
req = urllib.request.Request(
    "http://localhost:5001/console/api/refresh-token",
    method="POST",
    headers={"Cookie": "refresh_token=" + os.environ["RT"], "Content-Type": "application/json"},
    data=b"{}",
)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        cookies = r.headers.get_all("Set-Cookie") or []
except urllib.error.HTTPError as e:
    print(json.dumps({"ok": False, "status": e.code, "body": e.read().decode("utf-8", "replace")}))
    raise SystemExit(0)
out = {"ok": True}
for sc in cookies:
    head = sc.split(";", 1)[0]
    if "=" not in head:
        continue
    k, v = head.split("=", 1)
    if k.strip() in ("access_token", "refresh_token", "csrf_token"):
        out[k.strip()] = v
print(json.dumps(out))
'''


def sh(*args: str, check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        print(f"[fail] {' '.join(args)}\n{r.stdout}\n{r.stderr}", file=sys.stderr)
        raise SystemExit(1)
    return (r.stdout or "").strip()


def main() -> None:
    old = sh("docker", "exec", REDIS, "redis-cli", "-a", REDIS_PW, "--no-auth-warning", "get", ACCOUNT_REFRESH_KEY)
    old = old.splitlines()[-1].strip()
    if not old:
        print("[fail] no refresh token in Redis; log into Dify in a browser first", file=sys.stderr)
        raise SystemExit(1)

    # Feed the exchange script through stdin, bypassing the arg-length limit.
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", f"RT={old}", API, "python", "-"],
        input=EXCHANGE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    line = [ln for ln in (r.stdout or "").splitlines() if ln.strip().startswith("{")]
    if not line:
        print(f"[fail] no JSON from exchange\n{r.stdout}\n{r.stderr}", file=sys.stderr)
        raise SystemExit(1)

    data = json.loads(line[-1])
    if not data.get("ok"):
        print(f"[fail] refresh rejected: {data}", file=sys.stderr)
        raise SystemExit(1)

    tok_dir = HERE / ".build"
    tok_dir.mkdir(exist_ok=True)
    (tok_dir / "dify_access.txt").write_text(data["access_token"], encoding="ascii")
    (tok_dir / "dify_csrf.txt").write_text(data["csrf_token"], encoding="ascii")

    # Keep the browser session alive: restore the rotated mapping and leave the
    # previous token pointing at the same account.
    sh("docker", "exec", REDIS, "redis-cli", "-a", REDIS_PW, "--no-auth-warning", "set", ACCOUNT_REFRESH_KEY, data["refresh_token"])
    sh("docker", "exec", REDIS, "redis-cli", "-a", REDIS_PW, "--no-auth-warning", "set", f"refresh_token:{old}", ACCOUNT_ID, "EX", "2592000")

    sh("docker", "exec", API, "mkdir", "-p", TOK_DIR)
    sh("docker", "cp", str(HERE / "dify_client.py"), f"{API}:{TOK_DIR}/dify_client.py")
    for name in ("dify_access.txt", "dify_csrf.txt"):
        sh("docker", "cp", str(tok_dir / name), f"{API}:{TOK_DIR}/{name}")

    print(f"ok: access token refreshed ({len(data['access_token'])} chars), browser session preserved")
    print(f"    token files copied to {API}:{TOK_DIR}")


if __name__ == "__main__":
    main()
