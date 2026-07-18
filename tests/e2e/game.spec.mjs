// End-to-end tests: real browser, real Pyodide from the CDN, real engine.

import { test, expect } from '@playwright/test';

// Pyodide download + init dominates; do it once per test via cache.
async function waitForMenu(page) {
  await page.goto('/');
  await expect(page.locator('#menu-overlay')).toBeVisible({ timeout: 150_000 });
}

test('loads and initializes the Python engine', async ({ page }) => {
  await waitForMenu(page);
  await expect(page.locator('#engine-info')).toContainText('Python');
  await expect(page.locator('#error-overlay')).toBeHidden();
});

test('human move via clicks gets an AI reply', async ({ page }) => {
  await waitForMenu(page);
  await page.click('.diff-btn[data-d="0"]');          // Easy
  await page.click('#btn-start-hva');
  await expect(page.locator('#game-area')).toBeVisible();
  await expect(page.locator('#status-text')).toHaveText('Your turn');

  // Click the Blue rat at (6,6), then its target (6,5) — legal from the start.
  const cell = await page.evaluate(() => window.__jungle.app.renderer.cellSize);
  const board = page.locator('#board');
  await board.click({ position: { x: 6.5 * cell, y: 6.5 * cell } });
  await board.click({ position: { x: 6.5 * cell, y: 5.5 * cell } });

  // Human ply + AI reply must both land.
  await expect.poll(
    () => page.evaluate(() => window.__jungle.state?.plyCount),
    { timeout: 30_000 },
  ).toBeGreaterThanOrEqual(2);
  await expect(page.locator('#history-list li')).toHaveCount(
    await page.evaluate(() => window.__jungle.state.plyCount));
  await expect(page.locator('#error-overlay')).toBeHidden();
});

test('undo returns the game to the human turn', async ({ page }) => {
  await waitForMenu(page);
  await page.click('.diff-btn[data-d="0"]');
  await page.click('#btn-start-hva');
  const cell = await page.evaluate(() => window.__jungle.app.renderer.cellSize);
  const board = page.locator('#board');
  await board.click({ position: { x: 6.5 * cell, y: 6.5 * cell } });
  await board.click({ position: { x: 6.5 * cell, y: 5.5 * cell } });
  await expect.poll(
    () => page.evaluate(() => window.__jungle.state?.plyCount),
    { timeout: 30_000 },
  ).toBeGreaterThanOrEqual(2);

  await expect(page.locator('#btn-undo')).toBeEnabled();
  await page.click('#btn-undo');
  await expect.poll(
    () => page.evaluate(() => window.__jungle.state?.plyCount),
  ).toBe(0);
  await expect(page.locator('#status-text')).toHaveText('Your turn');
});

test('full AI-vs-AI game reaches a valid terminal state in-browser', async ({ page }) => {
  await waitForMenu(page);
  // Drive the engine stack directly (worker + Pyodide + engine) for speed;
  // UI-level interaction is covered by the click test above.
  const result = await page.evaluate(async () => {
    const client = window.__jungle.app.client;
    let { state } = await client.newGame(0);
    let plies = 0;
    while (!state.terminal && plies < 200) {
      ({ state } = await client.aiMove(300));
      plies++;
    }
    return { terminal: state.terminal, winner: state.winner, plies };
  });
  expect(result.terminal).toBe(true);
  expect(result.winner).not.toBeNull();
  expect(['den', 'elimination', 'stalemate', 'fifty_move'])
    .toContain(result.winner.reason);
});
