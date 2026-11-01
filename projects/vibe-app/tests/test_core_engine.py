from src.core_engine import solve_arithmetic

def test_solve_one_plus_one():
    assert solve_arithmetic("1+1") == 2

def test_solve_with_spaces():
    assert solve_arithmetic(" 1 + 1 ") == 2
