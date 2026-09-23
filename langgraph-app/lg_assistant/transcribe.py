"""会议录音转写 + 说话人分离。

为什么不能用现成的 qwen3-asr-flash：它**不支持说话人分离**。
实测传 `diarization_enabled` 返回逐字节相同的结果，只有一整段平文本，
没有 speaker、没有句级时间戳——参数被静默忽略。

因此这里走 paraformer-v2 的异步转写接口，它支持 `diarization_enabled`，
返回句级 `speaker_id` 与 `begin_time/end_time`。

**关键卡点与解法**：paraformer 只接受公网可访问的 URL，不接受本地文件、
不接受二进制流，也不接受 DashScope 自己的 file-id（三种形式都实测失败：
裸 id -> SERVER_ERROR、file:// -> DECODE_ERROR、dashscope:// -> FILE_DOWNLOAD_FAILED）。
解法是用官方的临时存储：

    1. GET  /api/v1/uploads?action=getPolicy&model=paraformer-v2   -> 上传凭证
    2. POST <upload_host>（OSS 表单）                              -> oss:// 临时 URL
    3. POST /api/v1/services/audio/asr/transcription（带
       X-DashScope-OssResourceResolve: enable 头）                 -> task_id
    4. POST /api/v1/tasks/{task_id} 轮询                           -> transcription_url

两个易错点（都实测踩过）：
  - 取凭证是 **GET + 查询参数**，用 POST 会返回 405
  - 用 oss:// URL 调模型**必须**带 `X-DashScope-OssResourceResolve: enable`，
    否则解析不了 oss:// 链接

**整段提交而不是分片**：分片各自 diarize 会让 speaker_id 在每片重新从 0 开始，
同一个人在第 2 片可能变成 0 号，拼起来会把一个人拆成多个。
实测 646 秒 / 1.9MB 整段可一次成功（官方建议开启分离时不超过 2 小时）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import config

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com"
MODEL_DIAR = "paraformer-v2"

#: 开启说话人分离时官方建议的上限
MAX_DIAR_SECONDS = 2 * 60 * 60
#: 轮询上限（每次 4 秒）
POLL_INTERVAL = 4
POLL_MAX = 90


class TranscriptionError(RuntimeError):
    """转写链路失败（上传、提交、轮询、解析）。"""


def _headers() -> dict[str, str]:
    key = config.DASHSCOPE_API_KEY
    if not key:
        raise TranscriptionError("未配置 DASHSCOPE_API_KEY")
    return {"Authorization": f"Bearer {key}"}


def _requests():
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        raise TranscriptionError("未安装 requests，无法调用转写接口") from e
    return requests


# --------------------------------------------------------------------------- #
def upload_for_temp_url(path: str | Path, model: str = MODEL_DIAR) -> str:
    """把本地文件传到百炼临时存储，返回 ``oss://`` 临时 URL（有效期 48 小时）。

    注意：**文件与模型绑定**——上传时指定的模型名必须与后续调用完全一致，
    否则解析不了。这也是本函数把 model 作为参数的原因。
    """
    req = _requests()
    p = Path(path)
    if not p.is_file():
        raise TranscriptionError(f"文件不存在：{p}")

    # 1) 取上传凭证：GET + 查询参数（用 POST 会 405）
    try:
        r = req.get(
            f"{DASHSCOPE_BASE}/api/v1/uploads",
            headers={**_headers(), "Content-Type": "application/json"},
            params={"action": "getPolicy", "model": model},
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        raise TranscriptionError(f"获取上传凭证失败：{type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise TranscriptionError(f"获取上传凭证失败 HTTP {r.status_code}：{r.text[:200]}")

    policy = (r.json() or {}).get("data") or {}
    for field in ("upload_host", "upload_dir", "oss_access_key_id", "policy", "signature"):
        if not policy.get(field):
            raise TranscriptionError(f"上传凭证缺少字段 {field}：{json.dumps(policy)[:200]}")

    key = f"{policy['upload_dir']}/{p.name}"
    files = {
        "OSSAccessKeyId": (None, policy["oss_access_key_id"]),
        "Signature": (None, policy["signature"]),
        "policy": (None, policy["policy"]),
        "x-oss-object-acl": (None, policy.get("x_oss_object_acl", "default")),
        "x-oss-forbid-overwrite": (None, policy.get("x_oss_forbid_overwrite", "false")),
        "key": (None, key),
        "success_action_status": (None, "200"),
        "file": (p.name, p.read_bytes(), "application/octet-stream"),
    }
    try:
        up = req.post(policy["upload_host"], files=files, timeout=600)
    except Exception as e:  # noqa: BLE001
        raise TranscriptionError(f"上传失败：{type(e).__name__}: {e}") from e
    if up.status_code != 200:
        raise TranscriptionError(f"上传失败 HTTP {up.status_code}：{up.text[:200]}")

    return f"oss://{key}"


def submit(file_url: str, *, speaker_count: int | None = None,
           model: str = MODEL_DIAR) -> str:
    """提交转写任务，返回 task_id。"""
    req = _requests()
    params: dict[str, Any] = {"channel_id": [0], "diarization_enabled": True}
    if speaker_count:
        params["speaker_count"] = int(speaker_count)

    try:
        r = req.post(
            f"{DASHSCOPE_BASE}/api/v1/services/audio/asr/transcription",
            headers={
                **_headers(),
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
                # 缺这个头就解析不了 oss:// 链接，任务会失败
                "X-DashScope-OssResourceResolve": "enable",
            },
            json={"model": model, "input": {"file_urls": [file_url]}, "parameters": params},
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        raise TranscriptionError(f"提交转写任务失败：{type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise TranscriptionError(f"提交转写任务失败 HTTP {r.status_code}：{r.text[:200]}")

    task_id = ((r.json() or {}).get("output") or {}).get("task_id")
    if not task_id:
        raise TranscriptionError(f"提交未返回 task_id：{r.text[:200]}")
    return task_id


def fetch_result(task_id: str) -> dict[str, Any]:
    """轮询直到完成，返回识别结果 JSON（含 sentences[].speaker_id）。"""
    req = _requests()
    headers = {**_headers(), "X-DashScope-OssResourceResolve": "enable"}

    last = ""
    for i in range(POLL_MAX):
        time.sleep(POLL_INTERVAL)
        try:
            q = req.post(f"{DASHSCOPE_BASE}/api/v1/tasks/{task_id}", headers=headers, timeout=60)
            out = (q.json() or {}).get("output") or {}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            continue

        status = out.get("task_status")
        last = str(status)
        if status == "SUCCEEDED":
            res = (out.get("results") or [{}])[0]
            if res.get("code"):
                raise TranscriptionError(
                    f"转写子任务失败 {res.get('subtask_status')} "
                    f"code={res['code']}：{res.get('message', '')}"
                )
            url = res.get("transcription_url")
            if not url:
                raise TranscriptionError(f"结果缺少 transcription_url：{json.dumps(res)[:200]}")
            try:
                data = req.get(url, timeout=180).json()
            except Exception as e:  # noqa: BLE001
                raise TranscriptionError(f"下载识别结果失败：{type(e).__name__}: {e}") from e
            return data
        if status in ("FAILED", "CANCELED"):
            raise TranscriptionError(
                f"转写任务失败：{json.dumps(out, ensure_ascii=False)[:300]}"
            )

    raise TranscriptionError(f"转写任务超时（最后状态 {last}）")


# --------------------------------------------------------------------------- #
def transcribe_with_speakers(
    path: str | Path, *, speaker_count: int | None = None
) -> dict[str, Any]:
    """完整链路：上传 → 提交 → 轮询 → 解析。返回统一结构。

    返回::

        {
          "sentences": [{"speaker": 0, "begin_ms": 2600, "end_ms": 4200, "text": "..."}],
          "utterances": [{"speaker": 0, "begin_ms": 2600, "text": "合并后的整段"}],
          "speakers": [0, 1, 2],
          "duration_ms": 646000,
          "text": "带说话人标记的完整转写",
        }
    """
    url = upload_for_temp_url(path)
    task_id = submit(url, speaker_count=speaker_count)
    data = fetch_result(task_id)
    return parse_result(data)


def parse_result(data: dict[str, Any]) -> dict[str, Any]:
    """把服务端结果整理成统一结构。纯函数，便于单测。"""
    sentences: list[dict[str, Any]] = []
    for tr in data.get("transcripts") or []:
        for s in tr.get("sentences") or []:
            text = str(s.get("text") or "").strip()
            if not text:
                continue
            sentences.append(
                {
                    "speaker": s.get("speaker_id"),
                    "begin_ms": int(s.get("begin_time") or 0),
                    "end_ms": int(s.get("end_time") or 0),
                    "text": text,
                }
            )
    sentences.sort(key=lambda x: x["begin_ms"])

    # 把同一说话人的连续句子合并成一段发言——逐句罗列读起来太碎
    utterances: list[dict[str, Any]] = []
    for s in sentences:
        if utterances and utterances[-1]["speaker"] == s["speaker"]:
            utterances[-1]["text"] += s["text"]
            utterances[-1]["end_ms"] = s["end_ms"]
        else:
            utterances.append(
                {
                    "speaker": s["speaker"],
                    "begin_ms": s["begin_ms"],
                    "end_ms": s["end_ms"],
                    "text": s["text"],
                }
            )

    props = data.get("properties") or {}
    return {
        "sentences": sentences,
        "utterances": utterances,
        "speakers": sorted({s["speaker"] for s in sentences if s["speaker"] is not None}),
        "duration_ms": int(props.get("original_duration_in_milliseconds") or 0),
        "text": format_transcript(utterances),
    }


def speaker_label(speaker: Any) -> str:
    """说话人编号 -> 展示名。

    **只说「发言人N」，不给任何姓名推断。** 声纹只能区分「是不是同一个人」，
    不能知道他是谁；从内容里猜姓名再冠上去，猜错就是把别人的话安在别人头上——
    会议纪要里这是很严重的错误。
    """
    if speaker is None:
        return "发言人"
    try:
        return f"发言人{int(speaker) + 1}"
    except (TypeError, ValueError):
        return f"发言人{speaker}"


def format_transcript(utterances: list[dict[str, Any]], *, with_time: bool = True) -> str:
    """把发言段渲染成可读文本。"""
    lines: list[str] = []
    for u in utterances:
        label = speaker_label(u.get("speaker"))
        if with_time:
            total = int(u.get("begin_ms") or 0) // 1000
            lines.append(f"[{total // 60:02d}:{total % 60:02d}] {label}：{u.get('text', '')}")
        else:
            lines.append(f"{label}：{u.get('text', '')}")
    return "\n".join(lines)


def speaker_stats(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按说话人统计发言段数与时长，供纪要与前端展示。"""
    agg: dict[Any, dict[str, Any]] = {}
    for u in utterances:
        spk = u.get("speaker")
        row = agg.setdefault(spk, {"speaker": spk, "label": speaker_label(spk), "turns": 0, "ms": 0})
        row["turns"] += 1
        row["ms"] += max(0, int(u.get("end_ms") or 0) - int(u.get("begin_ms") or 0))
    rows = list(agg.values())
    for r in rows:
        r["seconds"] = round(r["ms"] / 1000, 1)
    rows.sort(key=lambda r: r["ms"], reverse=True)
    return rows
