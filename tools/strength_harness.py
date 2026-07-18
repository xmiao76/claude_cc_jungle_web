"""Strength-measurement harness for the Jungle AI.

Pits two engine configurations head-to-head in self-play and reports the score,
so any change to the search/evaluation can be proven stronger (or not) against a
control. Also provides a node/depth benchmark on fixed positions.

The engine is deterministic, so variety comes from a handful of *seeded* random
opening plies. Each opening is played twice with the colors swapped, cancelling
first-move advantage.

Usage (run from the repo root, headless — no pygame needed):

    # All enhancements vs the original engine, equal time budget:
    python -m tools.strength_harness selfplay --a strong --b baseline --games 200 --budget 300

    # A/A sanity check (should be ~50%):
    python -m tools.strength_harness selfplay --a strong --b strong --games 20 --budget 200

    # Node / effective-depth benchmark:
    python -m tools.strength_harness bench --config strong --budget 2000
"""

from __future__ import annotations

import argparse
import random
import time

from ai.minimax import AIPlayer
from ai.search_config import (
    SearchConfig, baseline_config, strong_config,
    v13_strong_config, v14_strong_config,
)
from engine.game_state import GameState
from engine.pieces import Color

_CONFIGS = {
    "strong": strong_config,
    "baseline": baseline_config,
    "v13": v13_strong_config,   # the shipped 1.3 engine, frozen for A/B
    "v14": v14_strong_config,   # the shipped 1.4 engine, frozen for A/B
}

_HARD_DIFFICULTY = 2   # iterative deepening (time-controlled) for a fair comparison


def resolve_config(name: str) -> SearchConfig:
    """Map a CLI name to a SearchConfig (raises on unknown name)."""
    if name not in _CONFIGS:
        raise ValueError(f"unknown config {name!r}; choose from {sorted(_CONFIGS)}")
    return _CONFIGS[name]()


def _apply_random_opening(gs: GameState, rng: random.Random, plies: int) -> None:
    """Play *plies* random legal moves to diversify the opening."""
    for _ in range(plies):
        if gs.is_terminal():
            return
        moves = gs.legal_moves()
        if not moves:
            return
        gs.apply_move(rng.choice(moves))


def play_one(cfg_blue: SearchConfig, cfg_black: SearchConfig, *,
             budget_ms: int, max_moves: int,
             opening_seed: int, opening_plies: int,
             budget_black_ms: int | None = None) -> Color | None:
    """Play a single game. Returns the winning Color, or None for a draw/timeout.

    *budget_black_ms* enables time-odds games (e.g. simulating an older,
    slower engine build by shrinking its budget); default = same as Blue.
    """
    gs = GameState()
    gs.new_game()
    _apply_random_opening(gs, random.Random(opening_seed), opening_plies)

    ai_blue = AIPlayer(Color.BLUE, _HARD_DIFFICULTY, cfg_blue)
    ai_black = AIPlayer(Color.BLACK, _HARD_DIFFICULTY, cfg_black)
    black_ms = budget_ms if budget_black_ms is None else budget_black_ms

    for _ in range(max_moves):
        if gs.is_terminal():
            break
        if gs.turn == Color.BLUE:
            move = ai_blue.get_best_move(gs, time_budget_ms=budget_ms)
        else:
            move = ai_black.get_best_move(gs, time_budget_ms=black_ms)
        if move is None:
            break
        gs.apply_move(move)

    return gs.get_winner()


