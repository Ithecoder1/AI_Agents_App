"""Unit tests for Arithmetic Evaluator Engine."""

from __future__ import annotations

import pytest
from app.engine import solve_arithmetic


def test_solve_simple_addition():
    assert solve_arithmetic("1+1") == 2
    assert solve_arithmetic("2 + 3") == 5
    assert solve_arithmetic("0 + 0") == 0


def test_solve_invalid_expression():
    with pytest.raises(ValueError):
        solve_arithmetic("")

    with pytest.raises(ValueError):
        solve_arithmetic("10 - 5")
