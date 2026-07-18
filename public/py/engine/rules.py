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
    """Return the effective rank of a piece at (col, row).

    A piece inside the *opponent's* traps has effective rank 0.
    """
    color = piece_id_color(pid)
    if (col, row) in _opponent_traps(color):
        return 0
    return piece_id_rank(pid)


def can_capture_sq(attacker_pid: int, defender_pid: int,
                   atk_sq: int, def_sq: int, board) -> bool:
    """Flat-square capture legality — the engine's hottest predicate.

    Rules:
    - Must be different colors.
    - Rat in water cannot capture Elephant on land (or vice versa across boundary).
    - Rat (on land) can capture Elephant (on land).
    - A defender standing in the attacker-side's trap has effective rank 0
      and may be captured by ANY piece (outranks the rank hierarchy).
    - Otherwise: attacker effective_rank >= defender effective_rank.
    - Special: Rat can capture Rat regardless of water boundary as long as both are
      on the same terrain type (water-water or land-land).

    Works on plain int ranks (Animal is an IntEnum: Rat == 1, Elephant == 8)
    and flat config tables (IS_RIVER, TRAP_ZEROES).
    """
    atk_blue = attacker_pid > 0

    # Must be enemies
    if atk_blue == (defender_pid > 0):
        return False

    atk_rank = attacker_pid if atk_blue else -attacker_pid
    def_rank = defender_pid if defender_pid > 0 else -defender_pid

    # Rat-specific cross-boundary rules
    if atk_rank == 1 or def_rank == 1:
        atk_in_water = IS_RIVER[atk_sq]
        def_in_water = IS_RIVER[def_sq]
        # A piece in water is invulnerable to attacks from land pieces
        # (and vice versa) — except rat vs rat on same terrain type
        if atk_in_water != def_in_water:
            return False
        # Both on same terrain: Rat can capture Rat freely
        if atk_rank == 1 and def_rank == 1:
            return True
        # The trap rule outranks the rank hierarchy: a defender standing in
        # the attacker-side's trap has effective rank 0 and may be captured
        # by ANY piece — including the Elephant taking a trapped Rat.
        if TRAP_ZEROES[def_sq] == (1 if defender_pid > 0 else 2):
            return True
        # Rat on land can capture Elephant on land
        if atk_rank == 1 and def_rank == 8:
            return not atk_in_water  # attacker must be on land
        # Elephant can NOT capture an untrapped Rat (Rat beats Elephant in
        # the rank hierarchy)
        if atk_rank == 8 and def_rank == 1:
            return False

    # General rank comparison using effective ranks (opponent's trap = rank 0)
    atk_eff = 0 if TRAP_ZEROES[atk_sq] == (1 if atk_blue else 2) else atk_rank
    def_eff = 0 if TRAP_ZEROES[def_sq] == (1 if defender_pid > 0 else 2) else def_rank
    return atk_eff >= def_eff


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
