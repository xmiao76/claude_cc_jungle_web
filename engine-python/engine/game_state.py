"""GameState: wraps Board with turn tracking, history, and win detection."""

from __future__ import annotations

from engine.board import Board, Move
from engine.pieces import (
    Color, ANIMAL_NAMES, piece_id_color, piece_id_animal,
)
from engine.rules import check_win, WinResult
from engine.move_generator import generate_legal_moves


def _square_name(c: int, r: int) -> str:
    """Convert (col, row) to algebraic-like notation, e.g. (3, 0) -> 'D1'."""
    return f"{chr(ord('A') + c)}{r + 1}"


def format_move(move: Move, mover_pid: int, move_number: int) -> str:
    """Format a move for the history panel, e.g. '12. B Lion E1->D2 xDog'."""
    color = piece_id_color(mover_pid)
    color_tag = "B" if color == Color.BLUE else "K"
    animal = ANIMAL_NAMES[piece_id_animal(mover_pid)]
    src = _square_name(move.fc, move.fr)
    dst = _square_name(move.tc, move.tr)
    base = f"{move_number}. {color_tag} {animal} {src}->{dst}"
    if move.captured:
        cap = ANIMAL_NAMES[piece_id_animal(move.captured)]
        base += f" x{cap}"
    return base


_FIFTY_MOVE_PLIES = 100   # 50 full moves without a capture = draw


class GameState:
    """Complete game state: board + whose turn + move history + result."""

    __slots__ = (
        "board", "turn", "history", "mover_history", "result", "_legal_cache",
        "_hash_history", "_halfmove_clock", "_halfmove_history",
    )

    def __init__(self) -> None:
        self.board = Board()
        self.turn: Color = Color.BLUE   # Blue moves first
        self.history: list[Move] = []
        self.mover_history: list[int] = []   # parallel list of mover piece_ids
        self.result: WinResult | None = None
        self._legal_cache: list[Move] | None = None
        self._hash_history: list[int] = []
        self._halfmove_clock: int = 0
        self._halfmove_history: list[int] = []

    def new_game(self) -> None:
        """Reset to starting position."""
        self.board.setup_starting_position()
        self.turn = Color.BLUE
        self.history.clear()
        self.mover_history.clear()
        self.result = None
        self._legal_cache = None
        self._hash_history.clear()
        self._halfmove_clock = 0
        self._halfmove_history.clear()

    # ------------------------------------------------------------------
    # Move application / undo
    # ------------------------------------------------------------------

    def apply_move(self, move: Move) -> None:
        """Apply move, update turn, check for win."""
        # Record current position hash before mutation (for repetition).
        self._hash_history.append(self.board.turn_hash(self.turn))
        self._halfmove_history.append(self._halfmove_clock)

        mover_pid = self.board.get(move.fc, move.fr)
        self.board.make_move(move)
        self.history.append(move)
        self.mover_history.append(mover_pid)
        self._legal_cache = None

        if move.captured:
            self._halfmove_clock = 0
        else:
            self._halfmove_clock += 1

        # Check win *after* the move
        next_turn = Color.BLACK if self.turn == Color.BLUE else Color.BLUE
        self.result = check_win(self.board, move, next_turn)
        self.turn = next_turn

    def undo_move(self) -> None:
        """Undo the last move (used by AI search and human Undo)."""
        if not self.history:
            return
        move = self.history.pop()
        if self.mover_history:
            self.mover_history.pop()
        self.board.unmake_move(move)
        self.turn = Color.BLUE if self.turn == Color.BLACK else Color.BLACK
        self.result = None
        self._legal_cache = None
        if self._hash_history:
            self._hash_history.pop()
        if self._halfmove_history:
            self._halfmove_clock = self._halfmove_history.pop()

    def apply_null(self) -> None:
        """Pass the turn without moving (null-move pruning support).

        Used only inside AI search; not a legal game move.
        """
        self._hash_history.append(self.board.turn_hash(self.turn))
        self._halfmove_history.append(self._halfmove_clock)
        self.history.append(None)  # type: ignore[arg-type]
        self.mover_history.append(0)
        self._halfmove_clock += 1
        self.turn = Color.BLACK if self.turn == Color.BLUE else Color.BLUE
        self._legal_cache = None

    def undo_null(self) -> None:
        """Reverse a previous apply_null."""
        self.history.pop()
        self.mover_history.pop()
        self.turn = Color.BLUE if self.turn == Color.BLACK else Color.BLACK
        if self._hash_history:
            self._hash_history.pop()
        if self._halfmove_history:
            self._halfmove_clock = self._halfmove_history.pop()
        self._legal_cache = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def legal_moves(self) -> list[Move]:
        if self._legal_cache is None:
            self._legal_cache = generate_legal_moves(self.board, self.turn)
        return self._legal_cache

    def is_repetition(self) -> bool:
        """True if the current position has occurred before in this game."""
        cur = self.board.turn_hash(self.turn)
        return cur in self._hash_history

    def is_50_move_draw(self) -> bool:
        return self._halfmove_clock >= _FIFTY_MOVE_PLIES

    def is_terminal(self) -> bool:
        if self.result is not None:
            return True
        if self.is_50_move_draw():
            return True
        if not self.legal_moves():
            # No legal moves = current player loses (stalemate rule)
            return True
        return False

    def get_winner(self) -> Color | None:
        if self.result is not None:
            return self.result.winner
        if self.is_50_move_draw():
            return None
        if not self.legal_moves():
            # Current player has no moves → they lose
            return Color.BLACK if self.turn == Color.BLUE else Color.BLUE
        return None

    def is_draw(self) -> bool:
        """True for explicit draw conditions (50-move; 3-fold not auto-claimed)."""
        return self.is_50_move_draw() and self.result is None

    def copy(self) -> "GameState":
        gs = GameState()
        gs.board = self.board.copy()
        gs.turn = self.turn
        gs.history = self.history[:]
        gs.mover_history = self.mover_history[:]
        gs.result = self.result
        gs._hash_history = self._hash_history[:]
        gs._halfmove_clock = self._halfmove_clock
        gs._halfmove_history = self._halfmove_history[:]
        return gs

    # ------------------------------------------------------------------
    # Formatted history (for UI)
    # ------------------------------------------------------------------

    def formatted_history(self, n: int | None = None) -> list[str]:
        """Return human-readable strings for the last *n* moves (or all)."""
        moves = self.history
        movers = self.mover_history
        out: list[str] = []
        i = 0
        for m, pid in zip(moves, movers):
            i += 1
            if m is None:
                continue  # skip null-move entries
            out.append(format_move(m, pid, i))
        return out[-n:] if n else out
