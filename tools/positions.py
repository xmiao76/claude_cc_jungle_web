"""Builders for arbitrary Jungle positions, shared by the tests and the harness.

Everything here goes through :meth:`engine.board.Board.place_piece`, which
maintains the incremental Zobrist hash. Building a position by writing
``Board._sq`` directly leaves ``Board.hash`` at 0 — which aliases every
hand-built position to the same transposition-table key — and lets two pieces of
the same animal coexist, so the grid and the piece-position index silently
disagree.

Lives under ``tools/`` rather than ``tests/`` so the dependency runs one way:
``tests`` already imports the harness, and the harness needs these builders too.

A piece spec is a ``(col, row, Color, Animal)`` tuple.

Vendored from the desktop repo's ``tools/positions.py``; see ``rust/VENDORED.md``.
Adapted for this repo's board API (``place_piece``, flat Zobrist table).
"""

from __future__ import annotations

from config import COLS, ROWS
from engine.board import Board
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id

PieceSpec = tuple[int, int, Color, Animal]


def place(board: Board, col: int, row: int, color: Color, animal: Animal) -> int:
    """Place one piece and return its piece id."""
    pid = make_piece_id(color, animal)
    board.place_piece(col, row, pid)
    return pid


def empty_board(*specs: PieceSpec) -> Board:
    """Return a `Board` holding only *specs* (no starting position)."""
    board = Board()
    for (col, row, color, animal) in specs:
        place(board, col, row, color, animal)
    return board


def make_gs(*specs: PieceSpec, turn: Color = Color.BLUE) -> GameState:
    """Return a `GameState` holding only *specs*, with *turn* to move.

    The board hash is correct, so the result is safe to search, hash and copy.
    """
    gs = GameState()
    gs.board = empty_board(*specs)
    gs.turn = turn
    return gs


def recompute_hash(board: Board) -> int:
    """Recompute a board's Zobrist hash from scratch.

    `Board` maintains its hash incrementally in make_move/unmake_move. Comparing
    against this independent recomputation is what proves the incremental update
    is still correct after a change to the board representation.
    """
    from engine.board import _Z, _pid_index

    h = 0
    for c in range(COLS):
        for r in range(ROWS):
            pid = board.get(c, r)
            if pid != 0:
                h ^= _Z[(c * ROWS + r) * 16 + _pid_index(pid)]
    return h


def assert_board_consistent(board: Board) -> None:
    """Assert the grid, the piece-position index and the hash all agree.

    The invariant to lean on when touching the board representation: each of the
    three can be made correct in isolation while disagreeing with the others, and
    a disagreement is silent in play but corrupts search.
    """
    from engine.pieces import piece_id_color

    for color in (Color.BLUE, Color.BLACK):
        for pid, sq in board.pieces_of(color).items():
            c, r = divmod(sq, ROWS)
            assert board.get(c, r) == pid, (
                f"index says {pid} at ({c},{r}), grid says {board.get(c, r)}"
            )
            assert piece_id_color(pid) == color, f"{pid} indexed under {color}"

    for c in range(COLS):
        for r in range(ROWS):
            pid = board.get(c, r)
            if pid == 0:
                continue
            color = piece_id_color(pid)
            assert board.pieces_of(color).get(pid) == c * ROWS + r, (
                f"grid has {pid} at ({c},{r}), index disagrees"
            )

    assert board.hash == recompute_hash(board), "incremental hash has drifted"


# ---------------------------------------------------------------------------
# Shared fixed positions
# ---------------------------------------------------------------------------

# A reproducible, out-of-book midgame: 12 pieces, Blue to move.
FIXED_MIDGAME: tuple[PieceSpec, ...] = (
    (0, 6, Color.BLUE, Animal.ELEPHANT), (2, 6, Color.BLUE, Animal.WOLF),
    (4, 6, Color.BLUE, Animal.LEOPARD), (6, 6, Color.BLUE, Animal.RAT),
    (0, 8, Color.BLUE, Animal.TIGER), (6, 8, Color.BLUE, Animal.LION),
    (0, 2, Color.BLACK, Animal.RAT), (2, 2, Color.BLACK, Animal.LEOPARD),
    (4, 2, Color.BLACK, Animal.WOLF), (6, 2, Color.BLACK, Animal.ELEPHANT),
    (0, 0, Color.BLACK, Animal.LION), (6, 0, Color.BLACK, Animal.TIGER),
)


def fixed_midgame() -> GameState:
    """Return the shared 12-piece midgame position, Blue to move."""
    return make_gs(*FIXED_MIDGAME, turn=Color.BLUE)
