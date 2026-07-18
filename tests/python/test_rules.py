"""Tests for capture legality and terrain effects."""

from engine.board import Board
from engine.pieces import Animal, Color, make_piece_id
from engine.rules import can_capture, effective_rank
from engine.move_generator import generate_legal_moves


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def place(board: Board, col: int, row: int, color: Color, animal: Animal) -> int:
    pid = make_piece_id(color, animal)
    board.place_piece(col, row, pid)
    return pid


# ---------------------------------------------------------------------------
# Test: Higher rank captures lower rank
# ---------------------------------------------------------------------------

def test_higher_rank_captures_lower():
    b = Board()
    atk = make_piece_id(Color.BLUE, Animal.LION)    # rank 7
    def_ = make_piece_id(Color.BLACK, Animal.LEOPARD)  # rank 5
    assert can_capture(atk, def_, 3, 4, 3, 3, b)


def test_equal_rank_captures():
    b = Board()
    atk = make_piece_id(Color.BLUE, Animal.WOLF)   # rank 4
    def_ = make_piece_id(Color.BLACK, Animal.WOLF)  # rank 4
    assert can_capture(atk, def_, 3, 4, 3, 3, b)


def test_lower_rank_cannot_capture():
    b = Board()
    atk = make_piece_id(Color.BLUE, Animal.CAT)       # rank 2
    def_ = make_piece_id(Color.BLACK, Animal.LEOPARD)  # rank 5
    assert not can_capture(atk, def_, 3, 4, 3, 3, b)


# ---------------------------------------------------------------------------
# Test: Rat (land) captures Elephant (land)
# ---------------------------------------------------------------------------

def test_rat_land_captures_elephant_land():
    b = Board()
    rat = make_piece_id(Color.BLUE, Animal.RAT)       # rank 1
    ele = make_piece_id(Color.BLACK, Animal.ELEPHANT)  # rank 8
    # Both on land squares
    assert can_capture(rat, ele, 0, 0, 0, 1, b), "Rat on land should capture Elephant on land"


# ---------------------------------------------------------------------------
# Test: Elephant (land) cannot capture Rat (land)
# ---------------------------------------------------------------------------

def test_elephant_cannot_capture_rat():
    b = Board()
    ele = make_piece_id(Color.BLUE, Animal.ELEPHANT)
    rat = make_piece_id(Color.BLACK, Animal.RAT)
    assert not can_capture(ele, rat, 0, 0, 0, 1, b), "Elephant should NOT capture Rat"


# ---------------------------------------------------------------------------
# Test: Rat in water cannot capture Elephant on land
# ---------------------------------------------------------------------------

def test_rat_water_cannot_capture_elephant_land():
    b = Board()
    rat = make_piece_id(Color.BLUE, Animal.RAT)
    ele = make_piece_id(Color.BLACK, Animal.ELEPHANT)
    # Rat at (1,4) = river, Elephant at (0,4) = land
    assert not can_capture(rat, ele, 1, 4, 0, 4, b), "Rat in water should NOT capture Elephant on land"


# ---------------------------------------------------------------------------
# Test: Rat on land cannot capture Rat in water
# ---------------------------------------------------------------------------

def test_rat_land_cannot_capture_rat_water():
    b = Board()
    rat_blue = make_piece_id(Color.BLUE, Animal.RAT)
    rat_black = make_piece_id(Color.BLACK, Animal.RAT)
    # Blue rat at (0,4) = land, Black rat at (1,4) = river
    assert not can_capture(rat_blue, rat_black, 0, 4, 1, 4, b), "Land rat should NOT capture water rat"


# ---------------------------------------------------------------------------
# Test: Rat in water can capture Rat in water
# ---------------------------------------------------------------------------

def test_rat_water_captures_rat_water():
    b = Board()
    rat_blue = make_piece_id(Color.BLUE, Animal.RAT)
    rat_black = make_piece_id(Color.BLACK, Animal.RAT)
    # Both in river: (1,3) and (1,4) are both river squares
    assert can_capture(rat_blue, rat_black, 1, 3, 1, 4, b), "Water rat should capture water rat"


# ---------------------------------------------------------------------------
# Test: trap rule outranks the rank hierarchy (v1.5 fix)
# ---------------------------------------------------------------------------

def test_elephant_captures_trapped_rat():
    """An enemy Rat standing in the attacker-side trap has rank 0 and must be
    capturable by ANY adjacent piece — including the Elephant (the rank-
    hierarchy exception does not protect a trapped piece)."""
    b = Board()
    ele = place(b, 2, 7, Color.BLUE, Animal.ELEPHANT)
    rat = place(b, 2, 8, Color.BLACK, Animal.RAT)     # (2,8) is a Blue trap
    assert can_capture(ele, rat, 2, 7, 2, 8, b), \
        "Elephant must capture a Rat trapped in the Elephant-side trap"


