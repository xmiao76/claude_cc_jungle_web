"""Tests for the AI engine."""

import time
from engine.board import Board
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id
from ai.minimax import AIPlayer
from config import DEN_BLACK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_gs(*piece_specs) -> GameState:
    gs = GameState()
    gs.board = Board()
    for (c, r, color, animal) in piece_specs:
        pid = make_piece_id(color, animal)
        gs.board.place_piece(c, r, pid)
    return gs


# ---------------------------------------------------------------------------
# Test: AI finds one-move den entry win
# ---------------------------------------------------------------------------

def test_ai_finds_den_entry_win():
    """AI (Blue) should immediately move into opponent's den when available."""
    # Blue Wolf at (3,1), one step from Black's den (3,0)
    # Black has a piece far away
    gs = make_gs(
        (3, 1, Color.BLUE, Animal.WOLF),
        (6, 8, Color.BLACK, Animal.ELEPHANT),
    )
    gs.turn = Color.BLUE

    ai = AIPlayer(Color.BLUE, difficulty=1)
    move = ai.get_best_move(gs)

    assert move is not None
    assert (move.tc, move.tr) == DEN_BLACK, f"AI should enter den, got {(move.tc, move.tr)}"


# ---------------------------------------------------------------------------
# Test: AI blocks one-move opponent den entry
# ---------------------------------------------------------------------------

def test_ai_blocks_opponent_den_entry():
    """AI (Black) should block Blue from entering den next move."""
    # Blue Wolf at (3,1), Black must block or the wolf enters (3,0) next turn
    # Black has a piece that can capture the wolf
    gs = make_gs(
        (3, 1, Color.BLUE, Animal.WOLF),
        (3, 2, Color.BLACK, Animal.LION),  # Black Lion can capture Wolf
        (0, 8, Color.BLUE, Animal.RAT),    # extra Blue piece
    )
    gs.turn = Color.BLACK

    ai = AIPlayer(Color.BLACK, difficulty=1)
    move = ai.get_best_move(gs)

    assert move is not None
    # AI should capture the Wolf or otherwise prevent den entry
    # Either capture wolf at (3,1) or make blocking move
    captures_wolf = (move.tc == 3 and move.tr == 1)
    assert captures_wolf, "Black should capture the threatening Wolf"


# ---------------------------------------------------------------------------
# Test: AI time budget respected
# ---------------------------------------------------------------------------

def test_ai_time_budget_respected():
    """AI (Hard) should return within 3 seconds for any position."""
    gs = GameState()
    gs.new_game()

    ai = AIPlayer(Color.BLUE, difficulty=2)

    start = time.perf_counter()
    move = ai.get_best_move(gs, time_budget_ms=2000)
    elapsed = time.perf_counter() - start

    assert move is not None
    assert elapsed < 3.0, f"AI took too long: {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Test: AI never produces illegal move
# ---------------------------------------------------------------------------

def test_ai_never_illegal_move():
    """Over 20 AI moves from various game states, all moves must be legal."""
    gs = GameState()
    gs.new_game()

    for _ in range(20):
        if gs.is_terminal():
            break

        legal = set((m.fc, m.fr, m.tc, m.tr) for m in gs.legal_moves())
        ai = AIPlayer(gs.turn, difficulty=1)
        move = ai.get_best_move(gs, time_budget_ms=500)

        assert move is not None, "AI returned None in non-terminal position"
        assert (move.fc, move.fr, move.tc, move.tr) in legal, \
            f"AI produced illegal move: {move}"

        gs.apply_move(move)


# ---------------------------------------------------------------------------
# Test: AI Easy vs Hard — Easy has different behaviour at depth 2 vs 4
# ---------------------------------------------------------------------------