def play_match(cfg_a: SearchConfig, cfg_b: SearchConfig, *,
               games: int = 20, budget_ms: int = 300, max_moves: int = 200,
               opening_plies: int = 6, seed: int = 12345,
               verbose: bool = False, budget_b_ms: int | None = None) -> dict:
    """Play *games* games (rounded down to color-swapped pairs) of A vs B.

    *budget_b_ms* gives side B a different per-move budget (time odds); it
    follows B through the color swap. Returns a result dict: a_wins, b_wins,
    draws, games, a_score (0..1).
    """
    n_pairs = max(1, games // 2)
    a_wins = b_wins = draws = 0
    b_ms = budget_ms if budget_b_ms is None else budget_b_ms

    for p in range(n_pairs):
        opening_seed = seed + p
        # Game 1: A is Blue, B is Black.
        w1 = play_one(cfg_a, cfg_b, budget_ms=budget_ms, max_moves=max_moves,
                      opening_seed=opening_seed, opening_plies=opening_plies,
                      budget_black_ms=b_ms)
        if w1 == Color.BLUE:
            a_wins += 1
        elif w1 == Color.BLACK:
            b_wins += 1
        else:
            draws += 1
        # Game 2: same opening, colors swapped (B is Blue, A is Black).
        w2 = play_one(cfg_b, cfg_a, budget_ms=b_ms, max_moves=max_moves,
                      opening_seed=opening_seed, opening_plies=opening_plies,
                      budget_black_ms=budget_ms)
        if w2 == Color.BLUE:
            b_wins += 1
        elif w2 == Color.BLACK:
            a_wins += 1
        else:
            draws += 1

        if verbose:
            print(f"  pair {p + 1}/{n_pairs}: A={a_wins} B={b_wins} D={draws}")

    total = a_wins + b_wins + draws
    a_score = (a_wins + 0.5 * draws) / total if total else 0.0
    return {
        "a_wins": a_wins,
        "b_wins": b_wins,
        "draws": draws,
        "games": total,
        "a_score": a_score,
    }


def _fixed_midgame() -> GameState:
    """A hand-built, reproducible, out-of-book midgame (12 pieces, Blue to move)."""
    from engine.board import Board
    from engine.pieces import Animal, make_piece_id

    specs = [
        (0, 6, Color.BLUE, Animal.ELEPHANT), (2, 6, Color.BLUE, Animal.WOLF),
        (4, 6, Color.BLUE, Animal.LEOPARD), (6, 6, Color.BLUE, Animal.RAT),
        (0, 8, Color.BLUE, Animal.TIGER), (6, 8, Color.BLUE, Animal.LION),
        (0, 2, Color.BLACK, Animal.RAT), (2, 2, Color.BLACK, Animal.LEOPARD),
        (4, 2, Color.BLACK, Animal.WOLF), (6, 2, Color.BLACK, Animal.ELEPHANT),
        (0, 0, Color.BLACK, Animal.LION), (6, 0, Color.BLACK, Animal.TIGER),
    ]
    gs = GameState()
    gs.board = Board()
    for (c, r, color, animal) in specs:
        pid = make_piece_id(color, animal)
        gs.board.place_piece(c, r, pid)
    gs.turn = Color.BLUE
    return gs


def _bench_positions() -> list[tuple[str, GameState]]:
    """Return a fixed list of (name, GameState) benchmark positions.

    All positions are out-of-book so the search (not the book) is measured.
    """
    out: list[tuple[str, GameState]] = [("fixed-mid", _fixed_midgame())]
    for s in (1, 2):
        gs = GameState()
        gs.new_game()
        _apply_random_opening(gs, random.Random(100 + s), 12)
        if not gs.is_terminal():
            out.append((f"midgame-{s}", gs))
    return out


def run_bench(cfg: SearchConfig, budget_ms: int) -> None:
    """Run get_best_move on each bench position and print depth/nodes/nps."""
    print(f"{'position':<12} {'depth':>5} {'seldep':>7} {'nodes':>10} {'nps':>9} {'time_s':>7}")
    for name, gs in _bench_positions():
        ai = AIPlayer(gs.turn, _HARD_DIFFICULTY, cfg)
        t0 = time.perf_counter()
        ai.get_best_move(gs.copy(), time_budget_ms=budget_ms)
        dt = time.perf_counter() - t0
        nps = ai._nodes / dt if dt > 0 else 0.0
        print(f"{name:<12} {ai._last_depth:>5} {ai._seldepth:>7} "
              f"{ai._nodes:>10} {nps:>9.0f} {dt:>7.2f}")


def _cmd_selfplay(args: argparse.Namespace) -> None:
    cfg_a = resolve_config(args.a)
    cfg_b = resolve_config(args.b)
    print(f"Self-play: A={args.a} vs B={args.b} | "
          f"games={args.games} budget={args.budget}ms "
          f"opening_plies={args.opening_plies} seed={args.seed}")
    t0 = time.perf_counter()
    res = play_match(cfg_a, cfg_b, games=args.games, budget_ms=args.budget,
                     max_moves=args.max_moves, opening_plies=args.opening_plies,
                     seed=args.seed, verbose=args.verbose,
                     budget_b_ms=args.budget_b)
    dt = time.perf_counter() - t0
    print("-" * 56)
    print(f"A ({args.a}) wins : {res['a_wins']}")
    print(f"B ({args.b}) wins : {res['b_wins']}")
    print(f"draws          : {res['draws']}")
    print(f"games          : {res['games']}")
    print(f"A score        : {res['a_score'] * 100:.1f}%  "
          f"({'A stronger' if res['a_score'] > 0.5 else 'A not stronger'})")
    print(f"elapsed        : {dt:.1f}s")


def _cmd_bench(args: argparse.Namespace) -> None:
    cfg = resolve_config(args.config)
    print(f"Bench: config={args.config} budget={args.budget}ms")
    run_bench(cfg, args.budget)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Jungle AI strength harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("selfplay", help="A vs B self-play match")
    sp.add_argument("--a", default="strong", help="config for side A")
    sp.add_argument("--b", default="baseline", help="config for side B")
    sp.add_argument("--games", type=int, default=20)
    sp.add_argument("--budget", type=int, default=300, help="per-move ms")
    sp.add_argument("--budget-b", type=int, default=None,
                    help="per-move ms for side B (time odds; default = --budget)")
    sp.add_argument("--max-moves", type=int, default=200)
    sp.add_argument("--opening-plies", type=int, default=6)
    sp.add_argument("--seed", type=int, default=12345)
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=_cmd_selfplay)

    bp = sub.add_parser("bench", help="node/depth benchmark")
    bp.add_argument("--config", default="strong")
    bp.add_argument("--budget", type=int, default=2000, help="per-position ms")
    bp.set_defaults(func=_cmd_bench)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
