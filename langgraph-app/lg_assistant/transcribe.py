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

**两条提升准确率的路子，都实测过**：

- 定制热词（``ensure_vocabulary``）：领域词听错靠它治，实测「拆gbt / 巨身」
  变成「ChatGPT / 具身」，且只改解码偏好、不改音频内容。
- ``speaker_count``：只是**参考值**，服务文档明确「无法保证一定会输出此人数」。
  实测那段 7 人圆桌：自动判断出 6 类，显式传 7 之后**仍然是 6 类**，
  只是碎片变了。所以默认不传，仅保留参数供调用方按需使用。

**分离结果里哪些地方不能全信**：见 ``fragile_flags`` 与 ``diarization_report``。
我们只标注、不擅自改判——把别人的话安在别人头上，比标一句「存疑」严重得多。
"""

from __future__ import annotations

import hashlib
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
           vocabulary_id: str | None = None, model: str = MODEL_DIAR) -> str:
    """提交转写任务，返回 task_id。

    ``vocabulary_id`` 是定制热词表（见 ``ensure_vocabulary``）。热词只影响解码时
    的用词偏好，不改写音频内容，属于「零风险提升」的那一类参数。
    """
    req = _requests()
    params: dict[str, Any] = {
        "channel_id": [0],
        "diarization_enabled": True,
        # 中英混说的会议里，显式声明语种比让模型自己猜更稳
        "language_hints": ["zh", "en"],
    }
    if speaker_count:
        params["speaker_count"] = int(speaker_count)
    if vocabulary_id:
        params["vocabulary_id"] = vocabulary_id

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
# 定制热词：把领域词先告诉解码器
#
# 为什么需要（实测，同一段 646 秒具身智能圆桌，两次转写只差热词表）：
#   不带热词：「拆gbt」「chgbt」「巨身」「巨身的仿真引擎」「c dance two」
#   带热词  ：「ChatGPT」「具身」——同一个词反复听错，是解码器缺少这个领域的先验，
#             不是音频不清楚。热词表正好补这一块，而且**不会改写音频内容**：
#             它只调整解码时的用词偏好，写错词最多是没效果。
#
# 词表来源：``config.HOTWORDS_PATH``（一行一个词，# 开头是注释）；文件不存在时
# 用 ``DEFAULT_HOT_WORDS``。词表改了下次转写会自动重建（靠指纹比对）。
# --------------------------------------------------------------------------- #
DEFAULT_HOT_WORDS: tuple[str, ...] = (
    "ChatGPT", "大模型", "具身智能", "人形机器人", "世界模型", "灵巧手",
    "端到端", "多模态", "强化学习", "评测基准", "仿真引擎", "圆桌论坛",
)

#: 热词表 ID 的前缀，仅允许数字和小写字母（接口限制）
HOTWORD_PREFIX = "lgassist"
#: 热词表缓存：记下「词表指纹 -> vocabulary_id」，避免每次转写都新建一张表
HOTWORD_CACHE = "hotwords.cache.json"


def clean_hot_words(words: list[str]) -> list[str]:
    """按接口限制过滤词表：非 ASCII 不超过 15 字、纯 ASCII 不超过 7 段。"""
    out: list[str] = []
    for w in words:
        w = str(w or "").strip()
        if not w:
            continue
        if w.isascii():
            if len(w.split()) > 7:
                continue
        elif len(w) > 15:
            continue
        out.append(w)
    return out


def load_hot_words(path: str | Path | None = None) -> list[str]:
    """读词表文件；文件不存在或没写出任何词时用内置默认表。保序去重。"""
    p = Path(path) if path else config.HOTWORDS_PATH
    words: list[str] = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    if not words:
        words = list(DEFAULT_HOT_WORDS)
    seen: set[str] = set()
    uniq: list[str] = []
    for w in clean_hot_words(words):
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq


def create_vocabulary(
    words: list[str], *, prefix: str = HOTWORD_PREFIX,
    target_model: str = MODEL_DIAR, weight: int | None = None,
) -> str:
    """新建一张定制热词表，返回 vocabulary_id。

    注意 ``target_model`` 必须与转写用的模型完全一致，否则不生效。
    """
    req = _requests()
    cleaned = clean_hot_words(words)
    if not cleaned:
        raise TranscriptionError("热词表为空，没有可提交的词")
    w = int(weight or config.HOTWORD_WEIGHT)
    try:
        r = req.post(
            f"{DASHSCOPE_BASE}/api/v1/services/audio/asr/customization",
            headers={**_headers(), "Content-Type": "application/json"},
            json={
                "model": "speech-biasing",
                "input": {
                    "action": "create_vocabulary",
                    "target_model": target_model,
                    "prefix": prefix,
                    "vocabulary": [{"text": t, "weight": w} for t in cleaned],
                },
            },
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        raise TranscriptionError(f"创建热词表失败：{type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise TranscriptionError(f"创建热词表失败 HTTP {r.status_code}：{r.text[:200]}")

    vid = ((r.json() or {}).get("output") or {}).get("vocabulary_id")
    if not vid:
        raise TranscriptionError(f"创建热词表未返回 vocabulary_id：{r.text[:200]}")
    return str(vid)


def ensure_vocabulary(
    words: list[str] | None = None, *, cache_path: str | Path | None = None
) -> str:
    """确保存在一张与当前词表一致的热词表，返回它的 ID。

    热词是锦上添花：**失败就抛出，由调用方决定降级**，绝不在这里静默吞掉——
    「以为加了热词其实没加」会让后面所有的效果对比都不可信。
    """
    words = list(words) if words else load_hot_words()
    # 指纹按「去重后排序」算：热词是一张集合，用户调换两行顺序不该触发重建
    fingerprint = hashlib.sha1("\n".join(sorted(set(words))).encode("utf-8")).hexdigest()[:16]
    cache = Path(cache_path) if cache_path else (config.DATA_DIR / HOTWORD_CACHE)

    if cache.is_file():
        try:
            rec = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rec = {}
        if rec.get("fingerprint") == fingerprint and rec.get("vocabulary_id"):
            return str(rec["vocabulary_id"])

    vid = create_vocabulary(words)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {"fingerprint": fingerprint, "words": words, "vocabulary_id": vid},
                ensure_ascii=False, indent=1,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # 缓存写不进去不影响本次转写
    return vid


# --------------------------------------------------------------------------- #
def transcribe_with_speakers(
    path: str | Path, *, speaker_count: int | None = None, vocabulary_id: str | None = None
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
    task_id = submit(url, speaker_count=speaker_count, vocabulary_id=vocabulary_id)
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


def speaker_label(speaker: Any, names: dict[str, str] | None = None) -> str:
    """说话人编号 -> 展示名。

    **只说「发言人N」，不给任何姓名推断。** 声纹只能区分「是不是同一个人」，
    不能知道他是谁；从内容里猜姓名再冠上去，猜错就是把别人的话安在别人头上——
    会议纪要里这是很严重的错误。

    ``names`` 是**用户自己填写**的改名表（``{"3": "王浩"}``），只有人给的才用。
    """
    if speaker is None:
        return "发言人"
    key = str(speaker)
    if names and names.get(key):
        return str(names[key])
    try:
        return f"发言人{int(speaker) + 1}"
    except (TypeError, ValueError):
        return f"发言人{speaker}"


def format_transcript(
    utterances: list[dict[str, Any]], *, with_time: bool = True, mark_uncertain: bool = True,
    names: dict[str, str] | None = None,
) -> str:
    """把发言段渲染成可读文本。

    ``mark_uncertain`` 打开时，归属把握低的短促片段会带上「短促片段·归属存疑」，
    读者一眼能看出这一段是分离算法的猜测，而不是确定的事实。
    ``names`` 是用户自己填的改名表（``{"3": "王浩"}``）。
    """
    flags = fragile_flags(utterances) if mark_uncertain else [False] * len(utterances)
    lines: list[str] = []
    for i, u in enumerate(utterances):
        label = speaker_label(u.get("speaker"), names)
        if flags[i]:
            label += "（短促片段·归属存疑）"
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


# --------------------------------------------------------------------------- #
# 说话人分离的可信度：把「哪里可能错了」摆到明面上
#
# 为什么不是「自动修好」：分离是服务端聚类，我们手上只有 speaker_id。
# 645.76 秒的实测里，7 个人被聚成 6 类（罗荣青 8 秒的自我介绍并进了王浩），
# 显式传 speaker_count=7 也**没有**改变这个结果（服务文档写明「无法保证」）。
# 硬用规则去合并/拆分，风险是把一个人的话安到另一个人头上——比标出来更糟。
# --------------------------------------------------------------------------- #
#: 时长短于这个值、字数少于这个值，才可能被当作「片段」而不是「发言」
FRAGILE_MAX_MS = 4000
FRAGILE_MAX_CHARS = 20
#: 与前后两段几乎无缝衔接（间隔小于此值）才算「夹在中间」
FRAGILE_MAX_GAP_MS = 1500


def fragile_flags(utterances: list[dict[str, Any]]) -> list[bool]:
    """标出归属存疑的短促片段：A（长）→ B（很短）→ A（长），且几乎无间隔。

    这正是「一个人的发言被拆成两个人」的形态。之所以只标不改：短片段也可能是
    真实插话，合并回去就是把别人的话安在别人头上——本文件开头那条原则同样适用。
    """
    flags = [False] * len(utterances)
    for i in range(1, len(utterances) - 1):
        prev, cur, nxt = utterances[i - 1], utterances[i], utterances[i + 1]
        if prev.get("speaker") != nxt.get("speaker"):
            continue
        if cur.get("speaker") == prev.get("speaker"):
            continue
        dur = int(cur.get("end_ms") or 0) - int(cur.get("begin_ms") or 0)
        text = str(cur.get("text") or "")
        if dur > FRAGILE_MAX_MS or len(text) > FRAGILE_MAX_CHARS:
            continue
        gap_before = int(cur.get("begin_ms") or 0) - int(prev.get("end_ms") or 0)
        gap_after = int(nxt.get("begin_ms") or 0) - int(cur.get("end_ms") or 0)
        if gap_before > FRAGILE_MAX_GAP_MS or gap_after > FRAGILE_MAX_GAP_MS:
            continue
        flags[i] = True
    return flags


def diarization_report(utterances: list[dict[str, Any]]) -> dict[str, Any]:
    """给用户看的分离质量：人数、每人段数与秒数、存疑片段、发言过少的人。"""
    stats = speaker_stats(utterances)
    flags = fragile_flags(utterances)
    suspect = [s for s in stats if s["seconds"] < 5]
    return {
        "speakers": len(stats),
        "stats": stats,
        "fragile_index": [i for i, f in enumerate(flags) if f],
        "fragile_count": sum(1 for f in flags if f),
        "suspect": suspect,
        "duration_ms": sum(max(0, int(u.get("end_ms") or 0) - int(u.get("begin_ms") or 0)) for u in utterances),
    }


def describe_diarization(report: dict[str, Any]) -> str:
    """把分离质量渲染成一行中文，挂在转写正文前面。"""
    parts = "、".join(f"{s['label']} {s['turns']} 段" for s in report["stats"])
    line = f"（共识别出 {report['speakers']} 位发言人；{parts}"
    if report["fragile_count"]:
        line += f"；{report['fragile_count']} 个短促片段归属存疑，正文已标注"
    if report["suspect"]:
        who = "、".join(f"{s['label']}（{s['seconds']} 秒）" for s in report["suspect"])
        line += f"；{who} 发言过少，可能是误分"
    return line + "）"


# --------------------------------------------------------------------------- #
# 人工改判：**唯一能真正修好「谁说的是谁」的办法**
#
# 服务端的聚类到此为止：645.76 秒那段实测 7 人聚成 6 类，显式指定人数也改不动
# （见 ``fragile_flags`` 上面的说明）。剩下的错只能人来定：把「发言人3」里那句
# 属于罗荣青的话拆出来、把两个其实是一个人的标签合起来、给编号填上真名。
#
# 这一层是纯函数：编辑操作全部作用在「转写记录」这个 dict 上，返回新记录，
# 不改原对象——便于单测，也便于前端撤销（记录里留了一份 original）。
# --------------------------------------------------------------------------- #
#: 记录字段：utterances / names / original / next_speaker / summary / updated
def new_record(
    utterances: list[dict[str, Any]],
    *,
    names: dict[str, str] | None = None,
    summary: str = "",
) -> dict[str, Any]:
    """把分离结果包成可编辑记录。``original`` 留一份原始结果，供「恢复」用。"""
    items = [dict(u) for u in utterances]
    ids = [int(u["speaker"]) for u in items if isinstance(u.get("speaker"), int)]
    return {
        "utterances": items,
        "names": dict(names or {}),
        "original": [dict(u) for u in items],
        "next_speaker": (max(ids) + 1) if ids else 0,
        "summary": summary,
    }


def _remerge(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """相邻同说话人重新合并。

    改判之后必须重跑一次：把 B 并进 A 之后，A 的前后两段往往就贴在一起了，
    不合并的话正文里会出现同一个人的连续两行。
    """
    out: list[dict[str, Any]] = []
    for u in utterances:
        if out and out[-1].get("speaker") == u.get("speaker"):
            out[-1]["text"] = str(out[-1].get("text") or "") + str(u.get("text") or "")
            out[-1]["end_ms"] = u.get("end_ms")
        else:
            out.append(dict(u))
    return out


def record_speakers(record: dict[str, Any]) -> list[dict[str, Any]]:
    """记录里现存的说话人（按发言时长排序），带用户改的名字。"""
    names = record.get("names") or {}
    agg: dict[Any, dict[str, Any]] = {}
    for u in record.get("utterances") or []:
        spk = u.get("speaker")
        row = agg.setdefault(
            spk, {"id": spk, "label": speaker_label(spk, names), "turns": 0, "ms": 0, "named": bool(names.get(str(spk)))}
        )
        row["turns"] += 1
        row["ms"] += max(0, int(u.get("end_ms") or 0) - int(u.get("begin_ms") or 0))
    rows = list(agg.values())
    for r in rows:
        r["seconds"] = round(r["ms"] / 1000, 1)
    rows.sort(key=lambda r: r["ms"], reverse=True)
    return rows


def format_record(record: dict[str, Any], *, with_time: bool = True, mark_uncertain: bool = True) -> str:
    """按记录渲染正文（用上用户填的名字）。"""
    return format_transcript(
        record.get("utterances") or [],
        with_time=with_time,
        mark_uncertain=mark_uncertain,
        names=record.get("names") or {},
    )


def apply_edit(record: dict[str, Any], op: str, **kw: Any) -> dict[str, Any]:
    """执行一次人工改判，返回新记录。

    支持的操作：
      ``rename``   ``speaker`` + ``name``     给编号填真名（空名字 = 取消）
      ``merge``    ``source`` + ``target``    把 source 的所有发言并进 target
      ``reassign`` ``index`` + ``speaker``    把某一段改判给某个发言人
      ``split``    ``index``                  把某一段拆成一个新发言人
      ``speaker_of`` ``index``                只查不改（内部用）
      ``reset``                               恢复原始分离结果
    """
    rec = dict(record)  # 保留 id / scene / updated 这类记录级字段，编辑只管这几项
    rec.update(
        {
            "utterances": [dict(u) for u in (record.get("utterances") or [])],
            "names": dict(record.get("names") or {}),
            "original": [dict(u) for u in (record.get("original") or record.get("utterances") or [])],
            "next_speaker": int(record.get("next_speaker") or 0),
            "summary": str(record.get("summary") or ""),
        }
    )
    items = rec["utterances"]

    if op == "rename":
        key = str(kw.get("speaker"))
        name = str(kw.get("name") or "").strip()
        if name:
            rec["names"][key] = name[:24]
        else:
            rec["names"].pop(key, None)

    elif op == "merge":
        src, dst = kw.get("source"), kw.get("target")
        if src == dst:
            raise ValueError("不能把自己合并到自己")
        if not any(u.get("speaker") == src for u in items):
            raise ValueError(f"没有 {speaker_label(src, rec['names'])} 这个发言人")
        for u in items:
            if u.get("speaker") == src:
                u["speaker"] = dst
        # 名字跟着走：目标没名字、来源有名字时，用来源的名字
        src_name = rec["names"].pop(str(src), "")
        if src_name and not rec["names"].get(str(dst)):
            rec["names"][str(dst)] = src_name
        rec["utterances"] = _remerge(items)

    elif op == "reassign":
        idx = int(kw.get("index", -1))
        if not 0 <= idx < len(items):
            raise ValueError(f"没有第 {kw.get('index')} 段")
        speaker = kw.get("speaker")
        if speaker == "new":
            speaker = rec["next_speaker"]
            rec["next_speaker"] += 1
        items[idx]["speaker"] = speaker
        rec["utterances"] = _remerge(items)

    elif op == "split":
        idx = int(kw.get("index", -1))
        if not 0 <= idx < len(items):
            raise ValueError(f"没有第 {kw.get('index')} 段")
        items[idx]["speaker"] = rec["next_speaker"]
        rec["next_speaker"] += 1
        rec["utterances"] = _remerge(items)

    elif op == "reset":
        rec["utterances"] = [dict(u) for u in rec["original"]]
        rec["names"] = {}

    elif op != "speaker_of":
        raise ValueError(f"不认识的操作：{op}")

    return rec
