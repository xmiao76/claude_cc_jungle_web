// Canvas renderer: board terrain, pieces, highlights, and animations.
// All engine coordinates are unflipped; flipping happens only in the
// board->pixel mapping (mirrors the desktop renderer's contract).

import { COLS, ROWS, boardToPixel, pieceAnimal, pieceColor } from './coords.js';

const MOVE_ANIM_MS = 220;      // same feel as the desktop renderer
const CAPTURE_FLASH_MS = 300;

// Terrain map, identical to config.py. terrain[row][col]:
// 0 land, 1 river, 2 trap, 3 den.
export const TERRAIN = (() => {
  const t = Array.from({ length: ROWS }, () => Array(COLS).fill(0));
  for (const [c, r] of [[1, 3], [2, 3], [1, 4], [2, 4], [1, 5], [2, 5],
                        [4, 3], [5, 3], [4, 4], [5, 4], [4, 5], [5, 5]]) t[r][c] = 1;
  for (const [c, r] of [[2, 0], [4, 0], [3, 1], [2, 8], [4, 8], [3, 7]]) t[r][c] = 2;
  t[0][3] = 3;   // Black den (top)
  t[8][3] = 3;   // Blue den (bottom)
  return t;
})();

const TILE_NAMES = ['land', 'river', 'trap', 'den'];

