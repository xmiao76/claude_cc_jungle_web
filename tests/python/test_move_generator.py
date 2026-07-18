"""Tests for move generation covering all special Jungle movement rules."""

from engine.board import Board, Move
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id
from engine.move_generator import generate_legal_moves
from config import DEN_BLUE, DEN_BLACK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def empty_board() -> Board:
    b = Board()
    return b


def place(board: Board, col: int, row: int, color: Color, animal: Animal) -> int:
    pid = make_piece_id(color, animal)
    board.place_piece(col, row, pid)
    return pid


def moves_from(board: Board, color: Color, col: int, row: int) -> list[Move]:
    return [m for m in generate_legal_moves(board, color) if m.fc == col and m.fr == row]


def destinations(moves: list[Move]) -> set[tuple[int, int]]:
    return {(m.tc, m.tr) for m in moves}


# ---------------------------------------------------------------------------
# Test 1: Normal step — piece can move to all 4 adjacent land squares
# ---------------------------------------------------------------------------

def test_normal_steps_wolf_centre():
    b = empty_board()
    # (3,4) is land, but (2,4) and (4,4) are river squares — Wolf cannot enter rivers
    place(b, 3, 4, Color.BLUE, Animal.WOLF)
    mvs = moves_from(b, Color.BLUE, 3, 4)
    # Only (3,3) and (3,5) are reachable non-river adjacent squares
    assert (3, 3) in destinations(mvs)
    assert (3, 5) in destinations(mvs)
    assert (2, 4) not in destinations(mvs), "River square should be inaccessible to Wolf"
    assert (4, 4) not in destinations(mvs), "River square should be inaccessible to Wolf"


def test_normal_steps_wolf_on_open_land():
    b = empty_board()
    # (3,6) is open land with no adjacent rivers
    place(b, 3, 6, Color.BLUE, Animal.WOLF)
    mvs = moves_from(b, Color.BLUE, 3, 6)
    assert destinations(mvs) == {(2, 6), (4, 6), (3, 5), (3, 7)}


# ---------------------------------------------------------------------------
# Test 2: Lion can jump vertically over river
# ---------------------------------------------------------------------------

def test_lion_vertical_jump():
    b = empty_board()
    # Lion above river block 1 (col 1, rows 3-5), starting at row 2 → lands row 6
    place(b, 1, 2, Color.BLUE, Animal.LION)
    dests = destinations(moves_from(b, Color.BLUE, 1, 2))
    assert (1, 6) in dests, "Lion should be able to jump vertically over river"


# ---------------------------------------------------------------------------
# Test 3: Lion can jump horizontally over river (cols 1-2, row 4)
# ---------------------------------------------------------------------------

def test_lion_horizontal_jump():
    b = empty_board()
    # Lion to the left of river block 1, row 4 → should jump to col 3
    place(b, 0, 4, Color.BLUE, Animal.LION)
    dests = destinations(moves_from(b, Color.BLUE, 0, 4))
    assert (3, 4) in dests, "Lion should be able to jump horizontally over river"


# ---------------------------------------------------------------------------
# Test 4: Tiger CANNOT jump vertically (the 3-square crossing is too long)
# ---------------------------------------------------------------------------

def test_tiger_no_vertical_jump():
    b = empty_board()
    place(b, 1, 2, Color.BLUE, Animal.TIGER)
    dests = destinations(moves_from(b, Color.BLUE, 1, 2))
    assert (1, 6) not in dests, "Tiger should NOT make the 3-square vertical jump"


# ---------------------------------------------------------------------------
# Test 5: Tiger CAN jump horizontally (the 2-square crossing)
# ---------------------------------------------------------------------------

def test_tiger_horizontal_jump():
    b = empty_board()
    place(b, 0, 4, Color.BLUE, Animal.TIGER)
    dests = destinations(moves_from(b, Color.BLUE, 0, 4))
    assert (3, 4) in dests, "Tiger should be able to jump horizontally over river"


def test_tiger_jump_blocked_by_rat():
    """Rat sitting in the 2-square horizontal river path blocks the Tiger jump."""
    b = empty_board()
    place(b, 0, 4, Color.BLUE, Animal.TIGER)
    place(b, 1, 4, Color.BLACK, Animal.RAT)   # in path of (0,4) -> (3,4)
    dests = destinations(moves_from(b, Color.BLUE, 0, 4))
    assert (3, 4) not in dests, "Tiger jump should be blocked by rat in river"


# ---------------------------------------------------------------------------
# Test 6: River jump blocked by own rat in river
# ---------------------------------------------------------------------------

def test_lion_jump_blocked_by_own_rat():
    b = empty_board()
    place(b, 1, 2, Color.BLUE, Animal.LION)
    place(b, 1, 4, Color.BLUE, Animal.RAT)  # own rat in river
    dests = destinations(moves_from(b, Color.BLUE, 1, 2))
    assert (1, 6) not in dests, "Jump should be blocked by own rat in river"


