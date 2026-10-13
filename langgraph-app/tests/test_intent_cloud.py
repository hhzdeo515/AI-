"""Intent gateway auth and transport boundaries; no live tools or model calls."""
import io
import json
import sys
from pathlib import Path
from unittest.mock import Mock

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lg_assistant.web import auth, intent_cloud

PAYLOAD = {"text": "先总结再翻译", "reason": "multiple_tools", "context": [], "requestId": "12345678-1234-1234-1234-123456789012"}


def test_requires_auth_and_reports_unconfigured(monkeypatch):
    monkeypatch.delenv("INTENT_CLOUD_URL", raising=False)
    app = Flask(__name__)
    auth.install(app, "test-access")
    app.register_blueprint(intent_cloud.blueprint)
    client = app.test_client()
    assert client.post("/api/intent-cloud", json=PAYLOAD).status_code == 401
    headers = {"Authorization": "Bearer test-access"}
    response = client.post("/api/intent-cloud", json=PAYLOAD, headers=headers)
    assert response.status_code == 503
    assert response.json["code"] == "intent_cloud_not_configured"
    assert client.post("/api/intent-cloud", json=PAYLOAD, headers={**headers, "Origin": "https://other.example"}).status_code == 403
    assert client.post("/api/intent-cloud", json={**PAYLOAD, "reason": "local"}, headers=headers).status_code == 400
    assert client.post("/api/intent-cloud", json={**PAYLOAD, "text": "x" * 17000}, headers=headers).status_code == 413


def test_forwards_only_scoped_text_and_validates_upstream(monkeypatch):
    monkeypatch.setenv("INTENT_CLOUD_URL", "https://cloud.example/tasks")
    monkeypatch.setenv("INTENT_CLOUD_TOKEN", "server-only-test-token")
    opener = Mock()
    opener.open.return_value = io.BytesIO(json.dumps({"reply": "实际工具结果", "secret": "hidden"}).encode())
    monkeypatch.setattr(intent_cloud, "build_opener", lambda *_: opener)
    body, status = intent_cloud.dispatch({**PAYLOAD, "audio": "not forwarded"})
    assert status == 200 and body == {"reply": "实际工具结果", "requestId": PAYLOAD["requestId"]}
    outgoing = opener.open.call_args.args[0]
    assert json.loads(outgoing.data) == PAYLOAD
    assert outgoing.get_header("Authorization") == "Bearer server-only-test-token"
    assert outgoing.get_header("Idempotency-key") == PAYLOAD["requestId"]
    opener.open.side_effect = TimeoutError("private upstream detail")
    body, status = intent_cloud.dispatch(PAYLOAD)
    assert status == 502 and "private" not in body["error"]
