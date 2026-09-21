"""Safe arithmetic expression evaluation for project variables."""
from __future__ import annotations

import ast
from typing import Dict

import numpy as np


_ALLOWED_FUNCTIONS = {
    "abs": np.abs,
    "acos": np.arccos,
    "asin": np.arcsin,
    "atan": np.arctan,
    "ceil": np.ceil,
    "clip": np.clip,
    "cos": np.cos,
    "degrees": np.degrees,
    "exp": np.exp,
    "floor": np.floor,
    "hypot": np.hypot,
    "log": np.log,
    "log10": np.log10,
    "max": np.maximum,
    "min": np.minimum,
    "radians": np.radians,
    "sign": np.sign,
    "sin": np.sin,
    "sqrt": np.sqrt,
    "tan": np.tan,
}
_ALLOWED_CONSTANTS = {"pi": float(np.pi), "e": float(np.e), "inf": float(np.inf)}
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)


def evaluate_expression(expression: str, variables: Dict[str, float]) -> float:
    """Evaluate a restricted numeric expression using project variables."""
    try:
        tree = ast.parse(str(expression).strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid formula: {exc.msg}") from exc

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in variables:
                return float(variables[node.id])
            if node.id in _ALLOWED_CONSTANTS:
                return float(_ALLOWED_CONSTANTS[node.id])
            raise ValueError(f"Unknown parameter: {node.id}")
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            return left ** right
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARY):
            value = evaluate(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = _ALLOWED_FUNCTIONS.get(node.func.id)
            if function is None or node.keywords:
                raise ValueError("Only approved numeric functions are allowed")
            return float(function(*(evaluate(arg) for arg in node.args)))
        raise ValueError("Formula contains an unsupported expression")

    return float(evaluate(tree))
