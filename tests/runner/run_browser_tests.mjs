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

const PYTHON = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const server = spawn(PYTHON, [path.join(here, 'e2e_server.py'), String(PORT)], { stdio: 'inherit' });
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
  assert.equal(res.failed_step, 'Set New Password');
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
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b);  // POSTs only
  assert.equal(bodies.length, 3);                       // chat, confirm, result
  assert.ok(bodies.every(b => !b.includes('Tr0ub4dor')), 'password leaked to the agent service');
  assert.equal(JSON.parse(bodies[2]).status, 'success');
});

test('record once by hand -> review -> save -> agent does it for a new user', async page => {
  await page.click('#psa-fab');
  await page.locator('#psa-rec').waitFor();             // visible for teachers only
  await page.click('#psa-rec');
  const rc = page.locator('.psa-card').last();
  await rc.locator('[data-k="title"]').fill('Create user (quick)');
  await rc.locator('[data-k="page"]').fill('MAINTAIN_SECURITY.USERMAINT.GBL');
  await rc.locator('.psa-ok').click();

  // The teacher does the transaction by hand in the workspace
  const f = page.frameLocator('#psa-ws-frame');
  await f.locator('#PSOPRDEFN_SRCH_OPRID').fill('KUMAR');
  await f.locator('#PTS_CFG_CL_WRK_PTS_ADD_BTN').click();
  await f.locator('#PSOPRDEFN_SYMBOLICID').selectOption('SYSADM1');  // after the full reload
  await f.locator('#PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT').fill('Rec0rdPw!');
  await f.locator('#PSUSRPRFL_WRK_PTPSWDCLEARTXTCONF').fill('Rec0rdPw!');
  await f.locator('#ICTAB_1').click();                               // ID tab
  await f.locator('#OPRALIASTYPE_X').selectOption('EMP');
  await f.locator('[id="ATTRVAL_X$0"]').fill('KU0099');
  await f.locator('[id="PSOPRDEFN_DVW_OPRDEFNDESC$0"]').click();     // focus: PS processes the EMPLID
  await page.waitForTimeout(800);
  await f.locator('[id="PSOPRDEFN_DVW_OPRDEFNDESC$0"]').fill('Kumar Raja');
  await f.locator('#ICTAB_2').click();                               // Roles tab
  await f.locator('[id="PSROLEUSER_VW_ROLENAME$0"]').fill('EMPLOYEE');
  await f.locator('[id="PSROLEUSER_VW$new$0$$0"]').click();
  await f.locator('[id="PSROLEUSER_VW_ROLENAME$1"]').fill('Manager');
  await f.locator('[id="#ICSave"]').click();
  await f.locator('#SAVED_win0').waitFor();
  assert.equal((await saved(page)).OPRID, 'KUMAR');
  await page.click('#psa-rec-done');

  // Review card: Claude's suggestions, one changed by the teacher
  const review = page.locator('.psa-card', { hasText: 'Review the recorded task' });
  await review.waitFor();
  const rtext = await review.innerText();
  for (const s of ['User ID', 'recorded: KUMAR', 'Roles (list)', 'recorded: EMPLOYEE, Manager', 'Password', '13 recorded steps'])
    assert.ok(rtext.includes(s), s);
  assert.equal(await review.locator('select[data-key="OPRALIASTYPE_X"]').inputValue(), 'fixed');
  await review.locator('select[data-key="ATTRVAL_X$0"]').selectOption('optional');
  await review.locator('.psa-ok').click();
  await page.locator('.psa-bot', { hasText: 'Saved Create User (quick)' }).waitFor();

  const yml = await (await fetch(`${BASE}/__test/task_file?id=create_user_quick`)).text();
  assert.match(yml, /^# Recorded with PSAgent teach mode by TESTER/);
  assert.match(yml, /when: emplid/);                     // ID tab only runs when an EMPLID is given
  assert.match(yml, /value: Employee/);                  // fixed value kept
  assert.ok(!yml.includes('Rec0rdPw'), 'recorded password leaked into the task');

  // Now the agent does it for a different user, using the recorded task
  await page.fill('#psa-input', 'create user anita, Anita Rao, roles EMPLOYEE and Manager');
  await page.press('#psa-input', 'Enter');
  const card = page.locator('.psa-card', { hasText: 'please review' });
  await card.waitFor();
  await card.locator('[data-secret="password"]').fill('An1ta!pw');
  await card.locator('[data-confirm="password"]').fill('An1ta!pw');
  await card.locator('.psa-ok').click();
  await page.locator('.psa-bot', { hasText: 'created with the recorded task' }).waitFor({ timeout: 60000 });
  assert.deepEqual(await saved(page), {
    OPRID: 'ANITA', DESCR: 'Anita Rao', SYMBOLICID: 'SYSADM1', PWD: 'An1ta!pw', PWD2: 'An1ta!pw',
    OPRCLASS: '', IDTYPE: 'NON', EMPLID: '', ROLES: ['EMPLOYEE', 'Manager'],
  });
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).join('\n');
  assert.ok(!bodies.includes('Rec0rdPw') && !bodies.includes('An1ta'), 'password reached the agent service');
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
