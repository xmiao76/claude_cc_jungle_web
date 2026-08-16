"""JSON bridge between the JS Web Worker and the Jungle engine/AI.

Every public function returns a JSON string with the envelope
{"ok": bool, "data": ..., "error": str | None} so the JS side never has
to hold PyProxy objects — only plain strings cross the boundary.

The module owns a single game session (one worker == one game at a time),
mirroring the desktop Controller's wiring of GameState + two AIPlayers.
"""

from __future__ import annotations

import json

import config
from engine.board import Move
from engine.game_state import GameState
from engine.pieces import (
    ANIMAL_NAMES,
    Color,
    piece_id_animal,
    piece_id_color,
)
from ai.minimax import AIPlayer

# Per-difficulty AI time budgets for the browser (ms). Only Hard (2) uses
# iterative deepening, so only its entry matters for search cutoff; the
# others are safety caps passed through to get_best_move.
AI_TIME_BUDGET_MS = {0: 1000, 1: 2000, 2: 2500}

_WIN_REASON_DEN = "den"
_WIN_REASON_ELIMINATION = "elimination"
_WIN_REASON_STALEMATE = "stalemate"
_DRAW_REASON_FIFTY = "fifty_move"


class _Session:
    """Holds the authoritative game state and the two AI players."""

    def __init__(self) -> None:
        self.gs = GameState()
        self.gs.new_game()
        self.difficulty = 1
        self.ai_players: dict[Color, AIPlayer] = {}

    def start(self, difficulty: int) -> None:
        self.difficulty = difficulty
        self.gs.new_game()
        # Fresh AIPlayer per color, like the desktop controller: separate
        # transposition tables and time banks per side.
        self.ai_players = {
            Color.BLUE: AIPlayer(Color.BLUE, difficulty),
            Color.BLACK: AIPlayer(Color.BLACK, difficulty),
        }


_session = _Session()


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------

def _ok(data: dict) -> str:
    return json.dumps({"ok": True, "data": data, "error": None})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "data": None, "error": message})


# ---------------------------------------------------------------------------
# State serialization
# ---------------------------------------------------------------------------

def _move_dict(move: Move) -> dict:
    return {
        "fc": move.fc, "fr": move.fr, "tc": move.tc, "tr": move.tr,
        "captured": move.captured,
    }


def _winner_dict(gs: GameState) -> dict | None:
    """Terminal result with a reason string, or None if the game continues."""
    if gs.result is not None:
        winner = gs.result.winner
        loser = Color.BLACK if winner == Color.BLUE else Color.BLUE
        if gs.board.alive_count(loser) == 0:
            reason = _WIN_REASON_ELIMINATION
        else:
            reason = _WIN_REASON_DEN
        return {"color": int(winner), "reason": reason}
    if gs.is_50_move_draw():
        return {"color": None, "reason": _DRAW_REASON_FIFTY}
    if not gs.legal_moves():
        winner = Color.BLACK if gs.turn == Color.BLUE else Color.BLUE
        return {"color": int(winner), "reason": _WIN_REASON_STALEMATE}
    return None


def _captured_lists(gs: GameState) -> dict:
    """Animals each side has LOST, in capture order (for the side panel)."""
    lost: dict[str, list[str]] = {"blue": [], "black": []}
    for move in gs.history:
        if move is None or not move.captured:
            continue
        victim_color = piece_id_color(move.captured)
        key = "blue" if victim_color == Color.BLUE else "black"
        lost[key].append(ANIMAL_NAMES[piece_id_animal(move.captured)])
    return lost


def _state_dict(gs: GameState) -> dict:
    board_rows = [
        [gs.board.get(c, r) for c in range(config.COLS)]
        for r in range(config.ROWS)
    ]
    winner = _winner_dict(gs)
    last = None
    for move in reversed(gs.history):
        if move is not None:
            last = _move_dict(move)
            break
    return {
        "board": board_rows,           # board[row][col] -> piece id
        "turn": int(gs.turn),
        "plyCount": len(gs.history),
        "legalMoves": [] if winner else [_move_dict(m) for m in gs.legal_moves()],
        "history": gs.formatted_history(),
        "lastMove": last,
        "winner": winner,
        "terminal": winner is not None,
        "captured": _captured_lists(gs),
    }


