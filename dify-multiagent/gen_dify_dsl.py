"""Generate the Dify DSL for the glasses multi-agent scene layer.

Design (see docs/端侧适配设计_眼镜.md):
  - Dify hosts ONLY the five scene Agents (meeting / exam / fitness / resource / general).
  - Routing authority stays in assistant-lite's Python orchestrator; Dify receives an
    already-routed `scene` hint but re-classifies with a question-classifier so the
    workflow is also usable standalone from the Dify UI.
  - Deterministic work (arithmetic, grid checks, export, archiving) is NOT reimplemented
    here; those stay in assistant-lite tools.

ASCII-only source: emits a UTF-8 YAML file with `allow_unicode=True`.
Usage: python gen_dify_dsl.py <out.yml>
"""

from __future__ import annotations

import sys
import uuid

import yaml

APP_NAME = "眼镜多Agent场景层"
APP_DESC = (
    "端侧 1-3B 只做唤醒/意图/决策，本应用承载云端五个场景 Agent："
    "会议纪要 / 拍照解题 / 锻炼指导 / 资料检索 / 通用问答。"
    "权威路由与确定性工具仍在 assistant-lite。"
)

# Model provider plugins actually installed on this instance.
PROVIDER_DEEPSEEK = "langgenius/deepseek/deepseek"
MODEL_DEEPSEEK = "deepseek-v4-flash"
PROVIDER_VOLC = "langgenius/volcengine/volcengine"
MODEL_VOLC = "doubao-seed-2-1-pro-260628"


def uid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Scene agent definitions: (scene_key, edge_handle, title, system_prompt)
# --------------------------------------------------------------------------- #
SCENES: list[tuple[str, str, str, str]] = [
    (
        "meeting",
        "meeting",
        "Agent · 会议纪要",
        "你是会议纪要 Agent。把用户提供的会议转写/录音文本整理成结构化纪要。\n"
        "输出包含：主题、覆盖范围、讨论要点、明确决策、行动项、待确认。\n"
        "严格约束：只能依据原文，禁止补全原文没有的时间期限、流程步骤或责任人，"
        "原文没说的字段写「未明确」。不要编造参会人姓名。",
    ),
    (
        "exam",
        "exam",
        "Agent · 拍照解题",
        "你是拍照解题 Agent，面向公务员考试行测六类题"
        "（言语理解/数量关系/判断推理/资料分析/政治理论/常识）。\n"
        "先复述题干与全部选项，再给出答案与解析。\n"
        "图形推理题不能只做 OCR：按行列坐标记录每格的黑白与位置关系，"
        "说明所用规律，并逐一排除选项。\n"
        "算术必须逐步写出算式。公式/规律无法确定时明确说「无法确定」，不要猜答案。\n"
        "输出末尾单独一行给出播报语，前缀 <<<SPEECH>>>，不超过 60 字。",
    ),
    (
        "fitness",
        "fitness",
        "Agent · 锻炼指导",
        "你是锻炼指导 Agent。用户在运动中双手被占用，回答要短、可执行。\n"
        "只依据用户自述的信息给建议，没有的信息不要假设。\n"
        "重要：本设备没有心率等生理传感器，禁止给出心率区间、训练负荷、"
        "恢复度这类需要生理数据才能得出的结论；卡路里只能粗估并标注误差。\n"
        "用户报告疼痛/不适时：立即让其停止该动作，给处置建议，"
        "并说明何种情况必须就医；同时声明你不是医生。\n"
        "输出末尾单独一行给出播报语，前缀 <<<SPEECH>>>，安全相关内容必须完整念出。",
    ),
    (
        "resource",
        "resource",
        "Agent · 资料检索",
        "你是资料检索 Agent。用户想查找、列出或导出历史资料"
        "（会议纪要/题解/训练记录）。\n"
        "真正的检索与导出由 assistant-lite 的资料库执行；"
        "在本次纯文本上下文中，请明确说明你无法直接访问资料库，"
        "并给出用户应当说的指令句式（例如「导出最新一份成 Word」「我有哪些资料」）。\n"
        "不要编造任何资料标题、ID 或内容。",
    ),
    (
        "general",
        "general",
        "Agent · 通用问答",
        "你是通用问答 Agent。日常问题简洁作答，不啰嗦。\n"
        "不确定的信息要说明不确定，不编造事实、不宣称未实际执行的动作。\n"
        "输出末尾单独一行给出播报语，前缀 <<<SPEECH>>>，不超过 60 字。",
    ),
]


