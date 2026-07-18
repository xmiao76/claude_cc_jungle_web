// UI controller: state machine wiring the engine worker, canvas renderer,
// input, audio, and DOM chrome together (web equivalent of controller.py).

import { pixelToBoard, COLS, ROWS } from './coords.js';
import { BoardRenderer, loadAssets } from './board-renderer.js';
import { EngineClient } from './worker-client.js';
import { AudioManager } from './audio.js';

const BLUE = 0;
const BLACK = 1;
const AVA_MOVE_DELAY_MS = 400;

const LOADING_STAGES = {
  runtime: 'Downloading Python runtime…',
  engine: 'Loading game engine…',
  import: 'Initializing AI…',
};

const $ = (id) => document.getElementById(id);

class App {
  constructor() {
    this.client = null;
    this.renderer = null;
    this.audio = new AudioManager();
    this.screen = 'loading';        // loading | menu | playing | gameover
    this.mode = 'hva';              // hva | ava
    this.difficulty = 1;
    this.playerFirst = true;
    this.humanColor = BLUE;
    this.state = null;              // latest engine snapshot
    this.aiThinking = false;
    this.avaPaused = false;
    this.gen = 0;                   // game generation: drops stale AI replies
    this.selected = null;
    this._undoInFlight = false;
  }

  // ------------------------------------------------------------------
  // Boot
  // ------------------------------------------------------------------

  async boot() {
    try {
      this.client = new EngineClient((stage) => {
        $('loading-stage').textContent = LOADING_STAGES[stage] || stage;
      });
      const [info, assets] = await Promise.all([this.client.init(), loadAssets()]);
      this.renderer = new BoardRenderer($('board'), assets);
      $('engine-info').textContent =
        `engine v${info.engineVersion} · Python ${info.python} in your browser`;
      this._bindUi();
      this._resize();
      window.addEventListener('resize', () => this._resize());
      this._showScreen('menu');
      requestAnimationFrame(() => this._frame());
      window.__jungle.ready = true;
    } catch (err) {
      this._fatal(`Failed to load the game engine: ${err.message}`);
    }
  }

  _bindUi() {
    for (const btn of document.querySelectorAll('.diff-btn')) {
      btn.addEventListener('click', () => {
        this.difficulty = Number(btn.dataset.d);
        this._syncMenu();
      });
    }
    $('first-toggle').addEventListener('change', (ev) => {
      this.playerFirst = ev.target.checked;
    });
    $('btn-start-hva').addEventListener('click', () => this.startGame('hva'));
    $('btn-start-ava').addEventListener('click', () => this.startGame('ava'));
    $('btn-undo').addEventListener('click', () => this.undo());
    $('btn-flip').addEventListener('click', () => this.toggleFlip());
    $('btn-mute').addEventListener('click', () => {
      const muted = this.audio.toggleMute();
      $('btn-mute').textContent = muted ? '🔇 Unmute' : '🔊 Mute';
    });
    $('btn-menu').addEventListener('click', () => this.toMenu());
    $('btn-pause').addEventListener('click', () => this.togglePause());
    $('btn-again').addEventListener('click', () => this.startGame(this.mode));
    $('btn-tomenu').addEventListener('click', () => this.toMenu());
    $('btn-reload').addEventListener('click', () => location.reload());
    $('board').addEventListener('click', (ev) => this._onBoardClick(ev));
    this._syncMenu();
  }

  _syncMenu() {
    for (const btn of document.querySelectorAll('.diff-btn')) {
      btn.classList.toggle('active', Number(btn.dataset.d) === this.difficulty);
    }
    $('first-toggle').checked = this.playerFirst;
  }

  _resize() {
    const panel = 260;
    const availW = Math.max(window.innerWidth - panel - 64, 240);
    const availH = Math.max(window.innerHeight - 48, 360);
    const cell = Math.max(36, Math.min(90, Math.floor(availW / COLS),
                                       Math.floor(availH / ROWS)));
    if (this.renderer) this.renderer.resize(cell);
  }

  _frame() {
    if (this.renderer && this.screen !== 'loading' && this.screen !== 'menu') {
      this.renderer.draw();
    }
    requestAnimationFrame(() => this._frame());
  }

  // ------------------------------------------------------------------
  // Screens
  // ------------------------------------------------------------------

  _showScreen(name) {
    this.screen = name;
    $('loading-overlay').hidden = name !== 'loading';
    $('menu-overlay').hidden = name !== 'menu';
    $('game-area').hidden = name === 'loading' || name === 'menu';
    $('gameover-overlay').hidden = name !== 'gameover';
  }

