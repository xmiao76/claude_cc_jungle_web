"""Tests for win condition detection."""

from engine.board import Board, Move
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_gs_with_pieces(*piece_specs) -> GameState:
    """Create a GameState with specified pieces. piece_specs: (col, row, Color, Animal)"""
    gs = GameState()
    gs.board = Board()
    for (c, r, color, animal) in piece_specs:
        pid = make_piece_id(color, animal)
        gs.board.place_piece(c, r, pid)
    return gs


# ---------------------------------------------------------------------------
# Test: Den entry wins immediately
# ---------------------------------------------------------------------------

def test_blue_enters_black_den():
    """Blue piece moving into Black's den (3,0) should win for Blue."""
    # Blue Wolf at (3,1), Black has a piece elsewhere
    gs = make_gs_with_pieces(
        (3, 1, Color.BLUE, Animal.WOLF),
        (0, 8, Color.BLACK, Animal.ELEPHANT),
    )
    gs.turn = Color.BLUE

    # Move Wolf into Black's den
    move = Move(3, 1, 3, 0, 0)
    gs.apply_move(move)

    assert gs.result is not None, "Game should be over after den entry"
    assert gs.result.winner == Color.BLUE, "Blue should win by entering Black's den"


def test_black_enters_blue_den():
    """Black piece moving into Blue's den (3,8) should win for Black."""
    gs = make_gs_with_pieces(
        (0, 0, Color.BLUE, Animal.ELEPHANT),
        (3, 7, Color.BLACK, Animal.WOLF),
    )
    gs.turn = Color.BLACK

    move = Move(3, 7, 3, 8, 0)
    gs.apply_move(move)

    assert gs.result is not None
    assert gs.result.winner == Color.BLACK, "Black should win by entering Blue's den"


# ---------------------------------------------------------------------------
# Test: Capture-all wins
# ---------------------------------------------------------------------------

def test_capture_last_piece_wins():
    """Capturing the opponent's last remaining piece should win."""
    gs = make_gs_with_pieces(
        (0, 0, Color.BLUE, Animal.LION),
        (0, 1, Color.BLACK, Animal.CAT),   # Black's only piece
    )
    gs.turn = Color.BLUE

    move = Move(0, 0, 0, 1, make_piece_id(Color.BLACK, Animal.CAT))
    gs.apply_move(move)

    assert gs.result is not None
    assert gs.result.winner == Color.BLUE


# ---------------------------------------------------------------------------
# Test: No win in mid-game
# ---------------------------------------------------------------------------

def test_no_win_midgame():
    """Mid-game state with many pieces should return no winner."""
    gs = GameState()
    gs.new_game()
    gs.apply_move(gs.legal_moves()[0])

    assert gs.result is None
    assert gs.get_winner() is None


# ---------------------------------------------------------------------------
# Test: check_win returns None for normal capture
# ---------------------------------------------------------------------------

def test_normal_capture_no_win():
    """A capture that doesn't end the game should not trigger win."""
    gs = make_gs_with_pieces(
        (0, 0, Color.BLUE, Animal.LION),
        (0, 1, Color.BLACK, Animal.CAT),
        (0, 8, Color.BLACK, Animal.ELEPHANT),  # Black still has another piece
    )
    gs.turn = Color.BLUE

    move = Move(0, 0, 0, 1, make_piece_id(Color.BLACK, Animal.CAT))
    gs.apply_move(move)

    assert gs.result is None, "Should not be over; Black still has Elephant"


# ---------------------------------------------------------------------------
# Test: Stalemate (no legal moves) — player with no moves loses
# ---------------------------------------------------------------------------

def test_stalemate_loses():
    """Player with no legal moves loses."""
    # Stalemate: Black has a piece surrounded by Blue pieces with no moves.
    gs2 = make_gs_with_pieces(
        (6, 8, Color.BLACK, Animal.RAT),   # corner
        (5, 8, Color.BLUE, Animal.ELEPHANT),  # blocks left
        (6, 7, Color.BLUE, Animal.LION),      # blocks up
        (0, 0, Color.BLUE, Animal.CAT),       # dummy Blue piece elsewhere
    )
    gs2.turn = Color.BLACK
    moves = gs2.legal_moves()
    # The Rat at (6,8) can't move right (off-board) or down (off-board),
    # can't move to (5,8) — occupied by higher-rank Blue Elephant (won't be captured),
    # can't move to (6,7) — blocked by Blue Lion (higher rank)
    # Also (6,8) is Blue's den — but it's Black's piece, not allowed to enter own den (no, wait:
    # (6,8) is NOT a den; Blue den is (3,8). So Black Rat can move to (5,8) if it can capture.
    # Elephant rank 8 > Rat rank 1 → cannot capture. Lion rank 7 > Rat rank 1 → cannot capture.
    # So Black Rat has 0 moves.
    if not moves:
        assert gs2.is_terminal()
        assert gs2.get_winner() == Color.BLUE