# ---------------------------------------------------------------------------
# Test 7: River jump blocked by opponent rat in river
# ---------------------------------------------------------------------------

def test_lion_jump_blocked_by_opponent_rat():
    b = empty_board()
    place(b, 1, 2, Color.BLUE, Animal.LION)
    place(b, 1, 3, Color.BLACK, Animal.RAT)  # enemy rat in river
    dests = destinations(moves_from(b, Color.BLUE, 1, 2))
    assert (1, 6) not in dests, "Jump should be blocked by opponent rat in river"


# ---------------------------------------------------------------------------
# Test 8: Jump NOT blocked when rat is on land (not in river)
# ---------------------------------------------------------------------------

def test_lion_jump_not_blocked_by_land_rat():
    b = empty_board()
    place(b, 1, 2, Color.BLUE, Animal.LION)
    place(b, 1, 7, Color.BLACK, Animal.RAT)  # rat on land, not in river
    dests = destinations(moves_from(b, Color.BLUE, 1, 2))
    assert (1, 6) in dests, "Jump should not be blocked by rat on land"


# ---------------------------------------------------------------------------
# Test 9: Rat can enter river squares
# ---------------------------------------------------------------------------

def test_rat_enters_river():
    b = empty_board()
    place(b, 1, 2, Color.BLUE, Animal.RAT)  # adjacent to river row 3
    dests = destinations(moves_from(b, Color.BLUE, 1, 2))
    assert (1, 3) in dests, "Rat should be able to enter river"


# ---------------------------------------------------------------------------
# Test 10: Non-rat pieces cannot enter river
# ---------------------------------------------------------------------------

def test_non_rat_cannot_enter_river():
    for animal in [Animal.CAT, Animal.DOG, Animal.WOLF, Animal.LEOPARD,
                   Animal.TIGER, Animal.LION, Animal.ELEPHANT]:
        b2 = empty_board()
        place(b2, 1, 2, Color.BLUE, animal)
        dests = destinations(moves_from(b2, Color.BLUE, 1, 2))
        assert (1, 3) not in dests, f"{animal.name} should not enter river"


# ---------------------------------------------------------------------------
# Test 11: dedicated noisy-only generator (v1.4 speed pack) is behaviorally
# identical to filtering the full legal-move generation
# ---------------------------------------------------------------------------

def test_noisy_only_matches_filtered_full_generation():
    import random
    from engine.game_state import GameState
    from engine.move_generator import generate_noisy_moves, generate_noisy_only

    rng = random.Random(20260715)
    for game in range(6):
        gs = GameState()
        gs.new_game()
        for _ in range(60):
            if gs.is_terminal():
                break
            for color in (Color.BLUE, Color.BLACK):
                fast = set(generate_noisy_only(gs.board, color))
                slow = set(generate_noisy_moves(gs.board, color))
                assert fast == slow, (
                    f"noisy-only mismatch (game {game}): "
                    f"extra={fast - slow} missing={slow - fast}"
                )
            gs.apply_move(rng.choice(gs.legal_moves()))


# ---------------------------------------------------------------------------
# Test 11: Moving to own den is illegal
# ---------------------------------------------------------------------------

def test_cannot_enter_own_den_blue():
    b = empty_board()
    # Place Blue piece adjacent to Blue's den (3,8)
    place(b, 3, 7, Color.BLUE, Animal.WOLF)
    dests = destinations(moves_from(b, Color.BLUE, 3, 7))
    assert DEN_BLUE not in dests, "Blue should not be able to enter own den"


def test_cannot_enter_own_den_black():
    b = empty_board()
    place(b, 3, 1, Color.BLACK, Animal.WOLF)
    dests = destinations(moves_from(b, Color.BLACK, 3, 1))
    assert DEN_BLACK not in dests, "Black should not be able to enter own den"


# ---------------------------------------------------------------------------
# Test 12: Can enter opponent's den
# ---------------------------------------------------------------------------

def test_can_enter_opponent_den():
    b = empty_board()
    # Blue piece adjacent to Black's den (3,0)
    place(b, 3, 1, Color.BLUE, Animal.WOLF)
    dests = destinations(moves_from(b, Color.BLUE, 3, 1))
    # Note: (3,1) is Black's trap — adjacent to (3,0) Black's den
    assert DEN_BLACK in dests, "Blue should be able to enter opponent's den"


# ---------------------------------------------------------------------------
# Test 13: Starting position has 24 moves for Blue
# ---------------------------------------------------------------------------

def test_starting_position_move_count():
    gs = GameState()
    gs.new_game()
    assert len(gs.legal_moves()) == 24


# ---------------------------------------------------------------------------
# Test 14: Piece blocked by friendly piece
# ---------------------------------------------------------------------------

def test_friendly_piece_blocks_move():
    b = empty_board()
    place(b, 3, 4, Color.BLUE, Animal.WOLF)
    place(b, 3, 3, Color.BLUE, Animal.CAT)  # blocks upward move
    dests = destinations(moves_from(b, Color.BLUE, 3, 4))
    assert (3, 3) not in dests