  _fatal(message) {
    $('error-text').textContent = message;
    $('error-overlay').hidden = false;
  }

  // ------------------------------------------------------------------
  // Game lifecycle
  // ------------------------------------------------------------------

  async startGame(mode) {
    this.gen++;
    const gen = this.gen;
    this.mode = mode;
    this.aiThinking = false;
    this.avaPaused = false;
    this.selected = null;
    try {
      const res = await this.client.newGame(this.difficulty);
      if (gen !== this.gen) return;
      this._setState(res.state);
      this.renderer.clearAnimations();
      // Blue always moves first; playerFirst decides who controls Blue.
      this.humanColor = this.playerFirst ? BLUE : BLACK;
      // Default the view so the human's pieces start at the bottom.
      this.renderer.flipped = mode === 'hva' && this.humanColor === BLACK;
      $('btn-pause').hidden = mode !== 'ava';
      $('btn-undo').hidden = mode !== 'hva';
      $('btn-pause').textContent = '⏸ Pause';
      this._showScreen('playing');
      this._updatePanel();
      if (mode === 'ava' || this.humanColor !== BLUE) {
        this._requestAiMove(gen);
      }
    } catch (err) {
      if (gen === this.gen) this._fatal(err.message);
    }
  }

  toMenu() {
    this.gen++;
    this.aiThinking = false;
    this.selected = null;
    if (this.renderer) this.renderer.clearAnimations();
    this._showScreen('menu');
  }

  togglePause() {
    this.avaPaused = !this.avaPaused;
    $('btn-pause').textContent = this.avaPaused ? '▶ Resume' : '⏸ Pause';
    if (!this.avaPaused && !this.aiThinking && this.screen === 'playing') {
      this._requestAiMove(this.gen);
    }
    this._updatePanel();
  }

  toggleFlip() {
    if (!this.renderer) return;
    this.renderer.flipped = !this.renderer.flipped;
  }

  async undo() {
    if (this.mode !== 'hva' || this.aiThinking || this._undoInFlight) return;
    if (!this.state || this.state.plyCount < 1) return;
    const gen = this.gen;
    this._undoInFlight = true;
    try {
      const res = await this.client.undo(this.humanColor);
      if (gen !== this.gen) return;
      this._setState(res.state);
      this.renderer.clearAnimations();
      this.selected = null;
      this._showScreen('playing');
      this._updatePanel();
    } catch {
      // Nothing to undo — ignore, like the desktop app.
    } finally {
      this._undoInFlight = false;
    }
  }

  // ------------------------------------------------------------------
  // Input (two-click model, mirroring gui/input_handler.py)
  // ------------------------------------------------------------------

  _onBoardClick(ev) {
    if (this.screen !== 'playing' || this.mode !== 'hva') return;
    if (this.aiThinking || !this.state || this.state.terminal) return;
    if (this.state.turn !== this.humanColor) return;

    const rect = $('board').getBoundingClientRect();
    const sq = pixelToBoard(ev.clientX - rect.left, ev.clientY - rect.top,
                            this.renderer.cellSize, this.renderer.flipped);
    if (!sq) { this._select(null); return; }
    const [col, row] = sq;

    if (this.selected) {
      const move = this.renderer.targets.find((m) => m.tc === col && m.tr === row);
      if (move) {
        this._select(null);
        this._applyHumanMove(move);
        return;
      }
    }

    const pid = this.state.board[row][col];
    const isOwn = pid !== 0 &&
      (pid > 0 ? BLUE : BLACK) === this.humanColor;
    if (isOwn) {
      const moves = this.state.legalMoves.filter((m) => m.fc === col && m.fr === row);
      this._select(moves.length ? [col, row] : null, moves);
    } else {
      this._select(null);
    }
  }

  _select(square, moves = []) {
    this.selected = square;
    this.renderer.selected = square;
    this.renderer.targets = square ? moves : [];
  }

  // ------------------------------------------------------------------
  // Moves
  // ------------------------------------------------------------------

  async _applyHumanMove(move) {
    const gen = this.gen;
    try {
      const res = await this.client.applyMove(move.fc, move.fr, move.tc, move.tr);
      if (gen !== this.gen) return;
      this._animateMove(res.move, res.moverPid, res.state);
      if (res.state.terminal) {
        this._finishGame(gen);
        return;
      }
      this._requestAiMove(gen);
    } catch (err) {
      if (gen === this.gen) this._fatal(err.message);
    }
  }