# --------------------------------------------------------------------------- #
# Node builders
# --------------------------------------------------------------------------- #
def n_start() -> dict:
    return {
        "id": "start",
        "type": "custom",
        "width": 242,
        "height": 72,
        "position": {"x": 0, "y": 300},
        "positionAbsolute": {"x": 0, "y": 300},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "zIndex": 0,
        "data": {
            "type": "start",
            "title": "端侧事件入口",
            "desc": "text=用户话语/转写；device_intent=端侧 1-3B 判定的意图枚举",
            "variables": [
                {
                    "variable": "text",
                    "label": "用户话语 / 转写文本",
                    "type": "text-input",
                    "required": False,
                    "max_length": 48000,
                    "options": [],
                },
                {
                    "variable": "device_intent",
                    "label": "端侧意图（枚举）",
                    "type": "text-input",
                    "required": False,
                    "max_length": 64,
                    "options": [],
                },
                {
                    "variable": "device_confidence",
                    "label": "端侧置信度",
                    "type": "text-input",
                    "required": False,
                    "max_length": 16,
                    "options": [],
                },
                {
                    "variable": "scene_hint",
                    "label": "云端路由提示（assistant-lite 给出）",
                    "type": "text-input",
                    "required": False,
                    "max_length": 32,
                    "options": [],
                },
            ],
        },
    }


NORMALIZE_CODE = '''import json


def main(text, device_intent, device_confidence, scene_hint):
    """Deterministic pre-processing. No model call, no network.

    Returns `query`, the single string the classifier consumes. Device judgement
    is folded into it as a labelled hint instead of a hard gate, so the cloud
    classifier always sees the evidence but stays the authority.
    """
    text = (text or "").strip()
    intent = (device_intent or "").strip()

    try:
        conf = float(device_confidence) if str(device_confidence or "").strip() else 0.0
    except (TypeError, ValueError):
        conf = 0.0

    hint = (scene_hint or "").strip().lower()
    if hint not in ("meeting", "exam", "fitness", "resource", "general"):
        hint = ""

    INTENT_SCENE = {
        "meeting_start": "meeting",
        "meeting_append": "meeting",
        "meeting_summarize": "meeting",
        "meeting_stop": "meeting",
        "solve": "exam",
        "train_start": "fitness",
        "train_done": "fitness",
        "train_pain": "fitness",
        "switch_scene": "",
        "stop": "",
        "cancel": "",
        "wake": "",
    }
    scene_from_intent = INTENT_SCENE.get(intent, "")
    trusted = bool(scene_from_intent) and conf >= 0.75
    if intent == "train_pain":
        trusted = True  # safety content is never gated on confidence

    # assistant-lite already routed; its hint wins over a weak device guess.
    effective = hint or (scene_from_intent if trusted else "")

    parts = [text or "(no text)"]
    evidence = []
    if effective:
        evidence.append("cloud_routing_hint=" + effective)
    if scene_from_intent:
        evidence.append(
            "device_intent=" + intent
            + " (confidence " + str(conf)
            + ", " + ("trusted" if trusted else "LOW - verify") + ")"
        )
    if evidence:
        parts.append("[routing evidence from device/cloud: " + "; ".join(evidence) + "]")

    return {
        "query": "\\n".join(parts),
        "scene_hint": effective or "unknown",
        "device_scene": scene_from_intent or "none",
        "device_trusted": "yes" if trusted else "no",
        "confidence": str(conf),
        "router_note": json.dumps(
            {
                "device_intent": intent,
                "confidence": conf,
                "trusted": trusted,
                "hint": hint,
            },
            ensure_ascii=False,
        ),
    }
'''


