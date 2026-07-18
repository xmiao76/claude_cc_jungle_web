# Jungle (Dou Shou Qi) — Web Edition

Play the classic **Jungle / Dou Shou Qi** board game against an AI, entirely in
your browser. The original Python game engine and alpha-beta AI run **client-side
via [Pyodide](https://pyodide.org)** (CPython compiled to WebAssembly) inside a
Web Worker — no server, no backend, zero hosting cost on the
**Cloudflare Pages free tier**.

This is the web port of the Windows desktop app in `claude_cc_jungle`
(Python + Pygame). The `engine/` and `ai/` packages are reused **verbatim** —
a single Python source of truth for the rules and the AI — and only the
presentation layer was rewritten for the web (HTML/Canvas/JS).

## Live demo

**https://claude-jungle.pages.dev** — deployed on the Cloudflare Pages free tier.
(The project name derives from the repo name `claude_cc_jungle_web`; DNS
subdomains cannot contain underscores, so `claude_jungle` becomes
`claude-jungle`.)

## Features

- Human vs AI and AI-vs-AI (watch) modes
- Three difficulties: Easy (3-ply), Medium (5-ply), Hard (iterative deepening,
  ~2.5 s budget) — the desktop negamax/PVS engine, upgraded here to **v1.6**
  (internal iterative reduction, capture history, SEE pruning; adopted after a
  300-game self-play gate at 53.7% vs the frozen v1.5 control)
- Choose who moves first (Blue always opens; "You move first" decides who
  controls Blue). When the AI opens, the board auto-flips so you play from
  the bottom
- Two-click move input with legal-move indicators, capture highlights, move
  animation, capture flash, move history, captured-piece list, undo,
  board flip (visual only), sound effects with mute
- Loading screen while the Python runtime initializes (~10 MB first load,
  cached afterwards)

## Architecture

```
Main thread (JS)                     Web Worker
┌─────────────────────────┐  msgs   ┌────────────────────────────┐
│ main.js    UI state     │ ──────► │ engine-worker.js           │
│ board-renderer.js Canvas│ ◄────── │  Pyodide (Python 3.12 wasm)│
│ coords.js  mapping      │  JSON   │   web_api.py  JSON bridge  │
│ audio.js   Web Audio    │         │    engine/  game rules     │
│ worker-client.js        │         │    ai/      negamax PVS    │
└─────────────────────────┘         └────────────────────────────┘
```

- The worker owns the authoritative `GameState`; the UI never re-implements
  rules in JS (no rule drift).
- The AI searches inside the worker, so the page stays responsive — the web
  equivalent of the desktop app's AI thread.
- Board flip is purely visual: the engine always sees Blue at the bottom
  (row 8), exactly like the desktop renderer.

## Run locally

Requires Node ≥ 20 and Python ≥ 3.11 (Python only for tests).

```bash
npm run dev          # serves public/ at http://localhost:8788
```

## Tests

```bash
python -m venv .venv && .venv/Scripts/pip install pytest   # once (Windows)
npm install                                                # once

npm run test:py        # 139 pytest tests: full desktop engine/AI suite + web bridge
npm run test:js        # JS unit tests (coordinate mapping, terrain)
npm run test:pyodide   # engine smoke test under a real Pyodide runtime (Node)
npm run test:e2e       # Playwright: browser load, clicks, AI replies, full game
                       # (first time: npx playwright install chromium)
```

## Deploy to Cloudflare Pages (free)

Option A — Git integration (recommended):
1. Push this repo to GitHub.
2. Cloudflare dashboard → Workers & Pages → Create → Pages → connect the repo.
3. Build command: *(leave empty)* · Build output directory: `public`.
4. Every push to `main` deploys automatically.

Option B — Direct upload from the CLI:
```bash
npx wrangler login
npm run deploy       # wrangler pages deploy public --project-name claude-jungle
```

Post-deploy check: open the URL, wait for the loading screen to finish, play a
few moves on Easy, and confirm no errors in the browser console.

## Project layout

```
public/            deployed static site
  index.html       UI shell
  css/, js/        presentation layer (Canvas renderer, worker client)
  js/engine-worker.js  Pyodide host (pinned v0.27.7 from jsDelivr CDN)
  py/              Python sources loaded into Pyodide
    config.py      constants (verbatim from desktop)
    engine/        rules, board, move generation (verbatim)
    ai/            negamax PVS search, evaluator (verbatim)
    web_api.py     JSON bridge for the worker (web-only)
  assets/          piece/tile art + sounds (from the desktop release)
tests/python/      pytest: desktop suite + web_api bridge tests
tests/js/          node --test unit tests
tests/pyodide/     smoke test under real Pyodide (Node)
tests/e2e/         Playwright browser tests
tools/             dev-only: static server, strength harness (not deployed)
```

## Rules interpretation

Identical to the desktop engine (`engine/rules.py`): Lion jumps rivers
horizontally (3 squares) and vertically (2); Tiger jumps vertically only; a
Rat on any water square along the path blocks the jump; Rat may capture the
Elephant from land; pieces in an enemy trap are rank-0; win by den entry or
capturing every enemy piece; stalemate loses; 100 plies without a capture is
a draw.

## Credits

- Game: traditional Jungle / Dou Shou Qi
  ([rules](https://en.wikipedia.org/wiki/Jungle_(board_game)))
- **Built with Claude Code using the Claude Fable 5 model (Anthropic).**
  The desktop engine this port reuses was also developed with Claude Code.
