// Optional real Chromium checks; needs Node + Playwright, never a runtime dependency.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.BISTRO_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
async function setup(t, ai = false) {
  const server = spawn(python, [path.join(__dirname, 'browser_test_server.py'), ...(ai ? ['--ai'] : [])], {cwd: root});
  let stderr = ''; server.stderr.on('data', data => { stderr += data; });
  const [data] = await once(server.stdout, 'data');
  const url = data.toString().trim();
  for (let i = 0; i < 100; i++) { try { if ((await fetch(url + '/health')).ok) break; } catch (_) {} await new Promise(resolve => setTimeout(resolve, 20)); }
  const browser = await chromium.launch({headless: true, ...(process.env.BISTRO_CHROMIUM ? {executablePath: process.env.BISTRO_CHROMIUM} : {}), args: ['--no-sandbox']});
  t.after(async () => { await browser.close(); server.kill(); await once(server, 'exit'); });
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], stderr));
  return {page, url};
}
async function start(page, url) {
  await page.goto(url);
  await page.getByLabel('Your table number').fill('3');
  await page.getByRole('button', {name: 'Start session'}).click();
  await page.waitForFunction(() => !document.querySelector('#menu-items button').disabled);
}
async function idle(page) { await page.waitForFunction(() => !busy); }

test('No Ollama: only chat is disabled and complete deterministic ordering works', {timeout: 30000}, async t => {
  const {page, url} = await setup(t);
  await start(page, url);
  await page.waitForFunction(() => document.querySelector('#ai-status').textContent.includes('AI unavailable'));
  assert(await page.getByLabel('Message your waiter').isDisabled());
  await page.locator('#categories').getByRole('button', {name: 'Mains', exact: true}).click();
  const add = page.getByRole('button', {name: 'Add Chicken Adobo to order'});
  assert(await add.isEnabled()); await add.click(); await idle(page);
  const qty = page.locator('#cart-items').getByLabel('Quantity of Chicken Adobo');
  await qty.fill('3'); await page.locator('#cart-items').getByRole('button', {name: 'Update', exact: true}).click(); await idle(page);
  await page.getByRole('button', {name: 'Review order', exact: false}).click(); await idle(page);
  assert(await page.getByRole('button', {name: 'Confirm & place order'}).isEnabled());
  await page.getByRole('button', {name: 'Confirm & place order'}).click(); await idle(page);
  assert(await page.getByRole('heading', {name: 'Your order is confirmed'}).isVisible());
  const receipt = await page.locator('#receipt-meta').textContent();
  await page.reload(); await page.locator('#receipt').waitFor({state: 'visible'});
  assert.equal(await page.locator('#receipt-meta').textContent(), receipt);
});

test('Failed chat and stalled AI recheck cannot lock menu or cart controls', {timeout: 30000}, async t => {
  const {page, url} = await setup(t, true);
  await start(page, url); await page.waitForFunction(() => !document.querySelector('#send').disabled);
  // Simulate Ollama disappearing after an initially successful status probe.
  await page.route('**/api/chat', route => route.fulfill({contentType: 'application/json', status: 503, body: JSON.stringify({ok: false, code: 'ollama_unavailable', message: 'Cannot reach Ollama.'})}));
  let release; const stalled = new Promise(resolve => { release = resolve; });
  await page.route('**/api/ai-status', async route => { await stalled; await route.abort().catch(() => {}); });
  t.after(() => release());
  await page.getByLabel('Message your waiter').fill('Hello po'); await page.getByRole('button', {name: 'Send', exact: false}).click();
  await page.waitForFunction(() => !busy, null, {timeout: 1500});
  assert(await page.getByRole('button', {name: 'Add Chicken Adobo to order'}).isEnabled());
  assert.match(await page.locator('#ai-status').textContent(), /AI unavailable/);
  assert(await page.getByLabel('Message your waiter').isDisabled());
  await page.locator('#categories').getByRole('button', {name: 'Mains', exact: true}).click();
  await page.getByRole('button', {name: 'Add Chicken Adobo to order'}).click(); await idle(page);
  assert(await page.getByRole('button', {name: 'Review order', exact: false}).isEnabled());
  release();
});

test('AI mode still sends to the shared engine and synchronizes the cart', {timeout: 30000}, async t => {
  const {page, url} = await setup(t, true);
  await start(page, url); await page.waitForFunction(() => !document.querySelector('#send').disabled);
  await page.getByLabel('Message your waiter').fill('Dalawang Chicken Adobo po.');
  await page.getByLabel('Message your waiter').press('Enter'); await idle(page);
  assert.match(await page.locator('#messages').textContent(), /Order updated/);
  assert.equal(await page.locator('#cart-items').getByLabel('Quantity of Chicken Adobo').inputValue(), '2');
  assert(await page.getByRole('button', {name: 'Add Chicken Adobo to order'}).isEnabled());
});

test('Check again reprobes connectivity, reconnects, and ignores an older ready response', {timeout: 30000}, async t => {
  const {page, url} = await setup(t);
  await start(page, url);
  await page.waitForFunction(() => document.querySelector('#ai-status').textContent.includes('AI unavailable'));
  const endpoint = 'http://127.0.0.1:11434';
  let release; const delayed = new Promise(resolve => { release = resolve; });
  let calls = 0;
  await page.route('**/api/ai-status', async route => {
    const call = ++calls;
    if (call === 1) await delayed;
    await route.fulfill({contentType: 'application/json', body: JSON.stringify({available: call !== 2, message: call === 2 ? 'Cannot reach Ollama.' : 'Local AI is ready.', model: 'qwen2.5:3b', endpoint})}).catch(() => {});
  });
  t.after(() => release());
  await page.getByRole('button', {name: 'Check again'}).click();
  await page.getByRole('button', {name: 'Check again'}).click();
  await page.waitForFunction(() => document.querySelector('#ai-status').textContent.includes('Cannot reach Ollama'));
  release(); await page.waitForTimeout(100);
  assert.match(await page.locator('#ai-status').textContent(), /AI unavailable/);
  assert(await page.getByRole('button', {name: 'Add Chicken Adobo to order'}).isEnabled());
  await page.getByRole('button', {name: 'Check again'}).click();
  await page.waitForFunction(() => !document.querySelector('#send').disabled);
  assert.match(await page.locator('#ai-status').textContent(), /AI available/);
  assert(calls >= 3);
});
