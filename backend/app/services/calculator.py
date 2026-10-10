"""Bounded symbolic expression parser backed by SymPy; never evaluates input code."""

from __future__ import annotations

import ast
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import sympy as sp

MAX_EXPRESSION_LENGTH = 256
MAX_EXPRESSION_NODES = 64
MAX_EXPRESSION_DEPTH = 32
MAX_SYMBOLS = 16
MAX_NUMERIC_ADJUSTED_EXPONENT = 100
MAX_NUMERIC_EXPONENT = 100
MAX_RESULT_OPERATIONS = 128
MAX_RESULT_LENGTH = 512
MAX_NUMERIC_MAGNITUDE = sp.Integer(10) ** MAX_NUMERIC_ADJUSTED_EXPONENT
MIN_NUMERIC_MAGNITUDE = sp.Rational(1, 10**MAX_NUMERIC_ADJUSTED_EXPONENT)
_SYMBOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")

_CONSTANTS = {"pi": sp.pi, "e": sp.E}
_FUNCTIONS = {
    "sqrt": (sp.sqrt, 1, 1),
    "sin": (sp.sin, 1, 1),
    "cos": (sp.cos, 1, 1),
    "tan": (sp.tan, 1, 1),
    "asin": (sp.asin, 1, 1),
    "acos": (sp.acos, 1, 1),
    "atan": (sp.atan, 1, 1),
    "sinh": (sp.sinh, 1, 1),
    "cosh": (sp.cosh, 1, 1),
    "tanh": (sp.tanh, 1, 1),
    "exp": (sp.exp, 1, 1),
    "log": (sp.log, 1, 2),
    "abs": (sp.Abs, 1, 1),
    "simplify": (sp.simplify, 1, 1),
    "factor": (sp.factor, 1, 1),
    "expand": (sp.expand, 1, 1),
}


class CalculatorInputError(ValueError):
    """The submitted expression is invalid, unsupported, or outside limits."""


def evaluate_expression(expression: str) -> str:
    """Parse and stringify a bounded numeric or symbolic expression."""
    expression = expression.strip()
    if not expression or len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculatorInputError

    try:
        parsed = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise CalculatorInputError from None

    _validate_ast_size(parsed)
    symbols: dict[str, sp.Symbol] = {}
    try:
        result = _evaluate_node(parsed.body, expression, symbols)
        _validate_numeric_value(result)
        if len(symbols) > MAX_SYMBOLS or sp.count_ops(result) > MAX_RESULT_OPERATIONS:
            raise CalculatorInputError
        rendered = sp.sstr(result)
    except CalculatorInputError:
        raise
    except (ArithmeticError, InvalidOperation, RecursionError, TypeError, ValueError):
        raise CalculatorInputError from None

    if not rendered or len(rendered) > MAX_RESULT_LENGTH:
        raise CalculatorInputError
    return rendered


def _validate_ast_size(tree: ast.AST) -> None:
    stack = [(tree, 1)]
    node_count = 0
    while stack:
        node, depth = stack.pop()
        node_count += 1
        if node_count > MAX_EXPRESSION_NODES or depth > MAX_EXPRESSION_DEPTH:
            raise CalculatorInputError
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))


def _evaluate_node(node: ast.AST, source: str, symbols: dict[str, sp.Symbol]) -> sp.Expr:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorInputError
        literal = ast.get_source_segment(source, node)
        if literal is None or "_" in literal:
            raise CalculatorInputError
        try:
            decimal_value = Decimal(literal)
        except InvalidOperation:
            raise CalculatorInputError from None
        if (
            not decimal_value.is_finite()
            or abs(decimal_value.adjusted()) > MAX_NUMERIC_ADJUSTED_EXPONENT
        ):
            raise CalculatorInputError
        value = sp.Rational(literal)
        _validate_numeric_value(value)
        return value

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        if not _SYMBOL_NAME.fullmatch(node.id) or node.id in _FUNCTIONS:
            raise CalculatorInputError
        symbol = symbols.get(node.id)
        if symbol is None:
            if len(symbols) >= MAX_SYMBOLS:
                raise CalculatorInputError
            symbol = sp.Symbol(node.id)
            symbols[node.id] = symbol
        return symbol

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand, source, symbols)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            value = -operand
        else:
            raise CalculatorInputError
        _validate_numeric_value(value)
        return value

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, source, symbols)
        right = _evaluate_node(node.right, source, symbols)

        if isinstance(node.op, ast.Add):
            value = left + right
        elif isinstance(node.op, ast.Sub):
            value = left - right
        elif isinstance(node.op, ast.Mult):
            value = left * right
        elif isinstance(node.op, ast.Div):
            if right.is_zero is True:
                raise CalculatorInputError
            value = left / right
        elif isinstance(node.op, ast.FloorDiv):
            if right.is_zero is True:
                raise CalculatorInputError
            value = sp.floor(left / right)
        elif isinstance(node.op, ast.Mod):
            if right.is_zero is True:
                raise CalculatorInputError
            value = sp.Mod(left, right)
        elif isinstance(node.op, ast.Pow):
            _validate_exponent(right)
            value = left**right
        else:
            raise CalculatorInputError
        _validate_numeric_value(value)
        return value

    if isinstance(node, ast.Call):
        if (
            not isinstance(node.func, ast.Name)
            or node.keywords
            or node.func.id not in _FUNCTIONS
        ):
            raise CalculatorInputError
        function, min_args, max_args = _FUNCTIONS[node.func.id]
        if not min_args <= len(node.args) <= max_args:
            raise CalculatorInputError
        args = [_evaluate_node(arg, source, symbols) for arg in node.args]
        value = function(*args)
        _validate_numeric_value(value)
        return value

    # Attribute access, comparisons, containers, lambdas, and all other syntax
    # are rejected. Only the AST nodes explicitly handled above are accepted.
    raise CalculatorInputError


def _validate_exponent(exponent: sp.Expr) -> None:
    if exponent.is_number:
        if exponent.is_real is not True:
            raise CalculatorInputError
        if sp.Abs(exponent) > MAX_NUMERIC_EXPONENT:
            raise CalculatorInputError


def _validate_numeric_value(value: Any) -> None:
    if not isinstance(value, sp.Basic) or value.is_number is not True:
        return
    if value.is_finite is False:
        raise CalculatorInputError

    magnitude = sp.Abs(value)
    if magnitude.is_zero is True:
        return
    if magnitude.is_real is not True or magnitude.is_finite is False:
        raise CalculatorInputError
    try:
        if magnitude > MAX_NUMERIC_MAGNITUDE or magnitude < MIN_NUMERIC_MAGNITUDE:
            raise CalculatorInputError
    except TypeError:
        raise CalculatorInputError from None