export class BoardRenderer {
  constructor(canvas, assets) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.assets = assets;         // {tiles: {name: Image}, pieces: {key: Image}}
    this.cellSize = 64;
    this.flipped = false;
    this.state = null;            // last engine state snapshot
    this.selected = null;         // [col, row] | null
    this.targets = [];            // legal moves for the selected piece
    this._animations = [];        // {pid, fc, fr, tc, tr, start}
    this._flashes = [];           // {col, row, start}
  }

  resize(cellSize) {
    const dpr = window.devicePixelRatio || 1;
    this.cellSize = cellSize;
    this.canvas.width = COLS * cellSize * dpr;
    this.canvas.height = ROWS * cellSize * dpr;
    this.canvas.style.width = `${COLS * cellSize}px`;
    this.canvas.style.height = `${ROWS * cellSize}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  startMoveAnimation(pid, fc, fr, tc, tr) {
    this._animations.push({ pid, fc, fr, tc, tr, start: performance.now() });
  }

  triggerCaptureFlash(col, row) {
    this._flashes.push({ col, row, start: performance.now() });
  }

  hasActiveAnimation(now = performance.now()) {
    return this._animations.some((a) => now - a.start < MOVE_ANIM_MS);
  }

  clearAnimations() {
    this._animations = [];
    this._flashes = [];
  }

  draw(now = performance.now()) {
    const { ctx, cellSize: cs } = this;
    this._animations = this._animations.filter((a) => now - a.start < MOVE_ANIM_MS);
    this._flashes = this._flashes.filter((f) => now - f.start < CAPTURE_FLASH_MS);

    ctx.clearRect(0, 0, COLS * cs, ROWS * cs);
    this._drawTiles();
    this._drawLastMove();
    this._drawSelection();
    this._drawPieces(now);
    this._drawFlashes(now);
  }

  _drawTiles() {
    const { ctx, cellSize: cs } = this;
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const [px, py] = boardToPixel(c, r, cs, this.flipped);
        const img = this.assets.tiles[TILE_NAMES[TERRAIN[r][c]]];
        if (img) {
          ctx.drawImage(img, px, py, cs, cs);
        } else {
          ctx.fillStyle = ['#8bb25a', '#4085c4', '#b48232', '#dcb43c'][TERRAIN[r][c]];
          ctx.fillRect(px, py, cs, cs);
        }
        ctx.strokeStyle = 'rgba(20, 20, 20, 0.35)';
        ctx.lineWidth = 1;
        ctx.strokeRect(px + 0.5, py + 0.5, cs - 1, cs - 1);
      }
    }
  }

  _drawLastMove() {
    const last = this.state && this.state.lastMove;
    if (!last) return;
    const { ctx, cellSize: cs } = this;
    ctx.fillStyle = 'rgba(255, 255, 160, 0.28)';
    for (const [c, r] of [[last.fc, last.fr], [last.tc, last.tr]]) {
      const [px, py] = boardToPixel(c, r, cs, this.flipped);
      ctx.fillRect(px, py, cs, cs);
    }
  }

  _drawSelection() {
    const { ctx, cellSize: cs } = this;
    if (this.selected) {
      const [px, py] = boardToPixel(this.selected[0], this.selected[1], cs, this.flipped);
      ctx.strokeStyle = '#ffd700';
      ctx.lineWidth = 3;
      ctx.strokeRect(px + 2, py + 2, cs - 4, cs - 4);
    }
    for (const m of this.targets) {
      const [px, py] = boardToPixel(m.tc, m.tr, cs, this.flipped);
      if (m.captured) {
        ctx.strokeStyle = 'rgba(220, 60, 50, 0.95)';
        ctx.lineWidth = 3;
        ctx.strokeRect(px + 3, py + 3, cs - 6, cs - 6);
      } else {
        ctx.fillStyle = 'rgba(90, 220, 90, 0.75)';
        ctx.beginPath();
        ctx.arc(px + cs / 2, py + cs / 2, cs * 0.13, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  _drawPieces(now) {
    if (!this.state) return;
    const { cellSize: cs } = this;
    const animating = new Map();
    for (const a of this._animations) {
      animating.set(`${a.tc},${a.tr}`, a);
    }
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const pid = this.state.board[r][c];
        if (pid === 0 || animating.has(`${c},${r}`)) continue;
        const [px, py] = boardToPixel(c, r, cs, this.flipped);
        this._drawPiece(pid, px, py);
      }
    }
    for (const a of this._animations) {
      const t = Math.min((now - a.start) / MOVE_ANIM_MS, 1);
      const ease = 1 - (1 - t) * (1 - t);
      const [fx, fy] = boardToPixel(a.fc, a.fr, cs, this.flipped);
      const [tx, ty] = boardToPixel(a.tc, a.tr, cs, this.flipped);
      this._drawPiece(a.pid, fx + (tx - fx) * ease, fy + (ty - fy) * ease);
    }
  }

  _drawPiece(pid, px, py) {
    const { ctx, cellSize: cs } = this;
    const animal = pieceAnimal(pid);
    const img = this.assets.pieces[`${animal}_${pieceColor(pid)}`];
    const pad = cs * 0.08;
    if (img) {
      ctx.drawImage(img, px + pad, py + pad, cs - pad * 2, cs - pad * 2);
    } else {
      ctx.fillStyle = pid > 0 ? '#3c78dc' : '#282828';
      ctx.beginPath();
      ctx.arc(px + cs / 2, py + cs / 2, cs * 0.4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#f0f0f0';
      ctx.font = `bold ${Math.round(cs * 0.4)}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(animal[0].toUpperCase(), px + cs / 2, py + cs / 2);
    }
    this._drawPieceLabel(animal, px, py);
  }

  // Small animal-name caption at the bottom of the cell, outlined so it
  // reads on both blue and black piece art at any cell size.
  _drawPieceLabel(animal, px, py) {
    const { ctx, cellSize: cs } = this;
    const name = animal[0].toUpperCase() + animal.slice(1);
    const fontPx = Math.max(8, Math.round(cs * 0.16));
    ctx.font = `600 ${fontPx}px "Segoe UI", system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.lineJoin = 'round';
    ctx.lineWidth = Math.max(2, Math.round(fontPx / 4));
    ctx.strokeStyle = 'rgba(15, 20, 12, 0.85)';
    ctx.strokeText(name, px + cs / 2, py + cs - 1);
    ctx.fillStyle = '#f4f7ee';
    ctx.fillText(name, px + cs / 2, py + cs - 1);
  }

  _drawFlashes(now) {
    const { ctx, cellSize: cs } = this;
    for (const f of this._flashes) {
      const t = (now - f.start) / CAPTURE_FLASH_MS;
      const [px, py] = boardToPixel(f.col, f.row, cs, this.flipped);
      ctx.fillStyle = `rgba(220, 50, 50, ${0.55 * (1 - t)})`;
      ctx.fillRect(px, py, cs, cs);
    }
  }
}

// Load every image asset up front; missing files fall back to shapes.
export async function loadAssets() {
  const tiles = {};
  const pieces = {};
  const load = (url) => new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
  await Promise.all([
    ...TILE_NAMES.map(async (n) => { tiles[n] = await load(`assets/tiles/${n}.png`); }),
    ...['rat', 'cat', 'dog', 'wolf', 'leopard', 'tiger', 'lion', 'elephant'].flatMap(
      (animal) => ['blue', 'black'].map(async (color) => {
        pieces[`${animal}_${color}`] = await load(`assets/pieces/${animal}_${color}.png`);
      })
    ),
  ]);
  return { tiles, pieces };
}
