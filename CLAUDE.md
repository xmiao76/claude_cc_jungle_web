# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project Overview

Browser port of the Jungle (Dou Shou Qi) desktop game in `C:\GitHub\claude_cc_jungle`.
The Python engine/AI run client-side via Pyodide in a Web Worker; the UI is
vanilla JS + Canvas. Deployed as a static site to Cloudflare Pages (`public/`
is the site root — nothing outside it is deployed).

## Commands

| Task | Command |
|------|---------|
| Dev server | `npm run dev` (http://localhost:8788) |
| Python tests | `.venv/Scripts/python -m pytest` (139 tests) |
| JS unit tests | `npm run test:js` |
| Pyodide smoke test | `npm run test:pyodide` |
| E2E (Playwright) | `npm run test:e2e` |
| Deploy | `npm run deploy` (needs `wrangler login`) |

## Architecture

- `public/py/engine/`, `public/py/ai/`, `public/py/config.py` — **verbatim
  copies of the desktop repo (engine v1.5). Do not fork rule/search logic
  here; port changes from the desktop repo instead, then re-run pytest.**
- `public/py/web_api.py` — web-only JSON bridge. All functions return
  `{"ok", "data", "error"}` JSON strings so no PyProxies cross the boundary.
  Owns the single game session (GameState + one AIPlayer per color).
- `public/js/engine-worker.js` — classic Web Worker; pins Pyodide 0.27.7 from
  jsDelivr, fetches the `PY_FILES` list into Pyodide's FS, imports `web_api`.
  **When adding a Python file, add it to `PY_FILES` in both the worker and
  `tests/pyodide/smoke.mjs`.**
- `public/js/main.js` — UI state machine (web equivalent of desktop
  `controller.py`). Game-generation counter (`this.gen`) drops stale worker
  replies after New Game/Menu. Blue always moves first; `playerFirst` decides
  who controls Blue; board flip is visual-only (engine always unflipped).
- `public/js/coords.js` — pure mapping helpers (unit-tested); board is
  7 cols × 9 rows, Blue at row 8 (bottom), piece id = ±rank.

## Key invariants (from the desktop project — keep them)

- Eval symmetry: `evaluate(BLUE) == -evaluate(BLACK)` (pinned by tests).
- Fixed-depth signature tests in `tests/python/test_strength.py` pin engine
  behavior byte-for-byte; if one fails, a change leaked outside its flag.
- The flip option must never mutate game state — mapping only.

## Testing notes

- pytest pythonpath is `public/py` + repo root (see `pyproject.toml`).
- E2E downloads Pyodide from the CDN — needs network; first run:
  `npx playwright install chromium`.
