// Engine Web Worker: hosts the Rust engine compiled to WebAssembly, so the UI
// thread never blocks while the AI thinks. Classic worker (importScripts) for
// broad browser support, which is why the module is built with wasm-pack's
// `--target no-modules`.
//
// This replaced a Pyodide worker that downloaded a ~10 MB CPython build from a
// CDN and then wrote fifteen .py files into its virtual filesystem. The engine is
// now two same-origin files totalling under 100 KB, so the boot sequence is one
// fetch and one instantiate, and there is nothing left to keep in sync by hand.

'use strict';

let api = null;         // the wasm_bindgen exports
let initPromise = null;

function post(msg) { self.postMessage(msg); }
function progress(stage) { post({ type: 'progress', stage }); }

async function initEngine() {
  progress('engine');
  const base = new URL('../wasm/', self.location).href;
  importScripts(base + 'jungle_wasm.js');
  // `wasm_bindgen` is the global the no-modules build defines.
  await wasm_bindgen({ module_or_path: base + 'jungle_wasm_bg.wasm' });
  // Turn a Rust panic into a console error with a stack, rather than the bare
  // "unreachable executed" a release build would otherwise surface.
  wasm_bindgen.start();
  api = wasm_bindgen;
  return JSON.parse(api.engineInfo());
}

// Bridge calls: every engine function returns the same JSON envelope string the
// Python bridge returned, which is why nothing above this file had to change.
function callApi(type, payload) {
  switch (type) {
    case 'new_game':
      return api.newGame(payload.difficulty);
    case 'apply_move':
      return api.applyMove(payload.fc, payload.fr, payload.tc, payload.tr);
    case 'ai_move':
      return api.aiMove(payload.budgetMs ?? undefined);
    case 'undo':
      return api.undoForHuman(payload.humanColor);
    case 'get_state':
      return api.getState();
    case 'replay':
      return api.replayMoves(JSON.stringify(payload.moves));
    default:
      throw new Error(`unknown message type: ${type}`);
  }
}

self.onmessage = async (ev) => {
  const { id, type, payload } = ev.data;
  try {
    if (type === 'init') {
      initPromise = initPromise || initEngine();
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
