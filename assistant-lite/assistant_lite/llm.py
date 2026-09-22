"""模型调用层：全部走阿里云百炼。

- 文本 / 视觉：openai SDK（百炼 OpenAI 兼容模式）
- 语音识别：dashscope SDK 的 MultiModalConversation（qwen3-asr-flash）

对外只暴露 4 个函数：chat / vision / json_chat / asr。
"""

from __future__ import annotations

import base64
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
            raise LLMError("配置不可用：" + "；".join(missing))
        _client = OpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=config.BASE_URL,
            timeout=config.REQUEST_TIMEOUT,
        )
    return _client


# --------------------------------------------------------------------------- #
# 图片处理
# --------------------------------------------------------------------------- #
def to_data_url(path: str | Path) -> str:
    """把本地图片转成 data URL。超过长边上限时用 Pillow 缩放并转 JPEG。"""
    p = Path(path)
    if not p.is_file():
        raise LLMError(f"图片不存在：{p}")

    try:
        from PIL import Image  # 延迟导入，缺 Pillow 时不影响纯文本链路

        with Image.open(p) as im:
            im = im.convert("RGB")
            w, h = im.size
            longest = max(w, h)
            if longest > config.VISION_MAX_EDGE:
                scale = config.VISION_MAX_EDGE / longest
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            import io

            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=88)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
    except LLMError:
        raise
    except Exception:
        # Pillow 不可用或图片异常：退回原图直传
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"


# --------------------------------------------------------------------------- #
# 文本
# --------------------------------------------------------------------------- #
def chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int | None = None,
) -> str:
    """纯文本对话，返回助手回复文本。"""
    kwargs: dict[str, Any] = {
        "model": model or config.MODEL_TEXT,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    try:
        resp = client().chat.completions.create(**kwargs)
    except Exception as e:  # openai SDK 抛各类异常，统一包装
        raise LLMError(f"文本模型调用失败：{e}") from e
    return (resp.choices[0].message.content or "").strip()


def vision(
    prompt: str,
    images: list[str | Path],
    system: str = "",
    model: str | None = None,
    temperature: float = 0.2,
) -> str:
    """视觉对话：prompt + 一张或多张本地图片。"""
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


# --------------------------------------------------------------------------- #
# JSON 输出
# --------------------------------------------------------------------------- #
def strip_fences(text: str) -> str:
    """去掉模型爱加的 ```json 围栏。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def json_chat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    retries: int = 1,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """要求模型输出 JSON。解析失败时追加一条纠正消息重试。"""
    msgs = list(messages)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        raw = chat(msgs, model=model, temperature=temperature)
        try:
            data = json.loads(strip_fences(raw))
            if isinstance(data, dict):
                return data
            last_err = LLMError(f"期望 JSON 对象，得到 {type(data).__name__}")
        except json.JSONDecodeError as e:
            last_err = e
        if attempt < retries:
            msgs = msgs + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "上一条不是合法 JSON。请只输出一个 JSON 对象，不要任何解释、不要 Markdown 围栏。",
                },
            ]
    raise LLMError(f"模型未返回合法 JSON：{last_err}")


def vision_json(
    prompt: str,
    images: list[str | Path],
    user_text: str = "",
    retries: int = 1,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """视觉 + JSON 输出：prompt 作为 system，图片连同 user_text 一起给模型。

    解析失败时追加一条纠正消息重试。
    """
    text = user_text
    last: Exception | None = None
    for attempt in range(retries + 1):
        raw = vision(text or "请分析这张图片。", images, system=prompt, temperature=temperature)
        try:
            data = json.loads(strip_fences(raw))
            if isinstance(data, dict):
                return data
            last = LLMError("期望 JSON 对象")
        except json.JSONDecodeError as e:
            last = e
        if attempt < retries:
            text = (
                (user_text or "请分析这张图片。")
                + "\n\n【上次输出不是合法 JSON】请只输出一个 JSON 对象，"
                "不要任何解释文字、不要 Markdown 围栏。"
            )
    raise LLMError(f"模型未返回合法 JSON：{last}")


# --------------------------------------------------------------------------- #
# 语音识别
# --------------------------------------------------------------------------- #
def asr(audio_path: str | Path, model: str | None = None) -> str:
    """把本地音频文件转成文字。走 dashscope SDK 的 qwen3-asr-flash。"""
    p = Path(audio_path)
    if not p.is_file():
        raise LLMError(f"音频不存在：{p}")

    try:
        import dashscope
    except ImportError as e:
        raise LLMError("未安装 dashscope，无法做语音识别：pip install dashscope") from e

    dashscope.api_key = config.DASHSCOPE_API_KEY
    messages = [
        {
            "role": "user",
            "content": [{"audio": p.resolve().as_uri()}],
        }
    ]
    try:
        resp = dashscope.MultiModalConversation.call(
            model=model or config.MODEL_ASR,
            messages=messages,
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

    texts = [c.get("text", "") for c in parts if isinstance(c, dict)]
    return "".join(texts).strip()


# --------------------------------------------------------------------------- #
# 语音合成
# --------------------------------------------------------------------------- #
def tts(text: str, voice: str | None = None, model: str | None = None) -> bytes:
    """把文本合成为语音，返回 mp3 字节。

    走 dashscope 的 tts_v2（cosyvoice）。**不用 sambert 那套**——
    在百炼新账号上它返回空音频，实测过。
    """
    if not (text or "").strip():
        raise LLMError("要合成的文本为空")

    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer
    except ImportError as e:
        raise LLMError("未安装 dashscope，无法做语音合成") from e

    dashscope.api_key = config.DASHSCOPE_API_KEY
    synth = SpeechSynthesizer(
        model=model or config.MODEL_TTS,
        voice=voice or config.TTS_VOICE,
    )
    try:
        audio = synth.call(text)
    except Exception as e:
        raise LLMError(f"语音合成失败：{e}") from e
    if not audio:
        raise LLMError("语音合成返回空音频")
    return audio
