"""音频分片：长会议不能整段丢给 ASR。

为什么需要：ASR 单次调用有时长上限，一场 1 小时的会议必须分段转写再拼接。

格式支持：
- `.wav` 用标准库 `wave` 直接切，**零外部依赖**
- 其它格式需要 ffmpeg（约定装在 E 盘，见 config.TOOLS_DIR）

没有 ffmpeg 时**不静默失败**：原样返回单个文件，并把原因写在说明里，
由上层决定是提示用户还是照常提交。
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

from .. import config

#: 能被 wave 标准库直接处理的格式
WAVE_EXT = {".wav"}

#: 交给 ffmpeg 处理的格式
FFMPEG_EXT = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma", ".mp4"}


def find_ffmpeg() -> str | None:
    """按 配置路径 -> E 盘 tools 目录 -> PATH 的顺序找 ffmpeg。"""
    if config.FFMPEG_PATH and Path(config.FFMPEG_PATH).is_file():
        return config.FFMPEG_PATH

    for name in ("ffmpeg.exe", "ffmpeg"):
        cand = config.TOOLS_DIR / "ffmpeg" / "bin" / name
        if cand.is_file():
            return str(cand)

    return shutil.which("ffmpeg")


def probe_duration(path: str | Path) -> float | None:
    """读取音频时长（秒）。读不到返回 None。"""
    p = Path(path)
    if p.suffix.lower() in WAVE_EXT:
        try:
            with wave.open(str(p), "rb") as w:
                rate = w.getframerate()
                return w.getnframes() / rate if rate else None
        except (wave.Error, OSError):
            return None

    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        out = subprocess.run(
            [ff, "-i", str(p), "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # ffmpeg 把媒体信息写在 stderr，形如 Duration: 00:12:34.56
    for line in (out.stderr or "").splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            stamp = line.split("Duration:")[1].split(",")[0].strip()
            try:
                h, m, s = stamp.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except ValueError:
                return None
    return None


def _split_wave(src: Path, seconds: int, out_dir: Path) -> list[Path]:
    """用标准库切 wav。保留原采样率与声道数。"""
    with wave.open(str(src), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        per_chunk = max(1, rate * seconds)

        chunks: list[Path] = []
        index = 0
        while True:
            frames = w.readframes(per_chunk)
            if not frames:
                break
            dest = out_dir / f"{src.stem}_part{index:03d}.wav"
            with wave.open(str(dest), "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(width)
                out.setframerate(rate)
                out.writeframes(frames)
            chunks.append(dest)
            index += 1
        return chunks


def _split_ffmpeg(src: Path, seconds: int, out_dir: Path) -> list[Path]:
    """用 ffmpeg 切任意格式。

    先试 `-c copy`（不重编码，快且无损）；**失败则回退重编码**。

    为什么必须回退：`-c copy` 要求容器与编码匹配。实测踩过——用户上传的文件
    扩展名是 `.mp3`，实际内容是 AAC（`Audio: aac (LC) (mp4a)`，16kHz 单声道，
    10 分 45 秒），把 AAC 流直接写进 mp3 容器时 ffmpeg 报
    ``Invalid audio stream. Exactly one MP3 audio stream is required``，
    退出码 -22。而分片失败会导致整段音频送去 ASR，进而触发
    ``The audio is too long`` —— 用户的录音完全转不出来。

    回退时统一转成 16kHz 单声道 mp3：ASR 只需要能听清，
    采样率与声道降下来还能减小上传体积。
    """
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("未找到 ffmpeg")

    def run(codec_args: list[str]) -> subprocess.CompletedProcess:
        # 每次都用独立的输出前缀，避免上一次失败留下的 0 字节残片被当成有效分片
        pattern = out_dir / f"{src.stem}_part%03d{src.suffix}"
        return subprocess.run(
            [
                ff, "-y", "-loglevel", "error",
                "-i", str(src),
                "-f", "segment",
                "-segment_time", str(seconds),
                *codec_args,
                str(pattern),
            ],
            capture_output=True, text=True, timeout=900, check=False,
        )

    def existing() -> list[Path]:
        return [
            p for p in sorted(out_dir.glob(f"{src.stem}_part*{src.suffix}"))
            if p.stat().st_size > 0          # 0 字节残片不算分片
        ]

    def purge() -> None:
        for p in out_dir.glob(f"{src.stem}_part*{src.suffix}"):
            try:
                p.unlink()
            except OSError:
                pass

    purge()
    proc = run(["-c", "copy"])
    chunks = existing()
    if proc.returncode == 0 and chunks:
        return chunks

    # copy 不成立（容器/编码不匹配最常见），清理后重编码重试
    purge()
    proc2 = run(["-c:a", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k"])
    chunks2 = existing()
    if proc2.returncode == 0 and chunks2:
        return chunks2

    purge()
    detail = (proc2.stderr or proc.stderr or "").strip().splitlines()
    raise RuntimeError(
        "ffmpeg 分片失败（copy 与重编码都不成立）："
        + (detail[-1] if detail else f"exit={proc2.returncode}")
    )


def split(
    path: str | Path,
    chunk_seconds: int | None = None,
    out_dir: Path | None = None,
) -> tuple[list[Path], str]:
    """把音频切成若干段。

    返回 `(分片列表, 说明)`。**说明只在降级时非空**——正常切分不打扰上层：
    - 音频短于一段，无需切分 -> 返回 [原文件]，说明为空
    - 正常切分完成 -> 返回各分片，说明为空
    - 格式不支持切分 / 缺 ffmpeg / 切分报错 -> 返回 [原文件]，说明写明原因
    """
    src = Path(path)
    if not src.is_file():
        return [], f"音频不存在：{src}"

    seconds = chunk_seconds or config.ASR_CHUNK_SECONDS
    ext = src.suffix.lower()

    duration = probe_duration(src)
    if duration is not None and duration <= seconds:
        return [src], ""

    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if ext in WAVE_EXT:
        try:
            chunks = _split_wave(src, seconds, out_dir)
        except (wave.Error, OSError) as e:
            return [src], f"wav 分片失败（{e}），已整段提交"
        return (chunks, "") if len(chunks) > 1 else ([src], "")

    if ext in FFMPEG_EXT:
        if not find_ffmpeg():
            return [
                src
            ], "长音频需要分片，但未找到 ffmpeg（约定装在 E:\\AI智能助手\\tools\\ffmpeg），已整段提交"
        try:
            chunks = _split_ffmpeg(src, seconds, out_dir)
        except (OSError, subprocess.SubprocessError, RuntimeError) as e:
            # RuntimeError：_split_ffmpeg 在 copy 与重编码都失败时抛的。
            # 漏掉它会让「分片失败」变成未捕获异常，把整条转写链路炸掉——
            # 这里必须降级为「整段提交 + 写明原因」，由上层决定怎么提示。
            return [src], f"ffmpeg 分片失败（{e}），已整段提交"
        return (chunks, "") if len(chunks) > 1 else ([src], "")

    # 未知格式：交给 ASR 自己判断
    return [src], ""


def cleanup(chunks: list[Path], original: Path) -> None:
    """删掉切出来的临时分片，保留原文件。"""
    for c in chunks:
        if c != original:
            try:
                c.unlink(missing_ok=True)
            except OSError:
                pass
