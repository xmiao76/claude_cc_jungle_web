"""Capture legality, terrain effects, and win detection for Jungle."""

from __future__ import annotations

from config import (
    ROWS, TERRAIN, TERRAIN_RIVER,
    DEN_BLACK, DEN_BLUE, TRAPS_BLACK, TRAPS_BLUE,
    IS_RIVER, TRAP_ZEROES,
)
from engine.pieces import Animal, Color, piece_id_color, piece_id_animal, piece_id_rank


def _opponent_traps(color: Color) -> set[tuple[int, int]]:
    """Traps that reduce rank for the given color (i.e. the *opponent's* traps)."""
    return TRAPS_BLACK if color == Color.BLUE else TRAPS_BLUE


def effective_rank(pid: int, col: int, row: int) -> int:
    """Return the *defensive* effective rank of a piece at (col, row).

    A piece inside the *opponent's* traps has effective rank 0. This applies to
    defence only — see :func:`can_capture_sq`, which never applies it to the
    attacker.
    """
    color = piece_id_color(pid)
    if (col, row) in _opponent_traps(color):
        return 0
    return piece_id_rank(pid)


def can_capture_sq(attacker_pid: int, defender_pid: int,
                   atk_sq: int, def_sq: int, board) -> bool:
    """Flat-square capture legality — the engine's hottest predicate.

    The four checks below run in this order, and the order is load-bearing.

    1. **Enemies.** No friendly fire.
    2. **The water boundary**, absolute and symmetric: a piece in the river and a
       piece on land can never take one another, whichever way round. This is
       what makes a Rat in the river invulnerable to the bank, and what stops a
       Rat in the river taking an Elephant standing on it. It is checked before
       anything else because it outranks every other consideration.
    3. **A trapped defender** — one standing in the *attacker's* traps — has
       effective rank 0 and falls to any adjacent enemy, Elephant included, which
       is why this precedes the Rat/Elephant exception rather than following it.
    4. **Rank**, with the Rat/Elephant exception: Rat takes Elephant, Elephant
       does not take Rat, otherwise attacker rank >= defender rank.

    **The trap weakens a piece for defence only.** The attacker always fights at
    its real rank, so a piece standing in the enemy's traps is vulnerable but not
    disarmed. Applying the rank-0 effect to the attacker as well silently makes a
    class of legal captures illegal near both dens — it did here until the Rust
    engine's golden corpus disagreed on 14 positions in 10,000.

    Works on plain int ranks (Animal is an IntEnum: Rat == 1, Elephant == 8)
    and flat config tables (IS_RIVER, TRAP_ZEROES).
    """
    atk_blue = attacker_pid > 0

    # 1. Must be enemies
    if atk_blue == (defender_pid > 0):
        return False

    # 2. The water boundary, for every piece and not just the Rat
    if IS_RIVER[atk_sq] != IS_RIVER[def_sq]:
        return False

    # 3. A defender in the attacker's traps falls to anything
    if TRAP_ZEROES[def_sq] == (1 if defender_pid > 0 else 2):
        return True

    atk_rank = attacker_pid if atk_blue else -attacker_pid
    def_rank = defender_pid if defender_pid > 0 else -defender_pid

    # 4. Rank, with the Rat/Elephant exception. The attacker keeps its real rank.
    if atk_rank == 1 and def_rank == 8:
        return True
    if atk_rank == 8 and def_rank == 1:
        return False
    return atk_rank >= def_rank


def can_capture(attacker_pid: int, defender_pid: int,
                atk_col: int, atk_row: int,
                def_col: int, def_row: int,
                board) -> bool:
    """(col, row) wrapper around :func:`can_capture_sq` (public rules API)."""
    return can_capture_sq(attacker_pid, defender_pid,
                          atk_col * ROWS + atk_row, def_col * ROWS + def_row,
                          board)


def is_jump_blocked(fc: int, fr: int, tc: int, tr: int, board) -> bool:
    """Return True if a river-jump from (fc,fr) to (tc,tr) is blocked by a rat in the river.

    The jump must be a straight line crossing one of the two river blocks.
    Any rat (either color) on a water square along the path blocks the jump.
    """
    if fc == tc:
        # Vertical jump
        step = 1 if tr > fr else -1
        r = fr + step
        while r != tr:
            if TERRAIN[fc][r] == TERRAIN_RIVER:
                pid = board.get(fc, r)
                if pid != 0 and piece_id_animal(pid) == Animal.RAT:
                    return True
            r += step
    elif fr == tr:
        # Horizontal jump
        step = 1 if tc > fc else -1
        c = fc + step
        while c != tc:
            if TERRAIN[c][fr] == TERRAIN_RIVER:
                pid = board.get(c, fr)
                if pid != 0 and piece_id_animal(pid) == Animal.RAT:
                    return True
            c += step
    return False


class WinResult:
    __slots__ = ("winner",)

    def __init__(self, winner: Color) -> None:
        self.winner = winner


def check_win(board, last_move, current_turn: Color) -> WinResult | None:
    """Check if the game has ended after last_move was played.

    Win conditions:
    1. A piece entered the opponent's den.
    2. One side has no pieces remaining.
    3. The player whose turn it is has no legal moves (stalemate → they lose).

    'current_turn' is the side about to move (i.e. the side that did NOT just move).
    """
    if last_move is not None:
        # Condition 1: den entry
        mover_pid = board.get(last_move.tc, last_move.tr)
        if mover_pid != 0:
            mover_color = piece_id_color(mover_pid)
            opponent_den = DEN_BLUE if mover_color == Color.BLACK else DEN_BLACK
            if (last_move.tc, last_move.tr) == opponent_den:
                return WinResult(mover_color)

    # Condition 2: capture all
    for color in (Color.BLUE, Color.BLACK):
        if board.alive_count(color) == 0:
            opponent = Color.BLACK if color == Color.BLUE else Color.BLUE
            return WinResult(opponent)

    return None
