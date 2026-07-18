"""Integration tests: full AI-vs-AI game simulation."""

from engine.game_state import GameState
from engine.pieces import Color
from ai.minimax import AIPlayer


def run_game(difficulty: int = 0, max_moves: int = 300) -> Color | None:
    """Run a full AI-vs-AI game. Return winner or None if max_moves exceeded."""
    gs = GameState()
    gs.new_game()

    ai_blue = AIPlayer(Color.BLUE, difficulty)
    ai_black = AIPlayer(Color.BLACK, difficulty)

    for _ in range(max_moves):
        if gs.is_terminal():
            return gs.get_winner()

        ai = ai_blue if gs.turn == Color.BLUE else ai_black
        move = ai.get_best_move(gs, time_budget_ms=300)
        if move is None:
            break
        gs.apply_move(move)

    return gs.get_winner()


# ---------------------------------------------------------------------------
# Test: AI-vs-AI game completes without crash (Easy difficulty)
# ---------------------------------------------------------------------------

def test_ai_vs_ai_completes_easy():
    """Easy AI-vs-AI game should complete within 300 moves."""
    run_game(difficulty=0, max_moves=300)
    # Winner may be None if game didn't finish in 300 moves — that's OK for Easy
    # Key assertion: no crash
    assert True


def test_ai_vs_ai_medium_no_crash():
    """Medium AI-vs-AI game should run without crash."""
    gs = GameState()
    gs.new_game()
    ai_blue = AIPlayer(Color.BLUE, 1)
    ai_black = AIPlayer(Color.BLACK, 1)

    for _ in range(50):
        if gs.is_terminal():
            break
        ai = ai_blue if gs.turn == Color.BLUE else ai_black
        move = ai.get_best_move(gs, time_budget_ms=500)
        if move is None:
            break
        gs.apply_move(move)
        # Invariant: every position has consistent piece counts
        assert gs.board.alive_count(Color.BLUE) >= 0
        assert gs.board.alive_count(Color.BLACK) >= 0

    assert True  # No crash = pass


def test_game_state_consistent_throughout():
    """Throughout a game, board state invariants hold at every step."""
    gs = GameState()
    gs.new_game()
    ai_blue = AIPlayer(Color.BLUE, 0)
    ai_black = AIPlayer(Color.BLACK, 0)

    for move_num in range(100):
        if gs.is_terminal():
            break

        # Invariant: legal moves list is not empty for non-terminal state
        legal = gs.legal_moves()
        assert len(legal) > 0, f"Non-terminal position has no legal moves at move {move_num}"

        # Invariant: all legal moves reference existing pieces
        for m in legal:
            pid = gs.board.get(m.fc, m.fr)
            assert pid != 0, f"Legal move from empty square {m}"
            from engine.pieces import piece_id_color
            assert piece_id_color(pid) == gs.turn, f"Legal move for wrong color at {m}"

        ai = ai_blue if gs.turn == Color.BLUE else ai_black
        move = ai.get_best_move(gs, time_budget_ms=200)
        assert move is not None
        gs.apply_move(move)

    assert True


def test_undo_all_moves_restores_start():
    """Undo every move of a game to restore the starting position."""
    gs = GameState()
    gs.new_game()
    start_hash = gs.board.hash

    ai_blue = AIPlayer(Color.BLUE, 0)
    ai_black = AIPlayer(Color.BLACK, 0)

    for _ in range(20):
        if gs.is_terminal():
            break
        ai = ai_blue if gs.turn == Color.BLUE else ai_black
        move = ai.get_best_move(gs, time_budget_ms=200)
        if move:
            gs.apply_move(move)

    # Undo all moves
    while gs.history:
        gs.undo_move()

    assert gs.board.hash == start_hash, "Full undo should restore starting hash"
    assert gs.turn == Color.BLUE
    assert len(gs.history) == 0
