"""Legal move generation for all pieces in Jungle.

v1.5: hot paths run on the flat 63-square representation — precomputed
in-bounds neighbor lists (no per-step bounds checks or coordinate math) and
a flat jump table whose entries carry their precomputed river-path squares
(no per-jump path walking).
"""

from __future__ import annotations

from config import (
    COLS, ROWS, TERRAIN, TERRAIN_RIVER,
    DEN_BLACK, DEN_BLUE,
    NEIGHBORS, TERRAIN_FLAT, SQ_C, SQ_R,
    DEN_BLACK_SQ, DEN_BLUE_SQ,
)
from engine.board import Board, Move
from engine.pieces import Animal, Color
from engine.rules import can_capture_sq, is_jump_blocked  # noqa: F401 (API re-export)

# Cardinal directions (kept for the jump-table build; step generation uses
# the precomputed NEIGHBORS table, which encodes the same order)
_DIRS = [(0, -1), (0, 1), (-1, 0), (1, 0)]

# Integer ranks for the hot loops (Animal is an IntEnum; comparing plain ints
# avoids constructing an enum per piece per node).
_RAT = int(Animal.RAT)
_TIGER = int(Animal.TIGER)
_LION = int(Animal.LION)

# Precomputed jump endpoints for Lion and Tiger, keyed by (col, row).
# For each starting square on the edge of a river block, store a list of
# (dc, dr, landing_col, landing_row) where dc/dr is the jump direction.
_JUMP_TABLE: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}

# Flat variant used by the hot paths, keyed by square index. Each entry is
# (is_vertical, landing_sq, path_squares): is_vertical (dc == 0) marks the
# 3-river-square crossing Tiger cannot make; path_squares are the river
# squares crossed (a rat on any of them blocks the jump).
_JUMP_TABLE_FLAT: dict[int, tuple[tuple[bool, int, tuple[int, ...]], ...]] = {}

# HAS_JUMP[sq]: some jump endpoint exists at sq (eval's jump-readiness proxy).
HAS_JUMP: tuple[bool, ...] = ()


def _build_jump_table() -> None:
    """Precompute all valid river-jump endpoints for Lion and Tiger.

    For each land square adjacent to a river, follow each cardinal direction
    through the river to the first non-river square; that pair forms a jump.
    Direction order follows _DIRS — entry order affects move-list order.
    """
    global HAS_JUMP
    for c in range(COLS):
        for r in range(ROWS):
            if TERRAIN[c][r] == TERRAIN_RIVER:
                continue
            for (dc, dr) in _DIRS:
                nc, nr = c + dc, r + dr
                if not (0 <= nc < COLS and 0 <= nr < ROWS):
                    continue
                if TERRAIN[nc][nr] != TERRAIN_RIVER:
                    continue
                path = []
                lc, lr = nc, nr
                while (0 <= lc < COLS and 0 <= lr < ROWS and
                       TERRAIN[lc][lr] == TERRAIN_RIVER):
                    path.append(lc * ROWS + lr)
                    lc += dc
                    lr += dr
                if not (0 <= lc < COLS and 0 <= lr < ROWS):
                    continue
                if TERRAIN[lc][lr] == TERRAIN_RIVER:
                    continue
                _JUMP_TABLE.setdefault((c, r), []).append((dc, dr, lc, lr))
                sq = c * ROWS + r
                entry = (dc == 0, lc * ROWS + lr, tuple(path))
                _JUMP_TABLE_FLAT[sq] = _JUMP_TABLE_FLAT.get(sq, ()) + (entry,)
    HAS_JUMP = tuple(sq in _JUMP_TABLE_FLAT
                     for sq in range(COLS * ROWS))


_build_jump_table()


def _can_jump(animal: Animal, dc: int, dr: int) -> bool:
    """Return True if this animal can leap a river crossing in direction (dc, dr).

    The river has two crossings:
      - "Horizontal" crossing = 2 river squares (column-axis leap, dc != 0).
      - "Vertical"   crossing = 3 river squares (row-axis    leap, dr != 0).

    Lion can leap up to 3 squares -> both crossings.
    Tiger can leap up to 2 squares -> the horizontal crossing only.
    """
    if animal == Animal.LION:
        return True
    if animal == Animal.TIGER:
        return dc != 0   # horizontal (2-square) jump only
    return False


def is_capture(move: Move) -> bool:
    return move.captured != 0


def generate_capture_moves(board: Board, color: Color) -> list[Move]:
    """Generate only capture moves — used by quiescence search."""
    return [m for m in generate_legal_moves(board, color) if m.captured != 0]


def generate_noisy_moves(board: Board, color: Color) -> list[Move]:
    """Captures plus den-entry (immediately winning) moves.

    Used by quiescence so a winning den dash sitting just past the horizon is
    not missed. A den-entry move is always a non-capture (the enemy den is
    empty), so it would otherwise be invisible to a capture-only quiescence.
    """
    opp_den = DEN_BLACK if color == Color.BLUE else DEN_BLUE
    return [m for m in generate_legal_moves(board, color)
            if m.captured != 0 or (m.tc, m.tr) == opp_den]


