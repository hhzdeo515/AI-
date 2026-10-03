"""黑白格图形推理的确定性校验。

直接复制自旧 dify-assistant/exam_tools.py（纯函数，无依赖，已在本机验证）。
只做逐格运算校验，不做图像识别。
"""

from __future__ import annotations

from typing import Any
from math import isqrt


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
    transforms = {"identity": lambda s: s}
    side = isqrt(length)
    if side * side == length:
        def rotated(s, turns):
            for _ in range(turns):
                s = "".join(s[(side - 1 - c) * side + r] for r in range(side) for c in range(side))
            return s
        transforms.update({f"rotate{k * 90}": (lambda s, k=k: rotated(s, k)) for k in (1, 2, 3)})
        transforms["mirror_horizontal"] = lambda s: "".join(s[r * side + side - 1 - c] for r in range(side) for c in range(side))
        transforms["mirror_vertical"] = lambda s: "".join(s[(side - 1 - r) * side + c] for r in range(side) for c in range(side))
        transforms["transpose"] = lambda s: "".join(s[c * side + r] for r in range(side) for c in range(side))
        transforms["anti_transpose"] = lambda s: "".join(s[(side - 1 - c) * side + side - 1 - r] for r in range(side) for c in range(side))
    pairs = [("identity", "identity")]
    pairs += [(t, "identity") for t in transforms if t != "identity"]
    pairs += [("identity", t) for t in transforms if t != "identity"]
    candidates = []
    for direction, lines in [("row", rows), ("column", [list(x) for x in zip(*rows)])]:
        known = [r for r in lines if "?" not in r]
        unknown = [r for r in lines if "?" in r]
        if len(known) < 2 or len(unknown) != 1:
            continue
        for left, right in pairs:
            for name, op in operations.items():
                def combine(a: str, b: str) -> str:
                    return "".join(str(op(int(x), int(y))) for x, y in zip(transforms[left](a), transforms[right](b)))
                if not all(combine(a, b) == c for a, b, c in known):
                    continue
                missing = unknown[0].index("?")
                matches = []
                for label, value in options.items():
                    a, b, c = [value if v == "?" else v for v in unknown[0]]
                    if combine(a, b) == c:
                        matches.append(label)
                prediction = combine(*unknown[0][:2]) if missing == 2 else None
                candidates.append({"direction": direction, "rule": name,
                    "left_transform": left, "right_transform": right,
                    "known_checks": [{"a": a, "b": b, "actual": c, "computed": combine(a, b)} for a, b, c in known],
                    "prediction": prediction, "matching_options": matches})
    return {
        "status": "checked",
        "observed_rows": rows,
        "observed_options": options,
        "cell_count_per_panel": length,
        "square_shape_candidate": {"rows": side, "columns": side} if side * side == length else None,
        "candidates": candidates,
        "scope": "验证所提交01串的逐格运算及单个操作数的旋转/镜像；不证明图片识别正确，不排除其他规律，终审须回看原图。",
    }
