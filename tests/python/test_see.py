"""Tests for ai/see.py — Static Exchange Evaluation."""

from engine.board import Board, Move
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id
from ai.see import see_capture
from config import PIECE_VALUES


def make_gs(*piece_specs) -> GameState:
    gs = GameState()
    gs.board = Board()
    for (c, r, color, animal) in piece_specs:
        pid = make_piece_id(color, animal)
        gs.board.place_piece(c, r, pid)
    return gs


def _find(gs: GameState, fc, fr, tc, tr) -> Move:
    for m in gs.legal_moves():
        if (m.fc, m.fr, m.tc, m.tr) == (fc, fr, tc, tr):
            return m
    raise AssertionError(f"move {(fc,fr,tc,tr)} not legal")


def test_see_positive_for_undefended_capture():
    gs = make_gs(
        (3, 4, Color.BLUE, Animal.TIGER),
        (3, 5, Color.BLACK, Animal.WOLF),
    )
    gs.turn = Color.BLUE
    move = _find(gs, 3, 4, 3, 5)
    assert see_capture(gs.board, move) == PIECE_VALUES[int(Animal.WOLF)]


def test_see_negative_for_defended_capture():
    """Tiger captures Wolf, but defending Lion can recapture — net loss."""
    gs = make_gs(
        (3, 4, Color.BLUE, Animal.TIGER),     # 600
        (3, 5, Color.BLACK, Animal.WOLF),     # 400 - victim
        (3, 6, Color.BLACK, Animal.LION),     # 700 - recapturer
    )
    gs.turn = Color.BLUE
    move = _find(gs, 3, 4, 3, 5)
    # Net: +400 (Wolf) -600 (Tiger) = -200
    assert see_capture(gs.board, move) < 0


def test_see_zero_for_equal_trade():
    gs = make_gs(
        (3, 4, Color.BLUE, Animal.WOLF),
        (3, 5, Color.BLACK, Animal.WOLF),
        (3, 6, Color.BLACK, Animal.WOLF),
    )
    gs.turn = Color.BLUE
    move = _find(gs, 3, 4, 3, 5)
    # +400 -400 = 0
    assert see_capture(gs.board, move) == 0


def test_see_no_capture_returns_zero():
    gs = make_gs((3, 4, Color.BLUE, Animal.RAT))
    gs.turn = Color.BLUE
    move = Move(3, 4, 3, 3, 0)  # rat into water, no capture
    assert see_capture(gs.board, move) == 0
