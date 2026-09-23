"""算术表达式安全求值：只用 AST 解析，不 exec / 不 eval 用户输入。

移植自旧 dify-assistant/service.py 的 calculate()（已在本机验证）。
限制：表达式长度 <= 300、嵌套深度 <= 15、幂指数绝对值 <= 20、结果必须是有界实数。
"""

from __future__ import annotations

import ast
import math
import operator

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}

MAX_LEN = 300
MAX_DEPTH = 15
MAX_POW = 20
MAX_ABS = 1e100


class CalcError(ValueError):
    """表达式不合法或不可计算。"""


def calculate(expr: str) -> float | int:
    """安全求值一个算术表达式。失败抛 CalcError。"""
    if not isinstance(expr, str):
        raise CalcError("表达式必须是字符串")
    if len(expr) > MAX_LEN:
        raise CalcError(f"表达式过长（上限 {MAX_LEN} 字符）")

    def ev(node: ast.AST, depth: int = 0) -> float | int:
        if depth > MAX_DEPTH:
            raise CalcError("表达式过于复杂")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value: float | int = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = ev(node.operand, depth + 1) * (
                1 if isinstance(node.op, ast.UAdd) else -1
            )
        elif isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left = ev(node.left, depth + 1)
            right = ev(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and abs(right) > MAX_POW:
                raise CalcError("幂指数过大")
            value = _OPS[type(node.op)](left, right)
        else:
            raise CalcError("不支持的表达式（只允许数字与 + - * / ** % 和括号）")

        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or abs(value) > MAX_ABS
        ):
            raise CalcError("结果不是有界实数")
        return value

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"表达式语法错误：{e.msg}") from e

    try:
        return ev(tree.body)
    except ZeroDivisionError as e:
        raise CalcError("除数不能为零") from e


def calculate_many(expressions: list[str] | None) -> list[dict]:
    """批量求值。每项返回 {expression, result} 或 {expression, error}。"""
    out: list[dict] = []
    for expr in expressions or []:
        if not isinstance(expr, str):
            out.append({"expression": str(expr), "error": "表达式必须是字符串"})
            continue
        try:
            out.append({"expression": expr, "result": calculate(expr)})
        except CalcError as e:
            out.append({"expression": expr, "error": str(e)})
    return out
