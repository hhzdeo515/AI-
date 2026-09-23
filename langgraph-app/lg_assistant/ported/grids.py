"""黑白格图形推理的确定性校验。

直接复制自旧 dify-assistant/exam_tools.py（纯函数，无依赖，已在本机验证）。
只做逐格运算校验，不做图像识别。
"""

from __future__ import annotations

from typing import Any


def check_grids(spec: dict[str, Any] | None) -> dict[str, Any]:
    if not spec:
        return {"status": "not_applicable", "scope": "未提交等尺寸黑白格数据"}
    try:
        rows = spec["rows"]
        options = spec["options"]
        if not isinstance(rows, list) or len(rows) != 3 or any(
            not isinstance(r, list) or len(r) != 3 for r in rows
        ):
            raise ValueError()
        if not isinstance(options, dict) or not 2 <= len(options) <= 8:
            raise ValueError()
        values = [v for r in rows for v in r if v != "?"] + list(options.values())
        length = len(values[0])
        if (
            not 1 <= length <= 64
            or any(
                not isinstance(v, str) or len(v) != length or set(v) - {"0", "1"}
                for v in values
            )
        ):
            raise ValueError()
        if sum(v == "?" for r in rows for v in r) != 1:
            raise ValueError()
    except (KeyError, TypeError, ValueError, IndexError):
        return {
            "status": "invalid",
            "scope": "需要3行3列且只有一个问号，每幅图为等长01串，选项为字母到01串的映射",
        }
    operations = {
        "xor_去同存异": lambda a, b: a ^ b,
        "or_叠加取并集": lambda a, b: a | b,
        "and_求同取交集": lambda a, b: a & b,
        "xnor_相同为黑": lambda a, b: 1 - (a ^ b),
        "left_minus_right_左减右": lambda a, b: a & (1 - b),
        "right_minus_left_右减左": lambda a, b: b & (1 - a),
    }
    candidates = []
    for direction, lines in [("row", rows), ("column", [list(x) for x in zip(*rows)])]:
        known = [r for r in lines if "?" not in r]
        unknown = [r for r in lines if r[2] == "?" and "?" not in r[:2]]
        if len(known) < 2 or len(unknown) != 1:
            continue
        for name, op in operations.items():

            def combine(a: str, b: str) -> str:
                return "".join(str(op(int(x), int(y))) for x, y in zip(a, b))

            if not all(combine(a, b) == c for a, b, c in known):
                continue
            prediction = combine(*unknown[0][:2])
            matches = [k for k, v in options.items() if v == prediction]
            candidates.append(
                {
                    "direction": direction,
                    "rule": name,
                    "known_checks": [
                        {"a": a, "b": b, "actual": c, "computed": combine(a, b)}
                        for a, b, c in known
                    ],
                    "prediction": prediction,
                    "matching_options": matches,
                }
            )
    return {
        "status": "checked",
        "observed_rows": rows,
        "observed_options": options,
        "candidates": candidates,
        "scope": "只验证所提交01串的逐格运算；不证明图片识别正确，也不排除其他类型规律，终审须回看原图。",
    }
