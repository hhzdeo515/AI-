"""Minimal Dify console API client (ASCII only).

Usage:
  python dify_client.py <METHOD> <PATH> [body.json|-] [-o outfile] [--raw]

--raw writes the decoded payload body verbatim (for DSL YAML export).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DIFY_BASE", "http://localhost:5001")
TOK_DIR = os.environ.get("DIFY_TOK_DIR", "/tmp/tok")


def _read(name: str) -> str:
    with open(os.path.join(TOK_DIR, name), encoding="ascii") as f:
        return f.read().strip()


def call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    access = _read("dify_access.txt")
    csrf = _read("dify_csrf.txt")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": "Bearer " + access,
        "X-CSRF-Token": csrf,
        "Cookie": f"access_token={access}; csrf_token={csrf}",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def main() -> None:
    args = sys.argv[1:]
    outfile = None
    raw_mode = "--raw" in args
    if "--raw" in args:
        args.remove("--raw")
    if "-o" in args:
        i = args.index("-o")
        outfile = args[i + 1]
        del args[i : i + 2]

    method, path = args[0].upper(), args[1]
    body = None
    if len(args) > 2:
        body = json.loads(sys.stdin.read()) if args[2] == "-" else json.load(open(args[2], encoding="utf-8"))

    status, payload = call(method, path, body)

    if outfile:
        if raw_mode and isinstance(payload, dict):
            text = payload.get("data", "")
        else:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)
        print(json.dumps({"status": status, "wrote": outfile, "bytes": len(text)}))
        return

    print(json.dumps({"status": status, "payload": payload}, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
