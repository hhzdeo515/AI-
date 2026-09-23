"""移植工具层测试：音频分片与六种格式导出。不需要 API Key、不联网。

音频部分只覆盖 **wav 分支**（标准库 `wave`，零依赖）。mp3/m4a 分支需要 ffmpeg，
在缺少 ffmpeg 的环境上会走「不静默失败」路径，这里也一并验证那条路径。
"""

from __future__ import annotations

import sys
import tempfile
import threading
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lg_assistant import config, store  # noqa: E402
from lg_assistant.tools import audio, export  # noqa: E402


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lgtools-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "app.sqlite3"
    config.EXPORT_DIR = tmp / "exports"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    store._local = threading.local()
    store._initialised = False
    return tmp


def _wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    """生成一段静音 wav，用于验证分片切点与总时长守恒。"""
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return path


# --------------------------------------------------------------------------- #
# 音频分片
# --------------------------------------------------------------------------- #
def test_short_wav_is_not_split() -> None:
    _fresh()
    src = _wav(config.DATA_DIR / "short.wav", 10)
    chunks, note = audio.split(str(src), chunk_seconds=240)
    assert note in ("", None), note
    assert len(chunks) >= 1
    assert Path(chunks[0]).is_file()


def test_long_wav_is_split_and_covers_full_duration() -> None:
    """310 秒音频按 240 秒切 → 应得到多片，且总时长不丢。"""
    _fresh()
    src = _wav(config.DATA_DIR / "long.wav", 310)
    chunks, note = audio.split(str(src), chunk_seconds=240)
    assert len(chunks) >= 2, f"长音频应被切分，实际 {len(chunks)} 片"

    total = 0.0
    for c in chunks:
        assert Path(c).is_file()
        with wave.open(str(c), "rb") as w:  # 同样只接受 str
            total += w.getnframes() / w.getframerate()
    assert abs(total - 310) < 1.0, f"分片总时长应与原音频一致，实际 {total}"


def test_cleanup_removes_chunks_but_keeps_original() -> None:
    _fresh()
    src = _wav(config.DATA_DIR / "c.wav", 300)
    chunks, _ = audio.split(str(src), chunk_seconds=100)
    audio.cleanup(chunks, src)
    assert src.is_file(), "原始音频不能被删掉"
    for c in chunks:
        assert not Path(c).exists(), f"分片应被清理：{c}"


def test_missing_ffmpeg_is_reported_not_silent() -> None:
    """没有 ffmpeg 时必须把原因写进 note，而不是静默失败。

    mp3 无法用标准库切分；这条路径在没装 ffmpeg 的机器上就会走到。
    """
    _fresh()
    src = config.DATA_DIR / "fake.mp3"
    src.write_bytes(b"ID3fake")
    original = config.FFMPEG_PATH
    config.FFMPEG_PATH = str(config.DATA_DIR / "definitely-missing-ffmpeg.exe")
    try:
        chunks, note = audio.split(str(src), chunk_seconds=240)
    finally:
        config.FFMPEG_PATH = original
    assert len(chunks) == 1, "拿不到 ffmpeg 时应原样提交整段"
    assert note, "必须说明为什么没有分片（不能静默）"


def test_unknown_extension_passes_through() -> None:
    _fresh()
    src = config.DATA_DIR / "x.amr"
    src.write_bytes(b"data")
    chunks, note = audio.split(str(src), chunk_seconds=240)
    assert len(chunks) == 1
    assert note


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
def test_export_rejects_bad_format_and_empty_rows() -> None:
    _fresh()
    rows = [{"id": "1", "scene": "meeting", "title": "t", "content": "c", "created": "2026"}]
    for bad in ("exe", "", "zip"):
        try:
            export.export_rows(rows, bad)
        except export.ExportError:
            pass
        else:
            raise AssertionError(f"应拒绝格式：{bad!r}")
    try:
        export.export_rows([], "md")
    except export.ExportError:
        return
    raise AssertionError("空资料应抛 ExportError")


def test_export_all_six_formats_produce_files() -> None:
    _fresh()
    rows = [
        {
            "id": "abc123",
            "scene": "meeting",
            "title": "会议纪要",
            "content": "## 主题\n\n**决策**：继续\n\n| 项 | 值 |\n|---|---|\n| A | 1 |\n",
            "source": "src",
            "created": "2026-09-23T00:00:00Z",
        }
    ]
    for fmt in export.FORMATS:
        path = export.export_rows(rows, fmt)
        assert path.is_file(), fmt
        assert path.stat().st_size > 0, f"{fmt} 导出为空文件"


def test_markdown_export_keeps_content_verbatim() -> None:
    _fresh()
    rows = [{"title": "T", "content": "# 标题\n正文"}]
    path = export.export_rows(rows, "md")
    text = path.read_text(encoding="utf-8")
    assert "# T" in text
    assert "# 标题" in text, "Markdown 导出应保留原始 Markdown"


def test_text_export_strips_markdown_markers() -> None:
    _fresh()
    rows = [{"title": "T", "content": "**加粗**\n`代码`"}]
    text = export.export_rows(rows, "txt").read_text(encoding="utf-8")
    assert "**" not in text
    assert "`" not in text


def test_json_export_round_trips() -> None:
    import json

    _fresh()
    rows = [{"id": "1", "scene": "exam", "title": "题解", "content": "答案 B", "created": "2026"}]
    data = json.loads(export.export_rows(rows, "json").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert data[0]["title"] == "题解"
    assert data[0]["content"] == "答案 B"


def test_csv_export_has_bom_for_excel() -> None:
    """utf-8-sig 才能让 Excel 正确识别中文。"""
    _fresh()
    rows = [{"id": "1", "scene": "meeting", "title": "纪要", "content": "内容", "created": "2026"}]
    raw = export.export_rows(rows, "csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 必须带 UTF-8 BOM"
    assert "纪要" in raw.decode("utf-8-sig")


def test_export_resource_by_id_and_missing() -> None:
    _fresh()
    rid = store.archive("u", "meeting", "纪要", "内容", "src")
    path = export.export_resource("u", rid, "md")
    assert path.is_file()

    try:
        export.export_resource("u", "nope", "md")
    except export.ExportError as e:
        assert "找不到资料" in str(e)
        return
    raise AssertionError("不存在的资料应抛 ExportError")


def test_export_resource_is_owner_isolated() -> None:
    _fresh()
    rid = store.archive("owner-a", "meeting", "纪要", "内容", "src")
    try:
        export.export_resource("owner-b", rid, "md")
    except export.ExportError:
        return
    raise AssertionError("跨 owner 导出必须被拒绝")


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
        except Exception as e:  # noqa: BLE001
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
