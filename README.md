# Jungle (Dou Shou Qi) — Web Edition

Play the classic **Jungle / Dou Shou Qi** board game against a strong AI, entirely
in your browser. The engine is **Rust compiled to WebAssembly**, running in a Web
Worker — no server, no backend, zero hosting cost on the **Cloudflare Pages free
tier**.

This is the web port of the Windows desktop app in `claude_cc_jungle`, and it
shares that project's engine: the `rust/` workspace is vendored from it, with a
`wasm-bindgen` bridge added here. See [`rust/VENDORED.md`](rust/VENDORED.md) for
provenance and for the two rule divergences found (and fixed) while vendoring.

The original Python engine is still in the repository, under `engine-python/`. It
no longer plays and is no longer downloaded: it is the oracle the Rust engine is
verified against, on 10,000 positions and by exhaustive perft.

## Live demo

**https://claude-jungle.pages.dev** — deployed on the Cloudflare Pages free tier.
(The project name derives from the repo name `claude_cc_jungle_web`; DNS
subdomains cannot contain underscores, so `claude_jungle` becomes
`claude-jungle`.)

## The engine

Measured on this machine at a 2-second budget, on the engine's own bench
positions:

| Engine | depth | nodes/sec |
|---|---|---|
| Python (`engine-python/`), CPython | 8–10 | 23–30k |
| Python through Pyodide — what this site used to ship | ~5–7 | slower again |
| **Rust/wasm — what it ships now** | **15–16** | **2.2M** |
| Rust, native, for reference | 17 | 3.0M |

In the desktop project the same Rust engine scored **+424 Elo [+372, +495]** over
the Python v1.5 engine across 200 games at 500 ms per move, winning 168-0-32. For
scale, the entire multi-version Python strengthening effort that preceded it was
worth +171 Elo.

The payload went the other way: **~10 MB down to 94 KB** (42 KB gzipped), and boot
from several seconds to well under a tenth of one.

On top of the port, two search changes have since been measured and adopted —
`use_improving` (+26 Elo [+15, +36] over 2000 games) and `use_tt_eval` (+4.6%
nodes/sec at identical node counts). Three others were implemented, measured, and
rejected. Every result, including the failures, is in
[`rust/STRENGTH.md`](rust/STRENGTH.md).

## Features

- Human vs AI and AI-vs-AI (watch) modes
- Three difficulties: Easy (3-ply), Medium (5-ply), Hard (iterative deepening,
  ~2.5 s budget). Every level now has a real wall-clock bound; under the Python
  engine, Easy and Medium set a time limit of 999,999 seconds and were bounded by
  nothing at all.
- Choose who moves first (Blue always opens; "You move first" decides who
  controls Blue). When the AI opens, the board auto-flips so you play from
  the bottom
- Two-click move input with legal-move indicators, capture highlights, move
  animation, capture flash, move history, captured-piece list, undo,
  board flip (visual only), sound effects with mute

## Architecture

```
Main thread (JS)                     Web Worker
┌─────────────────────────┐  msgs   ┌────────────────────────────┐
│ main.js    UI state     │ ──────► │ engine-worker.js           │
│ board-renderer.js Canvas│ ◄────── │  jungle_wasm.wasm  (89 KB) │
│ coords.js  mapping      │  JSON   │   session.rs  JSON bridge  │
│ audio.js   Web Audio    │         │    jungle-core  rules      │
│ worker-client.js        │         │    jungle-search  PVS      │
└─────────────────────────┘         └────────────────────────────┘
```

- The worker owns the authoritative position; the UI never re-implements rules in
  JS (no rule drift).
- The AI searches inside the worker, so the page stays responsive.
- Board flip is purely visual: the engine always sees Blue at the bottom
  (row 8), exactly like the desktop renderer.
- The JSON protocol is unchanged from the Python bridge it replaced, which is why
  the renderer, the coordinate helpers and the browser tests needed no edits. The
  same assertions run against both implementations
  (`tests/python/test_web_api.py` and `rust/crates/jungle-wasm/tests/bridge.rs`).

## Run locally

Requires Node ≥ 20, a Rust toolchain with the `wasm32-unknown-unknown` target,
[`wasm-pack`](https://rustwasm.github.io/wasm-pack/), and Python ≥ 3.11 (Python
for the oracle tests only).

