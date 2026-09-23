"""Contract tests for the generated Dify DSL. No Dify, no network, no API key.

These lock the structural invariants that were expensive to discover:
  * question-classifier dispatches on `class_id` as sourceHandle, so every
    outgoing edge must use the class id (a generic "source" handle never fires);
  * the Code node's device gate must not let a low-confidence device guess win;
  * `train_pain` is never gated on confidence.

Run: python tests/test_dsl_contract.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCENES = ["meeting", "exam", "fitness", "resource", "general"]


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_dify_dsl", ROOT / "gen_dify_dsl.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GEN = _load_generator()
DOC = GEN.build()
GRAPH = DOC["workflow"]["graph"]
NODES = {n["id"]: n for n in GRAPH["nodes"]}
EDGES = GRAPH["edges"]


# --------------------------------------------------------------------------- #
# DSL envelope
# --------------------------------------------------------------------------- #
def test_envelope_is_importable_shape() -> None:
    assert DOC["kind"] == "app"
    assert DOC["version"] == "0.7.0", "must match the target Dify instance DSL version"
    assert DOC["app"]["mode"] == "workflow"
    assert DOC["app"]["name"]
    assert DOC["dependencies"], "model provider plugins must be declared"


def test_every_node_has_required_envelope_fields() -> None:
    for n in GRAPH["nodes"]:
        for key in ("id", "type", "position", "positionAbsolute", "data"):
            assert key in n, f"{n.get('id')} missing {key}"
        assert n["data"].get("type"), f"{n['id']} missing data.type"
        assert n["data"].get("title"), f"{n['id']} missing data.title"


def test_every_edge_has_matching_node_and_data_block() -> None:
    for e in EDGES:
        assert e["source"] in NODES, f"edge source {e['source']} not a node"
        assert e["target"] in NODES, f"edge target {e['target']} not a node"
        assert e["data"]["sourceType"] == NODES[e["source"]]["data"]["type"]
        assert e["data"]["targetType"] == NODES[e["target"]]["data"]["type"]


# --------------------------------------------------------------------------- #
# Routing invariant -- the bug that silently stopped the graph
# --------------------------------------------------------------------------- #
def test_classifier_edges_use_class_id_as_source_handle() -> None:
    """Regression: graphon sets edge_source_handle = class_id for a
    question-classifier. An edge whose sourceHandle is not a class id is never
    taken, and the workflow silently ends with empty outputs."""
    classifier = NODES["scene_router"]
    class_ids = {c["id"] for c in classifier["data"]["classes"]}
    out_edges = [e for e in EDGES if e["source"] == "scene_router"]

    assert out_edges, "classifier must dispatch somewhere"
    for e in out_edges:
        assert e["sourceHandle"] in class_ids, (
            f"sourceHandle {e['sourceHandle']!r} is not a classifier class id; "
            "the branch will never fire"
        )
    assert {e["sourceHandle"] for e in out_edges} == class_ids, (
        "every declared class needs an outgoing edge, otherwise that class "
        "yields an empty answer"
    )


def test_each_scene_has_an_agent_and_an_answer() -> None:
    for scene in SCENES:
        agent = NODES.get(f"agent_{scene}")
        answer = NODES.get(f"answer_{scene}")
        assert agent is not None, f"missing agent node for {scene}"
        assert answer is not None, f"missing answer node for {scene}"
        assert agent["data"]["type"] == "llm"
        assert answer["data"]["type"] == "answer"
        assert answer["data"]["answer"] == "{{#agent_" + scene + ".text#}}"
        assert agent["data"]["prompt_template"][0]["role"] == "system"


def test_no_variable_aggregator_between_agents_and_answers() -> None:
    """Only one branch runs per request, so an aggregator would deadlock
    waiting for the other four branches."""
    for scene in SCENES:
        direct = [
            e
            for e in EDGES
            if e["source"] == f"agent_{scene}" and e["target"] == f"answer_{scene}"
        ]
        assert direct, f"agent_{scene} must feed answer_{scene} directly"


def test_scene_prompts_forbid_unsupported_claims() -> None:
    fitness = NODES["agent_fitness"]["data"]["prompt_template"][0]["text"]
    for banned in ("心率", "训练负荷", "恢复度"):
        assert banned in fitness, (
            f"fitness prompt must explicitly forbid {banned} "
            "(no PPG sensor on the device)"
        )
    meeting = NODES["agent_meeting"]["data"]["prompt_template"][0]["text"]
    assert "禁止补全" in meeting, "meeting prompt must forbid fabricating constraints"
    resource = NODES["agent_resource"]["data"]["prompt_template"][0]["text"]
    assert "不要编造" in resource, "resource prompt must forbid inventing resources"


def test_llm_nodes_separate_reasoning_from_answer() -> None:
    """Reasoning tokens must not leak into `text`, which is what gets spoken."""
    for scene in SCENES:
        data = NODES[f"agent_{scene}"]["data"]
        assert data.get("reasoning_format") == "separated"
        assert data["model"]["provider"] and data["model"]["name"]


def test_agents_use_a_provider_that_is_not_overdue() -> None:
    """回归：火山方舟账户欠费后返回 403 AccountOverdueError，工作流整体失败。

    实测该错误发生在 Dify 的插件节点内，表现为 status=failed 加一大段
    PluginInvokeError。切换到 deepseek 后恢复。
    """
    for scene in SCENES:
        provider = NODES[f"agent_{scene}"]["data"]["model"]["provider"]
        assert "volcengine" not in provider, (
            f"{scene} 仍在使用火山引擎（该账户欠费，会 403）"
        )


# --------------------------------------------------------------------------- #
# Device gate -- executed as real code, not asserted as a string
# --------------------------------------------------------------------------- #
def _gate():
    code = NODES["normalize"]["data"]["code"]
    ns: dict = {}
    exec(compile(code, "<normalize>", "exec"), ns)  # noqa: S102 - test fixture
    return ns["main"]


def test_high_confidence_device_intent_is_honoured() -> None:
    out = _gate()("膝盖疼", "train_pain", "0.2", "")
    assert out["scene_hint"] == "fitness", "pain report is never confidence-gated"
    assert out["device_trusted"] == "yes"


def test_low_confidence_device_intent_is_not_trusted_but_still_shown() -> None:
    out = _gate()("我膝盖有点疼，练不下去了", "solve", "0.95", "")
    # device said exam with high confidence, but 'solve' maps to exam and the
    # cloud hint is empty -> the scaffold stays visible for the classifier
    assert out["device_scene"] == "exam"
    assert "device_intent=solve" in out["query"]

    weak = _gate()("随便说点什么", "solve", "0.3", "")
    assert weak["device_trusted"] == "no"
    assert weak["scene_hint"] == "unknown"


def test_cloud_routing_hint_wins_over_device_guess() -> None:
    out = _gate()("文本", "solve", "0.95", "fitness")
    assert out["scene_hint"] == "fitness"
    assert "cloud_routing_hint=fitness" in out["query"]


def test_unknown_intent_and_bad_confidence_do_not_crash() -> None:
    for intent, conf in [("nonsense", "abc"), ("", ""), ("solve", "-1")]:
        out = _gate()("文本", intent, conf, "not-a-scene")
        assert out["scene_hint"] == "unknown", (intent, conf)
        assert out["query"]


def test_empty_input_still_produces_a_query() -> None:
    out = _gate()("", "", "", "")
    assert out["query"] == "(no text)"


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