def n_normalize() -> dict:
    return {
        "id": "normalize",
        "type": "custom",
        "width": 242,
        "height": 100,
        "position": {"x": 300, "y": 300},
        "positionAbsolute": {"x": 300, "y": 300},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "zIndex": 0,
        "data": {
            "type": "code",
            "title": "端侧事件归一化（零 token）",
            "desc": "对齐设计文档 §3：固定枚举 + 置信度门控；train_pain 不降级",
            "code_language": "python3",
            "code": NORMALIZE_CODE,
            "variables": [
                {"variable": "text", "value_selector": ["start", "text"]},
                {"variable": "device_intent", "value_selector": ["start", "device_intent"]},
                {
                    "variable": "device_confidence",
                    "value_selector": ["start", "device_confidence"],
                },
                {"variable": "scene_hint", "value_selector": ["start", "scene_hint"]},
            ],
            "outputs": {
                "query": {"type": "string", "children": None},
                "scene_hint": {"type": "string", "children": None},
                "device_scene": {"type": "string", "children": None},
                "device_trusted": {"type": "string", "children": None},
                "confidence": {"type": "string", "children": None},
                "router_note": {"type": "string", "children": None},
            },
        },
    }


def n_classifier() -> dict:
    classes = [
        {
            "id": key,
            "name": key,
            "label": title.replace("Agent · ", ""),
        }
        for key, _handle, title, _p in SCENES
    ]
    return {
        "id": "scene_router",
        "type": "custom",
        "width": 242,
        "height": 150,
        "position": {"x": 620, "y": 300},
        "positionAbsolute": {"x": 620, "y": 300},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "zIndex": 0,
        "data": {
            "type": "question-classifier",
            "title": "场景分发 · 五 Agent",
            "desc": "端侧 hint 不可信时由云端复核；这是设计文档里的「权威路由」在 Dify 侧的兜底",
            "query_variable_selector": ["normalize", "query"],
            "model": {
                "provider": PROVIDER_DEEPSEEK,
                "name": MODEL_DEEPSEEK,
                "mode": "chat",
                "completion_params": {"temperature": 0},
            },
            "classes": classes,
            "instruction": (
                "根据用户话语判断属于哪个场景。拿不准时选 general。\n"
                "meeting: 会议、开会、纪要、转写、录音整理\n"
                "exam: 题目、解题、答案、讲解、计算、图形推理、行测\n"
                "fitness: 健身、锻炼、动作、几组、深蹲、卧推、身体不适、疼痛\n"
                "resource: 我有哪些资料、查找记录、导出、生成文件\n"
                "general: 其他日常问答"
            ),
            "memory": None,
        },
    }


def n_agent(key: str, handle: str, title: str, prompt: str, x: int) -> dict:
    return {
        "id": f"agent_{key}",
        "type": "custom",
        "width": 242,
        "height": 120,
        "position": {"x": x, "y": 300},
        "positionAbsolute": {"x": x, "y": 300},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "zIndex": 0,
        "data": {
            "type": "llm",
            "title": title,
            "desc": "",
            "model": {
                # 火山方舟账户欠费后返回 403 AccountOverdueError（实测），
                # 因此在 Dify 侧改用 deepseek——同实例内已验证可用，
                # 且与本地 LangGraph/assistant-lite 用的是同一家模型。
                "provider": PROVIDER_DEEPSEEK,
                "name": MODEL_DEEPSEEK,
                "mode": "chat",
                # The deviceless MVP must not leak chain-of-thought into TTS:
                # `reasoning_format: separated` keeps <think> out of `text`.
                "completion_params": {
                    "temperature": 0.3,
                },
            },
            # `reasoning_format` is a sibling of `model`, like the working app.
            "reasoning_format": "separated",
            "prompt_template": [
                {"id": uid(), "role": "system", "text": prompt},
                {
                    "id": uid(),
                    "role": "user",
                    "text": (
                        "用户输入：{{#normalize.query#}}\n\n"
                        "端侧判定：{{#normalize.router_note#}}\n"
                        "云端路由提示：{{#normalize.scene_hint#}}"
                    ),
                },
            ],
            "context": {"enabled": False, "variable_selector": []},
            "memory": None,
            "vision": {"enabled": False, "configs": {}},
        },
    }


