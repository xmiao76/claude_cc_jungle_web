// Engine Web Worker: hosts Pyodide + the Python engine/AI so the UI thread
// never blocks while the AI thinks. Classic worker (importScripts) for broad
// browser support.

'use strict';

const PYODIDE_VERSION = '0.27.7';
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

// Python sources served alongside the site, loaded into Pyodide's FS.
const PY_FILES = [
  'config.py',
  'web_api.py',
  'engine/__init__.py',
  'engine/pieces.py',
  'engine/board.py',
  'engine/rules.py',
  'engine/move_generator.py',
  'engine/game_state.py',
  'ai/__init__.py',
  'ai/search_config.py',
  'ai/transposition.py',
  'ai/see.py',
  'ai/evaluator.py',
  'ai/opening_book.py',
  'ai/minimax.py',
];

let api = null;         // pyimport('web_api') proxy
let initPromise = null;

function post(msg) { self.postMessage(msg); }
function progress(stage) { post({ type: 'progress', stage }); }

async function initPyodide() {
  progress('runtime');
  importScripts(PYODIDE_BASE + 'pyodide.js');
  const pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });

  progress('engine');
  const base = new URL('../py/', self.location).href;
  const sources = await Promise.all(
    PY_FILES.map(async (path) => {
      const res = await fetch(base + path);
      if (!res.ok) throw new Error(`failed to fetch ${path}: ${res.status}`);
      return [path, await res.text()];
    })
  );
  pyodide.FS.mkdirTree('/game/engine');
  pyodide.FS.mkdirTree('/game/ai');
  for (const [path, text] of sources) {
    pyodide.FS.writeFile('/game/' + path, text);
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/game')");

  progress('import');
  api = pyodide.pyimport('web_api');
  return JSON.parse(api.engine_info());
}

// Bridge calls: every web_api function returns a JSON envelope string.
function callApi(type, payload) {
  switch (type) {
    case 'new_game':
      return api.new_game(payload.difficulty);
    case 'apply_move':
      return api.apply_move(payload.fc, payload.fr, payload.tc, payload.tr);
    case 'ai_move':
      return api.ai_move(payload.budgetMs ?? null);
    case 'undo':
      return api.undo_for_human(payload.humanColor);
    case 'get_state':
      return api.get_state();
    case 'replay':
      return api.replay_moves(JSON.stringify(payload.moves));
    default:
      throw new Error(`unknown message type: ${type}`);
  }
}

self.onmessage = async (ev) => {
  const { id, type, payload } = ev.data;
  try {
    if (type === 'init') {
      initPromise = initPromise || initPyodide();
      const info = await initPromise;
      post({ id, ok: true, data: info.data });
      return;
    }
    if (!api) throw new Error('engine not initialized');
    const envelope = JSON.parse(callApi(type, payload));
    if (envelope.ok) post({ id, ok: true, data: envelope.data });
    else post({ id, ok: false, error: envelope.error });
  } catch (err) {
    post({ id, ok: false, error: String(err && err.message || err) });
  }
};
