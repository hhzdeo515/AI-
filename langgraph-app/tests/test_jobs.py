"""Durable tasks use isolated databases and never call a model."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lg_assistant import config, progress, store
from lg_assistant.web.app import create_app


def payload(rid="request", sid="session"):
    return dict(owner="local", session_id=sid, request_id=rid, text="计算 2+2",
                scene=None, files=[], event={}, rejected=[], exam_backend="original")


def wait_for(manager, tid, status="done"):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        task = manager.get(tid)
        if task["status"] == status:
            return task
        time.sleep(.01)
    pytest.fail(f"Task did not reach {status}: {task}")


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    for name, value in {"DATA_DIR": tmp_path, "DB_PATH": tmp_path / "app.sqlite3",
                        "CHECKPOINT_DB": tmp_path / "checkpoints.sqlite3",
                        "UPLOAD_DIR": tmp_path / "uploads", "EXPORT_DIR": tmp_path / "exports",
                        "ACCESS_TOKEN": ""}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(store, "_local", threading.local())
    monkeypatch.setattr(store, "_initialised", False)
    return tmp_path


class FakeGraph:
    def __init__(self):
        self.calls = []

    def invoke(self, state, cfg):
        self.calls.append(state)
        return {"routing": {"scene": "general"}, "result": {"text": "4"}}

    def get_state(self, cfg):
        return SimpleNamespace(values={}, next=())


def manager_type():
    from lg_assistant import jobs
    return jobs.TaskManager


def test_app_exposes_durable_manager(isolated):
    app = create_app(FakeGraph())
    assert "task_manager" in app.extensions


def test_completed_task_and_dedup_survive_restart(isolated):
    calls = []
    Manager = manager_type()
    first = Manager(config.DB_PATH, lambda p: calls.append(p) or {"text": "saved"})
    task = first.submit({**payload(), "scene": "meeting"})
    first.start()
    assert wait_for(first, task["id"])["result"]["text"] == "saved"
    first.shutdown()
    second = Manager(config.DB_PATH, lambda p: pytest.fail("Completed task reran"))
    try:
        second.start()
        assert second.submit(payload())["id"] == task["id"]
        assert second.get(task["id"])["status"] == "done"
        assert second.get(task["id"])["scene"] == "meeting"
        assert len(calls) == 1
    finally:
        second.shutdown()


def test_restart_recovers_pending_but_running_requires_explicit_retry(isolated):
    Manager = manager_type()
    before = Manager(config.DB_PATH, lambda p: {"text": "unused"})
    pending = before.submit(payload("pending"))
    running = before.submit(payload("interrupted", "other"))
    with sqlite3.connect(config.DB_PATH) as db:
        db.execute("UPDATE web_tasks SET status='running' WHERE id=?", (running["id"],))
    before.shutdown()
    calls = []
    after = Manager(config.DB_PATH, lambda p: calls.append(p) or {"text": "recovered"})
    try:
        after.start()
        wait_for(after, pending["id"])
        assert after.get(running["id"])["status"] == "interrupted"
        assert [p["request_id"] for p in calls] == ["pending"]
        assert after.retry(running["id"])["id"] == running["id"]
        wait_for(after, running["id"])
        assert calls[-1]["request_id"] == "interrupted"
        assert calls[-1]["_retry"] is True
    finally:
        after.shutdown()


def test_duplicate_concurrent_submissions_only_execute_once(isolated):
    Manager = manager_type()
    calls = []
    manager = Manager(config.DB_PATH, lambda p: calls.append(p) or {"text": "once"})
    try:
        manager.start()
        with ThreadPoolExecutor(8) as pool:
            tasks = list(pool.map(lambda _: manager.submit(payload()), range(20)))
        assert len({t["id"] for t in tasks}) == 1
        wait_for(manager, tasks[0]["id"])
        assert len(calls) == 1
    finally:
        manager.shutdown()


def test_workers_are_bounded_and_same_session_never_overlaps(isolated):
    Manager = manager_type()
    lock = threading.Lock()
    counts = dict(active=0, peak=0)
    sessions = set()
    overlaps = []
    both_running = threading.Event()
    release = threading.Event()
    def execute(p):
        with lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
            if p["session_id"] in sessions:
                overlaps.append(p["session_id"])
            sessions.add(p["session_id"])
            if counts["active"] == 2:
                both_running.set()
        release.wait(3)
        with lock:
            sessions.remove(p["session_id"])
            counts["active"] -= 1
        return {"text": "done"}
    manager = Manager(config.DB_PATH, execute, max_workers=2)
    tasks = [manager.submit(payload(str(i), "same" if i < 3 else str(i))) for i in range(7)]
    try:
        manager.start()
        assert both_running.wait(2), "A waiting same-session task must not starve other sessions"
        assert counts["peak"] == 2
        release.set()
        for task in tasks:
            wait_for(manager, task["id"])
        assert not overlaps
    finally:
        release.set()
        manager.shutdown()


def test_retryable_result_is_interrupted_and_retry_is_idempotent(isolated):
    Manager = manager_type()
    manager = Manager(config.DB_PATH, lambda p: {"text": "waiting", "status": "pending"})
    task = manager.submit(payload())
    manager.start()
    wait_for(manager, task["id"], "interrupted")
    manager.shutdown()
    manager = Manager(config.DB_PATH, lambda p: {"text": "collected"})
    try:
        first = manager.retry(task["id"])
        assert manager.retry(task["id"])["id"] == first["id"]
        manager.start()
        assert wait_for(manager, task["id"])["result"]["text"] == "collected"
    finally:
        manager.shutdown()


def test_public_tasks_do_not_expose_inputs_paths_or_exception_details(isolated):
    Manager = manager_type()
    secret_path = str(isolated / "secret-upload.wav")
    def execute(p):
        raise RuntimeError(f"provider token=secret-key file={secret_path}")
    manager = Manager(config.DB_PATH, execute)
    private = {**payload(), "files": [secret_path], "text": "private input"}
    task = manager.submit(private)
    try:
        manager.start()
        wait_for(manager, task["id"], "error")
        public = json.dumps(manager.list("local"), ensure_ascii=False)
        assert secret_path not in public
        assert "secret-key" not in public
        assert "private input" not in public
        assert manager.get(task["id"], "other") is None
    finally:
        manager.shutdown()


def test_progress_is_saved_and_read_after_restart(isolated):
    Manager = manager_type()
    manager = Manager(config.DB_PATH, lambda p: {"text": "done"})
    task = manager.submit(payload())
    progress.begin("request", "exam", "recognize", sink=lambda value: manager.save_progress("local", "request", value))
    progress.mark("solve", "request")
    progress.batch(4, 2, "request")
    manager.shutdown()
    restarted = Manager(config.DB_PATH, lambda p: {})
    saved = restarted.get(task["id"])["progress"]
    assert saved["batch"] == {"total": 4, "done": 2}
    assert [s["id"] for s in saved["steps"] if s["state"] == "active"] == ["solve"]
    restarted.shutdown()


def test_async_routes_list_retry_and_duplicate_done_task(isolated):
    graph = FakeGraph()
    app = create_app(graph)
    client = app.test_client()
    try:
        first = client.post("/api/chat/async", data=payload()).get_json()
        manager = app.extensions["task_manager"]
        wait_for(manager, first["task_id"])
        second = client.post("/api/chat/async", data=payload()).get_json()
        assert second["task_id"] == first["task_id"]
        assert second["status"] == "done"
        tasks = client.get("/api/tasks?owner=local").get_json()["tasks"]
        assert len(tasks) == 1 and tasks[0]["request_id"] == "request"
        assert client.get("/api/task", query_string={"task_id": first["task_id"], "owner": "other"}).status_code == 404
        retried = client.post("/api/task/retry", json={"task_id": first["task_id"], "owner": "local"})
        assert retried.status_code == 202 and retried.get_json()["task_id"] == first["task_id"]
        assert len(graph.calls) == 1
    finally:
        app.extensions.get("task_manager") and app.extensions["task_manager"].shutdown()


def test_retry_resumes_only_checkpoint_for_same_request(isolated):
    class CheckpointGraph(FakeGraph):
        def get_state(self, cfg):
            return SimpleNamespace(values={"request_id": "request", "result": {}}, next=("postprocess",))
    graph = CheckpointGraph()
    app = create_app(graph)
    manager = app.extensions["task_manager"]
    task = manager.submit(payload())
    with sqlite3.connect(config.DB_PATH) as db:
        db.execute("UPDATE web_tasks SET status='running' WHERE id=?", (task["id"],))
    try:
        manager.start()
        assert manager.get(task["id"])["status"] == "interrupted"
        app.test_client().post("/api/task/retry", json={"task_id": task["id"]})
        wait_for(manager, task["id"])
        assert graph.calls == [None]
    finally:
        manager.shutdown()


def test_sync_chat_and_reset_share_async_session_lock(isolated):
    entered, release = threading.Event(), threading.Event()
    violations = []
    class BlockingGraph(FakeGraph):
        def __init__(self):
            super().__init__()
            self.checkpointer = SimpleNamespace(delete_thread=self.reset)
        def invoke(self, state, cfg):
            if state["request_id"] == "async":
                entered.set()
                release.wait(3)
            elif not release.is_set():
                violations.append("sync overlapped")
            return super().invoke(state, cfg)
        def reset(self, thread):
            if not release.is_set():
                violations.append("reset overlapped")
    app = create_app(BlockingGraph())
    manager = app.extensions["task_manager"]
    try:
        first = app.test_client().post("/api/chat/async", data=payload("async")).get_json()
        assert entered.wait(1)
        with ThreadPoolExecutor(2) as pool:
            sync = pool.submit(lambda: app.test_client().post("/api/chat", data=payload("sync")))
            reset = pool.submit(lambda: app.test_client().post("/api/reset", data={"session_id": "session"}))
            time.sleep(.08)
            assert not sync.done() and not reset.done()
            release.set()
            assert sync.result().status_code == 200
            assert reset.result().status_code == 200
        wait_for(manager, first["task_id"])
        assert not violations
    finally:
        release.set()
        manager.shutdown()


def test_retry_of_old_task_cannot_resume_newer_session_checkpoint(isolated):
    class NewerGraph(FakeGraph):
        def get_state(self, cfg):
            return SimpleNamespace(values={"request_id": "newer-request", "result": {}}, next=("postprocess",))
    graph = NewerGraph()
    app = create_app(graph)
    manager = app.extensions["task_manager"]
    task = manager.submit(payload())
    with sqlite3.connect(config.DB_PATH) as db:
        db.execute("UPDATE web_tasks SET status='interrupted', attempts=1 WHERE id=?", (task["id"],))
    try:
        response = app.test_client().post("/api/task/retry", json={"task_id": task["id"]})
        assert response.status_code == 409
        assert "新请求" in response.get_json()["error"]
        assert graph.calls == []
        assert manager.get(task["id"])["status"] == "error"
    finally:
        manager.shutdown()


def test_completed_matching_checkpoint_recovers_without_graph_reexecution(isolated):
    class CompletedGraph(FakeGraph):
        def get_state(self, cfg):
            return SimpleNamespace(values={"request_id": "request", "routing": {"scene": "general"},
                                           "result": {"text": "checkpoint result"}}, next=())
    graph = CompletedGraph()
    app = create_app(graph)
    manager = app.extensions["task_manager"]
    task = manager.submit(payload())
    with sqlite3.connect(config.DB_PATH) as db:
        db.execute("UPDATE web_tasks SET status='running', attempts=1 WHERE id=?", (task["id"],))
    try:
        app.test_client().post("/api/task/retry", json={"task_id": task["id"]})
        assert wait_for(manager, task["id"])["result"]["text"] == "checkpoint result"
        assert graph.calls == []
    finally:
        manager.shutdown()


def test_retry_keeps_external_asr_submission_receipt(isolated, monkeypatch):
    from lg_assistant import meeting_jobs
    Manager = manager_type()
    audio = isolated / "voice.wav"
    audio.write_bytes(b"test-wave")
    submissions = []
    monkeypatch.setattr(meeting_jobs.audio, "normalize_browser_recording", lambda path: path)
    monkeypatch.setattr(meeting_jobs.transcribe, "upload_for_temp_url", lambda path: "https://example.test/audio")
    monkeypatch.setattr(meeting_jobs.transcribe, "submit", lambda *a, **k: submissions.append("asr-task") or "asr-task")
    monkeypatch.setattr(meeting_jobs.transcribe, "fetch_result", lambda tid: {"utterances": [{"text": "saved"}]})
    monkeypatch.setattr(meeting_jobs.transcribe, "parse_result", lambda value: value)
    def execute(p):
        key = meeting_jobs.prepare(p["owner"], p["request_id"], p["files"][0])
        meeting_jobs.submit_job(key)
        if not p.get("_retry"):
            return {"status": "pending", "text": "Awaiting transcript"}
        return {"text": meeting_jobs.collect_job(key)["result"]["utterances"][0]["text"]}
    before = Manager(config.DB_PATH, execute)
    task = before.submit({**payload(), "files": [str(audio)]})
    before.start()
    wait_for(before, task["id"], "interrupted")
    before.shutdown()
    after = Manager(config.DB_PATH, execute)
    try:
        after.start()
        after.retry(task["id"])
        assert wait_for(after, task["id"])["result"]["text"] == "saved"
        assert submissions == ["asr-task"]
    finally:
        after.shutdown()


def test_progress_api_reads_persisted_record_after_memory_clear(isolated):
    app = create_app(FakeGraph())
    manager = app.extensions["task_manager"]
    manager.submit(payload())
    manager.save_progress("local", "request", {"scene": "exam", "batch": {"total": 3, "done": 1}, "steps": []})
    with progress._lock:
        progress._records.pop("request", None)
    assert app.test_client().get("/api/progress?request_id=request").get_json()["batch"] == {"total": 3, "done": 1}
    manager.shutdown()


def test_retry_endpoint_rejects_missing_or_foreign_tasks(isolated):
    app = create_app(FakeGraph())
    manager = app.extensions["task_manager"]
    task = manager.submit(payload())
    try:
        client = app.test_client()
        assert client.post("/api/task/retry", json=[]).status_code == 400
        assert client.post("/api/task/retry", json={"task_id": task["id"], "owner": "other"}).status_code == 404
    finally:
        manager.start()
        manager.shutdown()


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/resume"])
def test_health_tracks_synchronous_graph_work_until_completion(isolated, endpoint):
    entered, release = threading.Event(), threading.Event()
    class BlockingGraph(FakeGraph):
        def invoke(self, state, cfg):
            entered.set()
            release.wait(3)
            return super().invoke(state, cfg)
        def get_state(self, cfg):
            return SimpleNamespace(values={"request_id": "request"}, next=("postprocess",))
    app = create_app(BlockingGraph())
    manager = app.extensions["task_manager"]
    try:
        with ThreadPoolExecutor(1) as pool:
            running = pool.submit(lambda: app.test_client().post(endpoint, data=payload()) if endpoint == "/api/chat"
                                  else app.test_client().get(endpoint))
            assert entered.wait(1)
            try:
                assert app.test_client().get("/health").get_json()["active_tasks"] == 1
            finally:
                release.set()
            assert running.result().status_code == 200
        assert app.test_client().get("/health").get_json()["active_tasks"] == 0
    finally:
        release.set()
        manager.shutdown()


def test_retryable_completed_graph_checkpoint_reenters_same_request(isolated):
    class PendingGraph(FakeGraph):
        def invoke(self, state, cfg):
            self.calls.append(state)
            return {"result": {"text": "waiting" if len(self.calls) == 1 else "ready", "retryable": len(self.calls) == 1}}
        def get_state(self, cfg):
            return SimpleNamespace(values={"request_id": "request", "result": {"retryable": True}}, next=())
    graph = PendingGraph()
    app = create_app(graph)
    manager = app.extensions["task_manager"]
    try:
        task = app.test_client().post("/api/chat/async", data=payload()).get_json()
        wait_for(manager, task["task_id"], "interrupted")
        assert store.receipt("local", "request", "handle") is None
        app.test_client().post("/api/task/retry", json={"task_id": task["task_id"]})
        assert wait_for(manager, task["task_id"])["result"]["text"] == "ready"
        assert len(graph.calls) == 2
        assert graph.calls[0] == graph.calls[1]
    finally:
        manager.shutdown()


def test_recovered_checkpoint_blocks_same_session_successor_until_explicit_retry(isolated):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, StateGraph
    from lg_assistant.graph import run_config
    from lg_assistant.state import AssistantState
    calls = []
    connection = sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False)
    builder = StateGraph(AssistantState)
    def finish(state):
        calls.append(state["request_id"])
        return {"result": {"text": state["request_id"]}}
    builder.add_node("finish", finish)
    builder.set_entry_point("finish")
    builder.add_edge("finish", END)
    graph = builder.compile(checkpointer=SqliteSaver(connection))
    app = create_app(graph)
    manager = app.extensions["task_manager"]
    first = manager.submit(payload("first"))
    successor = manager.submit(payload("successor"))
    other = manager.submit(payload("other", "another-session"))
    graph.invoke(payload("first"), run_config("local", "session"), interrupt_before=["finish"])
    with sqlite3.connect(config.DB_PATH) as db:
        db.execute("UPDATE web_tasks SET status='running', attempts=1 WHERE id=?", (first["id"],))
    try:
        manager.start()
        wait_for(manager, other["id"])
        assert manager.get(successor["id"])["status"] == "pending"
        assert graph.get_state(run_config("local", "session")).values["request_id"] == "first"
        assert calls == ["other"]
        assert app.test_client().post("/api/task/retry", json={"task_id": first["id"]}).status_code == 202
        wait_for(manager, first["id"])
        wait_for(manager, successor["id"])
        assert calls == ["other", "first", "successor"]
    finally:
        manager.shutdown()
        connection.close()


def test_live_external_pending_blocks_later_session_work_but_not_other_sessions(isolated):
    calls = []
    def execute(p):
        calls.append(p["request_id"])
        return {"status": "pending" if p["request_id"] == "first" and not p.get("_retry") else "ok"}
    manager = manager_type()(config.DB_PATH, execute)
    first = manager.submit(payload("first"))
    successor = manager.submit(payload("successor"))
    try:
        manager.start()
        wait_for(manager, first["id"], "interrupted")
        other = manager.submit(payload("other", "other-session"))
        wait_for(manager, other["id"])
        assert manager.get(successor["id"])["status"] == "pending"
        assert calls == ["first", "other"]
        manager.retry(first["id"])
        wait_for(manager, successor["id"])
        assert calls == ["first", "other", "first", "successor"]
    finally:
        manager.shutdown()


def test_shutdown_preserves_successors_blocked_on_interrupted_task(isolated):
    manager = manager_type()(config.DB_PATH, lambda p: {"status": "pending"})
    first = manager.submit(payload("first"))
    successor = manager.submit(payload("successor"))
    manager.start()
    wait_for(manager, first["id"], "interrupted")
    manager.shutdown(wait=False)
    try:
        for worker in manager._workers:
            worker.join(1)
        assert all(not worker.is_alive() for worker in manager._workers)
        assert manager.get(successor["id"])["status"] == "pending"
    finally:
        # Release daemon threads even if a broken implementation waits forever.
        with sqlite3.connect(config.DB_PATH) as db:
            db.execute("UPDATE web_tasks SET status='done'")
        manager.shutdown()


def test_stale_interrupted_predecessor_does_not_permanently_block_queue(isolated):
    class AdvancedGraph(FakeGraph):
        def get_state(self, cfg):
            return SimpleNamespace(values={"request_id": "already-newer"}, next=())
    graph = AdvancedGraph()
    app = create_app(graph)
    manager = app.extensions["task_manager"]
    first = manager.submit(payload("old"))
    successor = manager.submit(payload("next"))
    with sqlite3.connect(config.DB_PATH) as db:
        db.execute("UPDATE web_tasks SET status='interrupted', attempts=1 WHERE id=?", (first["id"],))
    try:
        manager.start()
        wait_for(manager, successor["id"])
        assert manager.get(first["id"])["status"] == "error"
        assert "新请求" in manager.get(first["id"])["error"]
        assert [p["request_id"] for p in graph.calls] == ["next"]
    finally:
        manager.shutdown()


@pytest.mark.parametrize("failure_status", ["error", "failed", "error_field"])
def test_returned_provider_error_retries_failed_receipt_and_checkpoint(isolated, failure_status):
    class TransientGraph(FakeGraph):
        def __init__(self):
            super().__init__()
            self.result = {}
        def invoke(self, state, cfg):
            self.calls.append(state)
            failure = {"error": "provider temporarily unavailable"} if failure_status == "error_field" else \
                {"status": failure_status, "text": "provider temporarily unavailable"}
            self.result = failure if len(self.calls) == 1 else {"text": "recovered"}
            return {"result": self.result}
        def get_state(self, cfg):
            return SimpleNamespace(values={"request_id": "request", "result": self.result}, next=())
    graph = TransientGraph()
    app = create_app(graph)
    manager = app.extensions["task_manager"]
    try:
        task = app.test_client().post("/api/chat/async", data=payload()).get_json()
        wait_for(manager, task["task_id"], "error")
        response = app.test_client().post("/api/task/retry", json={"task_id": task["task_id"]})
        assert response.status_code == 202
        assert wait_for(manager, task["task_id"])["result"]["text"] == "recovered"
        assert len(graph.calls) == 2
        assert graph.calls[0] == graph.calls[1]
    finally:
        manager.shutdown()


def test_ordinary_error_does_not_block_newer_request_in_same_session(isolated):
    calls = []
    def execute(p):
        calls.append(p["request_id"])
        return {"status": "error" if p["request_id"] == "first" else "ok"}
    manager = manager_type()(config.DB_PATH, execute)
    first = manager.submit(payload("first"))
    manager.start()
    try:
        wait_for(manager, first["id"], "error")
        newer = manager.submit(payload("newer"))
        wait_for(manager, newer["id"])
        assert calls == ["first", "newer"]
    finally:
        manager.shutdown()
