"""Deterministic event-contract evaluation for the fixed-window North Star.

This evaluates bookkeeping, not answer correctness or real user value.  A case
contains preselected participant pairs.  One user/pair/method is one window;
multiple windows for that key are rejected, never silently selected.  Event time
is ``elapsed_ms`` on the shared session clock; ``received_at`` is diagnostic only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


METHODS = ("product", "original")
ADOPTION_STATES = {"adopted", "not_adopted", "hesitating", "withdrawn"}
QUESTION_EVENTS = {
    "exam_attempt_started", "exam_input_finished", "exam_task_finished",
    "exam_result_presented", "exam_answer_recorded", "exam_adoption_changed",
    "exam_action_recorded",
}
EVENT_NAMES = QUESTION_EVENTS | {"test_session_started", "test_session_ended"}


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _version(value: Any) -> bool:
    return isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) != ""


def _event_fingerprint(event: dict[str, Any]) -> str:
    # Retransmission can carry a new receive timestamp without changing the event.
    return json.dumps({k: v for k, v in event.items() if k != "received_at"},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _session_result(session: dict[str, Any], index: int) -> dict[str, Any]:
    invalid: set[str] = set()
    pending: set[str] = set()
    method = session.get("method")
    user_id = session.get("user_id")
    pair_id = session.get("pair_id")
    budget = session.get("budget_ms")
    if method not in METHODS:
        invalid.add("invalid_method")
    if not isinstance(user_id, str) or not user_id:
        invalid.add("missing_user_id")
    if not isinstance(pair_id, str) or not pair_id:
        invalid.add("missing_pair_id")
    if not _integer(budget) or budget <= 0:
        invalid.add("invalid_budget")
        budget = 0
    known_clock = (session.get("clock_synchronized") is True
                   or session.get("clock_basis") == "single_session_monotonic")
    if session.get("clock_uncertain", False) or session.get("clock_synchronized") is False or not known_clock:
        pending.add("clock_uncertain")
    if session.get("complete") is not True:
        pending.add("incomplete_window")

    events = session.get("events")
    if not isinstance(events, list):
        invalid.add("invalid_events")
        events = []
    unique: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for event in events:
        if not isinstance(event, dict):
            invalid.add("invalid_event")
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            invalid.add("missing_event_id")
            continue
        if event_id in unique:
            if _event_fingerprint(unique[event_id]) != _event_fingerprint(event):
                invalid.add("conflicting_event_id")
            else:
                duplicates += 1
            continue
        unique[event_id] = event
        if event.get("event_name") not in EVENT_NAMES:
            invalid.add("invalid_event_name")
        elapsed = event.get("elapsed_ms", event.get("session_elapsed_ms"))
        if not _integer(elapsed) or elapsed < 0:
            invalid.add("invalid_elapsed_ms")
        if event.get("clock_uncertain", False):
            pending.add("clock_uncertain")
        if event.get("event_name") in QUESTION_EVENTS and (
            not isinstance(event.get("question_id"), str) or not event.get("question_id")
        ):
            invalid.add("missing_question_id")

    def elapsed(event: dict[str, Any]) -> int:
        value = event.get("elapsed_ms", event.get("session_elapsed_ms"))
        return value if _integer(value) else -1

    starts = [e for e in unique.values() if e.get("event_name") == "test_session_started"]
    ends = [e for e in unique.values() if e.get("event_name") == "test_session_ended"]
    if not starts:
        pending.add("missing_window_start")
    elif len(starts) != 1:
        invalid.add("multiple_window_starts")
    elif elapsed(starts[0]) != 0:
        invalid.add("invalid_window_start_time")
    if not ends:
        pending.add("missing_window_end")
    elif len(ends) != 1:
        invalid.add("multiple_window_ends")
    elif ends[0].get("complete", True) is not True:
        pending.add("incomplete_window")

    exit_end = (ends[0] if len(ends) == 1 and ends[0].get("exit_reason") in
                {"early_exit", "user_exit", "abandoned"} and 0 <= elapsed(ends[0]) <= budget else None)
    timeline_end = elapsed(exit_end) if exit_end is not None else budget
    after_exit_count = sum(timeline_end < elapsed(e) <= budget for e in unique.values()
                           if exit_end is not None and e.get("event_name") in QUESTION_EVENTS)
    if after_exit_count:
        invalid.add("event_after_exit")
    if exit_end is not None and "question_states" not in exit_end:
        pending.add("missing_exit_snapshot")
    in_time = [e for e in unique.values() if 0 <= elapsed(e) <= timeline_end]
    late_count = sum(elapsed(e) > budget for e in unique.values()
                     if e.get("event_name") in QUESTION_EVENTS)
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in in_time:
        if event.get("event_name") in QUESTION_EVENTS and isinstance(event.get("question_id"), str):
            by_question[event["question_id"]].append(event)
    question_states: dict[str, dict[str, Any]] = {}
    attempted = 0
    adopted = 0
    snapshots: dict[str, dict[str, Any]] = {}
    if len(ends) == 1 and "question_states" in ends[0]:
        raw_snapshots = ends[0]["question_states"]
        if not isinstance(raw_snapshots, list):
            invalid.add("invalid_end_snapshot")
        else:
            for snapshot in raw_snapshots:
                if not isinstance(snapshot, dict) or not isinstance(snapshot.get("question_id"), str):
                    invalid.add("invalid_end_snapshot")
                    continue
                qid = snapshot["question_id"]
                if qid in snapshots:
                    invalid.add("duplicate_snapshot_question")
                snapshots[qid] = snapshot

    for qid in sorted(by_question):
        qevents = by_question[qid]
        attempt_times = [elapsed(e) for e in qevents if e.get("event_name") == "exam_attempt_started"]
        if not attempt_times:
            invalid.add("orphan_question_event")
            continue
        attempted += 1
        first_attempt = min(attempt_times)
        if any(elapsed(e) < first_attempt for e in qevents):
            invalid.add("event_before_question_start")
        mutations = [e for e in qevents if e.get("event_name") in
                     {"exam_answer_recorded", "exam_adoption_changed"}]
        seq_seen: dict[int, dict[str, Any]] = {}
        for event in mutations:
            seq = event.get("state_seq")
            if not _integer(seq) or seq <= 0:
                pending.add("missing_state_seq")
            elif seq in seq_seen:
                invalid.add("conflicting_state_seq")
            else:
                seq_seen[seq] = event
        # Sequence and occurrence time must agree on a synchronized clock.
        sequenced = sorted(seq_seen.items())
        if any(elapsed(right[1]) < elapsed(left[1])
               for left, right in zip(sequenced, sequenced[1:])):
            pending.add("state_time_conflict")
        if sequenced and [n for n, _ in sequenced] != list(range(1, max(seq_seen) + 1)):
            pending.add("state_sequence_gap")
        mutations.sort(key=lambda e: (elapsed(e), e.get("state_seq")
                                     if _integer(e.get("state_seq")) else -1, e["event_id"]))
        answer_version = None
        result_version = None
        answer_elapsed = None
        state = None
        state_version = None
        has_explicit_state = False
        for event in mutations:
            if event["event_name"] == "exam_answer_recorded":
                version = event.get("answer_version")
                if not _version(version):
                    pending.add("missing_answer_version")
                    continue
                if answer_version is not None and version == answer_version:
                    # Repeated recording of the same version is not a substantive edit.
                    continue
                if state == "adopted":
                    state = "unconfirmed"
                    state_version = None
                answer_version = version
                result_version = event.get("result_version")
                answer_elapsed = elapsed(event)
            else:
                new_state = event.get("adoption_status", event.get("state"))
                if new_state not in ADOPTION_STATES:
                    pending.add("missing_adoption_state")
                    continue
                has_explicit_state = True
                version = event.get("answer_version")
                if new_state == "adopted" and (answer_version is None or version != answer_version):
                    pending.add("adoption_answer_mismatch")
                    state = "unknown"
                    continue
                if version is not None and answer_version is not None and version != answer_version:
                    pending.add("adoption_answer_mismatch")
                    state = "unknown"
                    continue
                state, state_version = new_state, version
                if state == "adopted" and method == "product":
                    if not _version(result_version):
                        pending.add("missing_result_reference")
                    else:
                        presentations = [e for e in qevents if
                                         e.get("event_name") == "exam_result_presented"
                                         and e.get("result_version") == result_version
                                         and e.get("success") is True
                                         and elapsed(e) <= answer_elapsed]
                        if not presentations:
                            pending.add("missing_successful_presentation")
        snapshot = snapshots.get(qid)
        if exit_end is not None and snapshot is None:
            pending.add("missing_exit_snapshot")
        if snapshot:
            snapshot_state = snapshot.get("adoption_status", snapshot.get("state"))
            snapshot_answer = snapshot.get("answer_version")
            if snapshot_state not in ADOPTION_STATES | {"unconfirmed"}:
                pending.add("missing_adoption_state")
            elif has_explicit_state:
                if snapshot_state != state or snapshot_answer != answer_version:
                    pending.add("snapshot_detail_mismatch")
            elif snapshot_state == "adopted":
                # Adopted snapshots cannot fabricate the missing confirmation event.
                pending.add("missing_adoption_confirmation")
            elif snapshot_answer == answer_version:
                state = snapshot_state
                has_explicit_state = True
            else:
                pending.add("snapshot_detail_mismatch")
        if not has_explicit_state:
            pending.add("missing_adoption_state")
        is_adopted = state == "adopted" and state_version == answer_version and answer_version is not None
        adopted += int(is_adopted)
        question_states[qid] = {"answer_version": answer_version, "adoption_status": state or "unknown",
                                "adopted": is_adopted}
    if snapshots and set(snapshots) != set(question_states):
        pending.add("snapshot_question_mismatch")
    status = "invalid" if invalid else "pending" if pending else "complete"
    return {
        "session_index": index, "user_id": user_id, "pair_id": pair_id, "method": method,
        "started": bool(starts), "budget_ms": budget, "status": status,
        "Q": attempted if status == "complete" else None,
        "C": adopted if status == "complete" else None,
        "observed_Q": attempted, "observed_C": adopted,
        "duplicate_events": duplicates, "after_deadline_events": late_count,
        "after_exit_events": after_exit_count,
        "issues": sorted(invalid | pending), "questions": question_states,
    }


def evaluate_case(case_input: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-safe metrics with explicit invalid/pending completeness states.

    M counts started users (including zero attempts), one pair per user in the
    evaluated period; repeated rounds for a user are invalid.  Q/C
    are withheld when that group's records are incomplete.  Delta is official
    only when every preselected participant has exactly one complete window in
    each method, with equal budgets.  Ratios use pooled Q/C, never mean per-user
    ratios.  A zero-Q adoption rate is None rather than an invented zero.
    """
    if not isinstance(case_input, dict):
        case_input = {}
    raw_sessions = case_input.get("sessions")
    top_issues: set[str] = set()
    if not isinstance(raw_sessions, list):
        raw_sessions = []
        top_issues.add("invalid_sessions")
    results = [_session_result(s if isinstance(s, dict) else {}, i)
               for i, s in enumerate(raw_sessions)]
    windows: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        if isinstance(result["user_id"], str) and isinstance(result["pair_id"], str) and result["method"] in METHODS:
            windows[(result["user_id"], result["pair_id"], result["method"])].append(result)
    for duplicate_windows in windows.values():
        if len(duplicate_windows) > 1:
            top_issues.add("duplicate_window")
            for result in duplicate_windows:
                result["status"] = "invalid"
                result["Q"] = result["C"] = None
                result["issues"] = sorted(set(result["issues"]) | {"duplicate_window"})
    user_pairs: dict[str, set[str]] = defaultdict(set)
    for user_id, pair_id, _method in windows:
        user_pairs[user_id].add(pair_id)
    repeated_users = {user_id for user_id, pairs in user_pairs.items() if len(pairs) > 1}
    if repeated_users:
        top_issues.add("multiple_rounds_for_user")
        for result in results:
            if isinstance(result["user_id"], str) and result["user_id"] in repeated_users:
                result["status"] = "invalid"
                result["Q"] = result["C"] = None
                result["issues"] = sorted(set(result["issues"]) | {"multiple_rounds_for_user"})
    participants = {(key[0], key[1]) for key in windows}
    expected_participants = case_input.get("expected_participants", [])
    if not isinstance(expected_participants, list):
        top_issues.add("invalid_expected_participants")
        expected_participants = []
    for participant in expected_participants:
        if isinstance(participant, dict) and isinstance(participant.get("user_id"), str) and isinstance(participant.get("pair_id"), str):
            participants.add((participant["user_id"], participant["pair_id"]))
        else:
            top_issues.add("invalid_expected_participants")
    complete_pairs = 0
    started_budgets = {r["budget_ms"] for r in results if r["started"] and r["budget_ms"] > 0}
    if len(started_budgets) > 1:
        top_issues.add("mixed_window_budgets")
    for user_id, pair_id in sorted(participants):
        pair = [windows.get((user_id, pair_id, method), []) for method in METHODS]
        if any(not item for item in pair):
            top_issues.add("missing_paired_method")
        elif all(len(item) == 1 and item[0]["status"] == "complete" for item in pair):
            if pair[0][0]["budget_ms"] != pair[1][0]["budget_ms"]:
                top_issues.add("paired_budget_mismatch")
            else:
                complete_pairs += 1
    groups: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        method_results = [r for r in results if r["method"] == method]
        started = {r["user_id"] for r in method_results if r["started"]
                   and isinstance(r["user_id"], str) and r["user_id"]
                   and isinstance(r["pair_id"], str) and r["pair_id"]}
        m = len(started)
        valid = bool(method_results) and all(r["status"] == "complete" for r in method_results) and m > 0
        q = sum(r["Q"] for r in method_results) if valid else None
        c = sum(r["C"] for r in method_results) if valid else None
        group_status = ("invalid" if any(r["status"] == "invalid" for r in method_results)
                        else "pending" if any(r["status"] == "pending" for r in method_results)
                        else "complete" if valid else "no_sample")
        groups[method] = {"status": group_status, "M": m, "Q": q, "C": c,
                          "per_user_attempts": q / m if valid else None,
                          "adoption_rate": c / q if valid and q else None,
                          "per_user_adopted": c / m if valid else None}
    all_issues = top_issues | {code for result in results for code in result["issues"]}
    invalid_top = {"invalid_sessions", "duplicate_window", "invalid_expected_participants",
                   "paired_budget_mismatch", "mixed_window_budgets", "multiple_rounds_for_user"}
    invalid = bool(top_issues & invalid_top) or any(r["status"] == "invalid" for r in results)
    complete = (bool(participants) and complete_pairs == len(participants)
                and len(results) == 2 * len(participants) and not all_issues)
    status = "invalid" if invalid else "complete" if complete else "pending"
    if not participants:
        all_issues.add("no_participants")
    delta = (groups["product"]["per_user_adopted"] - groups["original"]["per_user_adopted"]
             if status == "complete" else None)
    return {"schema_version": 1, "score_scope": "event_contract", "status": status,
            "delta": delta, "groups": groups,
            "coverage": {"participants": len(participants), "complete_pairs": complete_pairs,
                         "product": groups["product"]["M"] / len(participants) if participants else None,
                         "original": groups["original"]["M"] / len(participants) if participants else None},
            "issues": sorted(all_issues), "sessions": results}


def compare_expected(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Compare a hand-authored expected projection, including nulls and ratios."""
    mismatches: list[str] = []

    def compare(value: Any, wanted: Any, path: str) -> None:
        if isinstance(wanted, dict):
            if not isinstance(value, dict):
                mismatches.append(path)
            else:
                for key, child in wanted.items():
                    if key not in value:
                        mismatches.append(f"{path}.{key}")
                    else:
                        compare(value[key], child, f"{path}.{key}")
        elif isinstance(wanted, float) and isinstance(value, (float, int)) and not isinstance(value, bool):
            if abs(value - wanted) > 1e-12:
                mismatches.append(path)
        elif value != wanted:
            mismatches.append(path)

    compare(actual, expected, "result")
    return mismatches
