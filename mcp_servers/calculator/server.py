"""
Calculator MCP Server

Exposes tools for safe math evaluation and unit conversion via the
Model Context Protocol.  Runs on port 8004 with SSE transport.
"""

import ast
import logging
import math
import operator
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("calculator")

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("calculator", host="0.0.0.0", port=8004)

# ---------------------------------------------------------------------------
# Safe math evaluation helpers
# ---------------------------------------------------------------------------

# Allowed binary operators
_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

# Allowed function names
_SAFE_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}


def _safe_eval_node(node: ast.AST) -> float:
    """Recursively evaluate an AST node with whitelisted operations."""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        return _SAFE_OPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = _safe_eval_node(node.operand)
        return _SAFE_OPS[op_type](operand)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only named function calls are supported")
        func_name = node.func.id
        if func_name not in _SAFE_FUNCS:
            raise ValueError(f"Unsupported function: {func_name}")
        args = [_safe_eval_node(a) for a in node.args]
        return float(_SAFE_FUNCS[func_name](*args))

    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def safe_calculate(expression: str) -> float:
    """Safely evaluate a math expression using AST parsing."""
    tree = ast.parse(expression, mode="eval")
    return _safe_eval_node(tree)


# ---------------------------------------------------------------------------
# Unit conversion tables
# ---------------------------------------------------------------------------

# Each entry: (from_unit, to_unit) -> (multiply_factor, offset)
# For linear: result = value * factor
# For affine (temperature): result = value * factor + offset
_CONVERSIONS: dict[tuple[str, str], tuple[float, float]] = {
    # Distance
    ("km", "miles"): (0.621371, 0.0),
    ("miles", "km"): (1.60934, 0.0),
    ("meters", "feet"): (3.28084, 0.0),
    ("feet", "meters"): (0.3048, 0.0),
    # Weight
    ("kg", "lbs"): (2.20462, 0.0),
    ("lbs", "kg"): (0.453592, 0.0),
    # Volume
    ("liters", "gallons"): (0.264172, 0.0),
    ("gallons", "liters"): (3.78541, 0.0),
}


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a value between units."""
    f = from_unit.lower().strip()
    t = to_unit.lower().strip()

    # Temperature special cases
    if f == "celsius" and t == "fahrenheit":
        return value * 9.0 / 5.0 + 32.0
    if f == "fahrenheit" and t == "celsius":
        return (value - 32.0) * 5.0 / 9.0

    key = (f, t)
    if key not in _CONVERSIONS:
        raise ValueError(
            f"Unsupported conversion: {from_unit} -> {to_unit}. "
            f"Supported: km/miles, kg/lbs, celsius/fahrenheit, "
            f"meters/feet, liters/gallons"
        )

    factor, offset = _CONVERSIONS[key]
    return value * factor + offset


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def calculate(expression: str) -> dict:
    """Evaluate a mathematical expression safely.

    Supports: +, -, *, /, ** (power), % (modulo), // (floor division),
    parentheses, and functions: sqrt, abs, round, min, max.

    Args:
        expression: A math expression string, e.g. "sqrt(144) + 2**3"

    Returns:
        Dictionary with the expression, result, and status.
    """
    logger.info("Tool calculate invoked — expression='%s'", expression)
    try:
        result = safe_calculate(expression)
        # Format nicely: drop .0 for whole numbers
        if result == int(result):
            formatted = str(int(result))
        else:
            formatted = f"{result:.6g}"
        return {
            "expression": expression,
            "result": formatted,
            "numeric_result": result,
            "status": "success",
        }
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as e:
        return {
            "expression": expression,
            "error": str(e),
            "status": "error",
        }


@mcp.tool()
def convert_units(value: float, from_unit: str, to_unit: str) -> dict:
    """Convert a value from one unit to another.

    Supported conversions:
    - Distance: km <-> miles, meters <-> feet
    - Weight: kg <-> lbs
    - Temperature: celsius <-> fahrenheit
    - Volume: liters <-> gallons

    Args:
        value: The numeric value to convert.
        from_unit: The source unit (e.g. "km", "celsius").
        to_unit: The target unit (e.g. "miles", "fahrenheit").

    Returns:
        Dictionary with the original value, converted value, and units.
    """
    logger.info("Tool convert_units invoked — %.4g %s -> %s", value, from_unit, to_unit)
    try:
        result = convert(value, from_unit, to_unit)
        if result == int(result):
            formatted = str(int(result))
        else:
            formatted = f"{result:.4f}"
        return {
            "original_value": value,
            "from_unit": from_unit,
            "converted_value": formatted,
            "to_unit": to_unit,
            "status": "success",
        }
    except ValueError as e:
        return {
            "error": str(e),
            "status": "error",
        }


@mcp.tool()
def health_check() -> dict:
    """Check whether the Calculator server is healthy.

    Returns:
        Dictionary with server status and timestamp.
    """
    logger.info("Tool health_check invoked")
    return {
        "status": "healthy",
        "server": "calculator",
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Calculator MCP server on port 8004 ...")
    mcp.run(transport="sse")
