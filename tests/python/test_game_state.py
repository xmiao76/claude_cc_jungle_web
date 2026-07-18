"""Tests for GameState: undo/redo, turn tracking, copy."""

from engine.board import Board, Move
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id


# ---------------------------------------------------------------------------
# Test: Undo move restores identical board state
# ---------------------------------------------------------------------------

def test_undo_restores_state():
    gs = GameState()
    gs.new_game()

    # Capture the initial hash
    initial_hash = gs.board.hash
    initial_turn = gs.turn

    # Apply first move
    move = gs.legal_moves()[0]
    gs.apply_move(move)

    assert gs.board.hash != initial_hash
    assert gs.turn != initial_turn

    # Undo
    gs.undo_move()

    assert gs.board.hash == initial_hash, "Hash should be restored after undo"
    assert gs.turn == initial_turn, "Turn should be restored after undo"


def test_undo_restores_captured_piece():
    """After undoing a capture, the captured piece is restored."""
    gs = GameState()
    gs.board = Board()
    # Blue Wolf at (3,4), Black Cat at (3,3)
    wolf_pid = make_piece_id(Color.BLUE, Animal.WOLF)
    cat_pid = make_piece_id(Color.BLACK, Animal.CAT)
    gs.board.place_piece(3, 4, wolf_pid)
    gs.board.place_piece(3, 3, cat_pid)
    gs.turn = Color.BLUE

    move = Move(3, 4, 3, 3, cat_pid)
    gs.apply_move(move)

    assert gs.board.get(3, 3) == wolf_pid
    assert cat_pid not in gs.board._piece_positions[int(Color.BLACK)]

    gs.undo_move()

    assert gs.board.get(3, 4) == wolf_pid, "Wolf should be back at source"
    assert gs.board.get(3, 3) == cat_pid, "Cat should be restored"
    assert cat_pid in gs.board._piece_positions[int(Color.BLACK)]


# ---------------------------------------------------------------------------
# Test: Turn alternates correctly
# ---------------------------------------------------------------------------

def test_starting_position_layout():
    """Initial layout: Lion/Tiger fixed at corners; rows 1-2 / 6-7 are
    top-to-bottom mirrors so each player's Elephant sits on their own left side
    (Black Elephant col 6, Blue Elephant col 0)."""
    from engine.pieces import (
        Animal, Color, piece_id_color, piece_id_animal,
    )
    expected = {
        # Black (top)
        (0, 0): (Color.BLACK, Animal.LION),
        (6, 0): (Color.BLACK, Animal.TIGER),
        (1, 1): (Color.BLACK, Animal.DOG),
        (5, 1): (Color.BLACK, Animal.CAT),
        (0, 2): (Color.BLACK, Animal.RAT),
        (2, 2): (Color.BLACK, Animal.LEOPARD),
        (4, 2): (Color.BLACK, Animal.WOLF),
        (6, 2): (Color.BLACK, Animal.ELEPHANT),
        # Blue (bottom)
        (0, 6): (Color.BLUE, Animal.ELEPHANT),
        (2, 6): (Color.BLUE, Animal.WOLF),
        (4, 6): (Color.BLUE, Animal.LEOPARD),
        (6, 6): (Color.BLUE, Animal.RAT),
        (1, 7): (Color.BLUE, Animal.CAT),
        (5, 7): (Color.BLUE, Animal.DOG),
        (0, 8): (Color.BLUE, Animal.TIGER),
        (6, 8): (Color.BLUE, Animal.LION),
    }
    gs = GameState()
    gs.new_game()
    for (c, r), (color, animal) in expected.items():
        pid = gs.board.get(c, r)
        assert pid != 0, f"({c},{r}) is empty; expected {color.name} {animal.name}"
        assert piece_id_color(pid) == color and piece_id_animal(pid) == animal, (
            f"({c},{r}) has wrong piece: got "
            f"{piece_id_color(pid).name} {piece_id_animal(pid).name}, "
            f"expected {color.name} {animal.name}"
        )
    # No extra pieces beyond the 16 expected
    total = sum(1 for c in range(7) for r in range(9) if gs.board.get(c, r) != 0)
    assert total == 16, f"expected 16 pieces, got {total}"


def test_turn_alternates():
    gs = GameState()
    gs.new_game()
    assert gs.turn == Color.BLUE

    move = gs.legal_moves()[0]
    gs.apply_move(move)
    assert gs.turn == Color.BLACK

    move2 = gs.legal_moves()[0]
    gs.apply_move(move2)
    assert gs.turn == Color.BLUE


# ---------------------------------------------------------------------------
# Test: copy produces independent state
# ---------------------------------------------------------------------------

def test_copy_is_independent():
    gs = GameState()
    gs.new_game()

    gs2 = gs.copy()

    # Modify original
    move = gs.legal_moves()[0]
    gs.apply_move(move)

    # Copy should be unchanged
    assert gs2.board.hash != gs.board.hash
    assert gs2.turn == Color.BLUE
    assert len(gs2.history) == 0


# ---------------------------------------------------------------------------
# Test: Zobrist hashes differ for different positions
# ---------------------------------------------------------------------------

def test_zobrist_different_positions():
    gs = GameState()
    gs.new_game()

    hashes = set()
    for move in gs.legal_moves()[:10]:
        gs.apply_move(move)
        hashes.add(gs.board.hash)
        gs.undo_move()

    assert len(hashes) == len(gs.legal_moves()[:10]), "Different positions should have different hashes"


# ---------------------------------------------------------------------------
# Test: Multiple undos work correctly
# ---------------------------------------------------------------------------

def test_multiple_undos():
    gs = GameState()
    gs.new_game()
    initial_hash = gs.board.hash

    moves_applied = []
    for _ in range(6):
        move = gs.legal_moves()[0]
        gs.apply_move(move)
        moves_applied.append(move)

    for _ in range(6):
        gs.undo_move()

    assert gs.board.hash == initial_hash
    assert gs.turn == Color.BLUE
    assert len(gs.history) == 0
