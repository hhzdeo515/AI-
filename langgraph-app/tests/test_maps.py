"""Amap configuration and restricted security-code proxy behind global auth."""
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import pytest
from flask import Flask

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from lg_assistant.web import auth, maps  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AMAP_JS_API_KEY","test-js-map-key")
    monkeypatch.setenv("AMAP_SECURITY_JS_CODE","test-private-map-security")
    app=Flask(__name__)
    auth.install(app,"test-only-global-access-token")
    app.register_blueprint(maps.blueprint)
    return app.test_client()


HEADERS={"Authorization":"Bearer test-only-global-access-token"}


def test_config_requires_auth_and_returns_key_without_security(client,monkeypatch):
    assert client.get("/api/map-config").status_code==401
    result=client.get("/api/map-config",headers=HEADERS)
    assert result.status_code==200
    assert result.get_json()=={"key":"test-js-map-key","serviceHost":"/_AMapService"}
    assert "test-private-map-security" not in result.get_data(as_text=True)
    assert result.headers["Cache-Control"]=="no-store"
    monkeypatch.delenv("AMAP_SECURITY_JS_CODE")
    result=client.get("/api/map-config",headers=HEADERS)
    assert result.status_code==503 and result.get_json()["code"]=="map_not_configured"


def test_proxy_has_fixed_upstream_and_overrides_client_credentials(client,monkeypatch):
    class Upstream:
        status_code=200
        headers={"Content-Type":"application/json"}
        def iter_content(self,chunk_size): return iter([b'{"status":"1","pois":[]}'])
        def close(self): pass
    def fetch(url,**options):
        parsed=urlsplit(url); query=parse_qs(parsed.query)
        assert parsed.scheme+"://"+parsed.netloc=="https://restapi.amap.com"
        assert parsed.path=="/v3/place/text"
        assert query["key"]==["test-js-map-key"]
        assert query["jscode"]==["test-private-map-security"]
        assert options["allow_redirects"] is False
        return Upstream()
    monkeypatch.setattr(maps.requests,"get",fetch)
    result=client.get("/_AMapService/v3/place/text?keywords=上海&jscode=forged&key=forged",headers=HEADERS)
    assert result.status_code==200 and b'"status":"1"' in result.data
    assert client.get("/api/amap-proxy?path=v3/place/text").status_code==401


def test_proxy_rejects_arbitrary_paths_and_jsonp_execution(client):
    for path in ["https://evil.example/v3/place/text","../v3/place/text","v3/cloud/search","v3/place/text/extra"]:
        assert client.get("/api/amap-proxy",query_string={"path":path},headers=HEADERS).status_code==400
    assert client.get("/_AMapService/v3/place/text?callback=alert(1)",headers=HEADERS).status_code==400
    assert client.get("/api/map-config",headers={**HEADERS,"Origin":"https://other.example"}).status_code==403


def test_upstream_cannot_reflect_private_security_code(client,monkeypatch):
    class Upstream:
        status_code=200
        headers={"Content-Type":"text/plain"}
        def iter_content(self,chunk_size): return iter([b'test-private-map-security'])
        def close(self): pass
    monkeypatch.setattr(maps.requests,"get",lambda *args,**kwargs:Upstream())
    result=client.get("/_AMapService/v3/place/text?keywords=上海",headers=HEADERS)
    assert result.status_code==502
    assert "test-private-map-security" not in result.get_data(as_text=True)


@pytest.mark.parametrize("encode",[quote,quote_plus])
def test_upstream_cannot_reflect_url_encoded_security_code(client,monkeypatch,encode):
    security="test private+map/code"
    monkeypatch.setenv("AMAP_SECURITY_JS_CODE",security)
    class Upstream:
        status_code=200
        headers={"Content-Type":"text/plain"}
        def iter_content(self,chunk_size): return iter([encode(security,safe="").encode("ascii")])
        def close(self): pass
    monkeypatch.setattr(maps.requests,"get",lambda *args,**kwargs:Upstream())
    result=client.get("/_AMapService/v3/place/text",headers=HEADERS)
    assert result.status_code==502 and encode(security,safe="") not in result.get_data(as_text=True)


def test_upstream_failure_and_size_limit_are_not_successful_map_responses(client,monkeypatch):
    class Upstream:
        status_code=200
        headers={"Content-Type":"application/octet-stream"}
        def iter_content(self,chunk_size): return iter([b"x"*(5*1024*1024+1)])
        def close(self): pass
    monkeypatch.setattr(maps.requests,"get",lambda *args,**kwargs:Upstream())
    assert client.get("/_AMapService/v3/place/text",headers=HEADERS).status_code==502
    def timeout(*args,**kwargs): raise maps.requests.Timeout("private upstream URL must not leak")
    monkeypatch.setattr(maps.requests,"get",timeout)
    result=client.get("/_AMapService/v3/place/text",headers=HEADERS)
    assert result.status_code==502 and "private upstream" not in result.get_data(as_text=True)
