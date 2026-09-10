"""
Unit tests for the pure math in app.py: material_balance() and
estimate_win_probability(). Both are plain functions with no Redis/Flask
dependency, so they're testable without standing up any live services
(unlike process_move()/listen_for_moves(), which do real I/O).
"""
import math

from app import estimate_win_probability, material_balance

STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_material_balance_starting_position_is_even():
    assert material_balance(STARTING_FEN) == 0


def test_material_balance_ignores_side_to_move_and_metadata():
    # Same position, just black to move - only the board part should matter.
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"
    assert material_balance(fen) == 0


def test_material_balance_white_up_a_queen():
    # Black's queen removed from the back rank.
    fen = "rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    assert material_balance(fen) == 9


def test_material_balance_black_up_a_rook():
    # White's a1 rook removed.
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/1NBQKBNR w KQkq - 0 1"
    assert material_balance(fen) == -5


def test_material_balance_kings_do_not_count():
    # Bare kings only - king value is defined as 0 in PIECE_VALUES.
    fen = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
    assert material_balance(fen) == 0


def test_material_balance_handles_all_piece_types():
    # One of each white piece vs. a lone black king: p+n+b+r+q = 1+3+3+5+9 = 21.
    fen = "4k3/8/8/8/8/8/8/PNBRQK2 w - - 0 1"
    assert material_balance(fen) == 21


def test_estimate_win_probability_even_material_is_fifty_fifty():
    result = estimate_win_probability(STARTING_FEN)
    assert result["material_balance"] == 0
    assert result["white_win_pct"] == 50.0
    assert result["black_win_pct"] == 50.0


def test_estimate_win_probability_percentages_sum_to_one_hundred():
    fen = "rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    result = estimate_win_probability(fen)
    assert round(result["white_win_pct"] + result["black_win_pct"], 1) == 100.0


def test_estimate_win_probability_favors_white_when_white_is_up_material():
    fen = "rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    result = estimate_win_probability(fen)
    assert result["material_balance"] == 9
    assert result["white_win_pct"] > 50.0
    assert result["black_win_pct"] < 50.0


def test_estimate_win_probability_favors_black_when_black_is_up_material():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/1NBQKBNR w KQkq - 0 1"
    result = estimate_win_probability(fen)
    assert result["material_balance"] == -5
    assert result["white_win_pct"] < 50.0
    assert result["black_win_pct"] > 50.0


def test_estimate_win_probability_matches_logistic_formula_directly():
    # Cross-check against the literal formula in app.py rather than just
    # asserting monotonicity, to catch any accidental change in curve shape.
    fen = "rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    diff = material_balance(fen)
    expected_white = 1 / (1 + math.exp(-diff / 3))
    result = estimate_win_probability(fen)
    assert result["white_win_pct"] == round(expected_white * 100, 1)
    assert result["black_win_pct"] == round((1 - expected_white) * 100, 1)


def test_estimate_win_probability_includes_room_independent_fields_only():
    # estimate_win_probability() itself doesn't know about roomId - that's
    # layered on by process_move(). Guard against it leaking in here.
    result = estimate_win_probability(STARTING_FEN)
    assert set(result.keys()) == {"white_win_pct", "black_win_pct", "material_balance"}