```bash
rustup target add wasm32-unknown-unknown
cargo install wasm-pack        # once

npm run build:wasm   # builds public/wasm/ — required before dev or deploy
npm run dev          # serves public/ at http://localhost:8788
```

## Tests

```bash
python -m venv .venv && .venv/Scripts/pip install pytest   # once (Windows)
npm install                                                # once

npm run test:rust    # 107 Rust tests: engine, perft, golden corpora, bridge protocol
npm run test:py      # 156 pytest tests: Python oracle, perft, golden corpus, bridge
npm run test:js      # JS unit tests (coordinate mapping, terrain)
npm run test:wasm    # engine smoke test + bench against the real .wasm (Node)
npm run test:e2e     # Playwright: browser load, clicks, AI replies, full game
                     # (first time: npx playwright install chromium)
```

Two rule implementations live here, which is normally how rule sets quietly
diverge. It is safe only because the agreement is measured on every run: frozen
perft counts, and a 10,000-position golden corpus of legal moves, terminal status
and winner. `python -m tools.golden` regenerates the corpus, and it must come out
byte-for-byte identical — a test asserts exactly that.

## Deploy to Cloudflare Pages (free)

There is a build step now, so `public/wasm/` must be produced before upload.

Option A — Direct upload from the CLI (what `npm run deploy` does):
```bash
npx wrangler login
npm run deploy       # runs build:wasm, then wrangler pages deploy public
```

Option B — Git integration: set the build command to `npm run build:wasm` and the
build output directory to `public`. Leaving the build command empty will deploy a
site with no engine in it.

Post-deploy check: open the URL, confirm the board appears almost immediately,
play a few moves on Hard, and confirm no errors in the browser console.

## Project layout

```
public/            deployed static site
  index.html       UI shell
  css/, js/        presentation layer (Canvas renderer, worker client)
  js/engine-worker.js  loads and drives the wasm engine
  wasm/            build output: jungle_wasm.js + jungle_wasm_bg.wasm (gitignored)
  assets/          piece/tile art + sounds (from the desktop release)
rust/              the engine (vendored — see rust/VENDORED.md)
  crates/jungle-core     rules, bitboards, move generation, perft
  crates/jungle-eval     static evaluation
  crates/jungle-search   negamax PVS, TT, ordering, SEE, quiescence
  crates/jungle-wasm     the browser bridge (this repo's own)
  crates/jungle-cli      dev-only: bench, perft, SPRT match runner
engine-python/     the Python engine: test oracle only, not shipped
tests/golden/      10,000-position rules + evaluation corpora
tests/python/      pytest: Python engine, perft, golden corpus, bridge
tests/js/          node --test unit tests
tests/wasm/        smoke test + bench against the built .wasm (Node)
tests/e2e/         Playwright browser tests
tools/             dev-only: static server, corpus generator, Python harness
```

## Rules interpretation

Matches the desktop engine, and both implementations here are checked against
each other on all of it:

- The river is columns {1,2} and {4,5} across rows {3,4,5}. A **horizontal**
  (column-axis) leap crosses **2** river squares; a **vertical** (row-axis) leap
  crosses **3**. The Lion makes both; the **Tiger makes the horizontal one only.**
- A Rat of *either* colour on any water square along the path blocks a leap.
- A Rat on land may capture the Elephant; the Elephant may not capture a Rat.
- Captures never cross the water/land boundary, in either direction. This is what
  makes a Rat in the river safe from the bank — and what stops it taking an
  Elephant standing there.
- A piece in an **enemy** trap has its rank reduced to 0 **for defence only**: it
  can be taken by any adjacent enemy, the Elephant included, but it still attacks
  at its own full rank. Vulnerable, not disarmed. A piece in its *own* trap is
  unaffected.
- Win by den entry or by capturing every enemy piece; having no legal move loses;
  100 plies without a capture is a draw.

## Credits

- Game: traditional Jungle / Dou Shou Qi
  ([rules](https://en.wikipedia.org/wiki/Jungle_(board_game)))
- **Built with Claude Code (Anthropic).** The Rust engine this port ships was
  developed in the desktop project, also with Claude Code.
