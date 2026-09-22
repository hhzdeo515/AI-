#!/usr/bin/env python
"""assistant-lite 命令行入口。

用法：
    python run.py check                  # 配置自检 + 模型连通性
    python run.py ask "帮我总结这场会议"    # 单轮提问
    python run.py ask "解这道题" -f a.png  # 单轮带图片
    python run.py chat                   # 交互式对话（可换场景）
    python run.py web --port 8801        # 起本地 Web 页
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assistant_lite import config  # noqa: E402

# Windows 控制台默认 GBK，中文会乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# --------------------------------------------------------------------------- #
# 编排层接入（P0 尚无 orchestrator，直连模型兜底；P1 起走编排）
# --------------------------------------------------------------------------- #
_orchestrator: Any = None


def _get_orchestrator() -> Any:
    global _orchestrator
    if _orchestrator is None:
        from assistant_lite.orchestrator import Orchestrator

        _orchestrator = Orchestrator()
    return _orchestrator


def _direct_brain(task: Any) -> Any:
    """兜底：编排层还没写出来时，直连文本模型。"""
    from assistant_lite import llm
    from assistant_lite.schemas import Reply

    text = llm.chat([{"role": "user", "content": task.text}])
    return Reply(text=text, scene="general", action="direct")


def make_task(
    text: str,
    owner: str = "local",
    session_id: str = "default",
    files: list[str] | None = None,
    scene_hint: str | None = None,
    event: dict[str, Any] | None = None,
) -> Any:
    from assistant_lite.schemas import Task

    return Task(
        text=text,
        owner=owner,
        session_id=session_id,
        request_id=uuid.uuid4().hex[:12],
        files=files or [],
        scene_hint=scene_hint,
        event=event or {},
    )


def handle(task: Any) -> Any:
    try:
        return _get_orchestrator().handle(task)
    except ImportError:
        return _direct_brain(task)


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_check(_args: argparse.Namespace) -> int:
    print(f"Python   : {sys.version.split()[0]}")
    print(f"项目根   : {config.ROOT}")
    print(f"数据目录 : {config.DATA_DIR}")
    print(f"文本模型 : {config.MODEL_TEXT}")
    print(f"视觉模型 : {config.MODEL_VISION}")
    print(f"语音模型 : {config.MODEL_ASR}")

    probs = config.problems()
    if probs:
        for p in probs:
            print(f"  [配置错误] {p}")
        return 1
    print("  配置     : OK")

    config.ensure_dirs()
    print("  目录     : OK")

    from assistant_lite import llm

    try:
        out = llm.chat(
            [{"role": "user", "content": "只回复两个字：就绪"}], max_tokens=16
        )
        print(f"  模型连通 : OK -> {out!r}")
    except llm.LLMError as e:
        print(f"  模型连通 : 失败 -> {e}")
        return 1
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    event: dict[str, Any] = {}
    if args.event:
        try:
            event = json.loads(args.event)
        except json.JSONDecodeError as e:
            print(f"[错误] --event 不是合法 JSON：{e}", file=sys.stderr)
            return 1
        if not isinstance(event, dict):
            print("[错误] --event 必须是 JSON 对象", file=sys.stderr)
            return 1
    task = make_task(
        args.text,
        owner=args.owner,
        session_id=args.session,
        files=args.file,
        scene_hint=args.scene,
        event=event,
    )
    try:
        reply = handle(task)
    except Exception as e:  # 顶层兜底，避免栈回溯糊屏
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"[场景 {reply.scene} / 动作 {reply.action} / 状态 {reply.status}]")
    print(reply.text)
    if reply.speech and reply.speech != reply.text:
        print(f"\n[播报] {reply.speech}")
    for a in reply.artifacts:
        ref = a.get("path") or a.get("id") or ""
        print(f"  -> {a.get('label', '产出')}: {ref}")
    return 0 if reply.status != "error" else 1


def cmd_chat(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    print("assistant-lite 交互对话。输入 /scene <meeting|exam|fitness|general> 切场景，/quit 退出。")
    scene: str | None = None
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            break
        if line.startswith("/scene"):
            parts = line.split()
            scene = parts[1] if len(parts) > 1 and parts[1] != "auto" else None
            print(f"  场景已设为 {scene or '自动'}")
            continue
        task = make_task(line, owner=args.owner, session_id=args.session, scene_hint=scene)
        try:
            reply = handle(task)
        except Exception as e:
            print(f"[错误] {type(e).__name__}: {e}")
            continue
        print(f"[{reply.scene}/{reply.action}] {reply.text}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    from assistant_lite import session
    from assistant_lite.tools import export as ex

    session.init()
    try:
        if args.id:
            path = ex.export_resource(args.owner, args.id, args.format)
        else:
            rows = session.list_full(args.owner, args.scene, limit=args.limit)
            if not rows:
                print("没有找到可导出的资料。", file=sys.stderr)
                return 1
            if not args.all and len(rows) > 1:
                rows = rows[:1]
            path = ex.export_rows(rows, args.format)
    except ex.ExportError as e:
        print(f"[导出失败] {e}", file=sys.stderr)
        return 1
    print(f"已导出：{path}")
    return 0


def cmd_resources(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    from assistant_lite import session

    session.init()
    rows = session.list_resources(args.owner, args.scene, limit=args.limit)
    if not rows:
        print("（暂无资料）")
        return 0
    for r in rows:
        print(f"{r['id'][:8]}  {r['scene']:<8} {r['title']}  ({r['size']} 字, {r['created'][:19]})")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    try:
        from assistant_lite.web.app import run as run_web
    except ImportError as e:
        print(f"[未实现] Web 页属于 P5 阶段：{e}", file=sys.stderr)
        return 1
    run_web(host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run.py", description="assistant-lite 命令行入口")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("check", help="配置自检 + 模型连通性")
    pc.set_defaults(func=cmd_check)

    pa = sub.add_parser("ask", help="单轮提问")
    pa.add_argument("text", help="提问内容")
    pa.add_argument("-f", "--file", action="append", default=[], help="附带图片/音频，可重复")
    pa.add_argument("-s", "--scene", default=None, choices=list(config.SCENES))
    pa.add_argument(
        "-e",
        "--event",
        default=None,
        help='结构化事件 JSON，如 \'{"semantic_action":"set_done"}\'（模拟眼镜按键/语音事件）',
    )
    pa.add_argument("--owner", default="local")
    pa.add_argument("--session", default="default")
    pa.set_defaults(func=cmd_ask)

    pch = sub.add_parser("chat", help="交互式对话")
    pch.add_argument("--owner", default="local")
    pch.add_argument("--session", default="default")
    pch.set_defaults(func=cmd_chat)

    pe = sub.add_parser("export", help="导出已归档资料为 md/txt/json/csv/docx/pdf")
    pe.add_argument("--owner", default="local")
    pe.add_argument("--id", default=None, help="资料 ID；不给则取最新一份")
    pe.add_argument("-s", "--scene", default=None, choices=list(config.SCENES))
    pe.add_argument(
        "-f", "--format", default="md", choices=["md", "txt", "json", "csv", "docx", "pdf"]
    )
    pe.add_argument("--all", action="store_true", help="导出全部匹配资料（默认只导最新一份）")
    pe.add_argument("--limit", type=int, default=20)
    pe.set_defaults(func=cmd_export)

    pr = sub.add_parser("resources", help="列出已归档资料")
    pr.add_argument("--owner", default="local")
    pr.add_argument("-s", "--scene", default=None, choices=list(config.SCENES))
    pr.add_argument("--limit", type=int, default=20)
    pr.set_defaults(func=cmd_resources)

    pw = sub.add_parser("web", help="起本地 Web 页")
    pw.add_argument("--host", default="127.0.0.1")
    pw.add_argument("--port", type=int, default=8801)
    pw.set_defaults(func=cmd_web)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
