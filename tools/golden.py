"""Golden differential corpus: the rules oracle for a re-implementation.

`tests/test_perft.py` freezes leaf *counts* for seven positions. That is a strong
contract but a blunt instrument: a mismatch tells you the rules diverged without
saying where, and it only ever visits positions reachable within four plies of
those seven roots.

This module records the complementary thing — a broad, diverse sample of *named*
positions, each with its exact legal-move list, terminal status and winner. A
re-implementation that reproduces this file move-for-move has the same rules, and
when it does not, the failing line names the position and the specific move that
was added or missed.

Format is deliberately dumb so any language can read it without a parser library.
One position per line, space-separated fields::

    <board> <stm> <halfmove> <terminal> <winner> <nmoves> <mv>...

* ``board``    63 chars, row-major (index = row * 7 + col, row 0 = Black's back
               rank). Blue pieces are ``A``..``H``, Black ``a``..``h``, both by
               rank (A/a = Rat … H/h = Elephant); ``.`` is empty.
* ``stm``      ``B`` for Blue to move, ``K`` for Black (matching `format_move`).
* ``halfmove`` plies since the last capture; 100 is the draw threshold.
* ``terminal`` ``1`` if `GameState.is_terminal()`, else ``0``.
* ``winner``   ``B``/``K``/``-`` from `GameState.get_winner()`.
* ``mv``       ``fc,fr,tc,tr,captured``, sorted, from raw move generation.

``terminal`` and ``winner`` are recorded independently of ``mv``: move generation
does not stop at a decided position, so a terminal line still lists whatever the
generator produces. A port must reproduce both, separately.

Note the corpus cannot carry repetition state — `is_repetition()` depends on the
whole move history, not on the position. That is a search-side draw score rather
than a rule, so it is out of scope here and is covered by `tests/test_repetition.py`.
"""

from __future__ import annotations

import gzip
import random
from pathlib import Path

from config import (
    COLS,
    DEN_BLACK,
    DEN_BLUE,
    RIVER_SQUARES,
    ROWS,
    TRAPS_BLACK,
    TRAPS_BLUE,
)
from engine.board import Board
from engine.game_state import GameState
from engine.move_generator import generate_legal_moves
from engine.pieces import Color, piece_id_color, piece_id_rank
from engine.rules import WinResult

_GOLDEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "golden"
CORPUS_PATH = _GOLDEN_DIR / "positions.txt.gz"
EVALS_PATH = _GOLDEN_DIR / "evals.txt.gz"

FORMAT_VERSION = 1
_EMPTY = "."


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_board(board: Board) -> str:
    """Return the 63-character row-major board string."""
    out = []
    for r in range(ROWS):
        for c in range(COLS):
            pid = board.get(c, r)
            if pid == 0:
                out.append(_EMPTY)
            else:
                base = "A" if pid > 0 else "a"
                out.append(chr(ord(base) + piece_id_rank(pid) - 1))
    return "".join(out)


def decode_board(text: str) -> Board:
    """Rebuild a Board from its 63-character encoding."""
    if len(text) != COLS * ROWS:
        raise ValueError(f"board string must be {COLS * ROWS} chars, got {len(text)}")
    board = Board()
    for i, ch in enumerate(text):
        if ch == _EMPTY:
            continue
        r, c = divmod(i, COLS)
        rank = ord(ch.upper()) - ord("A") + 1
        board.place_piece(c, r, rank if ch.isupper() else -rank)
    return board


def encode_position(gs: GameState) -> str:
    """Return the full corpus line for this position."""
    moves = sorted(
        generate_legal_moves(gs.board, gs.turn),
        key=lambda m: (m.fc, m.fr, m.tc, m.tr, m.captured),
    )
    winner = gs.get_winner()
    fields = [
        encode_board(gs.board),
        "B" if gs.turn == Color.BLUE else "K",
        str(gs._halfmove_clock),
        "1" if gs.is_terminal() else "0",
        "-" if winner is None else ("B" if winner == Color.BLUE else "K"),
        str(len(moves)),
    ]
    fields += [f"{m.fc},{m.fr},{m.tc},{m.tr},{m.captured}" for m in moves]
    return " ".join(fields)


