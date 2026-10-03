"""Build synthetic event-contract fixtures with independent expected outcomes.

No SDK, network or model calls.  Expectations below are hand authored; the
evaluator is used only by --verify, never to generate expectedOutput.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from evaluation.north_star_tracking import compare_expected, evaluate_case


def event(event_id: str, name: str, elapsed: int, question: str | None = None, **fields: Any) -> dict[str, Any]:
    value = {"event_id": event_id, "event_name": name, "elapsed_ms": elapsed,
             "source": "synthetic_fixture", "schema_version": 1}
    if question is not None:
        value["question_id"] = question
    value.update(fields)
    return value


def session(method: str, states: tuple[str, ...] = ("adopted",), user: str = "user-1",
            pair: str = "pair-1", budget: int = 1000) -> dict[str, Any]:
    prefix = f"{user}-{pair}-{method}"
    events = [event(f"{prefix}-start", "test_session_started", 0)]
    for i, state in enumerate(states, 1):
        qid, base = f"question-{i}", 100 * i
        events.append(event(f"{prefix}-q{i}-start", "exam_attempt_started", base, qid,
                            attempt_id=f"attempt-{i}-1", attempt_type="first"))
        if state == "adopted":
            if method == "product":
                events.append(event(f"{prefix}-q{i}-present", "exam_result_presented", base + 10,
                                    qid, result_version="result-1", success=True))
            events.append(event(f"{prefix}-q{i}-answer", "exam_answer_recorded", base + 20,
                                qid, answer_version="answer-1", state_seq=1,
                                result_version="result-1" if method == "product" else None))
            events.append(event(f"{prefix}-q{i}-adopt", "exam_adoption_changed", base + 30,
                                qid, answer_version="answer-1", state_seq=2,
                                adoption_status="adopted", confirmation_source="synthetic_test_observer"))
        else:
            events.append(event(f"{prefix}-q{i}-state", "exam_adoption_changed", base + 30,
                                qid, answer_version=None, state_seq=1, adoption_status=state,
                                confirmation_source="synthetic_test_observer"))
    events.append(event(f"{prefix}-end", "test_session_ended", budget, complete=True))
    return {"user_id": user, "pair_id": pair, "method": method, "budget_ms": budget,
            "complete": True, "clock_synchronized": True, "events": events}


def group(m: int, q: int | None, c: int | None, x: float | None,
          r: float | None, a: float | None, status: str = "complete") -> dict[str, Any]:
    return {"status": status, "M": m, "Q": q, "C": c, "per_user_attempts": x,
            "adoption_rate": r, "per_user_adopted": a}


G_ONE = group(1, 1, 1, 1.0, 1.0, 1.0)
G_NONE = group(1, 1, 0, 1.0, 0.0, 0.0)
G_ZERO = group(1, 0, 0, 0.0, None, 0.0)
G_PENDING = group(1, None, None, None, None, None, "pending")
G_INVALID = group(1, None, None, None, None, None, "invalid")
G_MISSING = group(0, None, None, None, None, None, "no_sample")


def expectation(product: dict[str, Any], original: dict[str, Any], delta: float | None,
                status: str = "complete", issues: tuple[str, ...] = (), participants: int = 1,
                complete_pairs: int | None = None) -> dict[str, Any]:
    # Ratios and delta are supplied explicitly by each fixture, not calculated.
    return {"score_scope": "event_contract", "status": status, "delta": delta,
            "groups": {"product": product, "original": original}, "issues": sorted(issues),
            "coverage": {"participants": participants,
                         "complete_pairs": participants if complete_pairs is None else complete_pairs}}


def build_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def add(case_id: str, sessions: list[dict[str, Any]], expected: dict[str, Any], family: str,
            attributes: tuple[str, ...], description: str, **case_fields: Any) -> None:
        items.append({"id": case_id,
                      "input": {"data_origin": "synthetic_event_fixture", "sessions": sessions, **case_fields},
                      "expectedOutput": copy.deepcopy(expected),
                      "metadata": {"data_origin": "synthetic_event_fixture", "score_scope": "event_contract",
                                   "case_family": family, "event_attributes": list(attributes),
                                   "description": description, "expected_status": expected["status"],
                                   "expected_issue_codes": expected["issues"],
                                   "not_real_user_data": True, "not_answer_correctness_evaluation": True}})

    add("event-positive-gain", [session("product", ("adopted", "adopted")), session("original")],
        expectation(group(1, 2, 2, 2.0, 1.0, 2.0), G_ONE, 1.0), "baseline",
        ("method", "question_id"), "同一预算内产品采用2题，原方式采用1题，净增量为1。")
    add("event-zero-attempts", [session("product", ()), session("original", ())],
        expectation(G_ZERO, G_ZERO, 0.0), "denominator", ("test_session_started", "complete"),
        "两组零尝试用户均计入M；Q与C为0，采用率无样本。")
    add("event-product-zero-negative-gain", [session("product", ()), session("original")],
        expectation(G_ZERO, G_ONE, -1.0), "denominator", ("method", "budget_ms"),
        "产品零尝试不被静默剔除，净增量允许为负。")

    p = session("product")
    p["events"][-2]["elapsed_ms"] = 1000
    add("event-confirm-at-deadline", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "deadline", ("elapsed_ms",), "采用发生时间恰为预算截止时，纳入计数。")
    p = session("product", ("not_adopted",))
    p["events"].insert(-1, event("late-answer", "exam_answer_recorded", 990, "question-1",
                                answer_version="answer-1", result_version="result-1", state_seq=2))
    p["events"].insert(-1, event("late-confirm", "exam_adoption_changed", 1001, "question-1",
                                answer_version="answer-1", adoption_status="adopted", state_seq=3))
    add("event-confirm-after-deadline", [p, session("original")], expectation(G_NONE, G_ONE, -1.0),
        "deadline", ("elapsed_ms",), "截止后确认不计；截止前明确未采用的题保留在Q。")
    p = session("product")
    p["events"][-2]["received_at"] = "2099-01-01T00:00:00Z"
    add("event-late-reception-in-time", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "ordering", ("elapsed_ms", "received_at"), "截止前发生、延迟收到的确认按发生时间计入。")
    p = session("product")
    p["events"].reverse()
    add("event-out-of-order-delivery", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "ordering", ("elapsed_ms", "state_seq"), "事件接收顺序打乱仍还原相同作答和采用。")
    p = session("product")
    p["events"].append(copy.deepcopy(p["events"][-2]))
    add("event-identical-retransmission", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "deduplication", ("event_id",), "完全相同event_id的重传不增加采用数。")
    p = session("product")
    duplicate = copy.deepcopy(p["events"][-2]); duplicate["adoption_status"] = "withdrawn"
    p["events"].append(duplicate)
    add("event-conflicting-retransmission", [p, session("original")],
        expectation(G_INVALID, G_ONE, None, "invalid", ("conflicting_event_id",), complete_pairs=0),
        "deduplication", ("event_id", "adoption_status"), "相同event_id不同载荷拒绝计数。")
    p = session("product")
    p["events"].insert(-1, event("retry-same-question", "exam_attempt_started", 140, "question-1",
                                attempt_id="attempt-1-2", attempt_type="retry"))
    add("event-same-question-retry", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "deduplication", ("question_id", "attempt_id", "attempt_type"), "同题重试只有1道尝试和1道采用。")

    p = session("product")
    p["events"].insert(-1, event("revised-answer", "exam_answer_recorded", 150, "question-1",
                                answer_version="answer-2", result_version="result-1", state_seq=3))
    p["events"].insert(-1, event("reconfirm-answer", "exam_adoption_changed", 160, "question-1",
                                answer_version="answer-2", adoption_status="adopted", state_seq=4))
    add("event-revise-and-reconfirm", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "answer_version", ("answer_version", "state_seq"), "改答后重新确认最新版本可计入。")
    p = session("product")
    p["events"].insert(-1, event("revised-unconfirmed", "exam_answer_recorded", 150, "question-1",
                                answer_version="answer-2", result_version="result-1", state_seq=3))
    add("event-revise-without-reconfirm", [p, session("original")], expectation(G_NONE, G_ONE, -1.0),
        "answer_version", ("answer_version",), "实质改答自动使旧确认失效，尚未重确认计0题采用。")
    p = session("product")
    p["events"].insert(-1, event("withdraw-answer", "exam_adoption_changed", 150, "question-1",
                                answer_version="answer-1", adoption_status="withdrawn", state_seq=3))
    add("event-withdraw-before-deadline", [p, session("original")], expectation(G_NONE, G_ONE, -1.0),
        "adoption", ("adoption_status", "state_seq"), "截止前撤回会移除采用但保留尝试。")
    p = session("product", ("not_adopted",))
    p["events"].insert(2, event("generated-only", "exam_task_finished", 120, "question-1",
                               status="success", result_version="result-1"))
    add("event-generated-without-presentation", [p, session("original")], expectation(G_NONE, G_ONE, -1.0),
        "presentation", ("result_version", "success", "adoption_status"), "只有服务端生成与明确未采用，不推定呈现或采用。")
    p = session("product", ("not_adopted",))
    p["events"].insert(-1, event("copy-action", "exam_action_recorded", 150, "question-1", action="copy"))
    add("event-copy-does-not-imply-adoption", [p, session("original")], expectation(G_NONE, G_ONE, -1.0),
        "adoption", ("action", "adoption_status"), "复制与切下一题不能代替采用确认。")
    p = session("product")
    p["events"] = [e for e in p["events"] if e["event_name"] != "exam_result_presented"]
    add("event-adopt-with-missing-presentation", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("missing_successful_presentation",), complete_pairs=0),
        "presentation", ("result_version", "success"), "显式采用产品结果但缺呈现回执，状态待核实而非伪造0。")
    for suffix, terminal in (("failure", "failed"), ("timeout", "timeout"), ("abandon", "abandoned")):
        p = session("product", ("not_adopted",))
        name = "exam_action_recorded" if terminal == "abandoned" else "exam_task_finished"
        p["events"].insert(2, event(f"terminal-{suffix}", name, 120, "question-1",
                                   status=terminal, action="abandon" if terminal == "abandoned" else None))
        add(f"event-{suffix}-retains-denominator", [p, session("original")],
            expectation(G_NONE, G_ONE, -1.0), "denominator", ("status", "action", "question_id"),
            f"{terminal}题保留在Q和用户分母，明确未采用计0题采用。")
    add("event-missing-original-method", [session("product")],
        expectation(G_ONE, G_MISSING, None, "pending", ("missing_paired_method",), complete_pairs=0),
        "pairing", ("user_id", "pair_id", "method"), "缺原方式窗口不能计算正式净增量。")
    p = session("product"); p["complete"] = False
    add("event-incomplete-window", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("incomplete_window",), complete_pairs=0),
        "completeness", ("complete",), "不完整窗口保留参与人数，Q/C与净增量待核实。")
    p = session("product"); p["events"].pop()
    add("event-missing-window-end", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("missing_window_end",), complete_pairs=0),
        "completeness", ("test_session_ended",), "只开始但没有结束收齐记录，不当作完成测试。")
    p = session("product"); p["events"] = [e for e in p["events"] if e["event_name"] != "exam_adoption_changed"]
    add("event-missing-adoption-state", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("missing_adoption_state",), complete_pairs=0),
        "completeness", ("adoption_status",), "已记答案但缺采用状态必须待核实。")
    p = session("product"); p["clock_uncertain"] = True
    add("event-uncertain-clock", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("clock_uncertain",), complete_pairs=0),
        "clock", ("clock_uncertain", "elapsed_ms"), "跨设备计时不确定时不输出正式净增量。")
    add("event-original-self-answer-without-presentation", [session("product"), session("original")],
        expectation(G_ONE, G_ONE, 0.0), "original_method", ("method", "answer_version"),
        "原方式自解或其他工具无需本产品结果呈现，只需真实记录及采用确认。")
    add("event-pooled-adoption-rate", [session("product", ("adopted",), "user-1", "pair-1"),
                                     session("original", ("not_adopted",), "user-1", "pair-1"),
                                     session("product", ("not_adopted", "not_adopted", "not_adopted"), "user-2", "pair-2"),
                                     session("original", ("adopted",), "user-2", "pair-2")],
        expectation(group(2, 4, 1, 2.0, 0.25, 0.5), group(2, 2, 1, 1.0, 0.5, 0.5), 0.0,
                    participants=2), "aggregation", ("user_id", "pair_id", "question_id"),
        "采用率按汇总C/Q=1/4，不平均用户的1和0；净增量为0。")
    add("event-duplicate-participant-window", [session("product"), session("product"), session("original")],
        expectation(G_INVALID, G_ONE, None, "invalid", ("duplicate_window",), complete_pairs=0),
        "pairing", ("user_id", "pair_id", "method"), "同人同轮同方式重复窗口全部拒绝，不能择优取一次。")
    add("event-unequal-paired-budgets", [session("product"), session("original", budget=2000)],
        expectation(G_ONE, G_ONE, None, "invalid", ("mixed_window_budgets", "paired_budget_mismatch"), complete_pairs=0),
        "pairing", ("budget_ms", "pair_id"), "对照时长不一致不能计算同时间净增量。")
    p = session("product"); p["events"][-2]["answer_version"] = "answer-obsolete"
    add("event-adoption-version-mismatch", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("adoption_answer_mismatch",), complete_pairs=0),
        "answer_version", ("answer_version",), "采用确认不能绑定不存在或过期的最终答案版本。")
    p = session("product"); del p["events"][-2]["state_seq"]
    add("event-missing-state-sequence", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("missing_state_seq",), complete_pairs=0),
        "ordering", ("state_seq",), "采用状态序号缺失时待核实，避免同毫秒状态冲突。")
    p = session("product"); p["events"][-2]["state_seq"] = 3
    add("event-state-sequence-gap", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("state_sequence_gap",), complete_pairs=0),
        "ordering", ("state_seq",), "状态序号断档说明可能缺失改答或撤回事件。")
    p = session("product")
    p["events"][-1]["question_states"] = [{"question_id": "question-1", "answer_version": "answer-1",
                                            "adoption_status": "withdrawn"}]
    add("event-end-snapshot-disagreement", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("snapshot_detail_mismatch",), complete_pairs=0),
        "completeness", ("question_states", "answer_version", "adoption_status"),
        "结束快照与明细冲突不能静默选一个计算。")
    add("event-preselected-participant-missing", [session("product"), session("original")],
        expectation(G_ONE, G_ONE, None, "pending", ("missing_paired_method",), participants=2, complete_pairs=1),
        "pairing", ("expected_participants",), "预选用户完全没有上传窗口仍计入覆盖分母。",
        expected_participants=[{"user_id": "user-1", "pair_id": "pair-1"},
                               {"user_id": "user-2", "pair_id": "pair-2"}])
    p = session("product")
    p["events"].insert(-1, event("same-answer-recording", "exam_answer_recorded", 150, "question-1",
                                answer_version="answer-1", result_version="result-1", state_seq=3))
    add("event-same-answer-version-recorded-again", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "answer_version", ("answer_version",), "重复记录同一答案版本不算实质改答，也不取消确认。")
    p = session("product")
    p["events"].append(event("late-revision", "exam_answer_recorded", 1001, "question-1",
                             answer_version="answer-2", result_version="result-1", state_seq=3))
    add("event-revision-after-deadline", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "deadline", ("answer_version", "elapsed_ms"), "截止后改答不覆盖截止时已采用的答案。")
    p = session("product", ())
    p["events"].append(event("late-first-attempt", "exam_attempt_started", 1001, "question-late"))
    add("event-first-attempt-after-deadline", [p, session("original", ())], expectation(G_ZERO, G_ZERO, 0.0),
        "deadline", ("question_id", "elapsed_ms"), "截止后首次开始题目不增加Q。")
    p = session("product"); del p["events"][-2]["event_id"]
    add("event-missing-event-id", [p, session("original")],
        expectation(G_INVALID, G_ONE, None, "invalid", ("missing_adoption_state", "missing_event_id"), complete_pairs=0),
        "deduplication", ("event_id",), "缺event_id的确认无法去重，拒绝并暴露采用状态缺失。")
    p = session("product")
    duplicate = copy.deepcopy(p["events"][-2]); duplicate["received_at"] = "2099-01-01T00:00:00Z"
    p["events"].append(duplicate)
    add("event-retransmission-new-receive-time", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "deduplication", ("event_id", "received_at"), "重传仅接收时间改变仍视为同一事件。")
    p = session("product", ("not_adopted",)); p["events"][-1]["elapsed_ms"] = 200
    p["events"][-1]["exit_reason"] = "early_exit"
    p["events"][-1]["question_states"] = [{"question_id": "question-1", "answer_version": None,
                                            "adoption_status": "not_adopted"}]
    add("event-early-exit-keeps-fixed-budget", [p, session("original")], expectation(G_NONE, G_ONE, -1.0),
        "denominator", ("exit_reason", "budget_ms"), "提前退出保留1000ms预算与参与人数，不能缩短计时来放大效率。")
    p = session("product", ("not_adopted",)); p["events"].pop(-2)
    p["events"][-1]["question_states"] = [{"question_id": "question-1", "answer_version": None,
                                            "adoption_status": "not_adopted"}]
    add("event-end-snapshot-known-non-adoption", [p, session("original")], expectation(G_NONE, G_ONE, -1.0),
        "completeness", ("question_states", "adoption_status"), "完整结束快照明确无答案且未采用，可补全非采用状态。")
    p = session("product"); p["events"][-2]["elapsed_ms"] = 120
    add("event-same-millisecond-sequence", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "ordering", ("elapsed_ms", "state_seq"), "同一毫秒先记录seq1再确认seq2，以序号还原。")
    p = session("product"); p["events"][-2]["elapsed_ms"] = 115
    add("event-state-clock-order-disagreement", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("adoption_answer_mismatch", "state_time_conflict"),
                    complete_pairs=0), "clock", ("elapsed_ms", "state_seq"),
        "状态序号说明确认在作答之后，发生时间却在作答之前，不能强行选一个时钟解释。")
    p = session("product"); p["events"][2]["elapsed_ms"] = 125
    add("event-presentation-after-answer-recorded", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("missing_successful_presentation",), complete_pairs=0),
        "presentation", ("result_version", "elapsed_ms"),
        "产品作答引用尚未呈现的结果版本，缺可支持该作答来源的呈现证据。")
    add("event-mixed-budgets-between-participants", [session("product", user="user-1", pair="pair-1"),
                                                  session("original", user="user-1", pair="pair-1"),
                                                  session("product", user="user-2", pair="pair-2", budget=2000),
                                                  session("original", user="user-2", pair="pair-2", budget=2000)],
        expectation(group(2, 2, 2, 1.0, 1.0, 1.0), group(2, 2, 2, 1.0, 1.0, 1.0), None,
                    "invalid", ("mixed_window_budgets",), participants=2, complete_pairs=2),
        "pairing", ("budget_ms", "user_id", "pair_id"),
        "各对预算相同但全体窗口时长混杂，不汇总成同一固定时间北极星。")
    p = session("product", ("not_adopted",)); p["events"][-1]["elapsed_ms"] = 200
    p["events"][-1]["exit_reason"] = "early_exit"
    add("event-early-exit-missing-snapshot", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("missing_exit_snapshot",), complete_pairs=0),
        "completeness", ("exit_reason", "question_states"),
        "提前退出缺最终快照，不能假设结束后的状态已完整收齐。")
    p = session("product", ("not_adopted",)); p["events"][-1]["elapsed_ms"] = 200
    p["events"][-1]["exit_reason"] = "early_exit"
    p["events"][-1]["question_states"] = [{"question_id": "question-1", "answer_version": None,
                                            "adoption_status": "not_adopted"}]
    p["events"].extend([event("post-exit-result", "exam_result_presented", 300, "question-1",
                              result_version="result-1", success=True),
                         event("post-exit-answer", "exam_answer_recorded", 310, "question-1",
                              answer_version="answer-1", result_version="result-1", state_seq=2),
                         event("post-exit-confirm", "exam_adoption_changed", 320, "question-1",
                              answer_version="answer-1", adoption_status="adopted", state_seq=3)])
    add("event-answer-after-early-exit", [p, session("original")],
        expectation(G_INVALID, G_ONE, None, "invalid", ("event_after_exit",), complete_pairs=0),
        "deadline", ("exit_reason", "elapsed_ms", "adoption_status"),
        "退出已经是终态，退出后即使仍在原预算内也不得补答案或采用。")
    p = session("product"); del p["clock_synchronized"]
    add("event-unknown-clock-basis", [p, session("original")],
        expectation(G_PENDING, G_ONE, None, "pending", ("clock_uncertain",), complete_pairs=0),
        "clock", ("clock_synchronized", "clock_basis"), "没有明确单时钟或校时声明时，时钟状态未知。")
    p = session("product"); del p["clock_synchronized"]; p["clock_basis"] = "single_session_monotonic"
    add("event-declared-single-session-clock", [p, session("original")], expectation(G_ONE, G_ONE, 0.0),
        "clock", ("clock_basis",), "明确全链路共用会话单调时钟时无需跨设备校时。")
    add("event-same-user-multiple-pairs", [session("product", user="user-1", pair="pair-1"),
                                          session("original", user="user-1", pair="pair-1"),
                                          session("product", user="user-1", pair="pair-2"),
                                          session("original", user="user-1", pair="pair-2")],
        expectation(G_INVALID, G_INVALID, None, "invalid", ("multiple_rounds_for_user",),
                    participants=2, complete_pairs=0), "pairing", ("user_id", "pair_id"),
        "当前固定一期每人仅一轮，同一用户第二pair拒绝，M仍为1人而非2人次。")
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=APP_ROOT.parent / "outputs/north-star-evaluation/2026-09-30/event-contract-dataset.json")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    items = build_items()
    if args.verify:
        failures = [{"id": item["id"], "mismatches": compare_expected(evaluate_case(item["input"]), item["expectedOutput"])}
                    for item in items]
        failures = [failure for failure in failures if failure["mismatches"]]
        if failures:
            raise SystemExit(json.dumps({"failed_cases": failures}, ensure_ascii=False, indent=2))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "case_count": len(items),
                      "data_origin": "synthetic_event_fixture", "score_scope": "event_contract",
                      "verified": args.verify}, ensure_ascii=False))


if __name__ == "__main__":
    main()
