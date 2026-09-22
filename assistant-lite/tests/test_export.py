"""导出功能测试：md/txt/json/csv/docx/pdf。不需要 API Key。"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session  # noqa: E402
from assistant_lite.tools import export as EX  # noqa: E402

SAMPLE_MD = """# 会议纪要

**主题**：本机版接入

## 讨论要点
- 张三负责接口文档
- 李四负责前端

| 项 | 负责人 | 截止 |
| --- | --- | --- |
| 接口文档 | 张三 | 周五 |
| 联调 | 李四 | 下周三 |

结论：先做本机版。
"""


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-exp-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


def _rows() -> list[dict]:
    return [
        {
            "id": "abc123",
            "scene": "meeting",
            "title": "会议纪要 2026-09-22",
            "content": SAMPLE_MD,
            "source": "张三：我负责接口文档。",
            "created": "2026-09-22T06:00:00+00:00",
        }
    ]


# --------------------------------------------------------------------------- #
# 文本解析
# --------------------------------------------------------------------------- #
def test_clean_text() -> None:
    assert EX.clean_text("**加粗**") == "加粗"
    assert EX.clean_text("`code`") == "code"
    assert "×" in EX.clean_text(r"3 \times 4")
    assert "\\[" not in EX.clean_text(r"\[x=1\]")


def test_blocks_parse() -> None:
    out = list(EX.blocks(SAMPLE_MD))
    kinds = [k for k, _ in out]
    assert "heading" in kinds
    assert "table" in kinds
    assert "paragraph" in kinds
    tables = [v for k, v in out if k == "table"]
    assert len(tables) == 1
    assert tables[0][0] == ["项", "负责人", "截止"]
    assert len(tables[0]) == 3


def test_render_markdown_joins() -> None:
    md = EX.render_markdown(_rows())
    assert md.startswith("# 会议纪要 2026-09-22")
    assert "张三" in md


def test_render_text_strips_markers() -> None:
    t = EX.render_text(_rows())
    assert "**" not in t
    assert "【讨论要点】" in t
    assert "张三" in t


# --------------------------------------------------------------------------- #
# 各格式导出
# --------------------------------------------------------------------------- #
def test_export_md_txt() -> None:
    _fresh()
    for fmt in ("md", "txt"):
        p = EX.export_rows(_rows(), fmt)
        assert p.is_file() and p.suffix == f".{fmt}"
        text = p.read_text(encoding="utf-8")
        assert "张三" in text


def test_export_json() -> None:
    _fresh()
    p = EX.export_rows(_rows(), "json")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["title"] == "会议纪要 2026-09-22"
    assert "content" in data[0]


def test_export_csv_has_bom_and_header() -> None:
    _fresh()
    p = EX.export_rows(_rows(), "csv")
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 应带 UTF-8 BOM 以便 Excel 识别中文"
    text = raw.decode("utf-8-sig")
    assert "资料ID" in text and "标题" in text
    assert "张三" in text


def test_export_docx_is_valid_and_has_chinese() -> None:
    _fresh()
    p = EX.export_rows(_rows(), "docx")
    assert p.is_file() and p.stat().st_size > 1000
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        assert "word/document.xml" in names, "docx 应是合法 OOXML 包"
        doc = z.read("word/document.xml").decode("utf-8")
    assert "会议纪要" in doc
    assert "张三" in doc
    # 表格应被转成真实 Word 表格
    assert "<w:tbl>" in doc


def test_export_pdf_is_valid_and_has_chinese() -> None:
    _fresh()
    p = EX.export_rows(_rows(), "pdf")
    raw = p.read_bytes()
    assert raw.startswith(b"%PDF"), "PDF 应有正确文件头"
    assert p.stat().st_size > 1000
    assert b"%%EOF" in raw[-2048:], "PDF 应正常收尾"


def test_export_all_formats_smoke() -> None:
    _fresh()
    for fmt in EX.FORMATS:
        p = EX.export_rows(_rows(), fmt)
        assert p.is_file() and p.stat().st_size > 0, f"{fmt} 导出为空"


# --------------------------------------------------------------------------- #
# 边界
# --------------------------------------------------------------------------- #
def test_unsupported_format() -> None:
    _fresh()
    for bad in ("xlsx", "exe", "", "doc"):
        try:
            EX.export_rows(_rows(), bad)
        except EX.ExportError as e:
            assert "不支持" in str(e)
            continue
        raise AssertionError(f"应拒绝格式：{bad!r}")


def test_empty_rows_rejected() -> None:
    _fresh()
    try:
        EX.export_rows([], "md")
    except EX.ExportError as e:
        assert "没有可导出" in str(e)
        return
    raise AssertionError("空资料应被拒绝")


def test_format_case_and_dot_tolerated() -> None:
    _fresh()
    p = EX.export_rows(_rows(), ".MD")
    assert p.suffix == ".md"


def test_export_resource_by_id() -> None:
    _fresh()
    rid = session.archive("u", "meeting", "会议纪要", SAMPLE_MD, "原始转写")
    p = EX.export_resource("u", rid, "docx")
    assert p.is_file()
    try:
        EX.export_resource("u", "not-exist", "md")
    except EX.ExportError as e:
        assert "找不到资料" in str(e)
        return
    raise AssertionError("不存在的资料 ID 应报错")


def test_export_does_not_leak_across_owners() -> None:
    """别人的资料 ID 导不出来。"""
    _fresh()
    rid = session.archive("alice", "meeting", "alice 的纪要", SAMPLE_MD)
    try:
        EX.export_resource("bob", rid, "md")
    except EX.ExportError:
        return
    raise AssertionError("跨 owner 导出必须被拒绝")


def test_multiple_rows_pagination() -> None:
    _fresh()
    rows = _rows() * 3
    p = EX.export_rows(rows, "md")
    text = p.read_text(encoding="utf-8")
    assert text.count("# 会议纪要 2026-09-22") == 3


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
