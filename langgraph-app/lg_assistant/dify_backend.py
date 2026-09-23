"""Dify 执行后端：图里一个可选的场景执行节点。

与 ``../dify-multiagent/`` 配对使用。定位不变：

- **路由不在这里**。五级路由与端侧判对率统计始终在本地图内。
- 这里只把「已判定好的场景 + 上下文」交给 Dify 的场景 Agent 执行。

刻意不导入 ``llm`` / ``nodes``，避免与图形成循环依赖。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from . import config


class DifyError(RuntimeError):
    """Dify 调用失败（网络、鉴权、工作流错误、返回结构异常）。"""


def available() -> bool:
    return bool(config.DIFY_API_KEY) and config.DIFY_API_KEY.startswith("app-")


def build_payload(
    *,
    text: str,
    scene: str,
    device_intent: str = "",
    device_confidence: float | None = None,
    observation: str = "",
    user: str = "lg-assistant",
) -> dict[str, Any]:
    """构造 /v1/workflows/run 请求体。

    ``scene`` 作为 ``scene_hint`` 传给 Dify：这是**本地权威路由的结论**，
    Dify 的 Code 节点会优先采信它，只在它为空时才用端侧意图。
    """
    query = (text or "").strip()
    if observation.strip():
        # 本地已完成视觉精读时并入 query，避免为同一张图付两次视觉调用
        query = f"{query}\n\n【本地已完成图片精读，记录如下】\n{observation.strip()}"

    return {
        "inputs": {
            "text": query,
            "device_intent": (device_intent or "").strip(),
            "device_confidence": "" if device_confidence is None else f"{float(device_confidence):.2f}",
            "scene_hint": scene if scene in config.SCENES else "",
        },
        "response_mode": "blocking",
        "user": user,
    }


def extract_text(payload: Any) -> str:
    """取正文。每个分支一个 answer 节点，键名随场景不同，故取首个非空字符串。"""
    if not isinstance(payload, dict):
        raise DifyError(f"响应不是 JSON 对象：{type(payload).__name__}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DifyError("响应缺少 data 字段")

    status = data.get("status")
    if status and status != "succeeded":
        raise DifyError(f"工作流未成功：status={status} error={data.get('error')!r}")

    outputs = data.get("outputs") or {}
    if not isinstance(outputs, dict):
        raise DifyError(f"outputs 不是对象：{type(outputs).__name__}")

    for value in outputs.values():
        if isinstance(value, str) and value.strip():
            return value.strip()

    # 空 outputs 通常意味着图在某个节点后静默停止（例如边没匹配上）。
    # 这类失败不报错、只返回空，必须显式暴露。
    raise DifyError(
        "工作流返回空 outputs：图可能在中途静默停止"
        f"（status={status} steps={data.get('total_steps')}）"
    )


def run(payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    if not available():
        raise DifyError("未配置 DIFY_API_KEY（或格式不是 app- 开头）")
    url = f"{config.DIFY_BASE_URL}/workflows/run"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.DIFY_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.DIFY_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise DifyError(f"Dify 返回 HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:400]}") from e
    except urllib.error.URLError as e:
        raise DifyError(f"无法连接 Dify（{url}）：{e.reason}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise DifyError(f"Dify 未返回合法 JSON：{raw[:200]!r}") from e


def run_scene(
    *,
    text: str,
    scene: str,
    device_intent: str = "",
    device_confidence: float | None = None,
    observation: str = "",
    user: str = "lg-assistant",
) -> str:
    """跑一个场景，返回正文。失败抛 DifyError，由图节点决定是否回退。"""
    if not available():
        raise DifyError("未配置 DIFY_API_KEY（或格式不是 app- 开头）")
    return extract_text(
        run(
            build_payload(
                text=text,
                scene=scene,
                device_intent=device_intent,
                device_confidence=device_confidence,
                observation=observation,
                user=user,
            )
        )
    )
