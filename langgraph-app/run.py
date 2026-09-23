#!/usr/bin/env python
"""LangGraph 版本命令行入口。

    python run.py check                       配置自检
    python run.py ask "计算 (18+24)*3"         单轮提问
    python run.py ask "我膝盖疼" -e '{"device":{"intent":"solve","confidence":0.95}}'
    python run.py stats                       端侧判对率
    python run.py graph                       打印图结构
    python run.py resume <owner> <session>    从检查点续跑（断连恢复）
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lg_assistant import config, graph, nodes, store  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def _compile(with_checkpointer: bool = True):
    cp = graph.open_checkpointer() if with_checkpointer else None
    return graph.build_graph(cp)


def cmd_check(_args) -> int:
    print(f"Python    : {sys.version.split()[0]}")
    print(f"项目根    : {config.ROOT}")
    print(f"数据目录  : {config.DATA_DIR}")
    print(f"检查点库  : {config.CHECKPOINT_DB}")
    print(f"执行后端  : {config.EXEC_BACKEND}")
    print(f"文本模型  : {config.MODEL_TEXT}")

    try:
        import langgraph  # noqa: F401
        import importlib.metadata as md

        print(f"LangGraph : {md.version('langgraph')}")
    except Exception as e:
        print(f"  [错误] 未安装 langgraph：{e}")
        return 1

    probs = config.problems()
    if probs:
        for p in probs:
            print(f"  [配置问题] {p}")
        if config.EXEC_BACKEND == "dify":
            return 1
        print("  （仅本地执行，可忽略模型 Key 缺失）")
    else:
        print("  配置      : OK")

    config.ensure_dirs()
    store.init()
    print("  目录/存储 : OK")
    return 0


def cmd_ask(args) -> int:
    config.ensure_dirs()
    event = {}
    if args.event:
        try:
            event = json.loads(args.event)
        except json.JSONDecodeError as e:
            print(f"[错误] --event 不是合法 JSON：{e}", file=sys.stderr)
            return 1

    app = _compile()
    state = {
        "text": args.text,
        "owner": args.owner,
        "session_id": args.session,
        "request_id": args.request_id or uuid.uuid4().hex[:12],
        "files": args.file or [],
        "event": event,
        "scene_hint": args.scene,
        "notes": [],
    }
    out = app.invoke(state, graph.run_config(args.owner, args.session, state["request_id"]))

    rt = out.get("routing") or {}
    res = out.get("result") or {}
    print(f"[场景 {rt.get('scene')} / 动作 {rt.get('action')} / 来源 {rt.get('source')} / 后端 {res.get('backend')}]")
    print(res.get("text") or "(无输出)")
    speech = out.get("speech") or ""
    if speech and speech != res.get("text"):
        print(f"\n[播报] {speech}")
    if res.get("note"):
        print(f"[说明] {res['note']}")
    for n in out.get("notes") or []:
        print(f"[提示] {n}")
    return 0


def cmd_stats(args) -> int:
    store.init()
    print(json.dumps(store.routing_stats(args.owner), ensure_ascii=False, indent=2))
    return 0


def cmd_graph(_args) -> int:
    app = _compile(with_checkpointer=False)
    print(app.get_graph().draw_ascii())
    return 0


def cmd_web(args) -> int:
    """起 Web 页（复用基线的前端资产，接口逐一对齐）。"""
    try:
        from lg_assistant.web.app import run as run_web
    except ImportError as e:
        print(f"[错误] 起 Web 页需要 Flask：{e}", file=sys.stderr)
        return 1
    run_web(host=args.host, port=args.port)
    return 0


def cmd_resume(args) -> int:
    """从检查点续跑。设备断连后用同一个 thread_id 恢复，不重跑已完成步骤。"""
    config.ensure_dirs()
    app = _compile()
    cfg = graph.run_config(args.owner, args.session)
    snap = app.get_state(cfg)
    if not snap or not snap.values:
        print(f"[错误] 线程 {graph.thread_id(args.owner, args.session)} 没有检查点", file=sys.stderr)
        return 1
    print(f"线程     : {graph.thread_id(args.owner, args.session)}")
    print(f"下一步   : {snap.next}")
    print(f"已完成步 : {len(snap.metadata.get('writes', {}) or {})} 个节点有写入")
    print(f"场景     : {(snap.values.get('routing') or {}).get('scene')}")
    print(f"正文     : {(snap.values.get('result') or {}).get('text', '')[:200]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run.py", description="LangGraph 版眼镜助手")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("check", help="配置自检")
    pc.set_defaults(func=cmd_check)

    pa = sub.add_parser("ask", help="单轮提问")
    pa.add_argument("text")
    pa.add_argument("-f", "--file", action="append", default=[], help="附带图片，可重复")
    pa.add_argument("-s", "--scene", default=None, choices=list(config.SCENES))
    pa.add_argument("-e", "--event", default=None, help='结构化事件 JSON')
    pa.add_argument("--owner", default="local")
    pa.add_argument("--session", default="default")
    pa.add_argument("--request-id", default=None)
    pa.set_defaults(func=cmd_ask)

    ps = sub.add_parser("stats", help="端侧判对率")
    ps.add_argument("--owner", default=None)
    ps.set_defaults(func=cmd_stats)

    pg = sub.add_parser("graph", help="打印图结构")
    pg.set_defaults(func=cmd_graph)

    pw = sub.add_parser("web", help="起 Web 页")
    pw.add_argument("--host", default="127.0.0.1")
    pw.add_argument("--port", type=int, default=8802)
    pw.set_defaults(func=cmd_web)

    pr = sub.add_parser("resume", help="从检查点续跑")
    pr.add_argument("owner")
    pr.add_argument("session")
    pr.set_defaults(func=cmd_resume)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
