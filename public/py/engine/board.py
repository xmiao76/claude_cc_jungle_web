"""Board state representation for Jungle.

The board is a 7-column × 9-row grid stored FLAT (v1.5): one 63-entry list
indexed by ``sq = col * ROWS + row``.
  board._sq[sq] = piece_id  (0 = empty, >0 = Blue piece, <0 = Black piece)
  piece_id magnitude = Animal rank (1=Rat … 8=Elephant)

The public accessors still speak (col, row); the engine hot paths (movegen,
rules, eval, SEE) index ``_sq`` and the flat tables in ``config`` directly.

Zobrist hashing is updated incrementally on make_move / unmake_move. The
random table is generated in the same seeded order as the historical nested
[col][row][piece] table, so hash VALUES are unchanged by the flat layout.
"""

from __future__ import annotations

import random
from typing import NamedTuple

from config import (
    COLS, ROWS, NUM_SQUARES, TERRAIN,
)
from engine.pieces import (
    Color, STARTING_POSITIONS,
    make_piece_id, piece_id_color,
)

# ---------------------------------------------------------------------------
# Move representation
# ---------------------------------------------------------------------------

class Move(NamedTuple):
    fc: int         # from col
    fr: int         # from row
    tc: int         # to col
    tr: int         # to row
    captured: int   # piece_id of captured piece (0 if none)


# ---------------------------------------------------------------------------
# Zobrist table
# ---------------------------------------------------------------------------

_RNG = random.Random(0xDEADBEEF)

# piece_id_index maps: pid in range -8..-1, 1..8 → index 0..15
# (v1.5 fix: positives previously mapped to pid - 1, colliding with the
# negative range — Blue rank k and Black rank 9-k shared Zobrist tokens, so
# positions with such pieces on swapped squares hashed identically.)
def _pid_index(pid: int) -> int:
    return pid + 8 if pid < 0 else pid + 7  # -8→0, -1→7, 1→8, 8→15

# Flat Zobrist table: _Z[sq * 16 + pid_index]. Generated in the historical
# nested (col, row, piece) order so values match the pre-flat engine.
_Z: list[int] = [0] * (NUM_SQUARES * 16)
for _c in range(COLS):
    for _r in range(ROWS):
        _base = (_c * ROWS + _r) * 16
        for _i in range(16):
            _Z[_base + _i] = _RNG.getrandbits(64)

_ZOBRIST_TURN = _RNG.getrandbits(64)   # XOR this when it's Black's turn


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------

class Board:
    """Mutable board state supporting incremental Zobrist hashing."""

    __slots__ = ("_sq", "hash", "_piece_positions")

    def __init__(self) -> None:
        # _sq[col * ROWS + row] = piece_id
        self._sq: list[int] = [0] * NUM_SQUARES
        self.hash: int = 0
        # _piece_positions[color] = dict of piece_id -> square index
        self._piece_positions: list[dict[int, int]] = [{}, {}]

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup_starting_position(self) -> None:
        """Place all pieces in their standard starting positions."""
        self._sq = [0] * NUM_SQUARES
        self.hash = 0
        self._piece_positions = [{}, {}]
        for (c, r, color, animal) in STARTING_POSITIONS:
            pid = make_piece_id(color, animal)
            sq = c * ROWS + r
            self._sq[sq] = pid
            self.hash ^= _Z[sq * 16 + _pid_index(pid)]
            self._piece_positions[int(color)][pid] = sq

    def place_piece(self, col: int, row: int, pid: int) -> None:
        """Put a piece on an empty square (position-setup helper).

        Keeps the grid, the piece-position index, and the Zobrist hash
        consistent. Used by tests and hand-built benchmark positions.
        """
        sq = col * ROWS + row
        self._sq[sq] = pid
        self._piece_positions[int(piece_id_color(pid))][pid] = sq
        self.hash ^= _Z[sq * 16 + _pid_index(pid)]

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, c: int, r: int) -> int:
        return self._sq[c * ROWS + r]

    def is_empty(self, c: int, r: int) -> bool:
        return self._sq[c * ROWS + r] == 0

    def in_bounds(self, c: int, r: int) -> bool:
        return 0 <= c < COLS and 0 <= r < ROWS

    def terrain(self, c: int, r: int) -> int:
        return TERRAIN[c][r]

    def pieces_of(self, color: Color) -> dict[int, int]:
        """Return {piece_id: square_index} for all living pieces of color."""
        return self._piece_positions[int(color)]

    def alive_count(self, color: Color) -> int:
        return len(self._piece_positions[int(color)])

    # ------------------------------------------------------------------
    # Move application / undo
    # ------------------------------------------------------------------

    def make_move(self, move: Move) -> None:
        """Apply move in-place (updates grid and Zobrist hash)."""
        sqs = self._sq
        fsq = move.fc * ROWS + move.fr
        tsq = move.tc * ROWS + move.tr
        pid = sqs[fsq]
        captured = move.captured

        # Remove mover from source
        sqs[fsq] = 0
        h = self.hash ^ _Z[fsq * 16 + (pid + 8 if pid < 0 else pid + 7)]

        # Remove captured piece from tracking
        if captured:
            self._piece_positions[1 if captured < 0 else 0].pop(captured, None)
            h ^= _Z[tsq * 16 + (captured + 8 if captured < 0 else captured + 7)]

        # Place mover at destination
        sqs[tsq] = pid
        h ^= _Z[tsq * 16 + (pid + 8 if pid < 0 else pid + 7)]
        self.hash = h

        # Update position tracking
        self._piece_positions[1 if pid < 0 else 0][pid] = tsq

    def unmake_move(self, move: Move) -> None:
        """Undo a previously made move (exact inverse of make_move)."""
        sqs = self._sq
        fsq = move.fc * ROWS + move.fr
        tsq = move.tc * ROWS + move.tr
        pid = sqs[tsq]
        captured = move.captured

        # Remove mover from destination
        sqs[tsq] = 0
        h = self.hash ^ _Z[tsq * 16 + (pid + 8 if pid < 0 else pid + 7)]

        # Restore captured piece
        if captured:
            sqs[tsq] = captured
            h ^= _Z[tsq * 16 + (captured + 8 if captured < 0 else captured + 7)]
            self._piece_positions[1 if captured < 0 else 0][captured] = tsq

        # Restore mover at source
        sqs[fsq] = pid
        h ^= _Z[fsq * 16 + (pid + 8 if pid < 0 else pid + 7)]
        self.hash = h

        # Update position tracking
        self._piece_positions[1 if pid < 0 else 0][pid] = fsq

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def copy(self) -> "Board":
        b = Board()
        b._sq = self._sq[:]
        b.hash = self.hash
        b._piece_positions = [dict(d) for d in self._piece_positions]
        return b

    def turn_hash(self, color: Color) -> int:
        """Full position hash including whose turn it is."""
        return self.hash ^ (_ZOBRIST_TURN if color == Color.BLACK else 0)
