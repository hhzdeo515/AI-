"""Web 层：Flask + LangGraph。

与 assistant-lite 的 Web 层接口**逐一对齐**（前端 `app.js` 原样复用），
但内部接的是状态图而不是 `Orchestrator`：

===================================  ==========================================
``POST /api/chat``                   同步跑图
``POST /api/chat/async``             异步提交，返回 task_id
``GET  /api/task``                   查异步任务
``GET  /api/state``                  读某 thread 的检查点状态
``GET  /api/routing-stats``          端侧判对率
``GET  /api/resources`` ``/api/resource``  资料归档
``GET  /api/export``                 导出（六种格式）
``GET  /api/speak``                  语音合成 + 播报代际号
``GET  /api/progress``               请求级进度
``GET  /api/resume``                 断点续跑（本版新增：基线的内存任务表做不到）
===================================  ==========================================

只绑定 127.0.0.1，不做多租户鉴权——面向单机个人使用。
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, make_response, render_template, request, send_file

from .. import config, llm, nodes, profile, progress, store, transcribe
from ..graph import build_graph, open_checkpointer, run_config, thread_id
from ..tools import export
from . import auth

SPEECH_MAX_CHARS = config.SPEECH_MAX_CHARS


def _as_int(value: Any) -> Any:
    """表单里的数字字段：能转就转，转不动原样返回（让纯函数去报错，别在这里猜）。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return value

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma"}
ALLOWED_EXT = IMAGE_EXT | AUDIO_EXT
MAX_UPLOAD_MB = 32

# --------------------------------------------------------------------------- #
# 请求级进度
#
# 记录本体在 lg_assistant/progress.py：链路（vision.py）在执行线程里上报步骤，
# Web 层在**另一个线程**里被轮询，两边靠 request_id 对齐。这里只做转发。
# --------------------------------------------------------------------------- #
def _progress_begin(rid: str, scene: str = "", step: str = "") -> None:
    progress.begin(rid, scene, step)


def _progress_finish(rid: str, error: bool = False) -> None:
    progress.finish(rid, error)


def _progress_snapshot(rid: str) -> dict[str, Any] | None:
    return progress.snapshot(rid)


# --------------------------------------------------------------------------- #
# 异步任务（线程池 + 内存表；与基线同样的取舍，进程重启即丢）
# --------------------------------------------------------------------------- #
_TASKS: dict[str, dict[str, Any]] = {}
_TASKS_LOCK = threading.RLock()


def _task_submit(fn) -> str:
    tid = uuid.uuid4().hex[:12]
    with _TASKS_LOCK:
        _TASKS[tid] = {"id": tid, "status": "pending", "result": None, "error": ""}

    def run() -> None:
        with _TASKS_LOCK:
            if tid in _TASKS:
                _TASKS[tid]["status"] = "running"
        try:
            value = fn()
        except Exception as e:  # 任务异常必须落到记录里
            with _TASKS_LOCK:
                _TASKS[tid].update(status="error", error=f"{type(e).__name__}: {e}")
            return
        with _TASKS_LOCK:
            _TASKS[tid].update(status="done", result=value)

    threading.Thread(target=run, daemon=True, name=f"lg-task-{tid}").start()
    return tid


# --------------------------------------------------------------------------- #
def _save_uploads(files) -> tuple[list[str], list[str]]:
    saved: list[str] = []
    rejected: list[str] = []
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for f in files or []:
        if not f or not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            rejected.append(f"{f.filename}（不支持的格式 {ext or '无扩展名'}）")
            continue
        dest = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
        f.save(dest)
        saved.append(str(dest))
    return saved, rejected


