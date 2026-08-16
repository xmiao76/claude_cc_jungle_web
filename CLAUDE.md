# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project Overview

Browser port of the Jungle (Dou Shou Qi) desktop game in `C:\GitHub\claude_cc_jungle`.
The engine is **Rust compiled to WebAssembly**, running in a Web Worker; the UI is
vanilla JS + Canvas. Deployed as a static site to Cloudflare Pages (`public/` is
the site root — nothing outside it is deployed).

There are **two engines here on purpose**: the Rust one in `rust/` plays, and the
Python one in `engine-python/` is the oracle it is verified against. Read
[`rust/VENDORED.md`](rust/VENDORED.md) before changing either.

## Commands

| Task | Command |
|------|---------|
| Build the engine | `npm run build:wasm` (needs `wasm-pack` + the `wasm32-unknown-unknown` target) |
| Dev server | `npm run dev` (http://localhost:8788) — build first |
| Rust tests | `npm run test:rust` (107 tests) |
| Python tests | `.venv/Scripts/python -m pytest` (156 tests) |
| JS unit tests | `npm run test:js` |
| Engine smoke test + bench | `npm run test:wasm` |
| E2E (Playwright) | `npm run test:e2e` |
| Engine bench (native) | `cd rust && cargo run --release -p jungle-cli --bin jungle -- bench 2000 [--params k=v,...]` |
| A/B match (search) | `cd rust && cargo run --release -p jungle-cli --bin jungle -- match --a <k=v,...> --nodes 25000 --games 2000 --threads 8` |
| A/B match (evaluation) | same, with `--a-eval <k=v,...>` (`pv1`..`pv8`, `den_threat`, …) |
| Deploy | `npm run deploy` (builds, then needs `wrangler login`) |

## Architecture

- `rust/` — the engine, vendored from the desktop repo. `jungle-core` (rules,
  bitboards, movegen, perft), `jungle-eval` (static evaluation), `jungle-search`
  (negamax PVS, TT, ordering, SEE, quiescence), `jungle-cli` (dev-only bench,
  perft and SPRT match runner), and `jungle-wasm` (this repo's browser bridge).
  63 squares fit in a `u64`, so every terrain mask and jump path is one word.
- `rust/crates/jungle-wasm/src/session.rs` — the JSON bridge, a shape-for-shape
  port of `engine-python/web_api.py`. Deliberately host-agnostic (no JS types),
  so `cargo test` covers the whole protocol on the host; `src/lib.rs` is only the
  `wasm_bindgen` forwarding layer. **Keep logic out of `lib.rs`** — anything that
  drifts there becomes untestable outside a browser.
- `rust/crates/jungle-search/src/clock.rs` — `std::time::Instant` compiles for
  `wasm32-unknown-unknown` and *panics at runtime*, unconditionally, even under a
  node limit. Everything time-related goes through `now_ms()`. Do not reintroduce
  `Instant` into the search.
- `engine-python/` — the Python engine (v1.6 lineage). No longer shipped or
  downloaded; it exists to be the differential oracle. Its search is far more
  feature-rich than the Rust one (see the strength notes below) and is worth
  reading as a specification when porting a technique across.
- `public/js/engine-worker.js` — classic Web Worker; loads `public/wasm/` and
  forwards the seven bridge calls. No file list to keep in sync any more.
- `public/js/main.js` — UI state machine (web equivalent of desktop
  `controller.py`). Game-generation counter (`this.gen`) drops stale worker
  replies after New Game/Menu. Blue always moves first; `playerFirst` decides
  who controls Blue; board flip is visual-only (engine always unflipped).
- `public/js/coords.js` — pure mapping helpers (unit-tested); board is
  7 cols × 9 rows, Blue at row 8 (bottom), piece id = ±rank.

## Key invariants

- **The three instruments must stay green.** Frozen perft counts, the
  10,000-position golden rules corpus, and the golden evaluation corpus. If a rule
  changes, *both* engines change and `python -m tools.golden` regenerates the
  corpora — which must then reproduce byte-for-byte, since the sampler's own walk
  depends on move order. See `rust/VENDORED.md`.
- The bridge protocol is a contract with the UI and the browser tests. It is
  asserted twice: `tests/python/test_web_api.py` and
  `rust/crates/jungle-wasm/tests/bridge.rs`. Change both, or neither.
- Eval symmetry: `evaluate(BLUE) == -evaluate(BLACK)` (pinned on both sides).
- Fixed-depth signature tests in `tests/python/test_strength.py` pin the *Python*
  engine's behavior byte-for-byte; if one fails, a change leaked outside its flag.
- The flip option must never mutate game state — mapping only.
- The trap rule is **defence only**: a piece in an enemy trap is taken by
  anything, but still attacks at its real rank. Applying the rank-0 effect to the
  attacker silently makes a class of legal captures illegal near both dens — it
  did, here, until the golden corpus caught it.

## Measure, do not assume

The most important convention inherited from the desktop project. Plausible ideas
lost strength there more often than they gained it: a Rat-premium value table
(-44 Elo), BFS true-distance-to-den (-70), a *corrected* jump-readiness test
(-98), and static hanging-piece detection (-255, decisive) were all implemented
and all measured worse than their absence.

Gate every search or evaluation change behind a `SearchParams` flag, run the match
above, and record the result in `rust/STRENGTH.md` whether it passed or failed.
Prefer `--nodes` (deterministic, reproducible); a pure speedup measures as exactly
zero there, so price those with `bench --params` instead and say which you used.

**Use enough games.** This repo has already produced the cautionary example:
`use_see_prune` read +15 Elo over 600 games and +6 [−4, +16] over 2000. Two
thousand games take about six minutes on eight threads. There is no reason to
decide on six hundred.

## Known headroom

`jungle-search` was a port of the **v1.3-era** Python search. All seven things
Python added in v1.4–v1.6 have now been implemented and measured. **Two shipped**
— `use_improving` and `use_tt_eval`. Five did not: `use_iir`, `use_see_prune`,
`use_lmr_log`, `use_capture_history`, `use_cont_history`.

**Do not keep porting search features from `engine-python` without reading
[`rust/STRENGTH.md`](rust/STRENGTH.md) first.** The pattern there is that
ordering heuristics measured on a 25k-nps, depth-8 search have little left to buy
on a 2.4M-nps, depth-16 one, where ordering is already good enough that most
nodes fail high on the first move. Each rejected flag has a specific retry
recorded; re-running the same tuning will just reproduce the same negative.

The remaining headroom is in the two directions that are not about ordering:

- **Evaluation.** `jungle-eval` is a verbatim port of the v1.3 evaluator — 13
  hand-set weights and one shared piece-square table, never tuned. The Python
  attempt failed on 42k positions from 500 games at 120 ms; this engine can
  harvest millions at real budgets.
- **Parallelism.** Nothing here is threaded. Dropping Pyodide removed the
  cross-origin CDN dependency, so COOP/COEP and `SharedArrayBuffer` are now
  available to a Lazy SMP build — at the cost of a nightly toolchain and a
  lock-free transposition table.

The two lineages also **contradict each other on the hanging-piece term**:
`engine-python` ships it on, the desktop measured it at -255 Elo and ships it off.
Nobody has re-measured it on a fast searcher.

## Testing notes

- pytest pythonpath is `engine-python` + repo root (see `pyproject.toml`).
- `test_corpus_regenerates_byte_for_byte` is marked `slow` (~14 s); deselect with
  `-m 'not slow'`.
- E2E is fully local and hermetic now (no CDN); first run:
  `npx playwright install chromium`.
- CI runs three jobs: `rust` (fmt on `jungle-wasm` only, clippy `-D warnings`,
  tests, wasm build), `python` (the oracle), and `web` (build, JS, smoke, E2E).
  The vendored crates are not rustfmt-clean upstream and are deliberately not
  reformatted, so future syncs stay reviewable.
