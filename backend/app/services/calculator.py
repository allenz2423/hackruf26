"""Bounded numeric expression evaluator; never executes Python code."""

from __future__ import annotations

import ast
from decimal import Decimal, Underflow, localcontext

MAX_EXPRESSION_LENGTH = 256
MAX_EXPRESSION_NODES = 64
MAX_ADJUSTED_EXPONENT = 100
MAX_POWER = 100
MAX_MAGNITUDE = Decimal("1e100")
PI = Decimal("3.141592653589793238462643383")
E = Decimal("2.718281828459045235360287471")


class CalculatorInputError(ValueError):
    """The submitted expression is invalid, unsupported, or outside limits."""


def evaluate_expression(expression: str) -> str:
    """Evaluate bounded decimal arithmetic and return a normalized result string."""
    expression = expression.strip()
    if not expression or len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculatorInputError

    try:
        parsed = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise CalculatorInputError from None

    if sum(1 for _ in ast.walk(parsed)) > MAX_EXPRESSION_NODES:
        raise CalculatorInputError

    with localcontext() as context:
        context.prec = 28
        context.Emax = MAX_ADJUSTED_EXPONENT
        context.Emin = -MAX_ADJUSTED_EXPONENT
        context.traps[Underflow] = True
        try:
            result = _evaluate_node(parsed.body, expression)
            result = _bounded(result)
        except CalculatorInputError:
            raise
        except (ArithmeticError, ValueError):
            raise CalculatorInputError from None

    if result == 0:
        return "0"
    return format(result.normalize(), "f")


def _evaluate_node(node: ast.AST, source: str) -> Decimal:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorInputError
        literal = ast.get_source_segment(source, node)
        if literal is None or "_" in literal:
            raise CalculatorInputError
        return _bounded(Decimal(literal))

    if isinstance(node, ast.Name):
        constants = {"pi": PI, "e": E}
        value = constants.get(node.id.lower())
        if value is None:
            raise CalculatorInputError
        return value

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand, source)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return _bounded(-operand)
        raise CalculatorInputError

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, source)
        right = _evaluate_node(node.right, source)

        if isinstance(node.op, ast.Add):
            value = left + right
        elif isinstance(node.op, ast.Sub):
            value = left - right
        elif isinstance(node.op, ast.Mult):
            value = left * right
        elif isinstance(node.op, ast.Div):
            value = left / right
        elif isinstance(node.op, ast.FloorDiv):
            value = left // right
        elif isinstance(node.op, ast.Mod):
            value = left % right
        elif isinstance(node.op, ast.Pow):
            if right != right.to_integral_value() or abs(right) > MAX_POWER:
                raise CalculatorInputError
            value = left ** int(right)
        else:
            raise CalculatorInputError
        return _bounded(value)

    # Calls, attributes, containers, comparisons, and all other Python syntax
    # are deliberately rejected. Only the AST nodes above are interpreted.
    raise CalculatorInputError


def _bounded(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise CalculatorInputError
    if value == 0:
        return value
    adjusted_exponent = value.copy_abs().adjusted()
    if (
        adjusted_exponent > MAX_ADJUSTED_EXPONENT
        or adjusted_exponent < -MAX_ADJUSTED_EXPONENT
        or value.copy_abs() > MAX_MAGNITUDE
    ):
        raise CalculatorInputError
    return value