def _parse_chat_form():
    owner = (request.form.get("owner") or "local").strip() or "local"
    sid = (request.form.get("session_id") or "web").strip() or "web"
    text = request.form.get("text") or ""
    scene = (request.form.get("scene") or "").strip()
    if scene in ("", "auto"):
        scene = None
    rid = (request.form.get("request_id") or "").strip() or uuid.uuid4().hex[:12]

    files, rejected = _save_uploads(request.files.getlist("files"))
    if not text.strip() and not files:
        return None, (jsonify({"error": "请提供文字，或上传图片/音频"}), 400)

    event: dict = {}
    raw_event = (request.form.get("event") or "").strip()
    if raw_event:
        try:
            parsed = json.loads(raw_event)
            if isinstance(parsed, dict):
                event = parsed
        except json.JSONDecodeError:
            return None, (jsonify({"error": "event 不是合法 JSON"}), 400)

    return {
        "owner": owner,
        "session_id": sid,
        "text": text,
        "scene": scene,
        "request_id": rid,
        "files": files,
        "event": event,
        "rejected": rejected,
    }, None


def create_app(app: Any = None) -> Flask:
    """``app`` 为已编译的图；测试可注入桩图。默认自行编译（带 checkpointer）。"""
    config.ensure_dirs()
    store.init()

    flask_app = Flask(
        __name__,
        template_folder=str(config.WEB_DIR / "templates"),
        static_folder=str(config.WEB_DIR / "static"),
    )
    flask_app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    flask_app.config["JSON_AS_ASCII"] = False
    # 本地工具：静态资源每次回源校验，否则改了 app.js 浏览器还拿旧的
    flask_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # 访问口令：设了 ACCESS_TOKEN 才启用，留空则保持本地零摩擦
    auth.install(flask_app, config.ACCESS_TOKEN)

    compiled = app if app is not None else build_graph(open_checkpointer())

    def _run_chat(p: dict) -> dict:
        state = {
            "text": p["text"],
            "owner": p["owner"],
            "session_id": p["session_id"],
            "request_id": p["request_id"],
            "files": p["files"],
            "event": p["event"],
            "scene_hint": p["scene"],
            "notes": [],
        }
        cfg = run_config(p["owner"], p["session_id"], p["request_id"])

        # 请求级幂等：同 request_id 直接回放上次结果（断连补传的地基）
        cached = store.receipt(p["owner"], p["request_id"], "handle")
        if cached:
            out = dict(cached)
            out["status"] = "duplicate"
            out["rejected_files"] = p["rejected"]
            return out

        # 只有带图片的请求才走四步视觉链，进度条也只在这种情况下有意义。
        # 图片在上传阶段就已落盘，所以 capture 一开始就算完成，从 recognize 开始上报。
        has_image = any(Path(f).suffix.lower() in IMAGE_EXT for f in p["files"])
        rid = p["request_id"]
        _progress_begin(rid, "exam" if has_image else "", "recognize" if has_image else "")
        # 把 request_id 绑到执行线程上，链路里的 progress.mark() 才能写回这条记录
        progress.bind(rid)
        try:
            result = compiled.invoke(state, cfg)
        except BaseException:
            _progress_finish(rid, error=True)
            raise
        else:
            _progress_finish(rid)
        finally:
            progress.unbind()

        rt = result.get("routing") or {}
        res = result.get("result") or {}
        payload = {
            "text": res.get("text") or "",
            "speech": result.get("speech") or res.get("speech") or "",
            "scene": rt.get("scene") or "general",
            "action": rt.get("action") or "",
            "source": rt.get("source") or "",
            "status": "ok",
            "backend": res.get("backend") or "",
            "note": res.get("note") or "",
            "artifacts": res.get("artifacts") or [],
            "archived_id": result.get("archived_id") or "",
            "rejected_files": p["rejected"],
            "request_id": p["request_id"],
            "notes": result.get("notes") or [],
        }
        store.remember(p["owner"], p["request_id"], "handle", payload)
        return payload

    # ------------------------------------------------------------------ #
    @flask_app.get("/")
    def index():
        return render_template("index.html", scenes=config.SCENES)

    @flask_app.get("/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "engine": "langgraph",
                "scenes": list(config.SCENES),
                "exec_backend": config.EXEC_BACKEND,
                "model_text": config.MODEL_TEXT,
                "model_vision": config.MODEL_VISION,
                "api_key_configured": bool(config.DASHSCOPE_API_KEY),
                # 不泄露口令本身，只说明是否启用了鉴权
                "auth_enabled": auth.is_enabled(config.ACCESS_TOKEN),
                "data_dir": str(config.DATA_DIR),
            }
        )

    @flask_app.post("/api/chat")
    def api_chat():
        p, err = _parse_chat_form()
        if err:
            return err
        try:
            return jsonify(_run_chat(p))
        except Exception as e:  # 兜底，不把栈回溯吐给前端
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    @flask_app.post("/api/chat/async")
    def api_chat_async():
        p, err = _parse_chat_form()
        if err:
            return err
        tid = _task_submit(lambda: _run_chat(p))
        return jsonify({"task_id": tid, "request_id": p["request_id"], "status": "pending"}), 202

    @flask_app.get("/api/task")
    def api_task():
        tid = (request.args.get("task_id") or "").strip()
        with _TASKS_LOCK:
            rec = _TASKS.get(tid)
        if not rec:
            return jsonify({"error": "找不到该任务（可能已过期）"}), 404
        return jsonify(dict(rec))

    @flask_app.get("/api/state")
    def api_state():
        owner = (request.args.get("owner") or "local").strip() or "local"
        sid = (request.args.get("session_id") or "web").strip() or "web"
        snap = compiled.get_state(run_config(owner, sid))
        vals = snap.values if snap else {}
        return jsonify(
            {
                "thread_id": thread_id(owner, sid),
                "next": list(snap.next) if snap else [],
                "has_checkpoint": bool(snap and snap.values),
                "values": vals,
                # 兼容基线 UI：卡片直接读顶层字段，而不是从 values 里取。
                # 前端是从 assistant-lite 原样复用的，那里这两个键在顶层。
                "meeting": vals.get("meeting") or {},
                "fitness": vals.get("fitness") or {},
            }
        )

    @flask_app.get("/api/resume")
    def api_resume():
        """从检查点续跑。**本版新增**——基线的异步任务是内存表，重启即丢。"""
        owner = (request.args.get("owner") or "local").strip() or "local"
        sid = (request.args.get("session_id") or "web").strip() or "web"
        cfg = run_config(owner, sid)
        snap = compiled.get_state(cfg)
        if not snap or not snap.values:
            return jsonify({"error": "该会话没有检查点"}), 404
        if not snap.next:
            res = snap.values.get("result") or {}
            return jsonify({"resumed": False, "reason": "已执行完毕", "text": res.get("text", "")})
        result = compiled.invoke(None, cfg)
        res = result.get("result") or {}
        return jsonify({"resumed": True, "text": res.get("text", ""), "speech": result.get("speech", "")})

    @flask_app.get("/api/routing-stats")
    def api_routing_stats():
        owner = (request.args.get("owner") or "").strip() or None
        return jsonify(store.routing_stats(owner))

    @flask_app.get("/api/resources")
    def api_resources():
        owner = (request.args.get("owner") or "local").strip() or "local"
        return jsonify({"resources": store.list_resources(owner, limit=50)})

    @flask_app.get("/api/resource")
    def api_resource():
        owner = (request.args.get("owner") or "local").strip() or "local"
        rid = (request.args.get("id") or "").strip()
        row = store.get_resource(owner, rid) if rid else None
        if not row:
            return jsonify({"error": "找不到该资料"}), 404
        # 会议产出带一份可改判的逐段转写；前端据此决定要不要画「发言人」面板
        row["has_transcript"] = bool(rid and store.get_transcript(owner, rid))
        return jsonify(row)

    # ------------------------------------------------------------------ #
    # 转写人工改判
    #
    # 服务端的说话人聚类到此为止（实测 7 人聚成 6 类，指定人数也改不动），
    # 「谁说的是谁」剩下的错只能人来定：拆分、合并、改名。这里只做落库与
    # 重渲染，判定逻辑全是 transcribe 里的纯函数。
    # ------------------------------------------------------------------ #
    def _transcript_payload(rec: dict[str, Any]) -> dict[str, Any]:
        utterances = rec.get("utterances") or []
        report = transcribe.diarization_report(utterances)
        names = rec.get("names") or {}
        return {
            "id": rec["id"],
            "scene": rec.get("scene") or "",
            "names": names,
            "utterances": utterances,
            "speakers": transcribe.record_speakers(rec),
            "fragile": transcribe.fragile_flags(utterances),
            "text": transcribe.format_record(rec),
            "report": transcribe.describe_diarization(report),
            "summary": rec.get("summary") or "",
            "updated": rec.get("updated") or "",
        }

    def _persist_transcript(owner: str, rid: str, rec: dict[str, Any]) -> dict[str, Any]:
        """存记录 + 按新归属重建归档正文（纪要 + 文字记录）。"""
        payload = _transcript_payload(rec)
        content = nodes.meeting_body(
            rec.get("summary") or "", payload["report"], transcribe.format_record(rec)
        )
        store.save_transcript(owner, rid, scene=rec.get("scene") or "meeting", record=rec)
        store.update_resource(owner, rid, content)
        payload["content"] = content
        return payload

    @flask_app.get("/api/transcript")
    def api_transcript():
        owner = (request.args.get("owner") or "local").strip() or "local"
        rid = (request.args.get("id") or "").strip()
        rec = store.get_transcript(owner, rid) if rid else None
        if not rec:
            return jsonify({"error": "找不到该转写记录（可能不是会议产出）"}), 404
        return jsonify(_transcript_payload(rec))

    @flask_app.post("/api/transcript")
    def api_transcript_edit():
        owner = (request.form.get("owner") or "local").strip() or "local"
        rid = (request.form.get("id") or "").strip()
        action = (request.form.get("action") or "").strip()
        rec = store.get_transcript(owner, rid) if rid else None
        if not rec:
            return jsonify({"error": "找不到该转写记录（可能不是会议产出）"}), 404

        if action == "resummarize":
            text = transcribe.format_record(rec)
            try:
                rec["summary"] = nodes.resummarize_meeting(text)
            except llm.LLMError as e:
                return jsonify({"error": f"重新生成纪要失败：{e}"}), 502
            return jsonify(_persist_transcript(owner, rid, rec))

        kw: dict[str, Any] = {}
        for field in ("speaker", "source", "target"):
            if request.form.get(field) not in (None, ""):
                raw = str(request.form.get(field))
                kw[field] = raw if field == "speaker" and raw == "new" else _as_int(raw)
        if request.form.get("name") is not None:
            kw["name"] = request.form.get("name")
        if request.form.get("index") not in (None, ""):
            kw["index"] = _as_int(request.form.get("index"))

        try:
            edited = transcribe.apply_edit(rec, action, **kw)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(_persist_transcript(owner, rid, edited))

    @flask_app.get("/api/export")
    def api_export():
        owner = (request.args.get("owner") or "local").strip() or "local"
        rid = (request.args.get("id") or "").strip()
        fmt = (request.args.get("format") or "md").strip().lower()
        try:
            if rid:
                row = store.get_resource(owner, rid)
                if not row:
                    return jsonify({"error": f"找不到资料：{rid}"}), 404
                path = export.export_rows([row], fmt)
            else:
                rows = store.list_full(owner, limit=1)
                path = export.export_rows(rows, fmt)
        except export.ExportError as e:
            return jsonify({"error": str(e)}), 400
        return send_file(
            str(path),
            as_attachment=True,
            download_name=f"{path.stem[:8]}.{path.suffix.lstrip('.')}",
        )

    @flask_app.get("/api/speak")
    def api_speak():
        """把播报语合成为音频。响应头带播报代际号，设备端比对即可实现打断。"""
        text = (request.args.get("text") or "").strip()
        if not text:
            return jsonify({"error": "缺少 text"}), 400
        if len(text) > config.SPEECH_MAX_CHARS:
            return jsonify(
                {"error": f"播报语过长（上限 {config.SPEECH_MAX_CHARS} 字）；长内容应走 text"}
            ), 400
        try:
            audio = llm.tts(text)
        except llm.LLMError as e:
            return jsonify({"error": str(e)}), 502
        resp = make_response(audio)
        resp.headers["Content-Type"] = "audio/mpeg"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @flask_app.get("/api/progress")
    def api_progress():
        rid = (request.args.get("request_id") or "").strip()
        return jsonify(_progress_snapshot(rid) or {"scene": "", "steps": [], "finished": True})

    @flask_app.post("/api/reset")
    def api_reset():
        """清空某会话：删掉检查点线程（下次请求即全新状态）。"""
        owner = (request.form.get("owner") or "local").strip() or "local"
        sid = (request.form.get("session_id") or "web").strip() or "web"
        try:
            compiled.checkpointer.delete_thread(thread_id(owner, sid))
        except Exception as e:
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ #
    # 健康档案：**手机端写入，眼镜端只读**
    #
    # 建档问卷有 9 个字段，眼镜端只有麦克风、没有屏幕：用户既看不到还剩
    # 几项，也改不了上一项填错的内容。所以主入口是手机（有屏幕、能改能确认），
    # 眼镜端只读档案、把它带进训练建议。
    #
    # GET 返回的东西要够前端**直接渲染**：字段清单（fields）由服务端给，
    # 前端不硬编码——否则加一个字段要改两处，迟早对不上。
    # ------------------------------------------------------------------ #
    def _profile_payload(owner: str) -> dict[str, Any]:
        data = store.load_profile(owner)
        return {
            "profile": data,
            "summary": profile.summarize(data),
            "bmi": profile.bmi(data),
            "risk": profile.risk_notes(data),
            "fields": profile.field_spec(),
            "complete": profile.is_complete(data),
            "missing": profile.missing_keys(data),
            "meta": store.profile_meta(owner),
            "note": "" if data else "尚未建立健康档案。",
        }

    @flask_app.get("/api/profile")
    def api_profile():
        owner = (request.args.get("owner") or "local").strip() or "local"
        return jsonify(_profile_payload(owner))

    @flask_app.post("/api/profile")
    def api_profile_save():
        """手机端提交整份档案。

        ``partial=1`` 时允许缺字段（存草稿），默认要求 9 项齐全——
        免得用户以为存上了、其实眼镜端读到的是一份半成品。
        """
        owner = (request.form.get("owner") or "local").strip() or "local"
        raw = request.form.get("profile") or ""
        try:
            submitted = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            return jsonify({"error": f"档案不是合法 JSON：{e}"}), 400
        if not isinstance(submitted, dict):
            return jsonify({"error": "档案必须是一个对象"}), 400

        partial = str(request.form.get("partial") or "") in ("1", "true", "yes")
        data, errors = profile.validate(submitted, partial=partial)
        if errors:
            return jsonify({"error": "；".join(errors), "errors": errors}), 400

        store.save_profile(owner, data, source="phone")
        return jsonify(_profile_payload(owner))

    @flask_app.delete("/api/profile")
    def api_profile_delete():
        owner = (request.args.get("owner") or "local").strip() or "local"
        return jsonify({"ok": store.delete_profile(owner)})

    return flask_app


