"""Restricted Amap JS API proxy; security code never enters browser config."""
import os
import re
from urllib.parse import quote, quote_plus, urlencode

import requests
from flask import Blueprint, Response, jsonify, request

from .translation import TranslationError

blueprint=Blueprint("maps",__name__)
UPSTREAMS={
    "v4/map/styles":"https://webapi.amap.com/v4/map/styles",
    "v3/vectormap":"https://fmap01.amap.com/v3/vectormap",
    **{path:"https://restapi.amap.com/"+path for path in ("v3/place/text","v3/place/around","v3/place/detail","v3/assistant/inputtips","v3/assistant/coordinate/convert","v3/geocode/geo","v3/geocode/regeo","v3/config/district")},
}


def _settings():
    key=(os.getenv("AMAP_JS_API_KEY") or "").strip()
    security=(os.getenv("AMAP_SECURITY_JS_CODE") or "").strip()
    if not key or not security:
        raise TranslationError("高德地图尚未配置，IMU 模拟仍可使用。",503,"map_not_configured")
    return key,security


def _origin():
    if request.headers.get("Sec-Fetch-Site")=="cross-site" or request.headers.get("Origin",request.host_url.rstrip("/"))!=request.host_url.rstrip("/"):
        raise TranslationError("请求来源不受支持。",403,"invalid_request_origin")


def _failure(error):
    response=jsonify({"error":str(error),"code":error.code})
    response.status_code=error.status
    response.headers["Cache-Control"]="no-store"
    return response


@blueprint.get("/api/map-config")
def map_config():
    try:
        _origin()
        key,_security=_settings()
        response=jsonify({"key":key,"serviceHost":"/_AMapService"})
        response.headers["Cache-Control"]="no-store"
        return response
    except TranslationError as error:
        return _failure(error)


@blueprint.get("/api/amap-proxy")
@blueprint.get("/_AMapService/<path:proxy_path>")
def map_proxy(proxy_path=None):
    upstream=None
    try:
        _origin()
        key,security=_settings()
        path=proxy_path if proxy_path is not None else request.args.get("path")
        url=UPSTREAMS.get(path)
        if not url or len(request.query_string)>8192:
            raise TranslationError("不支持该地图服务请求。",400,"invalid_map_request")
        params=[]
        for name,value in request.args.items(multi=True):
            if name in {"path","key","jscode"}:
                continue
            if name in {"callback","jsonp"} and not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*",value,flags=re.ASCII):
                raise TranslationError("地图请求回调无效。",400,"invalid_map_request")
            params.append((name,value))
        params.extend((("key",key),("jscode",security)))
        upstream=requests.get(url+"?"+urlencode(params),allow_redirects=False,stream=True,timeout=(3,10),headers={"Accept":"application/json, application/javascript, application/octet-stream"})
        kind=upstream.headers.get("Content-Type","application/octet-stream")
        if upstream.status_code!=200 or not re.match(r"^(?:application/(?:json|javascript|octet-stream|x-protobuf)|text/(?:plain|javascript))\b",kind,flags=re.I):
            raise TranslationError("高德地图服务暂时不可用，请稍后重试。",502,"map_upstream_unavailable")
        chunks=[]
        size=0
        for chunk in upstream.iter_content(chunk_size=65536):
            size+=len(chunk)
            if size>5*1024*1024:
                raise TranslationError("高德地图服务暂时不可用，请稍后重试。",502,"map_upstream_unavailable")
            chunks.append(chunk)
        body=b"".join(chunks)
        if any(value.encode("utf-8") in body for value in (security,quote(security,safe=""),quote_plus(security,safe=""))):
            raise TranslationError("高德地图服务暂时不可用，请稍后重试。",502,"map_upstream_unavailable")
        return Response(body,content_type=kind,headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
    except TranslationError as error:
        return _failure(error)
    except requests.RequestException:
        return _failure(TranslationError("高德地图服务暂时不可用，请稍后重试。",502,"map_upstream_unavailable"))
    finally:
        if upstream is not None:
            upstream.close()
