// Smoke test: loads the exact .wasm the browser is served, in Node, and plays
// moves at every difficulty. Also reports AI think times and search depth, so
// the per-difficulty budgets can be sanity-checked against a real build rather
// than against the numbers in a comment.
//
// This replaced a Pyodide smoke test that had to download a ~10 MB CPython build
// and keep its own duplicate list of fifteen .py files in step with the worker's.
// There is one artifact now and no list to drift.
//
// Usage: node tests/wasm/smoke.mjs   (run `npm run build:wasm` first)

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));
const WASM_DIR = join(ROOT, 'public', 'wasm');

let failures = 0;
function check(cond, label) {
  console.log(`${cond ? 'ok' : 'FAIL'} - ${label}`);
  if (!cond) failures++;
}

// The no-modules build defines a global `wasm_bindgen`; evaluate it the way a
// classic worker's importScripts would, rather than importing it as an ES module.
const glueSource = await readFile(join(WASM_DIR, 'jungle_wasm.js'), 'utf8').catch(() => {
  console.error('public/wasm/jungle_wasm.js is missing — run `npm run build:wasm` first.');
  process.exit(1);
});
const t0 = performance.now();
// eslint-disable-next-line no-new-func
const wasm_bindgen = new Function(`${glueSource}; return wasm_bindgen;`)();
await wasm_bindgen({ module_or_path: await readFile(join(WASM_DIR, 'jungle_wasm_bg.wasm')) });
wasm_bindgen.start();
console.log(`# engine loaded in ${Math.round(performance.now() - t0)}ms`);

const api = wasm_bindgen;
const call = (fn, ...args) => JSON.parse(api[fn](...args));

const info = call('engineInfo');
check(
  info.ok && info.data.engineVersion,
  `engineInfo: v${info.data.engineVersion}, backend ${info.data.backend}`
);

// Play the opening plies at each difficulty and time the AI.
for (const difficulty of [0, 1, 2]) {
  const res = call('newGame', difficulty);
  check(res.ok && res.data.state.legalMoves.length > 0, `newGame(${difficulty})`);
  const times = [];
  let state = res.data.state;
  for (let ply = 0; ply < 4 && !state.terminal; ply++) {
    const t = performance.now();
    const mv = call('aiMove');
    times.push(Math.round(performance.now() - t));
    check(mv.ok, `aiMove d${difficulty} ply ${ply}`);
    state = mv.data.state;
  }
  console.log(`# difficulty ${difficulty} think times (ms): ${times.join(', ')}`);
}

// Longer game at Easy: the engine must stay consistent deep into a game.
{
  const res = call('newGame', 0);
  check(res.ok, 'newGame(0) for long game');
  let state = res.data.state;
  let plies = 0;
  while (!state.terminal && plies < 120) {
    const mv = call('aiMove', 300);
    if (!mv.ok) { check(false, `long game aiMove failed at ply ${plies}: ${mv.error}`); break; }
    state = mv.data.state;
    plies++;
  }
  check(plies > 10, `long game played ${plies} plies (terminal=${state.terminal}` +
    (state.winner ? `, winner=${state.winner.color} by ${state.winner.reason})` : ')'));
}

// Bridge sanity: illegal move rejected, undo works.
{
  call('newGame', 0);
  check(!call('applyMove', 0, 8, 3, 3).ok, 'illegal move rejected');
  check(call('applyMove', 6, 6, 6, 5).ok, 'legal human move applied');
  check(call('aiMove').ok, 'ai reply');
  const undo = call('undoForHuman', 0);
  check(undo.ok && undo.data.state.plyCount === 0, 'undo restores start');
}

// Hard must actually use its clock. The Python engine's Easy and Medium set a
// time limit of 999,999 seconds and were bounded by nothing; this pins that the
// wasm build honours a budget on every level.
{
  call('newGame', 2);
  const t = performance.now();
  const mv = call('aiMove', 500);
  const dt = performance.now() - t;
  check(mv.ok, 'hard aiMove with a 500ms budget');
  check(dt < 2000, `hard aiMove respected its budget (${Math.round(dt)}ms for 500ms asked)`);
}

// What the browser actually gets. The engine reports its own depth and node
// count, so this is measured rather than asserted from a comment — and it is the
// only honest way to compare against the Python engine's 23-30k nps / depth 8-10,
// since that one ran through Pyodide and was slower again in a browser than on
// the host.
{
  call('newGame', 2);
  let nodes = 0;
  let ms = 0;
  const depths = [];
  for (let ply = 0; ply < 3; ply++) {
    const mv = call('aiMove', 2000);
    if (!mv.ok || !mv.data.search) { check(false, 'bench ply reported no search info'); break; }
    const s = mv.data.search;
    nodes += s.nodes;
    ms += s.timeMs;
    depths.push(s.depth);
  }
  const nps = ms > 0 ? Math.round((nodes / ms) * 1000) : 0;
  console.log(`# wasm bench: depth ${depths.join('/')}, ${nodes} nodes in ${ms}ms = ${nps} nps`);
  check(nps > 100_000, `wasm search rate is ${nps} nps (want > 100k)`);
  check(Math.max(...depths) >= 10, `wasm reaches depth ${Math.max(...depths)} (want >= 10)`);
}

console.log(failures ? `\n${failures} FAILURE(S)` : '\nall smoke checks passed');
process.exit(failures ? 1 : 0);
