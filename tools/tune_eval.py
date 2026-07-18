"""Texel-style evaluation tuning for the Jungle AI.

Pipeline:

1. ``harvest`` — fast self-play games; every post-opening position is
   serialized (pieces, side to move, noisy-move availability, ply) together
   with the eventual game result from Blue's perspective. Positions — not
   features — are stored, so the feature extractor can evolve after harvest.
2. ``fit`` — logistic (Texel) fit of the fittable eval weights over the
   harvested quiet positions and print a drop-in ``EVAL_WEIGHTS_TUNED``
   block. Material values are frozen (they anchor the centipawn scale that
   the search margins assume); ``den_proximity_max_dist`` is structural.
   The fitted table ships only if it passes its own self-play gate.

v1.5 finding (42k positions from 500 games @120ms): the outcome-prediction
surface over these 13 weights is nearly flat — regularized fits stay at the
hand values, and the unregularized fit buys its ~3% MSE gain with
correlation artifacts (negative advancement/mobility, 27x acceleration:
"winners already have deep pieces", not "advancing is good"). The hand
weights therefore shipped unchanged in v1.5. Re-attempt with materially
more games, longer time controls, or opponent-diverse data.

Usage (repo root, headless):

    python -m tools.tune_eval harvest --games 500 --budget 120 --out positions.jsonl
    python -m tools.tune_eval fit --data positions.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import time

from ai.minimax import AIPlayer
from ai.search_config import strong_config
from engine.board import Board
from engine.game_state import GameState
from engine.move_generator import generate_noisy_only
from engine.pieces import Color

# The weight keys the fit optimizes, in fixed column order. rat_in_water is
# the MERGED rat-river coefficient (rat_in_water + rat_blocks_river fire on
# the same indicator, so they are one feature; at emit time the fitted total
# is split by keeping rat_blocks_river at its current value).
_FIT_KEYS = (
    "advancement_per_row",
    "den_proximity_per_step",
    "den_defender",
    "rat_in_water",
    "rat_adjacent_to_enemy_elephant",
    "trap_control",
    "jump_ready",
    "advancement_acceleration",
    "tempo",
    "mobility",
    "pst",
    "den_threat",
    "hanging",
)


def _apply_random_opening(gs: GameState, rng: random.Random, plies: int) -> None:
    for _ in range(plies):
        if gs.is_terminal():
            return
        moves = gs.legal_moves()
        if not moves:
            return
        gs.apply_move(rng.choice(moves))


def _serialize(gs: GameState) -> dict:
    """One storable row for the current position (result added at game end)."""
    pieces = []
    for color in (Color.BLUE, Color.BLACK):
        for pid, sq in gs.board.pieces_of(color).items():
            pieces.append([pid, sq // 9, sq % 9])
    return {
        "pieces": pieces,
        "turn": int(gs.turn),
        "noisy": bool(generate_noisy_only(gs.board, gs.turn)),
        "ply": len(gs.history),
    }


def _cmd_harvest(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()
    total = 0
    with open(args.out, "a", encoding="utf-8") as out:
        for g in range(args.games):
            gs = GameState()
            gs.new_game()
            _apply_random_opening(gs, random.Random(args.seed + g),
                                  args.opening_plies)
            ai_blue = AIPlayer(Color.BLUE, 2, strong_config())
            ai_black = AIPlayer(Color.BLACK, 2, strong_config())
            rows: list[dict] = []
            for _ in range(args.max_moves):
                if gs.is_terminal():
                    break
                rows.append(_serialize(gs))
                ai = ai_blue if gs.turn == Color.BLUE else ai_black
                move = ai.get_best_move(gs, time_budget_ms=args.budget)
                if move is None:
                    break
                gs.apply_move(move)
            winner = gs.get_winner()
            result = 0.5 if winner is None else (1.0 if winner == Color.BLUE
                                                 else 0.0)
            for row in rows:
                row["result"] = result
                out.write(json.dumps(row, separators=(",", ":")) + "\n")
            out.flush()
            total += len(rows)
            if (g + 1) % 10 == 0:
                dt = time.perf_counter() - t0
                print(f"game {g + 1}/{args.games}  positions={total}  "
                      f"elapsed={dt:.0f}s", flush=True)
    print(f"done: {total} positions -> {args.out}", flush=True)


def _load_matrix(path: str, min_ply: int):
    """Rebuild harvested quiet positions into (X, material, results) arrays."""
    import numpy as np
    from ai.evaluator import evaluate_features

    cfg = strong_config()
    rows_x: list[list[int]] = []
    material: list[int] = []
    results: list[float] = []
    kept = skipped = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["noisy"] or row["ply"] < min_ply:
                skipped += 1
                continue
            gs = GameState()
            gs.board = Board()
            for (pid, c, r) in row["pieces"]:
                gs.board.place_piece(c, r, pid)
            gs.turn = Color(row["turn"])
            mat, counts = evaluate_features(gs, Color.BLUE, cfg)
            rows_x.append([counts[k] for k in _FIT_KEYS])
            material.append(mat)
            results.append(row["result"])
            kept += 1
    print(f"loaded {kept} quiet positions ({skipped} skipped)")
    return (np.asarray(rows_x, dtype=np.float64),
            np.asarray(material, dtype=np.float64),
            np.asarray(results, dtype=np.float64))


def _cmd_fit(args: argparse.Namespace) -> None:
    import numpy as np
    from config import EVAL_WEIGHTS

    X, material, y = _load_matrix(args.data, args.min_ply)
    n = len(y)
    if n < 5000:
        print(f"WARNING: only {n} positions — fit may be unstable")

    # Hold out 10% for a sanity check.
    rng = np.random.default_rng(args.seed)
    idx = rng.permutation(n)
    cut = n // 10
    hold, train = idx[:cut], idx[cut:]

    w0 = np.array(
        [(EVAL_WEIGHTS["rat_in_water"] + EVAL_WEIGHTS["rat_blocks_river"])
         if k == "rat_in_water" else EVAL_WEIGHTS[k] for k in _FIT_KEYS],
        dtype=np.float64)

    ln10 = np.log(10.0)

    def mse(w, k_scale, rows):
        s = material[rows] + X[rows] @ w
        p = 1.0 / (1.0 + np.power(10.0, -s / k_scale))
        return float(np.mean((p - y[rows]) ** 2))

    # Stage 1: fit the sigmoid scale K under the current weights.
    ks = np.arange(100.0, 1501.0, 25.0)
    k_scale = min(ks, key=lambda k: mse(w0, k, train))
    print(f"K = {k_scale:.0f}   baseline train MSE = {mse(w0, k_scale, train):.5f}"
          f"   holdout = {mse(w0, k_scale, hold):.5f}")

    # Stage 2: gradient descent on the weights (material frozen), with a mild
    # L2 pull toward the hand weights to keep collinear terms sane.
    w = w0.copy()
    lam = args.l2
    lr = args.lr
    xt = X[train]
    yt = y[train]
    mt = material[train]
    prev = mse(w, k_scale, train)
    for it in range(args.iters):
        s = mt + xt @ w
        p = 1.0 / (1.0 + np.power(10.0, -s / k_scale))
        grad_common = 2.0 * (p - yt) * p * (1.0 - p) * ln10 / k_scale
        grad = xt.T @ grad_common / len(yt) + 2.0 * lam * (w - w0)
        w -= lr * grad
        if (it + 1) % 200 == 0:
            cur = mse(w, k_scale, train)
            print(f"iter {it + 1}: train MSE {cur:.5f}  holdout {mse(w, k_scale, hold):.5f}")
            if cur > prev - 1e-9:
                lr *= 0.5
            prev = cur

    print("\nfitted weights (hand -> tuned):")
    tuned: dict[str, int] = {}
    for i, k in enumerate(_FIT_KEYS):
        tuned[k] = int(round(w[i]))
        print(f"  {k:34s} {int(w0[i]):5d} -> {tuned[k]:5d}")

    # Split the merged rat-river coefficient: keep rat_blocks_river as-is.
    blocks = EVAL_WEIGHTS["rat_blocks_river"]
    tuned_rat = tuned.pop("rat_in_water") - blocks

    print("\n# Drop-in for config.EVAL_WEIGHTS_TUNED:")
    print("EVAL_WEIGHTS_TUNED = {")
    print(f'    "advancement_per_row": {tuned["advancement_per_row"]},')
    print(f'    "den_proximity_max_dist": {EVAL_WEIGHTS["den_proximity_max_dist"]},')
    print(f'    "den_proximity_per_step": {tuned["den_proximity_per_step"]},')
    print(f'    "rat_in_water": {tuned_rat},')
    print(f'    "rat_adjacent_to_enemy_elephant": {tuned["rat_adjacent_to_enemy_elephant"]},')
    print(f'    "trap_control": {tuned["trap_control"]},')
    print(f'    "mobility": {tuned["mobility"]},')
    print(f'    "den_defender": {tuned["den_defender"]},')
    print(f'    "jump_ready": {tuned["jump_ready"]},')
    print(f'    "rat_blocks_river": {blocks},')
    print(f'    "tempo": {tuned["tempo"]},')
    print(f'    "advancement_acceleration": {tuned["advancement_acceleration"]},')
    print(f'    "delta_margin": {EVAL_WEIGHTS["delta_margin"]},')
    print(f'    "pst": {tuned["pst"]},')
    print(f'    "den_threat": {tuned["den_threat"]},')
    print(f'    "hanging": {tuned["hanging"]},')
    print("}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Jungle eval tuning")
    sub = parser.add_subparsers(dest="cmd", required=True)

    hp = sub.add_parser("harvest", help="self-play position harvesting")
    hp.add_argument("--games", type=int, default=500)
    hp.add_argument("--budget", type=int, default=120, help="per-move ms")
    hp.add_argument("--max-moves", type=int, default=200)
    hp.add_argument("--opening-plies", type=int, default=6)
    hp.add_argument("--seed", type=int, default=777000)
    hp.add_argument("--out", required=True)
    hp.set_defaults(func=_cmd_harvest)

    fp = sub.add_parser("fit", help="logistic fit of the eval weights")
    fp.add_argument("--data", required=True)
    fp.add_argument("--min-ply", type=int, default=8)
    fp.add_argument("--iters", type=int, default=2000)
    fp.add_argument("--lr", type=float, default=200.0)
    fp.add_argument("--l2", type=float, default=1e-4)
    fp.add_argument("--seed", type=int, default=1)
    fp.set_defaults(func=_cmd_fit)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
