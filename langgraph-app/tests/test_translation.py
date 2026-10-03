"""Shared translation contracts at the HTTP/provider boundary; no live requests."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lg_assistant.web import auth, translation  # noqa: E402

CASES = json.loads((ROOT.parent / "frontend/tests/fixtures/translation-cases.json").read_text(encoding="utf-8"))


def _provider(body):
    return io.BytesIO(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def test_multilingual_provider_requests(monkeypatch):
    for item in CASES["inputs"]:
        def upstream(request, timeout):
            url = urlsplit(request.full_url)
            parameters = parse_qs(url.query)
            assert url.scheme + "://" + url.netloc + url.path == "https://api.mymemory.translated.net/get"
            assert parameters["q"] == [item["text"]]
            assert parameters["langpair"] == [item["source"] + "|" + item["target"]]
            assert "key" not in parameters
            assert timeout <= 10
            return _provider({"responseStatus": 200, "quotaFinished": False, "responseData": {"translatedText": item["translation"]}})
        monkeypatch.setattr(translation, "urlopen", upstream)
        assert translation.translate_text(item) == {"translation": item["translation"], "source": item["source"], "target": item["target"], "provider": "MyMemory"}


def test_supported_languages_and_same_language_stays_local(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Same-language must stay local")
    monkeypatch.setattr(translation, "urlopen", forbidden)
    for language in CASES["languages"]:
        assert translation.translate_text({"text": "保持原文", "source": language, "target": language}) == {"translation": "保持原文", "source": language, "target": language, "provider": "MyMemory"}


def test_invalid_inputs_and_utf8_byte_limit():
    for item in CASES["invalid"]:
        with pytest.raises(translation.TranslationError) as caught:
            translation.translate_text(item["payload"])
        assert caught.value.status == item["status"]
    with pytest.raises(translation.TranslationError) as caught:
        translation.translate_text({"text": "中" * 167, "source": "zh-CN", "target": "en"})
    assert caught.value.status == 400
    assert len(translation.translate_text({"text": "中" * 166, "source": "zh-CN", "target": "zh-CN"})["translation"]) == 166


def test_upstream_errors_and_quotas(monkeypatch):
    for item in CASES["upstreamErrors"]:
        monkeypatch.setattr(translation, "urlopen", lambda *args, **kwargs: _provider(item["body"]))
        with pytest.raises(translation.TranslationError) as caught:
            translation.translate_text({"text": "Hello", "source": "en", "target": "ja"})
        assert caught.value.status == item["status"]


def test_timeouts_are_502_without_sensitive_details(monkeypatch):
    def unavailable(*args, **kwargs):
        raise TimeoutError("private provider details")
    monkeypatch.setattr(translation, "urlopen", unavailable)
    with pytest.raises(translation.TranslationError) as caught:
        translation.translate_text({"text": "Hello", "source": "en", "target": "ja"})
    assert caught.value.status == 502
    assert caught.value.code == "translation_timeout"
    assert "private" not in str(caught.value)


def test_flask_translation_requires_existing_auth_and_validates_http():
    app = Flask(__name__)
    auth.install(app, "translation-test-access-token")
    app.register_blueprint(translation.blueprint)
    client = app.test_client()
    payload = {"text": "你好", "source": "zh-CN", "target": "zh-CN"}
    assert client.post("/api/translate", json=payload).status_code == 401
    headers = {"Authorization": "Bearer translation-test-access-token"}
    assert client.get("/api/translate", headers=headers).status_code == 405
    assert client.post("/api/translate", headers=headers, data="Hello").status_code == 415
    assert client.post("/api/translate", headers={**headers, "Origin": "https://unrelated.example"}, json=payload).status_code == 403
    assert client.post("/api/translate", headers={**headers, "Origin": "https://localhost"}, json=payload).status_code == 403
    assert client.post("/api/translate", headers={**headers, "Content-Type": "application/json"}, data="{").status_code == 400
    response = client.post("/api/translate", headers={**headers, "Origin": "http://localhost"}, json=payload)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {"translation": "你好", "source": "zh-CN", "target": "zh-CN", "provider": "MyMemory"}
