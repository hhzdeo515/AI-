"""模型调用：百炼 OpenAI 兼容接口。只保留编排需要的文本与视觉两条通路。

刻意**不使用 langchain-openai**：模型调用是外部服务，包一层不产生收益，
反而让「测试不依赖外部 API」这条硬约束更难维持（需要多打一层桩）。
编排才是 LangGraph 的价值所在，不在这里。
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from . import config


class LLMError(RuntimeError):
    """模型调用失败（网络、鉴权、超时、返回不合法等）。"""


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        missing = config.problems()
        if missing:
            raise LLMError(
                "配置不可用："
                + "；".join(missing)
                # 诊断信息：配置为空往往来自 .env 未加载或 DATA_DIR 被改写，
                # 不打印就只能靠猜（assistant-lite 阶段踩过一次同样的坑）。
                + f"［诊断 key_len={len(config.DASHSCOPE_API_KEY)}"
                f" env={config.ROOT / '.env'} exists={(config.ROOT / '.env').is_file()}］"
            )
        _client = OpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=config.BASE_URL,
            timeout=config.REQUEST_TIMEOUT,
        )
    return _client


def reset_client() -> None:
    """仅供测试：丢弃缓存的客户端，让配置改动生效。"""
    global _client
    _client = None


# --------------------------------------------------------------------------- #
def to_data_url(path: str | Path, max_edge: int = 2048) -> str:
    """本地图片 -> data URL。超过长边上限时缩放并转 JPEG。"""
    p = Path(path)
    if not p.is_file():
        raise LLMError(f"图片不存在：{p}")
    try:
        from PIL import Image

        with Image.open(p) as im:
            im = im.convert("RGB")
            w, h = im.size
            longest = max(w, h)
            if longest > max_edge:
                scale = max_edge / longest
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=88)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
    except LLMError:
        raise
    except Exception:
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def strip_fences(text: str) -> str:
    """去掉模型爱加的 ```json 围栏。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int | None = None,
) -> str:
    kwargs: dict[str, Any] = {
        "model": model or config.MODEL_TEXT,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    try:
        resp = client().chat.completions.create(**kwargs)
    except Exception as e:
        raise LLMError(f"文本模型调用失败：{e}") from e
    return (resp.choices[0].message.content or "").strip()


def vision(
    prompt: str,
    images: list[str | Path],
    system: str = "",
    model: str | None = None,
    temperature: float = 0.2,
) -> str:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": to_data_url(img)}})

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    try:
        resp = client().chat.completions.create(
            model=model or config.MODEL_VISION,
            messages=messages,
            temperature=temperature,
        )
    except Exception as e:
        raise LLMError(f"视觉模型调用失败：{e}") from e
    return (resp.choices[0].message.content or "").strip()


def json_chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    retries: int = 1,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """要求模型输出 JSON。解析失败时追加纠正消息重试。"""
    msgs = list(messages)
    last: Exception | None = None
    for attempt in range(retries + 1):
        raw = chat(msgs, model=model, temperature=temperature)
        try:
            data = json.loads(strip_fences(raw))
            if isinstance(data, dict):
                return data
            last = LLMError(f"期望 JSON 对象，得到 {type(data).__name__}")
        except json.JSONDecodeError as e:
            last = e
        if attempt < retries:
            msgs = msgs + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "上一条不是合法 JSON。请只输出一个 JSON 对象，"
                               "不要任何解释、不要 Markdown 围栏。",
                },
            ]
    raise LLMError(f"模型未返回合法 JSON：{last}")


def llm_router(text: str, event: dict[str, Any], file_count: int = 0) -> dict[str, Any]:
    """路由兜底用的模型调用。签名与 ``routing.route`` 的 ``llm_router`` 参数一致。"""
    from .routing import ROUTER_PROMPT

    payload = (
        f"当前请求：{text}\n"
        f"事件：{json.dumps(event or {}, ensure_ascii=False)}\n"
        f"附带文件数：{file_count}"
    )
    return json_chat(
        [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": payload},
        ]
    )


# --------------------------------------------------------------------------- #
# 语音识别 / 合成（移植自 assistant-lite，走 dashscope SDK）
# --------------------------------------------------------------------------- #
def asr(audio_path: str | Path, model: str | None = None) -> str:
    """把本地音频文件转成文字（qwen3-asr-flash）。"""
    p = Path(audio_path)
    if not p.is_file():
        raise LLMError(f"音频不存在：{p}")
    try:
        import dashscope
    except ImportError as e:
        raise LLMError("未安装 dashscope，无法做语音识别：pip install dashscope") from e

    dashscope.api_key = config.DASHSCOPE_API_KEY
    try:
        resp = dashscope.MultiModalConversation.call(
            model=model or config.MODEL_ASR,
            messages=[{"role": "user", "content": [{"audio": p.resolve().as_uri()}]}],
            result_format="message",
        )
    except Exception as e:
        raise LLMError(f"语音识别调用失败：{e}") from e

    if getattr(resp, "status_code", 200) != 200:
        raise LLMError(f"语音识别返回错误 {resp.status_code}：{getattr(resp, 'message', '')}")
    try:
        parts = resp.output.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as e:
        raise LLMError(f"语音识别返回结构异常：{resp}") from e
    return "".join(c.get("text", "") for c in parts if isinstance(c, dict)).strip()


def tts(text: str, voice: str | None = None, model: str | None = None) -> bytes:
    """把文本合成为语音，返回 mp3 字节（cosyvoice / tts_v2）。

    **不用 sambert 那套**——在百炼新账号上返回空音频（基线项目实测过）。
    """
    if not (text or "").strip():
        raise LLMError("要合成的文本为空")
    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer
    except ImportError as e:
        raise LLMError("未安装 dashscope，无法做语音合成") from e

    dashscope.api_key = config.DASHSCOPE_API_KEY
    synth = SpeechSynthesizer(model=model or config.MODEL_TTS, voice=voice or config.TTS_VOICE)
    try:
        audio = synth.call(text)
    except Exception as e:
        raise LLMError(f"语音合成失败：{e}") from e
    if not audio:
        raise LLMError("语音合成返回空音频")
    return audio
