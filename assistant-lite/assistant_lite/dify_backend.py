"""Dify 执行后端：把场景执行转发给 Dify 工作流。

定位（见 `docs/端侧适配设计_眼镜.md` 方案 B）：

- **路由不在这里**。五级路由、端侧判对率统计仍然由本地 `orchestrator` 负责——
  一旦路由进了 Dify，`source="device"` 打标与 reroute 统计就没有落点。
- 这里只做一件事：把「已判定好的场景 + 上下文」交给 Dify 的场景 Agent 执行，
  再把文本结果包装回 `Reply`，后续的播报语压缩、归档仍走本地既有约定。

刻意**不导入 llm / agents**，避免与本地执行链形成循环依赖。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from . import config

#: Dify 工作流的五个场景类名（与 dify-multiagent/gen_dify_dsl.py 的 class id 一致）
SCENE_CLASSES = frozenset(config.SCENES)


class DifyError(RuntimeError):
    """Dify 调用失败（网络、鉴权、工作流错误、返回结构异常）。"""


def available() -> bool:
    """Dify 后端是否可用。缺 Key 时视为不可用，由编排层决定是否回退。"""
    return bool(config.DIFY_API_KEY) and config.DIFY_API_KEY.startswith("app-")


# --------------------------------------------------------------------------- #
# 载荷构造（纯函数，便于不联网单测）
# --------------------------------------------------------------------------- #
def build_payload(
    *,
    text: str,
    scene: str,
    device_intent: str = "",
    device_confidence: float | None = None,
    observation: str = "",
    user: str = "assistant-lite",
) -> dict[str, Any]:
    """构造 /v1/workflows/run 的请求体。

    `scene` 作为 `scene_hint` 传给 Dify：这是**本地权威路由的结论**，
    Dify 的 Code 节点会优先采信它，只在它为空时才用端侧意图。
    """
    query = (text or "").strip()
    if observation.strip():
        # 本地已完成视觉精读时，把观察记录并入 query——
        # 这样 Dify 侧不拿图也能作答，避免为同一张图付两次视觉调用。
        query = f"{query}\n\n【本地已完成图片精读，记录如下】\n{observation.strip()}"

    conf = "" if device_confidence is None else f"{float(device_confidence):.2f}"
    return {
        "inputs": {
            "text": query,
            "device_intent": (device_intent or "").strip(),
            "device_confidence": conf,
            "scene_hint": scene if scene in SCENE_CLASSES else "",
        },
        "response_mode": "blocking",
        "user": user,
    }


def extract_text(payload: Any) -> str:
    """从 Dify 响应里取正文。

    工作流每个分支有一个 answer 节点，不同场景下 outputs 的键名不同，
    所以取「第一个非空字符串值」，而不是写死键名。
    """
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

    # outputs 为空通常意味着图在某个节点后静默停止（例如边没匹配上），
    # 这类失败不报错、只返回空，必须显式暴露而不是当成「模型没说话」。
    raise DifyError(
        "工作流返回空 outputs：图可能在中途静默停止"
        f"（status={status} steps={data.get('total_steps')}）"
    )


# --------------------------------------------------------------------------- #
# 调用
# --------------------------------------------------------------------------- #
def run(payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    """POST /v1/workflows/run，返回原始响应 dict。"""
    if not available():
        raise DifyError("未配置 DIFY_API_KEY（或格式不是 app- 开头），无法使用 Dify 后端")

    url = f"{config.DIFY_BASE_URL}/workflows/run"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.DIFY_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=body,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.DIFY_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise DifyError(f"Dify 返回 HTTP {e.code}：{detail}") from e
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
    user: str = "assistant-lite",
) -> str:
    """跑一个场景，返回正文文本。失败抛 DifyError，由编排层决定是否回退。"""
    # 先查可用性再组载荷：未配置 Key 时也走 DifyError，
    # 让回退逻辑只有一条分支，不依赖调用方预判。
    if not available():
        raise DifyError("未配置 DIFY_API_KEY（或格式不是 app- 开头），无法使用 Dify 后端")

    payload = build_payload(
        text=text,
        scene=scene,
        device_intent=device_intent,
        device_confidence=device_confidence,
        observation=observation,
        user=user,
    )
    return extract_text(run(payload))
