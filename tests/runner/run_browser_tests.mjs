// Browser tests for psagent.js against the mock USERMAINT page.
// Usage: node tests/runner/run_browser_tests.mjs   (needs python3 + playwright)
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const here = path.dirname(fileURLToPath(import.meta.url));
const PORT = 8765;
const BASE = `http://127.0.0.1:${PORT}`;
const HOME = `${BASE}/psc/ps/EMPLOYEE/HRMS/c/NUI_FRAMEWORK.PT_LANDINGPAGE.GBL`;

const server = spawn('python3', [path.join(here, 'e2e_server.py'), String(PORT)], { stdio: 'inherit' });
async function waitForServer() {
  for (let i = 0; i < 50; i++) {
    try { if ((await fetch(`${BASE}/health`)).ok) return; } catch {}
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error('server did not start');
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

async function actionFor(values) {
  const r = await fetch(`${BASE}/__test/action?values=${encodeURIComponent(JSON.stringify(values))}`);
  return r.json();
}
const saved = page => page.frameLocator('#psa-ws-frame').locator('body').evaluate(() => window.__mockSaved);

test('runner creates a full user (reload on Add, label/column fallbacks, grid rows)', async page => {
  const action = await actionFor({ user_id: 'velu', description: 'Velu Arumugam', emplid: 'KU0042',
    roles: 'EMPLOYEE, PeopleSoft User', primary_permission_list: 'HCCPEMPLOYEE' });
  const res = await page.evaluate(([a]) => PSAgent.run(a, { password: 'Secr3t!' }), [action]);
  assert.deepEqual(res, { status: 'success', message: 'Saved' });
  assert.deepEqual(await saved(page), {
    OPRID: 'VELU', DESCR: 'Velu Arumugam', SYMBOLICID: 'SYSADM1', PWD: 'Secr3t!', PWD2: 'Secr3t!',
    OPRCLASS: 'HCCPEMPLOYEE', IDTYPE: 'EMP', EMPLID: 'KU0042', ROLES: ['EMPLOYEE', 'PeopleSoft User'],
  });
});

test('runner stops on PeopleSoft message: user already exists', async page => {
  const action = await actionFor({ user_id: 'EXISTS', description: 'Dup' });
  const res = await page.evaluate(([a]) => PSAgent.run(a, { password: 'x' }), [action]);
  assert.equal(res.status, 'error');
  assert.match(res.message, /already exists/);
  assert.equal(res.failed_step, 'Adding user EXISTS');
});

test('runner stops on invalid role and never saves', async page => {
  const action = await actionFor({ user_id: 'VELU', description: 'Velu', roles: 'EMPLOYEE, BADROLE' });
  const res = await page.evaluate(([a]) => PSAgent.run(a, { password: 'x' }), [action]);
  assert.equal(res.status, 'error');
  assert.match(res.message, /Invalid value/);
  assert.equal(await saved(page), null);
});

test('runner refuses to run without the secret', async page => {
  const action = await actionFor({ user_id: 'VELU', description: 'Velu' });
  const res = await page.evaluate(([a]) => PSAgent.run(a, {}), [action]);
  assert.equal(res.status, 'error');
  assert.match(res.message, /Missing secret value for password/);
  assert.equal(res.failed_step, 'Set Password');
});

test('chat flow: ask -> confirm card -> password -> runs -> result; password never sent', async page => {
  await page.click('#psa-fab');
  await page.fill('#psa-input', 'create user profile named velu, Velu Arumugam, employee KU0042, roles EMPLOYEE and PeopleSoft User, primary HCCPEMPLOYEE');
  await page.press('#psa-input', 'Enter');
  const card = page.locator('.psa-card');
  await card.waitFor();
  const text = await card.innerText();
  for (const s of ['User ID', 'VELU', 'Symbolic ID', 'SYSADM1', 'KU0042', 'EMPLOYEE, PeopleSoft User']) assert.ok(text.includes(s), s);
  const ok = card.locator('.psa-ok');
  assert.equal(await ok.isDisabled(), true);
  await card.locator('[data-secret="password"]').fill('Tr0ub4dor&3');
  await card.locator('[data-confirm="password"]').fill('wrong');
  assert.equal(await ok.isDisabled(), true);           // mismatch keeps Confirm disabled
  await card.locator('[data-confirm="password"]').fill('Tr0ub4dor&3');
  await ok.click();
  await page.locator('.psa-bot', { hasText: 'was created' }).waitFor({ timeout: 60000 });
  assert.equal((await saved(page)).PWD, 'Tr0ub4dor&3');
  assert.match(await page.locator('#psa-ws-status').innerText(), /^Done/);
  const bodies = await (await fetch(`${BASE}/__test/bodies`)).json();
  assert.equal(bodies.length, 3);                       // chat, confirm, result
  assert.ok(bodies.every(b => !b.includes('Tr0ub4dor')), 'password leaked to the agent service');
  assert.equal(JSON.parse(bodies[2]).status, 'success');
});

let failed = 0;
try {
  await waitForServer();
  const browser = await chromium.launch();
  for (const t of tests) {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(HOME);
    await page.waitForFunction(() => window.PSAgent);
    try {
      await t.fn(page);
      assert.deepEqual(errors, []);
      console.log(`ok   - ${t.name}`);
    } catch (e) {
      failed++;
      console.log(`FAIL - ${t.name}\n${e.stack}`);
      await page.screenshot({ path: path.join(here, 'failure.png') });
    }
    await page.close();
  }
  await browser.close();
} finally {
  server.kill();
}
console.log(failed ? `${failed} failed` : 'all passed');
process.exit(failed ? 1 : 0);
