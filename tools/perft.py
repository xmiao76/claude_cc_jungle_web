"""Perft: exhaustive leaf-node counts for move generation.

Perft walks the full game tree to a fixed depth and counts the leaves. The
number is a fingerprint of the move generator and the rules: if a refactor
changes any legality decision anywhere, the count changes. That makes it the
contract to hold fixed while optimising move generation, where correctness is
otherwise easy to break silently and hard to notice in play.

It is also a clean speed benchmark, because it exercises make/unmake and move
generation with no evaluation, search heuristics or time control involved.

Leaf conventions (kept explicit so the frozen counts are unambiguous):

* A position whose game is already decided — den entry or capture-all, i.e.
  ``GameState.result is not None`` — is a leaf, counted as 1, and is not
  expanded. Continuing past a win would count illegal continuations.
* A position with no legal moves is a leaf, counted as 1. The side to move has
  lost, so there is nothing to expand.
* Otherwise ``perft(0) == 1`` and ``perft(1) == len(legal_moves)``.
"""

from __future__ import annotations

import time

from engine.game_state import GameState


def perft(gs: GameState, depth: int) -> int:
    """Return the number of leaves in the tree of depth *depth* below *gs*."""
    if gs.result is not None:
        return 1
    if depth <= 0:
        return 1
    moves = gs.legal_moves()
    if not moves:
        return 1
    if depth == 1:
        return len(moves)

    total = 0
    for move in moves:
        gs.apply_move(move)
        total += perft(gs, depth - 1)
        gs.undo_move()
    return total


def perft_divide(gs: GameState, depth: int) -> list[tuple[str, int]]:
    """Return per-root-move leaf counts, for locating a perft mismatch.

    When a total diverges, the differing root move narrows the search to one
    subtree instead of the whole game.
    """
    if depth < 1:
        raise ValueError("divide needs depth >= 1")
    out: list[tuple[str, int]] = []
    for move in gs.legal_moves():
        label = f"{move.fc},{move.fr}->{move.tc},{move.tr}"
        gs.apply_move(move)
        out.append((label, perft(gs, depth - 1)))
        gs.undo_move()
    out.sort()
    return out


def timed_perft(gs: GameState, depth: int) -> tuple[int, float]:
    """Return (leaves, seconds) for one perft run."""
    t0 = time.perf_counter()
    nodes = perft(gs, depth)
    return nodes, time.perf_counter() - t0
