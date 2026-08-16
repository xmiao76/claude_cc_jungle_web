"""Tiny hand-crafted opening book for Jungle.

Keyed by `state.board.turn_hash(state.turn)`. The Zobrist seed is fixed
(see engine/board.py), so hashes are stable across runs.

The book is built lazily on first access by replaying a few principled
opening lines from the standard starting position.
"""

from __future__ import annotations

from engine.board import Move
from engine.game_state import GameState


_BOOK: dict[int, Move] | None = None


def _build_book() -> dict[int, Move]:
    """Replay a handful of strong opening lines and record their first move.

    Lines chosen for symmetric, central, river-controlling development.
    Each line is a sequence of (fc, fr, tc, tr) tuples.
    """
    book: dict[int, Move] = {}

    # A few well-known opening ideas: develop tigers/lions toward the river,
    # bring rats to water for control, advance leopards and wolves up the wing.
    lines: list[list[tuple[int, int, int, int]]] = [
        # Blue tiger forward; Black mirrors. Then leopards advance.
        [(0, 8, 0, 7), (0, 0, 0, 1), (4, 6, 4, 5), (2, 2, 2, 3)],
        # Blue lion forward; Black mirrors with tiger.
        [(6, 8, 6, 7), (6, 0, 6, 1), (6, 6, 6, 5), (0, 2, 0, 3)],
        # Rat to river: Blue rat dashes for water control.
        [(6, 6, 6, 5), (0, 2, 0, 3), (6, 5, 6, 4), (0, 3, 0, 4)],
    ]

    for line in lines:
        gs = GameState()
        gs.new_game()
        for (fc, fr, tc, tr) in line:
            legal = gs.legal_moves()
            move = next((m for m in legal if (m.fc, m.fr, m.tc, m.tr) == (fc, fr, tc, tr)), None)
            if move is None:
                break  # line invalid; skip rest
            key = gs.board.turn_hash(gs.turn)
            book.setdefault(key, move)
            gs.apply_move(move)
    return book


def lookup(state: GameState) -> Move | None:
    """Return a book move for *state*, or None if not in book."""
    global _BOOK
    if _BOOK is None:
        _BOOK = _build_book()
    key = state.board.turn_hash(state.turn)
    move = _BOOK.get(key)
    if move is None:
        return None
    # Validate the move is still legal (paranoia for hash collisions).
    legal = state.legal_moves()
    for m in legal:
        if (m.fc, m.fr, m.tc, m.tr) == (move.fc, move.fr, move.tc, move.tr):
            return m
    return None