def test_movegen_produces_elephant_takes_trapped_rat():
    b = Board()
    place(b, 2, 7, Color.BLUE, Animal.ELEPHANT)
    place(b, 2, 8, Color.BLACK, Animal.RAT)           # Blue trap
    moves = generate_legal_moves(b, Color.BLUE)
    assert any((m.fc, m.fr, m.tc, m.tr) == (2, 7, 2, 8) and m.captured != 0
               for m in moves), "movegen must offer Elephant x trapped Rat"


def test_untrapped_elephant_vs_rat_unchanged():
    """The classic rule stays: Elephant cannot take an untrapped Rat."""
    b = Board()
    ele = place(b, 3, 4, Color.BLUE, Animal.ELEPHANT)
    rat = place(b, 3, 3, Color.BLACK, Animal.RAT)     # plain land square
    assert not can_capture(ele, rat, 3, 4, 3, 3, b)


def test_water_guard_precedes_trap_rule():
    """A Rat in the river stays immune to land attackers; the trap return
    must sit after the water-boundary guard."""
    b = Board()
    lion = place(b, 0, 4, Color.BLUE, Animal.LION)
    rat = place(b, 1, 4, Color.BLACK, Animal.RAT)     # river square
    assert not can_capture(lion, rat, 0, 4, 1, 4, b)


# ---------------------------------------------------------------------------
# Test: Piece in opponent trap has effective rank 0
# ---------------------------------------------------------------------------

def test_piece_in_opponent_trap_rank_zero():
    # Blue trap squares: (2,8),(4,8),(3,7)
    # A Black piece in a Blue trap has effective rank 0
    black_elephant = make_piece_id(Color.BLACK, Animal.ELEPHANT)
    trap_col, trap_row = 2, 8  # Blue trap
    rank = effective_rank(black_elephant, trap_col, trap_row)
    assert rank == 0, "Piece in opponent trap should have effective rank 0"


def test_any_piece_captures_trapped_piece():
    b = Board()
    # Black Elephant in Blue trap (2,8)
    trap_col, trap_row = 2, 8
    ele_pid = make_piece_id(Color.BLACK, Animal.ELEPHANT)
    # Blue Rat trying to capture
    rat_pid = make_piece_id(Color.BLUE, Animal.RAT)
    assert can_capture(rat_pid, ele_pid, 2, 7, trap_col, trap_row, b), \
        "Rat should capture Elephant that is trapped (rank 0)"


# ---------------------------------------------------------------------------
# Test: Piece in OWN trap has normal rank (no reduction)
# ---------------------------------------------------------------------------

def test_piece_in_own_trap_normal_rank():
    # Blue piece in Blue trap — own trap, no rank reduction
    blue_cat = make_piece_id(Color.BLUE, Animal.CAT)
    own_trap_col, own_trap_row = 2, 8  # Blue trap
    rank = effective_rank(blue_cat, own_trap_col, own_trap_row)
    assert rank == Animal.CAT, "Piece in own trap should retain normal rank"


# ---------------------------------------------------------------------------
# Test: Same-color pieces cannot capture each other
# ---------------------------------------------------------------------------

def test_cannot_capture_own_piece():
    b = Board()
    blue_lion = make_piece_id(Color.BLUE, Animal.LION)
    blue_rat = make_piece_id(Color.BLUE, Animal.RAT)
    assert not can_capture(blue_lion, blue_rat, 3, 4, 3, 3, b)


# ---------------------------------------------------------------------------
# Test: Rat in water is invulnerable to land piece attacks
# ---------------------------------------------------------------------------

def test_rat_in_water_invulnerable_to_land_attacks():
    """A land piece adjacent to the river cannot capture a rat in the river."""
    rat_pid = make_piece_id(Color.BLACK, Animal.RAT)

    # All Blue pieces on land adjacent to river — none should be able to capture the rat
    for animal in [Animal.CAT, Animal.DOG, Animal.WOLF, Animal.LEOPARD,
                   Animal.LION, Animal.ELEPHANT]:
        b2 = Board()
        b2.place_piece(1, 4, rat_pid)
        atk_pid = make_piece_id(Color.BLUE, animal)
        b2.place_piece(0, 4, atk_pid)
        moves = generate_legal_moves(b2, Color.BLUE)
        # No move should capture the rat at (1,4)
        captures = [m for m in moves if m.tc == 1 and m.tr == 4 and m.captured != 0]
        assert not captures, f"{animal.name} on land should not capture Rat in water"
