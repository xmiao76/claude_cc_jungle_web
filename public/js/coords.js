// Pure board/pixel coordinate mapping. Mirrors the desktop
// gui/input_handler.pixel_to_board contract: flipping is visual-only —
// the engine always sees an unflipped board (Blue at row 8, Black at row 0).

export const COLS = 7;
export const ROWS = 9;

// Engine square -> the square where it is DRAWN (identity unless flipped).
export function displaySquare(col, row, flipped) {
  return flipped ? [COLS - 1 - col, ROWS - 1 - row] : [col, row];
}

// Engine square -> top-left pixel of its cell on the canvas.
export function boardToPixel(col, row, cellSize, flipped) {
  const [dc, dr] = displaySquare(col, row, flipped);
  return [dc * cellSize, dr * cellSize];
}

// Canvas pixel -> engine (col, row), or null if outside the board.
export function pixelToBoard(px, py, cellSize, flipped) {
  const col = Math.floor(px / cellSize);
  const row = Math.floor(py / cellSize);
  if (col < 0 || col >= COLS || row < 0 || row >= ROWS) return null;
  return flipped ? [COLS - 1 - col, ROWS - 1 - row] : [col, row];
}

// Piece id helpers (encoding: positive = Blue, negative = Black, |pid| = rank).
const ANIMALS = ['rat', 'cat', 'dog', 'wolf', 'leopard', 'tiger', 'lion', 'elephant'];

export function pieceAnimal(pid) {
  return ANIMALS[Math.abs(pid) - 1];
}

export function pieceColor(pid) {
  return pid > 0 ? 'blue' : 'black';
}