  async _requestAiMove(gen) {
    this.aiThinking = true;
    this._updatePanel();
    try {
      const res = await this.client.aiMove();
      if (gen !== this.gen) return;
      await this._waitForAnimation();
      if (gen !== this.gen) return;
      this.aiThinking = false;
      this._animateMove(res.move, res.moverPid, res.state);
      if (res.state.terminal) {
        this._finishGame(gen);
        return;
      }
      if (this.mode === 'ava' && !this.avaPaused) {
        setTimeout(() => {
          if (gen === this.gen && this.screen === 'playing' && !this.avaPaused) {
            this._requestAiMove(gen);
          }
        }, AVA_MOVE_DELAY_MS);
      }
      this._updatePanel();
    } catch (err) {
      if (gen === this.gen) { this.aiThinking = false; this._fatal(err.message); }
    }
  }

  _waitForAnimation() {
    return new Promise((resolve) => {
      const check = () => {
        if (!this.renderer.hasActiveAnimation()) resolve();
        else setTimeout(check, 40);
      };
      check();
    });
  }

  _animateMove(move, moverPid, newState) {
    this.renderer.startMoveAnimation(moverPid, move.fc, move.fr, move.tc, move.tr);
    if (move.captured) {
      this.renderer.triggerCaptureFlash(move.tc, move.tr);
      this.audio.play('capture');
    } else {
      this.audio.play('move');
    }
    this._setState(newState);
    this._updatePanel();
  }

  _finishGame(gen) {
    this.aiThinking = false;
    this.audio.play('win');
    const w = this.state.winner;
    let title, sub;
    const reasons = {
      den: 'den entry',
      elimination: 'all pieces captured',
      stalemate: 'opponent has no legal moves',
      fifty_move: '50 moves without a capture',
    };
    if (!w || w.color === null) {
      title = 'Draw';
      sub = reasons[w ? w.reason : 'fifty_move'];
    } else if (this.mode === 'hva') {
      title = w.color === this.humanColor ? '🎉 You win!' : 'AI wins';
      sub = `${w.color === BLUE ? 'Blue' : 'Black'} wins by ${reasons[w.reason]}`;
    } else {
      title = `${w.color === BLUE ? 'Blue' : 'Black'} wins`;
      sub = `by ${reasons[w.reason]}`;
    }
    // Let the final animation play out before the overlay appears.
    setTimeout(() => {
      if (gen === this.gen &&
          this.state && this.state.terminal && this.screen === 'playing') {
        $('gameover-title').textContent = title;
        $('gameover-sub').textContent = sub;
        this._showScreen('gameover');
        $('game-area').hidden = false;   // keep the final board visible
      }
    }, 450);
  }

  // ------------------------------------------------------------------
  // Panel
  // ------------------------------------------------------------------

  _setState(state) {
    this.state = state;
    this.renderer.state = state;
    window.__jungle.state = state;
  }

  _updatePanel() {
    if (!this.state) return;
    const turnName = this.state.turn === BLUE ? 'Blue' : 'Black';
    let status;
    if (this.state.terminal) {
      status = 'Game over';
    } else if (this.mode === 'ava') {
      status = this.avaPaused ? `Paused — ${turnName} to move`
                              : `${turnName} is thinking…`;
    } else if (this.aiThinking) {
      status = 'AI is thinking…';
    } else {
      status = this.state.turn === this.humanColor ? 'Your turn' : `${turnName} to move`;
    }
    $('status-text').textContent = status;
    $('status-spinner').hidden = !(this.aiThinking ||
      (this.mode === 'ava' && !this.avaPaused && !this.state.terminal));
    $('btn-undo').disabled = !(this.mode === 'hva' && !this.aiThinking &&
      this.state.plyCount >= 2 &&
      (this.state.turn === this.humanColor || this.state.terminal));

    const diffName = ['Easy', 'Medium', 'Hard'][this.difficulty];
    $('game-sub').textContent = this.mode === 'ava'
      ? `AI vs AI · ${diffName}`
      : `You play ${this.humanColor === BLUE ? 'Blue' : 'Black'} · ${diffName}`;

    $('captured-blue').textContent = this.state.captured.blue.join(', ') || '—';
    $('captured-black').textContent = this.state.captured.black.join(', ') || '—';

    const list = $('history-list');
    list.innerHTML = '';
    for (const entry of this.state.history) {
      const li = document.createElement('li');
      li.textContent = entry;
      list.appendChild(li);
    }
    list.scrollTop = list.scrollHeight;
  }
}

// E2E/test hooks: Playwright drives the app through this handle.
window.__jungle = { ready: false, state: null, app: null };

const app = new App();
window.__jungle.app = app;
app.boot();