def run(host: str | None = None, port: int | None = None) -> None:
    host = host or config.WEB_HOST
    port = int(port or config.WEB_PORT)
    app = create_app()

    lan = host not in ("127.0.0.1", "localhost")
    print(f"langgraph-app Web 已启动： http://{host}:{port}")
    if host == "0.0.0.0":
        for ip in _lan_ips():
            print(f"  局域网可访问： http://{ip}:{port}")

    if not config.DASHSCOPE_API_KEY:
        print("  [警告] 未配置 DASHSCOPE_API_KEY，需要模型的场景会失败。")

    # 对外暴露却没设口令，是这次改动最想拦住的一种配置
    if lan and not auth.is_enabled(config.ACCESS_TOKEN):
        print("  " + "!" * 62)
        print("  [危险] 正在对局域网/公网监听，但没有设置 ACCESS_TOKEN。")
        print("         任何能访问该地址的人都可以使用你的 API Key（消耗额度）")
        print("         并读写本机 data/ 目录下的资料与上传文件。")
        print("         请在 .env 里设置 ACCESS_TOKEN=<一段足够长的随机字符串> 后重启。")
        print("  " + "!" * 62)

    app.run(host=host, port=port, threaded=True)


def _lan_ips() -> list[str]:
    """列出本机局域网 IPv4，供启动时直接给出可访问地址。"""
    import socket

    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips
