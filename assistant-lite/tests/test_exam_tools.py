"""拍照解题的工具与确定性路径测试：不需要 API Key。

直接运行：python tests/test_exam_tools.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant_lite import config, session  # noqa: E402
from assistant_lite.agents.exam.agent import ExamAgent  # noqa: E402
from assistant_lite.orchestrator import Orchestrator  # noqa: E402
from assistant_lite.schemas import STATUS_OK, Task  # noqa: E402
from assistant_lite.tools import calc as C  # noqa: E402
from assistant_lite.tools import grids as G  # noqa: E402


def _fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="alite-exam-"))
    config.DATA_DIR = tmp
    config.DB_PATH = tmp / "t.sqlite3"
    config.PROFILE_DIR = tmp / "profiles"
    config.UPLOAD_DIR = tmp / "uploads"
    config.EXPORT_DIR = tmp / "exports"
    session._local = threading.local()
    session._initialised = False
    return tmp


# --------------------------------------------------------------------------- #
# 算术工具
# --------------------------------------------------------------------------- #
def test_calc_basic() -> None:
    assert C.calculate("1+2") == 3
    assert C.calculate("(18+24)*3") == 126
    assert C.calculate("2+3*4") == 14, "应遵守运算优先级"
    assert C.calculate("2**10") == 1024
    assert C.calculate("7%3") == 1
    assert C.calculate("-5+3") == -2
    assert abs(C.calculate("120/(1+0.2)") - 100.0) < 1e-9


def test_calc_rejects_bad_input() -> None:
    for bad in ["__import__('os')", "1+", "abc", "1/0", "2**100", "1 if 1 else 2", "open('x')"]:
        try:
            C.calculate(bad)
        except C.CalcError:
            continue
        raise AssertionError(f"应拒绝：{bad!r}")


def test_calc_length_limit() -> None:
    try:
        C.calculate("1+" * 200 + "1")
    except C.CalcError as e:
        assert "过长" in str(e)
        return
    raise AssertionError("超长表达式应被拒绝")


def test_calc_many() -> None:
    out = C.calculate_many(["1+1", "1/0", "3*3"])
    assert out[0]["result"] == 2
    assert "error" in out[1]
    assert out[2]["result"] == 9


# --------------------------------------------------------------------------- #
# 黑白格工具
# --------------------------------------------------------------------------- #
def test_grids_xor_detected() -> None:
    spec = {
        "rows": [["01", "10", "11"], ["11", "01", "10"], ["10", "11", "?"]],
        "options": {"A": "01", "B": "10", "C": "00", "D": "11"},
    }
    out = G.check_grids(spec)
    assert out["status"] == "checked", out
    xor_cands = [c for c in out["candidates"] if "xor" in c["rule"]]
    assert xor_cands, "应识别出异或规律"
    assert any(c["prediction"] == "01" for c in xor_cands)
    assert any("A" in c["matching_options"] for c in xor_cands)


def test_grids_invalid_inputs() -> None:
    # 两个问号
    assert G.check_grids({"rows": [["0?", "1", "1"], ["1", "0", "1"], ["1", "1", "?"]], "options": {"A": "0"}})["status"] == "invalid"
    # 非 01 字符
    assert G.check_grids({"rows": [["0a", "10", "11"], ["11", "01", "10"], ["10", "11", "?"]], "options": {"A": "01"}})["status"] == "invalid"
    # 长度不一致
    assert G.check_grids({"rows": [["01", "10", "111"], ["11", "01", "10"], ["10", "11", "?"]], "options": {"A": "01"}})["status"] == "invalid"
    # 行数不对
    assert G.check_grids({"rows": [["01", "10", "11"]], "options": {"A": "01"}})["status"] == "invalid"
    # 空输入
    assert G.check_grids(None)["status"] == "not_applicable"


def test_grids_does_not_fabricate_option() -> None:
    """工具可以报出候选规律，但若选项都不匹配，不能硬凑一个答案。"""
    spec = {
        "rows": [["01", "10", "00"], ["11", "01", "11"], ["10", "11", "?"]],
        "options": {"A": "01", "B": "10"},
    }
    out = G.check_grids(spec)
    assert out["status"] == "checked"
    assert not any(c["matching_options"] for c in out["candidates"]), (
        f"不应给出不存在的匹配选项：{out['candidates']}"
    )


# --------------------------------------------------------------------------- #
# Agent 确定性路径
# --------------------------------------------------------------------------- #
def test_exam_pure_arithmetic_no_api() -> None:
    agent = ExamAgent()
    reply = agent.handle(Task(text="计算 (18+24)*3"), {})
    assert reply.status == STATUS_OK
    assert "126" in reply.text, reply.text
    assert reply.action == "calculate"


def test_exam_non_arithmetic_falls_through() -> None:
    """不是纯算术的文本不应被快路径截走（这里只验证快路径没命中）。"""
    assert ExamAgent._pure_arithmetic("这道题选什么", []) is None
    assert ExamAgent._pure_arithmetic("计算 1+2", ["a.png"]) is None, "有图时不走快路径"
    assert ExamAgent._pure_arithmetic("计算 x+1", []) is None, "含变量不走快路径"


def test_exam_routing_by_keyword() -> None:
    _fresh_db()
    o = Orchestrator()
    for text in ["这道题怎么做", "帮我看看图形推理", "行测资料分析这道题"]:
        scene, _, source = o.route(Task(text=text))
        assert scene == "exam", f"{text!r} 应路由到 exam，实际 {scene}"
        assert source == "keyword"


def test_tools_are_wired_into_solver() -> None:
    """_run_tools 应真的执行算式与黑白格，而不是把模型输出原样带过。"""
    draft = {
        "calculations": [{"expression": "120/(1+0.2)"}, {"expression": "1/0"}],
        "binary_grid": {
            "rows": [["01", "10", "11"], ["11", "01", "10"], ["10", "11", "?"]],
            "options": {"A": "01", "B": "10"},
        },
    }
    out = ExamAgent._run_tools(draft)
    assert abs(out["calculations"][0]["result"] - 100.0) < 1e-9
    assert "error" in out["calculations"][1]
    assert out["grid_checks"]["status"] == "checked"


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