def derive_result(board: Board) -> WinResult | None:
    """Recompute the game result from the position alone.

    `GameState.result` is set by `apply_move` and cannot be carried in a position
    encoding, so decoding has to rebuild it. This is well defined because both
    win conditions are positional: nothing but a den entry can put a piece on an
    enemy den (and the game stops the instant it happens, so it stays there), and
    capture-all is just a piece count. A port computes terminality this same way,
    from the board rather than from a remembered event.
    """
    for color, den in ((Color.BLUE, DEN_BLACK), (Color.BLACK, DEN_BLUE)):
        pid = board.get(*den)
        if pid != 0 and piece_id_color(pid) == color:
            return WinResult(color)
    for color in (Color.BLUE, Color.BLACK):
        if board.alive_count(color) == 0:
            return WinResult(Color.BLACK if color == Color.BLUE else Color.BLUE)
    return None


def decode_position(line: str) -> tuple[GameState, bool, str, list[tuple[int, ...]]]:
    """Parse a corpus line into (state, terminal, winner_char, moves)."""
    parts = line.split()
    gs = GameState()
    gs.board = decode_board(parts[0])
    gs.turn = Color.BLUE if parts[1] == "B" else Color.BLACK
    gs._halfmove_clock = int(parts[2])
    gs.result = derive_result(gs.board)
    terminal = parts[3] == "1"
    winner = parts[4]
    n = int(parts[5])
    moves = [tuple(int(x) for x in tok.split(",")) for tok in parts[6 : 6 + n]]
    return gs, terminal, winner, moves


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def _playout(rng: random.Random, capture_bias: float, max_plies: int) -> list[str]:
    """Play one random game; return every position visited, encoded."""
    gs = GameState()
    gs.new_game()
    seen = []
    for _ in range(max_plies):
        seen.append(encode_position(gs))
        if gs.is_terminal():
            break
        moves = gs.legal_moves()
        if not moves:
            break
        captures = [m for m in moves if m.captured]
        pool = captures if (captures and rng.random() < capture_bias) else moves
        gs.apply_move(rng.choice(pool))
    return seen


def _random_sparse(rng: random.Random, allow_odd_terrain: bool) -> str:
    """Build a random few-piece position directly.

    Playouts alone leave the corpus crowded — half the positions still have all
    sixteen pieces, and the sparse endgames where den races and the Rat/Elephant
    interaction decide games are barely represented. Constructing positions
    directly reaches that regime immediately. They need not be reachable: move
    generation is a function of the position, so any legal placement is a valid
    test of it.

    `allow_odd_terrain` lets a non-Rat stand in the river. That is unreachable in
    play, but `tools.positions.place` can build it, and it is the one case that
    distinguishes "a Rat blocks a jump" from "any occupant blocks a jump" — so a
    few of them pin that rule explicitly.
    """
    board = Board()
    n_blue = rng.randint(0, 4)
    n_black = rng.randint(0, 4)
    if n_blue + n_black == 0:
        n_blue = 1

    free = [
        (c, r)
        for c in range(COLS)
        for r in range(ROWS)
    ]
    rng.shuffle(free)

    for color, count in ((Color.BLUE, n_blue), (Color.BLACK, n_black)):
        own_den = DEN_BLUE if color == Color.BLUE else DEN_BLACK
        for rank in rng.sample(range(1, 9), count):
            for i, (c, r) in enumerate(free):
                if (c, r) == own_den:
                    continue  # a piece can never stand on its own den
                in_river = (c, r) in RIVER_SQUARES
                if in_river and rank != 1 and not allow_odd_terrain:
                    continue
                board.place_piece(c, r, rank if color == Color.BLUE else -rank)
                free.pop(i)
                break

    gs = GameState()
    gs.board = board
    gs.turn = Color.BLUE if rng.random() < 0.5 else Color.BLACK
    gs._halfmove_clock = rng.choice([0, 0, 0, 1, 17, 98, 99, 100])
    gs.result = derive_result(board)
    return encode_position(gs)


def _encode_raw(specs, turn: Color, halfmove: int = 0) -> str:
    """Encode a position given as (col, row, pid) triples."""
    board = Board()
    for c, r, pid in specs:
        board.place_piece(c, r, pid)
    gs = GameState()
    gs.board = board
    gs.turn = turn
    gs._halfmove_clock = halfmove
    gs.result = derive_result(board)
    return encode_position(gs)


