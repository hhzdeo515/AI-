"""JEV/VL exam subgraph; shares the original app's storage and reference retrieval."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import exam_knowledge as knowledge
from . import llm, progress, search, vision, public_knowledge, exam_decision
from . import exam_graph as shared_exam

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
PROFILES = {"auto": "自动判断", "recruitment": "国央企 / 事业单位招聘", "internal": "单位内部考试", "campus": "高校课程测试"}
SPECIALISTS = {
    "quantitative": "数量与资料 SubAgent：统一单位与时间口径，列出关键算式供工具验证；高校数学保留适用条件。",
    "reasoning": "逻辑与图形 SubAgent：回看原图，以同一规则核验全部已知条件及选项；区分必要充分与否定问法。",
    "knowledge": "专业知识 SubAgent：区分招聘常识、内部制度和课程定义；依据指定版本资料，不把通用知识代替内部规定。",
    "general": "言语与综合 SubAgent：核对题型（单选、多选、判断、填空、简答）、否定词、选项对应与上下文；专业题超出依据时明确缺口。",
}


class ExamState(TypedDict, total=False):
    owner: str
    text: str
    files: list[str]
    event: dict
    request_id: str
    last_images: list[str]
    images: list[str]
    reused: bool
    options: dict
    observation: str
    specialist: str
    evidence: str
    references: list[dict]
    sources: list[dict]
    evidence_missing: str
    draft: dict
    tools: dict
    final: dict
    error: str
    repair_count: int
    feedback: str
    trace: list[dict]
    first_draft: dict
    attempts: list[dict]
    independent_draft: dict
    independent_error: str
    perception: dict
    jev_decisions: list[dict]
    result: dict


def options(event: Any) -> dict:
    return shared_exam.options(event)


def prepare(s: ExamState) -> dict:
    attached = s.get("files") or []
    images = [f for f in attached if Path(f).suffix.lower() in IMAGE_EXT]
    reused = False
    if not images and not attached:
        images = [f for f in s.get("last_images", []) if Path(f).suffix.lower() in IMAGE_EXT and Path(f).is_file()][-1:]
        reused = bool(images)
    # Every request resets private state, including a previous repair or selected documents.
    out = dict(images=images, reused=reused, trace=[], error="", observation="", draft={}, tools={}, final={},
               references=[], sources=[], evidence="", evidence_missing="", repair_count=0, feedback="", first_draft={}, attempts=[], independent_draft={}, independent_error="", perception={}, jev_decisions=[])
    try:
        out["options"] = options(s.get("event"))
    except (ValueError, TypeError) as exc:
        out.update(options=options({}), error=str(exc))
    if not images:
        out["error"] = "没有找到可识别的图片。请上传题目照片（png/jpg/webp）。"
    return out


def perceive(s: ExamState) -> dict:
    perception = exam_decision.perceive(s.get("text", ""), s["images"], s.get("feedback", ""))
    result = {"observation": json.dumps(perception, ensure_ascii=False), "perception": perception,
              "references": [], "sources": [], "evidence": "", "evidence_missing": ""}
    if not perception["complete"]:
        result["error"] = "题面不完整：" + "；".join(str(x) for x in perception["missing"] or ["请补拍清晰、完整的题干和选项"])
    return result


def classify(s: ExamState) -> dict:
    kind, record = exam_decision.route(s["perception"], s.get("text", ""), s["options"])
    return {"specialist": kind, "jev_decisions": [*s["jev_decisions"], record]}


def retrieve(s: ExamState) -> dict:
    return shared_exam.retrieve(s)


def request_text(s: ExamState) -> str:
    opt = s["options"]
    return f"场景：{PROFILES[opt['profile']]}；考试时间口径：{opt['exam_date'] or '遵守题干年份，未给年份不擅自补全'}。\n{s.get('text', '')}"


def solve(s: ExamState) -> dict:
    draft = exam_decision.solve(request_text(s), s["perception"], s["evidence"], SPECIALISTS[s["specialist"]], s.get("feedback", ""), images=s["images"])
    if type(draft.get("answerable")) is not bool:
        raise vision.VisionError("初解缺少有效的 answerable 布尔字段")
    return {"draft": draft, "first_draft": s.get("first_draft") or draft,
            "attempts": [*s.get("attempts", []), {"draft": draft, "feedback": s.get("feedback", "")} ]}


def verify_tools(s: ExamState) -> dict:
    return {"tools": exam_decision.run_tools(s["draft"])}


def independent_reasoning(s: ExamState) -> dict:
    try:
        draft = exam_decision.solve(request_text(s), s["perception"], s["evidence"], SPECIALISTS[s["specialist"]], independent=True, images=s["images"])
        if type(draft.get("answerable")) is not bool:
            raise vision.VisionError("独立解答缺少有效的 answerable 字段")
    except (vision.VisionError, llm.LLMError) as exc:
        # An optional second reader outage must not discard the first solution.
        # JEV still decides whether the remaining evidence is sufficient.
        return {"independent_draft": {}, "independent_error": str(exc)[:300]}
    return {"independent_draft": draft, "independent_error": "",
            "tools": {**s["tools"], "independent_checks": exam_decision.run_tools(draft)}}


def review(s: ExamState) -> dict:
    final = exam_decision.review(request_text(s), s["perception"], s["draft"], s["tools"], s["evidence"], s.get("independent_draft"), images=s["images"])
    decisions = [*s["jev_decisions"], *final.pop("jev_decisions", [])]
    if type(final.get("answerable")) is not bool or (final["answerable"] and not isinstance(final.get("answer"), str)):
        raise vision.VisionError("终审返回结构不完整")
    if final["answerable"] and not final["answer"].strip():
        raise vision.VisionError("终审答案为空")
    if not isinstance(final.get("citations"), list):
        final["citations"] = []
    issues = final.get("repair_issues", [])
    if not isinstance(issues, list):
        issues = []
    issues = [i for i in issues if isinstance(i, dict) and i.get("code") in {"calculation", "transcription", "option_mapping"} and isinstance(i.get("detail"), str) and i["detail"].strip()][:3]
    tool_errors = [x for x in s["tools"].get("calculations", []) if x.get("error")]
    if tool_errors:
        issues.append({"code": "calculation", "detail": "程序算式失败：" + json.dumps(tool_errors, ensure_ascii=False)})
    if s["tools"].get("grid_checks", {}).get("status") == "invalid":
        issues.append({"code": "transcription", "detail": "黑白格数据格式无效，请重新按原图提供等长01串。"})
    if s["references"] and final["answerable"]:
        by_id = {r["id"]: r for r in s["references"]}
        citations = final.get("citations", [])
        valid = isinstance(citations, list) and bool(citations) and all(
            isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"] in by_id and
            isinstance(c.get("quote"), str) and len(c["quote"].strip()) >= 4 and c["quote"] in by_id[c["id"]]["content"] for c in citations)
        strict = bool(s["options"]["document_ids"] or s["options"]["require_knowledge"])
        if not valid and strict:
            final = {**final, "answerable": False, "failure_reason": "evidence_missing", "needed": "未能给出可核对的资料原文引用，请补充直接相关的制度或课程段落。"}
            issues = []  # Missing evidence is not a reason to keep guessing.
        elif not valid:
            # Optional concepts are not mandatory evidence for self-contained
            # math/logic questions. Never display an invalid quotation.
            final = {**final, "citations": []}
    if s["evidence_missing"]:
        final = {**final, "answerable": False, "failure_reason": "evidence_missing", "needed": s["evidence_missing"]}
        issues = []
    if issues:
        final = {**final, "answerable": False, "failure_reason": "review_conflict", "needed": "复核发现尚未解决的问题：" + "；".join(i["detail"] for i in issues)}
    if not final["answerable"]:
        final.update(answer="", speech="暂时无法确定答案，请查看需要补充或核对的信息。")
    return {"final": final, "feedback": json.dumps(issues, ensure_ascii=False) if issues else "", "jev_decisions": decisions}


def repair(s: ExamState) -> dict:
    return {"repair_count": s["repair_count"] + 1}


def finish(s: ExamState) -> dict:
    final = s.get("final") or {"answerable": False, "needed": s.get("error") or s.get("evidence_missing", "")}
    if s.get("error"):
        final = {"answerable": False, "needed": s["error"]}
        if s.get("perception") and not s["perception"].get("complete"):
            final["failure_reason"] = "image_incomplete"
    body = vision.render(final, s.get("tools", {}))
    refs = {r["id"]: r for r in s.get("references", [])}
    if final.get("answerable") and refs:
        body += "\n\n**资料依据**"
        for c in final.get("citations", []):
            r = refs[c["id"]]
            body += f"\n- 《{r['title']}》 · {r['version'] or '未注明版本'} · 片段 {r['ordinal']}：{c['quote']}"
    if s.get("sources"):
        body += search.format_sources(s["sources"])
    if s.get("reused"):
        body = f"> 沿用上一轮的题目图片：{Path(s['images'][-1]).name}\n\n" + body
    trace = s.get("trace", [])
    points = sorted({tag for r in refs.values() for tag in r.get("tags", [])})
    if not points and final.get("answerable"):
        points = [str(final[k]) for k in ("module", "subtype") if final.get(k)]
    if points:
        body += "\n\n知识点：" + "、".join(points)
    body += f"\n\n核验流程：{len(trace)} 个步骤 · 纠正 {s.get('repair_count', 0)} 次"
    decisions = s.get("jev_decisions", [])
    if decisions:
        selected = next((r for r in reversed(decisions) if r["stage"] == "select_answer"), None)
        if selected:
            body += f"\n\nJEV 决策模型：{selected['model']} · 原始选择概率已保存在本次记录中；此数值不代表整链准确率。"
    result = {"text": body, "speech": vision.speech_of(final), "backend": "local", "exam_backend": "jev",
              "note": "" if final.get("answerable") else "模型判定无法确定作答",
              "artifacts": [{"kind": "vision", "module": final.get("module", ""), "subtype": final.get("subtype", ""),
                 "answerable": final.get("answerable", False), "tools_run": sorted(s.get("tools", {})), "reused_image": s.get("reused", False),
                 "failure_reason": final.get("failure_reason", ""),
                 "visual_recheck_available": bool(final.get("visual_review")),
                 "visual_recheck_error": final.get("visual_review_error", ""),
                 "specialist": s.get("specialist", ""), "repair_count": s.get("repair_count", 0), "trace": trace,
                 "independent_review_available": bool(s.get("independent_draft")),
                 "independent_review_error": s.get("independent_error", ""),
                 "knowledge_points": points,
                 "sources": [{"id": r["id"], "title": r["title"], "url": r.get("source_url"), "version": r["version"]} for r in refs.values()],
                 "verification": "reviewed" if final.get("answerable") else "needs_information",
                 "reference_ids": [c["id"] for c in final.get("citations", []) if isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"] in refs]},
                 {"kind": "jev_decisions", "decisions": decisions}]}
    return {"result": result, "final": final, "last_images": s.get("images", [])}


def timed(name, fn, phase=""):
    def invoke(s):
        started = time.perf_counter()
        if phase:
            progress.mark(phase, s.get("request_id", ""))
        try:
            out = fn(s)
        except (vision.VisionError, llm.LLMError, ValueError, TypeError, KeyError) as exc:
            label = {"prepare": "输入检查", "perception_agent": "图片识别", "route_specialist": "题型判断",
                     "knowledge_agent": "资料检索", "tool_verifier": "程序校验", "review_agent": "答案复核"}.get(name, "解答")
            out = {"error": f"{label}失败：{str(exc)[:400]}"}
        trace = out.get("trace", s.get("trace", []))
        out["trace"] = [*trace, {"node": name, "ms": round((time.perf_counter() - started) * 1000), "status": "error" if out.get("error") else "degraded" if out.get("independent_error") else "done"}]
        return out
    return invoke


def build_exam_graph():
    g = StateGraph(ExamState)
    steps = [("prepare", prepare, "capture"), ("perception_agent", perceive, "recognize"),
             ("route_specialist", classify, ""), ("knowledge_agent", retrieve, ""),
             ("tool_verifier", verify_tools, "verify"), ("independent_reasoning", independent_reasoning, "verify"),
             ("review_agent", review, "verify"), ("repair", repair, "")]
    for name, fn, phase in steps:
        g.add_node(name, timed(name, fn, phase))
    for kind in SPECIALISTS:
        g.add_node("solve_" + kind, timed("solve_" + kind, solve, "solve"))
    g.add_node("finish", finish)
    g.add_edge(START, "prepare")
    def chain(origin, target):
        g.add_conditional_edges(origin, lambda s: "finish" if s.get("error") else target, ["finish", target])
    chain("prepare", "perception_agent")
    chain("perception_agent", "route_specialist")
    chain("route_specialist", "knowledge_agent")
    g.add_conditional_edges("knowledge_agent", lambda s: "finish" if s.get("error") or s.get("evidence_missing") else "solve_" + s["specialist"], ["finish", *["solve_" + k for k in SPECIALISTS]])
    for kind in SPECIALISTS:
        chain("solve_" + kind, "tool_verifier")
    g.add_conditional_edges("tool_verifier", lambda s: "finish" if s.get("error") else
                            "independent_reasoning" if s["specialist"] == "reasoning" else "review_agent",
                            ["finish", "independent_reasoning", "review_agent"])
    chain("independent_reasoning", "review_agent")
    g.add_conditional_edges("review_agent", lambda s: "repair" if not s.get("error") and s.get("feedback") and s["repair_count"] < 1 else "finish", ["repair", "finish"])
    g.add_conditional_edges("repair", lambda s: "perception_agent" if '"transcription"' in s.get("feedback", "") else "solve_" + s["specialist"],
                            ["perception_agent", *["solve_" + k for k in SPECIALISTS]])
    g.add_edge("finish", END)
    return g.compile(name="exam-agent-jev-vl")


EXAM_GRAPH = build_exam_graph()
