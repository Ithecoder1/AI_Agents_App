"""Deterministic Math Engine."""

def solve_arithmetic(expr: str) -> int:
    """Evaluate a simple arithmetic expression."""
    clean = expr.strip().replace(" ", "")
    if "+" in clean:
        parts = clean.split("+")
        return int(parts[0]) + int(parts[1])
    raise ValueError(f"Unsupported expression: {expr}")