def _targeted_positions() -> list[str]:
    """Construct positions that probe the rules random sampling barely reaches.

    Random play is a poor adversary for the fiddly rules. Measured against a
    sampled corpus, mutating "only a Rat blocks a river jump" into "any piece
    blocks" changed exactly one position in ten thousand — detection by luck.
    These matrices pin the three rules that are easy to get subtly wrong, each
    with enough redundancy that no plausible regeneration loses them:

    * **Jump blocking** — every jump in the table, every square on its flight
      path, occupied by a Rat and by non-Rats of both colours. Only the Rat may
      block, and a Rat on the path blocks regardless of which side owns it.
    * **The trap rule** — every trap square, every defender rank, every attacker
      rank. The trap zeroes the *defender's* rank only; the attacker keeps its
      own, so a trapped piece is vulnerable but not disarmed.
    * **The water boundary** — the river/land divide is symmetric and absolute,
      and it is the reason a Rat in the river cannot take a bank Elephant.
    """
    from engine.move_generator import _JUMP_TABLE

    out: list[str] = []
    blockers = [1, 2, 8, -1, -2, -8]  # Rat and two non-Rats, both colours

    # --- jump blocking -----------------------------------------------------
    for (oc, orow), jumps in sorted(_JUMP_TABLE.items()):
        for dc, dr, lc, lr in jumps:
            path = []
            c, r = oc + dc, orow + dr
            while (c, r) != (lc, lr):
                path.append((c, r))
                c, r = c + dc, r + dr
            for jumper in (7, 6):  # Lion, Tiger
                out.append(_encode_raw([(oc, orow, jumper)], Color.BLUE))
                # with an enemy on the landing square, so jump-captures show up
                out.append(_encode_raw([(oc, orow, jumper), (lc, lr, -3)], Color.BLUE))
                for pc, pr in path:
                    for blocker in blockers:
                        if blocker == jumper:
                            continue
                        out.append(
                            _encode_raw([(oc, orow, jumper), (pc, pr, blocker)], Color.BLUE)
                        )

    # --- the trap rule -----------------------------------------------------
    for traps, victim_sign in ((TRAPS_BLACK, 1), (TRAPS_BLUE, -1)):
        for tc, tr in sorted(traps):
            neighbours = [
                (tc + dc, tr + dr)
                for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0))
                if 0 <= tc + dc < COLS and 0 <= tr + dr < ROWS
            ]
            attack_sq = next(
                (sq for sq in neighbours if sq not in (DEN_BLUE, DEN_BLACK)), None
            )
            if attack_sq is None:
                continue
            for victim_rank in range(1, 9):
                for attacker_rank in range(1, 9):
                    out.append(
                        _encode_raw(
                            [
                                (tc, tr, victim_sign * victim_rank),
                                (*attack_sq, -victim_sign * attacker_rank),
                            ],
                            Color.BLUE if victim_sign < 0 else Color.BLACK,
                        )
                    )

    # --- the water boundary ------------------------------------------------
    for water in sorted(RIVER_SQUARES):
        wc, wr = water
        for land in ((wc, wr - 1), (wc, wr + 1), (wc - 1, wr), (wc + 1, wr)):
            lc, lr = land
            if not (0 <= lc < COLS and 0 <= lr < ROWS) or land in RIVER_SQUARES:
                continue
            if land in (DEN_BLUE, DEN_BLACK):
                continue
            for other in (1, 2, 8):  # Rat vs Rat, and the Elephant cases
                out.append(_encode_raw([(wc, wr, 1), (lc, lr, -other)], Color.BLUE))
                out.append(_encode_raw([(wc, wr, -1), (lc, lr, other)], Color.BLUE))

    return out


