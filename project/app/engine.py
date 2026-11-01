"""Arithmetic Evaluator Engine.

Evaluates mathematical expressions deterministically.
Requirement: hi
"""

from __future__ import annotations


def solve_arithmetic(expression: str) -> int:
    """Evaluate simple arithmetic expression like '1+1' or '2 + 3'.

    Raises ValueError on invalid syntax or unsupported operations.
    """
    clean = expression.replace(" ", "").strip()
    if not clean:
        raise ValueError("Expression cannot be empty")

    if "+" in clean:
        parts = clean.split("+")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            a, b = int(parts[0]), int(parts[1])
            return a + b

    raise ValueError(f"Unsupported arithmetic expression: {expression!r}")
