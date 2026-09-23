"""Run the Dify multi-agent workflow end to end and print each scene's answer.

Usage: python run_e2e.py <api-key> [scene ...]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

URL = "http://localhost/v1/workflows/run"

CASES: dict[str, dict[str, str]] = {
    "exam": {
        "text": "计算 (18+24)*3 等于多少，请逐步说明",
        "device_intent": "solve",
        "device_confidence": "0.91",
        "scene_hint": "exam",
    },
    "meeting": {
        "text": (
            "测试转写：李四说我周五前提交接口文档，张三决定先做本机版。"
            "另外白板记了「风险：测试环境未就绪」。请生成纪要。"
        ),
        "device_intent": "meeting_summarize",
        "device_confidence": "0.88",
        "scene_hint": "meeting",
    },
    "fitness": {
        "text": "我42岁，高血压，膝盖有旧伤，想减脂，家里只有哑铃，今天该练什么？",
        "device_intent": "train_start",
        "device_confidence": "0.93",
        "scene_hint": "fitness",
    },
    "resource": {
        "text": "我有哪些资料？怎么导出最新一份成 Word？",
        "device_intent": "switch_scene",
        "device_confidence": "0.80",
        "scene_hint": "resource",
    },
    "general": {
        "text": "你好，介绍一下你自己能做什么",
        "device_intent": "wake",
        "device_confidence": "0.40",
        "scene_hint": "general",
    },
    # Device judgement is wrong on purpose: cloud classifier must override it.
    "override": {
        "text": "我膝盖有点疼，练不下去了",
        "device_intent": "solve",
        "device_confidence": "0.95",
        "scene_hint": "",
    },
}


def run(key: str, inputs: dict[str, str]) -> dict:
    body = json.dumps(
        {"inputs": inputs, "response_mode": "blocking", "user": "e2e-device"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        URL,
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json; charset=utf-8",
        },
        data=body,
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", "replace")[:400]}


def main() -> None:
    key = sys.argv[1]
    wanted = sys.argv[2:] or list(CASES)
    results = {}
    for name in wanted:
        inputs = CASES[name]
        t0 = time.time()
        resp = run(key, inputs)
        elapsed = round(time.time() - t0, 1)
        data = resp.get("data", {})
        outputs = data.get("outputs") or {}
        text = ""
        for v in outputs.values():
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break
        results[name] = {
            "status": data.get("status", resp.get("_http_error")),
            "elapsed": elapsed,
            "steps": data.get("total_steps"),
            "chars": len(text),
        }
        print("=" * 68)
        print(f"[{name}] status={results[name]['status']} {elapsed}s steps={data.get('total_steps')}")
        if resp.get("_http_error"):
            print("  HTTP", resp["_http_error"], resp["_body"])
        else:
            print(text[:700] if text else "  (empty output)")
        print()
    print("SUMMARY:", json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