# ---------------------------------------------------------------------------
# Public API (called from the worker)
# ---------------------------------------------------------------------------

def engine_info() -> str:
    import sys
    return _ok({
        "engineVersion": config.VERSION,
        "python": sys.version.split()[0],
    })


def new_game(difficulty: int) -> str:
    difficulty = int(difficulty)
    if difficulty not in (0, 1, 2):
        return _err(f"invalid difficulty: {difficulty}")
    _session.start(difficulty)
    return _ok({"state": _state_dict(_session.gs)})


def get_state() -> str:
    return _ok({"state": _state_dict(_session.gs)})


def apply_move(fc: int, fr: int, tc: int, tr: int) -> str:
    """Apply a move for the side to move. The move must be legal."""
    gs = _session.gs
    if _winner_dict(gs) is not None:
        return _err("game is over")
    fc, fr, tc, tr = int(fc), int(fr), int(tc), int(tr)
    move = next(
        (m for m in gs.legal_moves()
         if m.fc == fc and m.fr == fr and m.tc == tc and m.tr == tr),
        None,
    )
    if move is None:
        return _err(f"illegal move: ({fc},{fr}) -> ({tc},{tr})")
    mover_pid = gs.board.get(fc, fr)
    gs.apply_move(move)
    return _ok({
        "move": _move_dict(move),
        "moverPid": mover_pid,
        "state": _state_dict(gs),
    })


def ai_move(time_budget_ms: int | None = None) -> str:
    """Search and apply the best move for the side to move."""
    gs = _session.gs
    if _winner_dict(gs) is not None:
        return _err("game is over")
    ai = _session.ai_players.get(gs.turn)
    if ai is None:
        return _err("no game in progress (call new_game first)")
    budget = int(time_budget_ms) if time_budget_ms is not None else \
        AI_TIME_BUDGET_MS[_session.difficulty]
    move = ai.get_best_move(gs.copy(), time_budget_ms=budget)
    if move is None:
        return _err("AI found no legal move")
    mover_pid = gs.board.get(move.fc, move.fr)
    gs.apply_move(move)
    return _ok({
        "move": _move_dict(move),
        "moverPid": mover_pid,
        "state": _state_dict(gs),
    })


def undo_for_human(human_color: int) -> str:
    """Undo so the human is on move again (mirrors the desktop controller).

    Pops the AI reply and the human's move when both exist, or a single ply
    when rolling back from a game-over position.
    """
    gs = _session.gs
    if not gs.history:
        return _err("nothing to undo")
    human = Color(int(human_color))
    if gs.turn == human and len(gs.history) >= 2:
        gs.undo_move()
        gs.undo_move()
    else:
        gs.undo_move()
        if gs.history and gs.turn != human:
            gs.undo_move()
    return _ok({"state": _state_dict(gs)})


def replay_moves(moves_json: str) -> str:
    """Apply a sequence of [fc, fr, tc, tr] moves (test/E2E support).

    Stops at the first illegal move or terminal position and reports how
    many plies were applied.
    """
    try:
        moves = json.loads(moves_json)
    except (TypeError, ValueError) as exc:
        return _err(f"bad moves payload: {exc}")
    gs = _session.gs
    applied = 0
    for entry in moves:
        if _winner_dict(gs) is not None:
            break
        fc, fr, tc, tr = (int(v) for v in entry[:4])
        move = next(
            (m for m in gs.legal_moves()
             if m.fc == fc and m.fr == fr and m.tc == tc and m.tr == tr),
            None,
        )
        if move is None:
            return _err(
                f"illegal move at ply {applied}: ({fc},{fr}) -> ({tc},{tr})"
            )
        gs.apply_move(move)
        applied += 1
    return _ok({"applied": applied, "state": _state_dict(gs)})