def generate_noisy_only(board: Board, color: Color) -> list[Move]:
    """Captures + den entries, generated directly (v1.4 speed pack).

    Behaviorally identical to :func:`generate_noisy_moves`, but never builds
    the (much larger) quiet-move list. Quiescence calls this at every node.
    """
    moves: list[Move] = []
    append = moves.append
    sqs = board._sq
    terrain = TERRAIN_FLAT
    neighbors = NEIGHBORS
    jump_get = _JUMP_TABLE_FLAT.get
    sq_c = SQ_C
    sq_r = SQ_R
    is_blue = color == Color.BLUE
    own_den = DEN_BLUE_SQ if is_blue else DEN_BLACK_SQ
    opp_den = DEN_BLACK_SQ if is_blue else DEN_BLUE_SQ

    for pid, fsq in board.pieces_of(color).items():
        rank = pid if pid > 0 else -pid
        fc = sq_c[fsq]
        fr = sq_r[fsq]

        # --- Normal steps ---
        for nsq in neighbors[fsq]:
            if nsq == own_den:
                continue
            if terrain[nsq] == TERRAIN_RIVER and rank != _RAT:
                continue
            target_pid = sqs[nsq]
            if target_pid == 0:
                if nsq == opp_den:
                    append(Move(fc, fr, sq_c[nsq], sq_r[nsq], 0))
            elif (target_pid > 0) != is_blue:
                if can_capture_sq(pid, target_pid, fsq, nsq, board):
                    append(Move(fc, fr, sq_c[nsq], sq_r[nsq], target_pid))

        # --- River jumps (Lion and Tiger only) ---
        if rank == _LION or rank == _TIGER:
            for (vertical, lsq, path) in jump_get(fsq, ()):
                if vertical and rank == _TIGER:
                    # The vertical (3-river-square) crossing — Tiger may only
                    # make the horizontal (2-square) jump.
                    continue
                if lsq == own_den:
                    continue
                blocked = False
                for psq in path:
                    p = sqs[psq]
                    if p == 1 or p == -1:   # a rat in the river blocks
                        blocked = True
                        break
                if blocked:
                    continue
                land_pid = sqs[lsq]
                if land_pid == 0:
                    if lsq == opp_den:
                        append(Move(fc, fr, sq_c[lsq], sq_r[lsq], 0))
                elif (land_pid > 0) != is_blue:
                    if can_capture_sq(pid, land_pid, fsq, lsq, board):
                        append(Move(fc, fr, sq_c[lsq], sq_r[lsq], land_pid))

    return moves


def generate_legal_moves(board: Board, color: Color) -> list[Move]:
    """Generate all legal moves for *color* on *board*."""
    moves: list[Move] = []
    append = moves.append
    sqs = board._sq
    terrain = TERRAIN_FLAT
    neighbors = NEIGHBORS
    jump_get = _JUMP_TABLE_FLAT.get
    sq_c = SQ_C
    sq_r = SQ_R
    is_blue = color == Color.BLUE
    own_den = DEN_BLUE_SQ if is_blue else DEN_BLACK_SQ

    for pid, fsq in board.pieces_of(color).items():
        rank = pid if pid > 0 else -pid
        fc = sq_c[fsq]
        fr = sq_r[fsq]

        # --- Normal steps (all pieces) ---
        for nsq in neighbors[fsq]:
            # Cannot enter own den
            if nsq == own_den:
                continue
            # Only Rat can enter river squares
            if terrain[nsq] == TERRAIN_RIVER and rank != _RAT:
                continue
            target_pid = sqs[nsq]
            if target_pid == 0:
                # Empty square
                append(Move(fc, fr, sq_c[nsq], sq_r[nsq], 0))
            elif (target_pid > 0) != is_blue:
                # Enemy piece — check capture legality
                if can_capture_sq(pid, target_pid, fsq, nsq, board):
                    append(Move(fc, fr, sq_c[nsq], sq_r[nsq], target_pid))
            # else: own piece — skip

        # --- River jumps (Lion and Tiger only) ---
        if rank == _LION or rank == _TIGER:
            for (vertical, lsq, path) in jump_get(fsq, ()):
                if vertical and rank == _TIGER:
                    # The vertical (3-river-square) crossing — Tiger may only
                    # make the horizontal (2-square) jump.
                    continue
                # Cannot land on own den
                if lsq == own_den:
                    continue
                # A rat on any river square along the path blocks the jump
                blocked = False
                for psq in path:
                    p = sqs[psq]
                    if p == 1 or p == -1:
                        blocked = True
                        break
                if blocked:
                    continue
                # Check landing square
                land_pid = sqs[lsq]
                if land_pid == 0:
                    append(Move(fc, fr, sq_c[lsq], sq_r[lsq], 0))
                elif (land_pid > 0) != is_blue:
                    if can_capture_sq(pid, land_pid, fsq, lsq, board):
                        append(Move(fc, fr, sq_c[lsq], sq_r[lsq], land_pid))

    return moves
