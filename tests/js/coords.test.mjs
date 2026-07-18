// Unit tests for the pure coordinate/piece helpers (node --test).

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  COLS, ROWS, displaySquare, boardToPixel, pixelToBoard,
  pieceAnimal, pieceColor,
} from '../../public/js/coords.js';
import { TERRAIN } from '../../public/js/board-renderer.js';

const CELL = 64;

test('board dimensions are 7x9', () => {
  assert.equal(COLS, 7);
  assert.equal(ROWS, 9);
});

test('displaySquare is identity when not flipped', () => {
  assert.deepEqual(displaySquare(0, 0, false), [0, 0]);
  assert.deepEqual(displaySquare(3, 4, false), [3, 4]);
});

test('displaySquare mirrors both axes when flipped', () => {
  assert.deepEqual(displaySquare(0, 0, true), [6, 8]);
  assert.deepEqual(displaySquare(3, 4, true), [3, 4]);   // center is fixed
  assert.deepEqual(displaySquare(6, 8, true), [0, 0]);
});

test('pixelToBoard maps cell interiors, unflipped', () => {
  assert.deepEqual(pixelToBoard(1, 1, CELL, false), [0, 0]);
  assert.deepEqual(pixelToBoard(CELL * 6 + 10, CELL * 8 + 10, CELL, false), [6, 8]);
});

test('pixelToBoard mirrors when flipped (desktop input_handler contract)', () => {
  // Clicking the top-left cell on a flipped board selects engine square (6,8).
  assert.deepEqual(pixelToBoard(1, 1, CELL, true), [6, 8]);
  assert.deepEqual(pixelToBoard(CELL * 6 + 10, CELL * 8 + 10, CELL, true), [0, 0]);
});

test('pixelToBoard returns null outside the board', () => {
  assert.equal(pixelToBoard(-1, 10, CELL, false), null);
  assert.equal(pixelToBoard(CELL * 7 + 1, 10, CELL, false), null);
  assert.equal(pixelToBoard(10, CELL * 9 + 1, CELL, false), null);
});

test('boardToPixel/pixelToBoard round-trip for every square, both flips', () => {
  for (const flipped of [false, true]) {
    for (let c = 0; c < COLS; c++) {
      for (let r = 0; r < ROWS; r++) {
        const [px, py] = boardToPixel(c, r, CELL, flipped);
        assert.deepEqual(pixelToBoard(px + CELL / 2, py + CELL / 2, CELL, flipped),
                         [c, r], `square (${c},${r}) flipped=${flipped}`);
      }
    }
  }
});

test('piece id decoding matches the engine encoding', () => {
  assert.equal(pieceAnimal(1), 'rat');
  assert.equal(pieceAnimal(-8), 'elephant');
  assert.equal(pieceAnimal(7), 'lion');
  assert.equal(pieceColor(5), 'blue');
  assert.equal(pieceColor(-5), 'black');
});

test('terrain map matches config.py geometry', () => {
  assert.equal(TERRAIN[0][3], 3, 'Black den at (3,0)');
  assert.equal(TERRAIN[8][3], 3, 'Blue den at (3,8)');
  assert.equal(TERRAIN[1][3], 2, 'Black trap at (3,1)');
  assert.equal(TERRAIN[7][3], 2, 'Blue trap at (3,7)');
  const rivers = TERRAIN.flat().filter((t) => t === 1).length;
  assert.equal(rivers, 12, '12 river squares');
  const traps = TERRAIN.flat().filter((t) => t === 2).length;
  assert.equal(traps, 6, '6 trap squares');
});
