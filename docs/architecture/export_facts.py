"""导出真实架构信息：LangGraph 图结构 + Dify 工作流节点表。

不凭记忆画图——所有节点与边都从代码/数据库里读出来。
写入 docs/architecture/_facts.md 供人工核对。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LG = Path(__file__).resolve().parents[2] / "langgraph-app"
sys.path.insert(0, str(LG))

from lg_assistant import graph  # noqa: E402

out: list[str] = []

# ---------------- LangGraph ----------------
app = graph.build_graph(None)
g = app.get_graph()
out.append("## LangGraph 图（draw_mermaid 原始输出）\n")
out.append("```mermaid")
out.append(app.get_graph().draw_mermaid())
out.append("```\n")

out.append("## LangGraph 节点\n")
for n in sorted(x for x in g.nodes if not x.startswith("__")):
    out.append(f"- `{n}`")
out.append("")

out.append("## LangGraph 条件边（dispatch 的可能去向）\n")
out.append("- `meta`   → meta_command")
out.append("- `vision` → exam_vision（带图片且场景=exam）")
out.append("- `dify`   → dify_scene（EXEC_BACKEND=dify）")
out.append("- `local`  → calc_quick")
out.append("")

out.append("## calc_quick 之后的二选一\n")
out.append("- `done` → postprocess（命中纯算术，零 token）")
out.append("- `llm`  → local_llm")
out.append("")

Path(__file__).resolve().parent.mkdir(parents=True, exist_ok=True)
(Path(__file__).resolve().parent / "_facts.md").write_text("\n".join(out), encoding="utf-8")
print("已写出 _facts.md")
print("---- draw_mermaid ----")
print(app.get_graph().draw_mermaid())