def collect(target: int = 10_000, seed: int = 20260815) -> list[str]:
    """Collect a deduplicated, diverse sample of encoded positions.

    Positions come from two sources in roughly equal measure. Playouts supply
    reachable, plausible positions but are heavily biased toward the opening —
    every game starts there, so naive sampling drowns the corpus in 16-piece
    positions. So each playout is subsampled with a bias toward its later plies,
    and its terminal position is always kept: den entries, capture-alls and
    stalemates are rare in a uniform sample and are exactly where rules bugs
    hide. Constructed sparse positions supply the endgame regime directly.
    """
    rng = random.Random(seed)
    flavours = (
        (0.0, 140),   # unbiased: opening and midgame
        (0.5, 160),   # mixed
        (0.95, 220),  # capture-hungry: drives toward sparse endgames
    )
    per_playout = 8

    seen: set[str] = set()
    lines: list[str] = []

    def add(line: str) -> None:
        key = " ".join(line.split(" ", 3)[:3])
        if key not in seen:
            seen.add(key)
            lines.append(line)

    # Targeted positions go in first so they survive any target size.
    for line in _targeted_positions():
        add(line)

    game = 0
    while len(lines) < target and game < 200_000:
        # Alternate: half the budget from playouts, half constructed.
        if game % 2 == 0:
            capture_bias, max_plies = flavours[(game // 2) % len(flavours)]
            visited = _playout(rng, capture_bias, max_plies)
            n = len(visited)
            add(visited[-1])  # always keep the final (often terminal) position
            for _ in range(per_playout):
                # sqrt bias toward the end of the game
                add(visited[min(n - 1, int(n * rng.random() ** 0.5))])
        else:
            add(_random_sparse(rng, allow_odd_terrain=(game % 50 == 1)))
        game += 1

    return lines[:target]


def _write_gz(path: Path, text: str) -> None:
    """Write gzip deterministically.

    `gzip.open` stamps the current time into the header, so regenerating an
    identical corpus would still produce different bytes and show up as a diff.
    Pinning mtime to 0 keeps a committed data file byte-stable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(text.encode("ascii"))


def write(lines: list[str], path: Path = CORPUS_PATH) -> None:
    header = f"# jungle-golden v{FORMAT_VERSION} positions={len(lines)}\n"
    _write_gz(path, header + "\n".join(lines) + "\n")


def read(path: Path = CORPUS_PATH) -> list[str]:
    with gzip.open(path, "rt", encoding="ascii") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip() and not ln.startswith("#")]


# ---------------------------------------------------------------------------
# Evaluation corpus
# ---------------------------------------------------------------------------

def collect_evals(lines: list[str] | None = None) -> list[str]:
    """Score every corpus position with the evaluation `jungle-eval` ports.

    A second oracle, for the layer above the rules. Move generation being
    identical says nothing about whether two engines *judge* a position the same
    way, and an evaluation port is easy to get subtly wrong -- a term computed
    from the evaluating side rather than the piece's own colour still looks
    plausible and still passes an antisymmetry check.

    Only Blue's perspective is recorded: evaluation is antisymmetric, so Black's
    score is the negation, and storing both would test nothing extra.

    **`v13_strong_config()`, not `strong_config()`.** `jungle-eval` is a
    deliberately verbatim port of the v1.3-era Python evaluator -- the desktop
    repo's own note is that "a port that changes behaviour cannot be verified as
    a port". This repo's `strong_config()` has since moved on to v1.6 and adds
    `use_hanging_penalty`, so scoring the corpus with it would compare two
    evaluations that were never meant to agree and fail all 10,000 positions.
    `v13_strong_config()` is the frozen flag set the Rust port was written
    against. When Phase 5 retunes the evaluation, this line moves with it and
    both corpora are regenerated together.
    """
    from ai.evaluator import evaluate
    from ai.search_config import v13_strong_config

    cfg = v13_strong_config()
    out = []
    for line in lines if lines is not None else read():
        gs, _, _, _ = decode_position(line)
        board, stm = line.split(" ", 2)[:2]
        out.append(f"{board} {stm} {evaluate(gs, Color.BLUE, cfg)}")
    return out


def write_evals(lines: list[str], path: Path = EVALS_PATH) -> None:
    header = f"# jungle-golden-evals v{FORMAT_VERSION} positions={len(lines)}\n"
    _write_gz(path, header + "\n".join(lines) + "\n")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="generate the golden rules corpus")
    ap.add_argument("--positions", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=20260815)
    args = ap.parse_args()

    collected = collect(args.positions, args.seed)
    write(collected)
    size = CORPUS_PATH.stat().st_size
    print(f"wrote {len(collected)} positions to {CORPUS_PATH} ({size / 1024:.0f} KiB)")

    evals = collect_evals(collected)
    write_evals(evals)
    size = EVALS_PATH.stat().st_size
    print(f"wrote {len(evals)} evaluations to {EVALS_PATH} ({size / 1024:.0f} KiB)")
