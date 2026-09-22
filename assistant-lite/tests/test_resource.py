"""资料查询与导出场景测试：零 token，不需要 API Key。"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session  # noqa: E402
from assistant_lite.agents.resource import agent as RA  # noqa: E402
from assistant_lite.orchestrator import Orchestrator  # noqa: E402
from assistant_lite.schemas import STATUS_NEED_INPUT, STATUS_OK, Task  # noqa: E402

SAMPLE = "# 会议纪要\n\n**主题**：本机版接入\n\n- 张三负责接口文档\n"


def _fresh() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-res-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


def _seed(owner: str = "u", n: int = 1, scene: str = "meeting") -> list[str]:
    return [
        session.archive(owner, scene, f"{scene} 资料 {i}", SAMPLE, "来源") for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# 解析器
# --------------------------------------------------------------------------- #
def test_parse_format() -> None:
    assert RA.parse_format("导出成 Word") == "docx"
    assert RA.parse_format("给我 PDF") == "pdf"
    assert RA.parse_format("转成 markdown") == "md"
    assert RA.parse_format("存成 excel") == "csv"
    assert RA.parse_format("导出") == ""
    # 长词优先，"docx" 不能被 "doc" 抢先
    assert RA.parse_format("导出 docx") == "docx"


def test_parse_target() -> None:
    assert RA.parse_target("导出刚才的纪要") == "meeting"
    assert RA.parse_target("把题解导出来") == "exam"
    assert RA.parse_target("导出训练总结") == "fitness"
    assert RA.parse_target("导出最新一份") is None


def test_parse_id_prefix() -> None:
    assert RA.parse_id_prefix("导出 6e13c22b 成 pdf") == "6e13c22b"
    assert RA.parse_id_prefix("导出 abc123def 成 md") == "abc123def"
    # 纯数字不算资料 ID（可能是组数/年份）
    assert RA.parse_id_prefix("做了 4 组 10 次") == ""
    assert RA.parse_id_prefix("导出最新一份") == ""
    # 太短的十六进制不算
    assert RA.parse_id_prefix("导出 abc") == ""


def test_wants_export() -> None:
    assert RA.wants_export("导出成 Word")
    assert RA.wants_export("下载刚才的纪要")
    assert RA.wants_export("转成 pdf")
    assert not RA.wants_export("我有哪些资料")
    assert not RA.wants_export("查找会议纪要")


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
def test_export_intent_beats_other_scene_keywords() -> None:
    """回归：'导出刚才的纪要成 Word' 同时含 meeting 的'纪要'，必须路由到 resource。

    注意：编排层对关键词命中不填 action，动作由 Agent 内部推断，
    所以这里只断言场景与来源。
    """
    _fresh()
    o = Orchestrator()
    for text in ("导出刚才的纪要成 Word", "把会议纪要转成 PDF", "下载我的训练总结"):
        scene, _, source = o.route(Task(text=text))
        assert scene == "resource", f"{text!r} 应路由到 resource，实际 {scene}"
        assert source == "keyword", f"{text!r} 来源应为 keyword，实际 {source}"
        assert RA.wants_export(text)


def test_plain_summarize_still_goes_to_meeting() -> None:
    """反向确认：不带导出意图的'生成会议纪要'仍归 meeting。"""
    _fresh()
    o = Orchestrator()
    scene, _, _ = o.route(Task(text="生成会议纪要"))
    assert scene == "meeting"


def test_list_intent_routing() -> None:
    _fresh()
    o = Orchestrator()
    scene, _, source = o.route(Task(text="我有哪些资料"))
    assert scene == "resource"
    assert source == "keyword"


def test_event_routing_for_resource() -> None:
    _fresh()
    o = Orchestrator()
    scene, action, source = o.route(Task(text="x", event={"semantic_action": "export_resource"}))
    assert (scene, action, source) == ("resource", "export", "event")


# --------------------------------------------------------------------------- #
# 行为
# --------------------------------------------------------------------------- #
def test_list_empty() -> None:
    _fresh()
    o = Orchestrator()
    r = o.handle(Task(text="我有哪些资料", owner="u", session_id="s", request_id="r1"))
    assert r.scene == "resource"
    assert r.action == "list", f"列表意图不应被标成 export（实际 {r.action}）"
    assert r.status == STATUS_NEED_INPUT
    assert "还没有归档资料" in r.text


def test_list_shows_items() -> None:
    _fresh()
    _seed("u", 2)
    o = Orchestrator()
    r = o.handle(Task(text="我有哪些资料", owner="u", session_id="s", request_id="r2"))
    assert r.status == STATUS_OK
    assert "共找到 2 份资料" in r.text
    assert "meeting 资料 0" in r.text


def test_export_empty() -> None:
    _fresh()
    o = Orchestrator()
    r = o.handle(Task(text="导出最新一份成 Word", owner="u", session_id="s", request_id="r3"))
    assert r.status == STATUS_NEED_INPUT
    assert "没有找到可导出的资料" in r.text


def test_export_latest_single() -> None:
    _fresh()
    _seed("u", 1)
    o = Orchestrator()
    r = o.handle(Task(text="导出刚才的纪要成 Word", owner="u", session_id="s", request_id="r4"))
    assert r.status == STATUS_OK, r.text
    assert r.action == "export"
    assert r.artifacts and r.artifacts[0]["kind"] == "file"
    path = Path(r.artifacts[0]["path"])
    assert path.is_file() and path.suffix == ".docx"


def test_export_asks_when_ambiguous() -> None:
    """多份候选且用户没说清要哪份 -> 反问，而不是随便挑一份。"""
    _fresh()
    _seed("u", 3)
    o = Orchestrator()
    r = o.handle(Task(text="导出成 PDF", owner="u", session_id="s", request_id="r5"))
    assert r.status == STATUS_NEED_INPUT
    assert "请指定要导出哪一份" in r.text


def test_export_by_id_prefix() -> None:
    _fresh()
    ids = _seed("u", 3)
    target = ids[1]
    o = Orchestrator()
    r = o.handle(
        Task(
            text=f"导出 {target[:8]} 成 pdf",
            owner="u",
            session_id="s",
            request_id="r6",
        )
    )
    assert r.status == STATUS_OK, r.text
    assert Path(r.artifacts[0]["path"]).suffix == ".pdf"
    assert r.artifacts[0]["id"] == target


def test_export_unknown_id_prefix() -> None:
    _fresh()
    _seed("u", 1)
    o = Orchestrator()
    r = o.handle(
        Task(text="导出 deadbeef 成 pdf", owner="u", session_id="s", request_id="r7")
    )
    assert r.status == STATUS_NEED_INPUT
    assert "deadbeef" in r.text


def test_export_filtered_by_scene() -> None:
    """指定了场景时不因多份候选而反问。"""
    _fresh()
    _seed("u", 1, "meeting")
    _seed("u", 2, "fitness")
    o = Orchestrator()
    r = o.handle(
        Task(text="把训练总结导出成 markdown", owner="u", session_id="s", request_id="r8")
    )
    assert r.status == STATUS_OK, r.text
    assert Path(r.artifacts[0]["path"]).suffix == ".md"


def test_export_does_not_leak_across_owners() -> None:
    _fresh()
    ids = _seed("alice", 1)
    o = Orchestrator()
    r = o.handle(
        Task(text=f"导出 {ids[0][:8]} 成 pdf", owner="bob", session_id="s", request_id="r9")
    )
    assert r.status == STATUS_NEED_INPUT, "别人的资料 ID 不应能导出"


def test_all_formats_exportable_via_nl() -> None:
    _fresh()
    _seed("u", 1)
    o = Orchestrator()
    cases = {
        "导出成 Word": ".docx",
        "导出成 PDF": ".pdf",
        "导出成 markdown": ".md",
        "导出成 txt": ".txt",
        "导出成 json": ".json",
        "导出成 excel": ".csv",
    }
    for i, (text, ext) in enumerate(cases.items()):
        r = o.handle(Task(text=text, owner="u", session_id="s", request_id=f"f{i}"))
        assert r.status == STATUS_OK, f"{text}: {r.text}"
        assert Path(r.artifacts[0]["path"]).suffix == ext, text


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
