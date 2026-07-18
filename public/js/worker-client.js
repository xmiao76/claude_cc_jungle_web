// Promise-based client for the engine worker. One request at a time is
// enforced by the worker's sequential message handling; responses are
// matched to requests by id.

export class EngineClient {
  constructor(onProgress) {
    this._worker = new Worker('js/engine-worker.js');
    this._nextId = 1;
    this._pending = new Map();
    this._onProgress = onProgress || (() => {});
    this._worker.onmessage = (ev) => this._onMessage(ev.data);
    this._worker.onerror = (ev) => this._failAll(ev.message || 'worker error');
  }

  _onMessage(msg) {
    if (msg.type === 'progress') {
      this._onProgress(msg.stage);
      return;
    }
    const pending = this._pending.get(msg.id);
    if (!pending) return;
    this._pending.delete(msg.id);
    if (msg.ok) pending.resolve(msg.data);
    else pending.reject(new Error(msg.error || 'engine error'));
  }

  _failAll(message) {
    for (const { reject } of this._pending.values()) {
      reject(new Error(message));
    }
    this._pending.clear();
  }

  _call(type, payload = {}, timeoutMs = 120000) {
    const id = this._nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(id);
        reject(new Error(`engine call '${type}' timed out`));
      }, timeoutMs);
      this._pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this._worker.postMessage({ id, type, payload });
    });
  }

  init() { return this._call('init', {}, 180000); }
  newGame(difficulty) { return this._call('new_game', { difficulty }); }
  applyMove(fc, fr, tc, tr) { return this._call('apply_move', { fc, fr, tc, tr }); }
  aiMove(budgetMs) { return this._call('ai_move', { budgetMs }); }
  undo(humanColor) { return this._call('undo', { humanColor }); }
  getState() { return this._call('get_state'); }
  replay(moves) { return this._call('replay', { moves }); }
}
