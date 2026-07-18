"""Static Exchange Evaluation (SEE) for Jungle captures.

Returns the net material gain of a capture sequence on the target square,
assuming both sides recapture with the lowest-rank legal attacker available.
Used by the AI to filter losing captures in quiescence and to order captures
in the main search.

v1.5: runs on the flat 63-square representation (precomputed neighbor lists
and jump paths; no per-candidate coordinate math).
"""

from __future__ import annotations

from config import PIECE_VALUES, NEIGHBORS, ROWS
from engine.board import Board, Move
from engine.pieces import Color
from engine.rules import can_capture_sq
from engine.move_generator import _JUMP_TABLE_FLAT


def see_capture(board: Board, move: Move) -> int:
    """Static Exchange Evaluation for a capture move.

    Returns net material gain (positive = good for attacker side) assuming
    optimal recapture sequence using lowest-rank legal attacker each time.
    Approximation: treats traps and jumps but not chained tactical motifs.
    """
    if not move.captured:
        return 0

    fsq = move.fc * ROWS + move.fr
    tsq = move.tc * ROWS + move.tr
    attacker_pid = board._sq[fsq]
    if attacker_pid == 0:
        return 0

    captured = move.captured
    gain = [PIECE_VALUES[captured if captured > 0 else -captured]]

    # Simulate the capture; piece on target is now the original attacker.
    on_square_pid = attacker_pid
    # Track consumed attackers by square so we don't mutate the board.
    removed: set[int] = {fsq}

    side_blue = not (attacker_pid > 0)
    while True:
        attackers = _live_attackers_on(board, tsq, side_blue, removed,
                                       on_square_pid)
        if not attackers:
            break
        # Pick the lowest-rank legal attacker.
        attackers.sort(key=lambda t: t[1] if t[1] > 0 else -t[1])
        asq, apid = attackers[0]
        gain.append(PIECE_VALUES[on_square_pid if on_square_pid > 0
                                 else -on_square_pid])
        removed.add(asq)
        on_square_pid = apid
        side_blue = not side_blue

    # Backward propagation: at each step the side can stop or recapture,
    # whichever is better. value[i] = gain[i] - max(0, value[i+1]).
    for i in range(len(gain) - 2, -1, -1):
        gain[i] = gain[i] - max(0, gain[i + 1])
    return gain[0]


def _live_attackers_on(board: Board, tsq: int, side_blue: bool,
                       removed: set[int],
                       target_pid: int) -> list[tuple[int, int]]:
    """(square, pid) for *side*'s pieces that could legally capture a
    defender *target_pid* on *tsq*, skipping squares in *removed*.

    Considers normal steps and Lion/Tiger jumps; capture legality (including
    the water boundary and trap rules) is delegated to can_capture_sq.
    """
    out: list[tuple[int, int]] = []
    sqs = board._sq
    adjacent = NEIGHBORS[tsq]
    color = Color.BLUE if side_blue else Color.BLACK

    for pid, sq in board.pieces_of(color).items():
        if sq in removed:
            continue

        # Adjacency (one cardinal step)
        if sq in adjacent:
            if can_capture_sq(pid, target_pid, sq, tsq, board):
                out.append((sq, pid))
            continue

        # Lion / Tiger jumps
        rank = pid if pid > 0 else -pid
        if rank != 6 and rank != 7:
            continue
        for (vertical, lsq, path) in _JUMP_TABLE_FLAT.get(sq, ()):
            if lsq != tsq:
                continue
            if vertical and rank == 6:
                continue   # Tiger cannot make the vertical (3-square) jump
            blocked = False
            for psq in path:
                p = sqs[psq]
                if (p == 1 or p == -1) and psq not in removed:
                    blocked = True   # a live rat in the river blocks
                    break
            if blocked:
                continue
            if can_capture_sq(pid, target_pid, sq, tsq, board):
                out.append((sq, pid))
    return out
