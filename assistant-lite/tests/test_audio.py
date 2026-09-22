"""音频分片测试：wav 用标准库，不需要 ffmpeg、不需要 API Key。"""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import threading
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session  # noqa: E402
from assistant_lite.tools import audio  # noqa: E402

RATE = 16000


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-au-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


def _wav(path: Path, seconds: float, rate: int = RATE) -> Path:
    """生成一段正弦音 wav（内容无所谓，只为验证切分逻辑）。"""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(rate * seconds))
        )
        w.writeframes(frames)
    return path


# --------------------------------------------------------------------------- #
def test_probe_duration_wav() -> None:
    tmp = _fresh()
    p = _wav(tmp / "a.wav", 5.0)
    d = audio.probe_duration(p)
    assert d is not None and abs(d - 5.0) < 0.05, d


def test_short_audio_not_split() -> None:
    """短于一段的音频不该被切，说明也应为空（不是异常）。"""
    tmp = _fresh()
    p = _wav(tmp / "short.wav", 3.0)
    chunks, note = audio.split(p, chunk_seconds=240)
    assert chunks == [p]
    assert note == "", note


def test_long_wav_is_split() -> None:
    tmp = _fresh()
    p = _wav(tmp / "long.wav", 7.0)
    chunks, note = audio.split(p, chunk_seconds=2, out_dir=tmp)
    assert len(chunks) == 4, [c.name for c in chunks]
    assert note == "", "正常切分不应产生说明"
    for c in chunks:
        assert c.is_file() and c.stat().st_size > 0
    # 分片时长加起来应接近原时长
    total = sum(audio.probe_duration(c) or 0 for c in chunks)
    assert abs(total - 7.0) < 0.3, total


def test_split_preserves_format_params() -> None:
    tmp = _fresh()
    p = _wav(tmp / "p.wav", 5.0, rate=8000)
    chunks, _ = audio.split(p, chunk_seconds=2, out_dir=tmp)
    assert len(chunks) >= 2
    with wave.open(str(chunks[0]), "rb") as w:
        assert w.getframerate() == 8000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2


def test_cleanup_keeps_original() -> None:
    tmp = _fresh()
    p = _wav(tmp / "c.wav", 5.0)
    chunks, _ = audio.split(p, chunk_seconds=2, out_dir=tmp)
    assert len(chunks) > 1
    audio.cleanup(chunks, p)
    assert p.is_file(), "原文件必须保留"
    for c in chunks:
        assert not c.exists(), f"临时分片应被清掉：{c}"


def test_missing_file() -> None:
    _fresh()
    chunks, note = audio.split("不存在的文件.wav")
    assert chunks == []
    assert "不存在" in note


def test_unknown_extension_passes_through() -> None:
    """未知格式原样返回，交给 ASR 自己判断，不擅自报错。"""
    tmp = _fresh()
    p = tmp / "x.weird"
    p.write_bytes(b"\x00" * 100)
    chunks, note = audio.split(p)
    assert chunks == [p]
    assert note == ""


def test_missing_ffmpeg_degrades_with_reason() -> None:
    """没有 ffmpeg 时不能静默失败，必须说明原因。"""
    tmp = _fresh()
    p = tmp / "a.mp3"
    p.write_bytes(b"ID3" + b"\x00" * 100)

    original = audio.find_ffmpeg
    audio.find_ffmpeg = lambda: None
    try:
        chunks, note = audio.split(p)
    finally:
        audio.find_ffmpeg = original

    assert chunks == [p], "降级时应原样提交"
    assert "ffmpeg" in note, note
    assert "E:" in note or "tools" in note, "说明里应给出期望的安装位置"


def test_find_ffmpeg_returns_path_or_none() -> None:
    found = audio.find_ffmpeg()
    assert found is None or Path(found).is_file(), found


# --------------------------------------------------------------------------- #
def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    raise SystemExit(_run_all())