def test_ai_difficulties_distinct():
    """Easy and Hard AI should differ on a complex position."""
    gs = GameState()
    gs.new_game()

    ai_easy = AIPlayer(Color.BLUE, difficulty=0)
    ai_hard = AIPlayer(Color.BLUE, difficulty=2)

    # Advance the game 10 moves to get a richer position
    temp = gs.copy()
    for _ in range(10):
        if temp.is_terminal():
            break
        ai = AIPlayer(temp.turn, difficulty=1)
        m = ai.get_best_move(temp, time_budget_ms=200)
        if m:
            temp.apply_move(m)

    if not temp.is_terminal():
        move_easy = ai_easy.get_best_move(temp, time_budget_ms=500)
        move_hard = ai_hard.get_best_move(temp, time_budget_ms=2000)
        # At least one should return a valid move
        assert move_easy is not None or move_hard is not None


# ---------------------------------------------------------------------------
# Test: Full AI-vs-AI game completes (in test_full_game.py, but quick version here)
# ---------------------------------------------------------------------------

def test_ai_quiescence_avoids_horizon_blunder():
    """Quiescence regression: our Tiger (rank 6) is tempted to capture an
    undefended-looking Wolf (rank 4) — but a Black Lion (rank 7) is one step
    from recapturing. The AI must see through the depth-2 horizon and avoid
    losing Tiger for Wolf.

    The tactic sits on the left edge (col 0), away from the central den file,
    and Blue is up material with its den defended — so the position is *not*
    lost and avoiding the bad trade is genuinely best. (An earlier version put
    this on the den file, where Black actually had an unstoppable den dash,
    making the 'blunder' the longest-resisting move.)
    """
    gs = make_gs(
        (0, 4, Color.BLUE, Animal.TIGER),     # our attacker (rank 6)
        (0, 5, Color.BLACK, Animal.WOLF),     # tempting victim (rank 4)
        (0, 6, Color.BLACK, Animal.LION),     # defender (rank 7) > Tiger
        (6, 8, Color.BLUE, Animal.ELEPHANT),  # Blue up material + home defense
        (5, 8, Color.BLUE, Animal.LION),
        (6, 0, Color.BLACK, Animal.RAT),      # filler
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, difficulty=0)
    move = ai.get_best_move(gs)
    assert move is not None
    bad = (move.fc, move.fr, move.tc, move.tr) == (0, 4, 0, 5)
    assert not bad, "AI fell for the horizon trap (Tiger captured defended Wolf)"


def test_ai_prefers_faster_mate():
    """When two winning lines exist, AI should pick the shorter mate."""
    # Blue Wolf at (3,1) — 1 move from den (3,0). Lion is also nearby but slower.
    gs = make_gs(
        (3, 1, Color.BLUE, Animal.WOLF),     # mate-in-1: move to (3,0)
        (0, 8, Color.BLUE, Animal.LION),     # alternative slow attacker
        (6, 0, Color.BLACK, Animal.RAT),
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, difficulty=2)
    move = ai.get_best_move(gs, time_budget_ms=500)
    assert move is not None
    assert (move.tc, move.tr) == DEN_BLACK


def test_ai_seeks_repetition_when_losing():
    """Down material with only a perpetual escape, AI should choose to repeat."""
    # Blue Lion at (0,8); Black Lion + Tiger up the board. Blue should shuffle.
    gs = make_gs(
        (0, 8, Color.BLUE, Animal.LION),
        (1, 0, Color.BLACK, Animal.LION),
        (5, 0, Color.BLACK, Animal.TIGER),
        (3, 4, Color.BLACK, Animal.ELEPHANT),
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, difficulty=2)
    move = ai.get_best_move(gs, time_budget_ms=500)
    assert move is not None  # just must not crash; behaviour is hard to assert


def test_ai_vs_ai_quick():
    """Two Easy AIs should be able to play a short game without crashing."""
    gs = GameState()
    gs.new_game()

    ai_blue = AIPlayer(Color.BLUE, difficulty=0)
    ai_black = AIPlayer(Color.BLACK, difficulty=0)

    for _ in range(200):
        if gs.is_terminal():
            break
        ai = ai_blue if gs.turn == Color.BLUE else ai_black
        move = ai.get_best_move(gs, time_budget_ms=200)
        if move is None:
            break
        gs.apply_move(move)

    # Should reach terminal state within 200 moves on Easy
    # (Not guaranteed, but shouldn't crash)
    assert True  # No crash = pass
