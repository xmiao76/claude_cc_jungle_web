"""Tests for repetition / 50-move tracking in GameState."""

from engine.board import Board
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id


def make_gs(*piece_specs) -> GameState:
    gs = GameState()
    gs.board = Board()
    for (c, r, color, animal) in piece_specs:
        pid = make_piece_id(color, animal)
        gs.board.place_piece(c, r, pid)
    return gs


def _step(gs: GameState, fc, fr, tc, tr) -> None:
    move = next(m for m in gs.legal_moves()
                if (m.fc, m.fr, m.tc, m.tr) == (fc, fr, tc, tr))
    gs.apply_move(move)


def test_repetition_detected_after_third_visit():
    gs = make_gs(
        (0, 8, Color.BLUE, Animal.LION),
        (6, 0, Color.BLACK, Animal.LION),
    )
    gs.turn = Color.BLUE
    # Cycle: B(0,8)->(0,7)->(0,8); K(6,0)->(6,1)->(6,0); repeat
    _step(gs, 0, 8, 0, 7)
    _step(gs, 6, 0, 6, 1)
    assert not gs.is_repetition()
    _step(gs, 0, 7, 0, 8)
    _step(gs, 6, 1, 6, 0)
    # Back to starting position with Blue to move — repetition detected.
    assert gs.is_repetition()


def test_repetition_survives_copy():
    gs = make_gs(
        (0, 8, Color.BLUE, Animal.LION),
        (6, 0, Color.BLACK, Animal.LION),
    )
    gs.turn = Color.BLUE
    _step(gs, 0, 8, 0, 7)
    _step(gs, 6, 0, 6, 1)
    gs2 = gs.copy()
    assert gs2._hash_history == gs._hash_history
    assert gs2._halfmove_clock == gs._halfmove_clock


def test_halfmove_clock_resets_on_capture():
    gs = make_gs(
        (3, 4, Color.BLUE, Animal.TIGER),
        (3, 5, Color.BLACK, Animal.WOLF),
    )
    gs.turn = Color.BLUE
    _step(gs, 3, 4, 3, 5)  # capture
    assert gs._halfmove_clock == 0


def test_halfmove_clock_increments_on_quiet_move():
    gs = make_gs(
        (0, 8, Color.BLUE, Animal.LION),
        (6, 0, Color.BLACK, Animal.LION),
    )
    gs.turn = Color.BLUE
    _step(gs, 0, 8, 0, 7)
    assert gs._halfmove_clock == 1
    _step(gs, 6, 0, 6, 1)
    assert gs._halfmove_clock == 2


def test_apply_undo_null_round_trip():
    gs = make_gs(
        (0, 8, Color.BLUE, Animal.LION),
        (6, 0, Color.BLACK, Animal.LION),
    )
    gs.turn = Color.BLUE
    h0 = gs.board.turn_hash(gs.turn)
    gs.apply_null()
    assert gs.turn == Color.BLACK
    gs.undo_null()
    assert gs.turn == Color.BLUE
    assert gs.board.turn_hash(gs.turn) == h0
    assert gs._hash_history == []
    assert gs._halfmove_clock == 0
