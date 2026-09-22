"""语音播报文本测试：纯规则，不需要 API Key。

核心约束：**播报语兜底绝不能调用模型**。否则每个长回复都会多一次调用，
而且测试会意外打到真实 API。
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session, speech  # noqa: E402
from assistant_lite.orchestrator import Orchestrator  # noqa: E402
from assistant_lite.schemas import Task  # noqa: E402


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-sp-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
def test_to_plain_strips_markdown() -> None:
    src = (
        "# 会议纪要\n\n"
        "**主题**：一期计划\n\n"
        "- 张三负责 `接口文档`\n"
        "1. 李四负责前端\n"
        "> 备注：待确认\n\n"
        "| 项 | 人 |\n| --- | --- |\n| 接口 | 张三 |\n\n"
        "---\n"
        "结束。"
    )
    out = speech.to_plain(src)
    for bad in ("#", "**", "`", "|", "---", ">"):
        assert bad not in out, f"{bad!r} 应被清掉：{out}"
    assert "一期计划" in out
    assert "张三" in out


def test_truncate_short_text_untouched() -> None:
    assert speech.truncate("第 2 组已记录。") == "第 2 组已记录。"


def test_truncate_cuts_at_sentence_end() -> None:
    text = "第一句话讲完了。第二句话也很长需要被截掉因为它超出了限制范围。" * 3
    out = speech.truncate(text, 40)
    assert len(out) <= 41
    assert out.endswith("。"), out


def test_truncate_hard_cut_without_punctuation() -> None:
    out = speech.truncate("一二三四五六七八九十" * 20, 20)
    assert len(out) <= 21
    assert out.endswith("…"), out


def test_split_speech_with_marker() -> None:
    body, sp = speech.split_speech("正文内容\n\n<<<SPEECH>>>\n播报语在这")
    assert body == "正文内容"
    assert sp == "播报语在这"


def test_split_speech_without_marker() -> None:
    body, sp = speech.split_speech("只有正文")
    assert body == "只有正文"
    assert sp == ""


def test_resolve_uses_marker() -> None:
    body, sp = speech.resolve("正文\n<<<SPEECH>>>\n一句话播报")
    assert body == "正文"
    assert sp == "一句话播报"


def test_resolve_falls_back_to_truncate() -> None:
    src = "**答案：B** " + "解析内容" * 40
    body, sp = speech.resolve(src)
    assert body == src, "正文应原样返回，Markdown 交给渲染层"
    assert "**" not in sp, "播报语必须是纯文本"
    assert len(sp) <= 81
    assert "答案" in sp


# --------------------------------------------------------------------------- #
# 编排层兜底
# --------------------------------------------------------------------------- #
def test_orchestrator_fills_speech_without_calling_model() -> None:
    """兜底必须是纯规则的——测试里绝不能因此打真实 API。"""
    _fresh()
    from assistant_lite import llm as L

    original = L.chat
    calls: list[int] = []

    def spy(*a, **kw):
        calls.append(1)
        raise AssertionError("播报语兜底不应调用模型")

    L.chat = spy
    try:
        o = Orchestrator()
        r = o.handle(
            Task(text="开始会议记录", owner="u", session_id="s", request_id="sp1")
        )
    finally:
        L.chat = original

    assert r.speech, "必须给出播报语"
    assert not calls, "兜底压缩不能调模型"


def test_meeting_summary_speech_from_model_marker() -> None:
    _fresh()
    from assistant_lite import llm as L

    original = L.chat
    L.chat = lambda *a, **kw: (
        "# 会议纪要\n\n**主题**：一期计划\n\n- 张三负责接口\n\n"
        "<<<SPEECH>>>\n纪要已生成，共 3 条要点、2 项行动项。"
    )
    try:
        o = Orchestrator()
        o.handle(Task(text="开始会议记录", owner="u", session_id="s", request_id="m1"))
        o.handle(Task(text="张三：我负责接口文档。", owner="u", session_id="s", request_id="m2"))
        r = o.handle(Task(text="生成会议纪要", owner="u", session_id="s", request_id="m3"))
    finally:
        L.chat = original

    assert speech.SPEECH_MARK not in r.text, "标记不能留在正文里"
    assert "会议纪要" in r.text
    assert r.speech == "纪要已生成，共 3 条要点、2 项行动项。"


def test_meeting_summary_speech_falls_back_when_model_omits_marker() -> None:
    """模型没按格式给播报语时，也要有兜底，且不能报错。"""
    _fresh()
    from assistant_lite import llm as L

    original = L.chat
    L.chat = lambda *a, **kw: "# 会议纪要\n\n" + "讨论要点若干。" * 40
    try:
        o = Orchestrator()
        o.handle(Task(text="开始会议记录", owner="u", session_id="s", request_id="f1"))
        o.handle(Task(text="张三：我负责接口。", owner="u", session_id="s", request_id="f2"))
        r = o.handle(Task(text="生成会议纪要", owner="u", session_id="s", request_id="f3"))
    finally:
        L.chat = original

    assert r.status == "ok"
    assert r.speech, "必须兜底出播报语"
    assert len(r.speech) <= 81
    assert speech.SPEECH_MARK not in r.text


def test_pain_report_speech_keeps_safety_content() -> None:
    """安全相关的播报语不能被通用截断截掉就医提示。"""
    _fresh()
    o = Orchestrator()
    common = {"owner": "u", "session_id": "s"}
    o.handle(Task(text="开始深蹲", event={"semantic_action": "start_exercise"},
                  request_id="p1", **common))
    r = o.handle(Task(text="膝盖疼", event={"semantic_action": "pain_report"},
                      request_id="p2", **common))

    assert "暂停" in r.speech, r.speech
    assert "膝盖" in r.speech, r.speech
    assert "就医" in r.speech, r.speech
    assert len(r.speech) <= 100, r.speech


def test_set_done_speech_is_short() -> None:
    _fresh()
    o = Orchestrator()
    common = {"owner": "u", "session_id": "s"}
    o.handle(Task(text="开始卧推 3组10次 60公斤",
                  event={"semantic_action": "start_exercise"}, request_id="d1", **common))
    r = o.handle(Task(text="做完一组", event={"semantic_action": "set_done"},
                      request_id="d2", **common))
    assert r.speech
    assert len(r.speech) <= 81
    assert "卧推" in r.speech


def test_exam_speech_always_mentions_answer() -> None:
    """终审没给 speech 时，至少要保证答案被念出来。"""
    import json as _json

    from PIL import Image

    from assistant_lite import llm as L

    tmp = _fresh()
    img = tmp / "q.png"
    Image.new("RGB", (8, 8), "white").save(img)

    draft = {"module": "数量关系", "answerable": True, "candidate": "B",
             "evidence": [], "option_checks": [], "uncertainties": [],
             "calculations": [], "binary_grid": None}
    final = {"module": "数量关系", "answerable": True, "answer": "B) 4",
             "explanation": "2x=8，x=4", "review_notes": "已核对", "needed": ""}

    original = L.vision

    def fake(prompt, images, system="", **kw):
        s = system or ""
        if "观察员" in s:
            return "方程题"
        if "初解节点" in s:
            return _json.dumps(draft, ensure_ascii=False)
        return _json.dumps(final, ensure_ascii=False)

    L.vision = fake
    try:
        o = Orchestrator()
        r = o.handle(Task(text="解这道题", owner="u", session_id="s",
                          request_id="ex1", files=[str(img)]))
    finally:
        L.vision = original

    assert "B) 4" in r.speech, r.speech
    assert len(r.speech) <= 81


def test_resource_speech_is_one_line() -> None:
    _fresh()
    o = Orchestrator()
    r = o.handle(Task(text="我有哪些资料", owner="u", session_id="s", request_id="r1"))
    assert r.speech
    assert len(r.speech) <= 81


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