def n_answer(key: str, x: int) -> dict:
    # Answer nodes must stay on the agent node directly: a variable-aggregator
    # between them would need all five branches to complete first, which never
    # happens (only one branch runs per request).
    return {
        "id": f"answer_{key}",
        "type": "custom",
        "width": 242,
        "height": 110,
        "position": {"x": x, "y": 520},
        "positionAbsolute": {"x": x, "y": 520},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "zIndex": 0,
        "data": {
            "type": "answer",
            "title": f"输出 · {key}",
            "desc": "",
            "answer": "{{#agent_" + key + ".text#}}",
            "variables": [],
        },
    }


def edge(src: str, src_handle: str, dst: str, src_type: str, dst_type: str) -> dict:
    return {
        "id": f"{src}-{src_handle}-{dst}",
        "source": src,
        "sourceHandle": src_handle,
        "target": dst,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
        "data": {
            "isInIteration": False,
            "isInLoop": False,
            "iteration_id": None,
            "loop_id": None,
            "sourceType": src_type,
            "targetType": dst_type,
        },
    }


def build() -> dict:
    # NOTE(design): question-classifier emits `edge_source_handle = class_id`
    # (see graphon/nodes/question_classifier/question_classifier_node.py:447).
    # Its outgoing edges must use the class id as sourceHandle; a generic
    # "source" handle never fires. So the classifier dispatches straight to the
    # five scene agents, and the deterministic device gate lives in the
    # normalize Code node (which rewrites the query it feeds the classifier).
    nodes: list[dict] = [n_start(), n_normalize(), n_classifier()]
    edges: list[dict] = [
        edge("start", "source", "normalize", "start", "code"),
        edge("normalize", "source", "scene_router", "code", "question-classifier"),
    ]

    for i, (key, handle, title, prompt) in enumerate(SCENES):
        x = 960 + i * 340
        nodes.append(n_agent(key, handle, title, prompt, x))
        nodes.append(n_answer(key, x))
        edges.append(edge("scene_router", handle, f"agent_{key}", "question-classifier", "llm"))
        edges.append(edge(f"agent_{key}", "source", f"answer_{key}", "llm", "answer"))

    return {
        "app": {
            "description": APP_DESC,
            "icon": "\U0001f9e0",
            "icon_background": "#E8F3FF",
            "icon_type": "emoji",
            "mode": "workflow",
            "name": APP_NAME,
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [
            {
                "current_identifier": None,
                "type": "marketplace",
                "value": {
                    "marketplace_plugin_unique_identifier": (
                        "langgenius/deepseek:0.0.21@"
                        "5873fe73395aeb49ba26035059eb4e0ec5af01bb66d0545b031f8917b12e8c29"
                    ),
                    "version": None,
                },
            },
            {
                "current_identifier": None,
                "type": "marketplace",
                "value": {
                    "marketplace_plugin_unique_identifier": (
                        "langgenius/volcengine:0.0.19@"
                        "b18c7067025ff53909fd63a4146a98ee2b0e4b452086b45b774242790fc569ed"
                    ),
                    "version": None,
                },
            },
        ],
        "kind": "app",
        "version": "0.7.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": {
                "file_upload": {
                    "allowed_file_extensions": [],
                    "allowed_file_types": [],
                    "allowed_file_upload_methods": [],
                    "enabled": False,
                    "fileUploadConfig": {
                        "audio_file_size_limit": 50,
                        "batch_count_limit": 5,
                        "file_size_limit": 15,
                        "image_file_size_limit": 10,
                        "video_file_size_limit": 100,
                        "workflow_file_upload_limit": 10,
                    },
                    "number_limits": 3,
                },
                "opening_statement": "",
                "retriever_resource": {"enabled": False},
                "sensitive_word_avoidance": {"enabled": False},
                "speech_to_text": {"enabled": False},
                "suggested_questions": [],
                "suggested_questions_after_answer": {"enabled": False},
                "text_to_speech": {"enabled": False},
            },
            "graph": {
                "edges": edges,
                "nodes": nodes,
                "viewport": {"x": 0, "y": 0, "zoom": 0.7},
            },
        },
    }


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "dify_glasses_multiagent.yml"
    doc = build()
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False, width=1000)
    n = len(doc["workflow"]["graph"]["nodes"])
    e = len(doc["workflow"]["graph"]["edges"])
    print(f"wrote {out}: {n} nodes, {e} edges")


if __name__ == "__main__":
    main()
