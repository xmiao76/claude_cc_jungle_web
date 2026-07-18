// Pyodide smoke test: loads the exact Python sources served to browsers
// into a real Pyodide runtime (same WASM engine as the browser) and plays
// moves at every difficulty. Also reports AI think times so the web time
// budgets can be sanity-checked.
//
// Usage: node tests/pyodide/smoke.mjs

import { loadPyodide } from 'pyodide';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PY_ROOT = join(fileURLToPath(new URL('../..', import.meta.url)), 'public', 'py');
const PY_FILES = [
  'config.py', 'web_api.py',
  'engine/__init__.py', 'engine/pieces.py', 'engine/board.py',
  'engine/rules.py', 'engine/move_generator.py', 'engine/game_state.py',
  'ai/__init__.py', 'ai/search_config.py', 'ai/transposition.py',
  'ai/see.py', 'ai/evaluator.py', 'ai/opening_book.py', 'ai/minimax.py',
];

let failures = 0;
function check(cond, label) {
  console.log(`${cond ? 'ok' : 'FAIL'} - ${label}`);
  if (!cond) failures++;
}

const t0 = performance.now();
const pyodide = await loadPyodide();
console.log(`# pyodide loaded in ${Math.round(performance.now() - t0)}ms`);

pyodide.FS.mkdirTree('/game/engine');
pyodide.FS.mkdirTree('/game/ai');
for (const path of PY_FILES) {
  pyodide.FS.writeFile('/game/' + path, await readFile(join(PY_ROOT, path), 'utf8'));
}
pyodide.runPython("import sys; sys.path.insert(0, '/game')");

const api = pyodide.pyimport('web_api');
const call = (fn, ...args) => JSON.parse(api[fn](...args));

const info = call('engine_info');
check(info.ok && info.data.engineVersion, `engine_info: v${info.data.engineVersion}, Python ${info.data.python}`);

// Play the opening plies at each difficulty and time the AI.
for (const difficulty of [0, 1, 2]) {
  const res = call('new_game', difficulty);
  check(res.ok && res.data.state.legalMoves.length > 0, `new_game(${difficulty})`);
  const times = [];
  let state = res.data.state;
  for (let ply = 0; ply < 4 && !state.terminal; ply++) {
    const t = performance.now();
    const mv = call('ai_move');
    times.push(Math.round(performance.now() - t));
    check(mv.ok, `ai_move d${difficulty} ply ${ply}`);
    state = mv.data.state;
  }
  console.log(`# difficulty ${difficulty} think times (ms): ${times.join(', ')}`);
}

// Longer game at Easy: engine must stay consistent deep into a game.
{
  const res = call('new_game', 0);
  check(res.ok, 'new_game(0) for long game');
  let state = res.data.state;
  let plies = 0;
  while (!state.terminal && plies < 120) {
    const mv = call('ai_move', 300);
    if (!mv.ok) { check(false, `long game ai_move failed at ply ${plies}: ${mv.error}`); break; }
    state = mv.data.state;
    plies++;
  }
  check(plies > 10, `long game played ${plies} plies (terminal=${state.terminal}` +
    (state.winner ? `, winner=${state.winner.color} by ${state.winner.reason})` : ')'));
}

// Bridge sanity: illegal move rejected, undo works.
{
  call('new_game', 0);
  check(!call('apply_move', 0, 8, 3, 3).ok, 'illegal move rejected');
  check(call('apply_move', 6, 6, 6, 5).ok, 'legal human move applied');
  check(call('ai_move').ok, 'ai reply');
  const undo = call('undo_for_human', 0);
  check(undo.ok && undo.data.state.plyCount === 0, 'undo restores start');
}

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall smoke checks passed');
process.exit(failures ? 1 : 0);
