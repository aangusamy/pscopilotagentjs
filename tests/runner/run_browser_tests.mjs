// Browser tests for psagent.js against the mock USERMAINT page.
// Usage: node tests/runner/run_browser_tests.mjs   (needs python3 + playwright)
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';
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
/* PSA_SNAP=<folder>: save screenshots of the main screens there as the tests run */
const snap = (page, name) => process.env.PSA_SNAP ? page.screenshot({ path: path.join(process.env.PSA_SNAP, name + '.png') }) : null;

async function actionFor(values) {
  const r = await fetch(`${BASE}/__test/action?values=${encodeURIComponent(JSON.stringify(values))}`);
  return r.json();
}
const saved = (page, frame = '#psa-ws-frame') => page.frameLocator(frame).locator('body').evaluate(() => window.__mockSaved);
const modalOpen = page => page.evaluate(() => { const e = document.querySelector('#pt_modals #alertmsg'); return !!e && !!e.offsetHeight; });

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

test('assign role: the grid step adds a row and never replaces a role', async page => {
  const assign = (roles, role) => page.evaluate(([roles, role]) => PSAgent.run({
    run_id: 't', title: 'Assign role', component: { menu: 'MAINTAIN_SECURITY', component: 'USERMAINT', market: 'GBL' },
    steps: [
      { do: 'navigate', query: { Action: 'A', OPRID: 'RT1', ROLES: roles } },
      { do: 'tab', label: 'Roles' },
      { do: 'grid', field: { id: 'PSROLEUSER_VW_ROLENAME', column: 'Role Name' }, values: [role] },
      { do: 'click', button: { id: '#ICSave', text: 'Save' } },
      { do: 'expect_saved' },
    ] }, {}), [roles, role]);
  assert.deepEqual(await assign('PeopleSoft User', 'EMPLOYEE'), { status: 'success', message: 'Saved' });
  assert.deepEqual((await saved(page)).ROLES, ['PeopleSoft User', 'EMPLOYEE'], 'one role: a row is added');
  await assign('PeopleSoft User,EMPLOYEE', 'Manager');
  assert.deepEqual((await saved(page)).ROLES, ['PeopleSoft User', 'EMPLOYEE', 'Manager'], 'two roles: the second stays');
  await assign('PeopleSoft User,EMPLOYEE', 'EMPLOYEE');
  assert.deepEqual((await saved(page)).ROLES, ['PeopleSoft User', 'EMPLOYEE'], 'already there: no second row');
  const bad = await assign('PeopleSoft User', 'BADROLE');
  assert.equal(bad.status, 'error');
  assert.match(bad.message, /Invalid value/);
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
  assert.match(res.message, /Invalid value/);       // the box is drawn in the TOP window, like PeopleSoft does
  assert.equal(res.failed_step, 'Fill Role Name rows');
  assert.equal(await modalOpen(page), false);         // OK-only box closed after its text was captured
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

test('record in the real UI across page loads -> review -> save -> agent does it for a new user', async page => {
  await page.click('#psa-fab');
  await page.locator('#psa-rec').waitFor();             // visible for teachers only
  await page.click('#psa-rec');
  const rc = page.locator('.psa-card').last();
  assert.equal(await rc.locator('[data-k="page"]').inputValue(), '');   // the homepage is never offered
  await rc.locator('[data-k="title"]').fill('Create user (quick)');
  await rc.locator('[data-k="page"]').fill('NUI_FRAMEWORK.PT_LANDINGPAGE.GBL');
  assert.equal(await rc.locator('.psa-ok').isDisabled(), true);
  assert.match(await rc.innerText(), /homepage or navigation page/);
  await rc.locator('[data-k="page"]').fill('');                          // the page is optional
  await rc.locator('.psa-ok').click();
  const bar = page.locator('#psa-recbar');
  await bar.waitFor();
  assert.match(await bar.innerText(), /Go to the page/);

  // The teacher navigates the normal way (a full page load, like the NavBar does)
  await page.goto(`${BASE}/psp/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.USERMAINT.GBL`);
  await bar.waitFor();                                                   // recording resumed after the page load
  await page.waitForFunction(() => /steps on USERMAINT/.test(document.querySelector('#psa-recbar').textContent));
  await page.click('#PT_NAVBAR');                                        // portal header: not part of the task
  const f = page.frameLocator('#ptifrmtgtframe');
  await f.locator('#PTS_CFG_CL_WRK_PTS_PAGE_TOGGLE').click();           // Find -> Add a New Value: becomes Action=A
  await f.locator('#PTS_CFG_CL_WRK_PTS_ADD_BTN').waitFor();
  await f.locator('#PSOPRDEFN_SRCH_OPRID').fill('KUMAR');
  await f.locator('#PTS_CFG_CL_WRK_PTS_ADD_BTN').click();
  await f.locator('#PSOPRDEFN_SYMBOLICID').selectOption('SYSADM1');      // after the frame's full reload
  await f.locator('#PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT').fill('Rec0rdPw!');
  await f.locator('#PSUSRPRFL_WRK_PTPSWDCLEARTXTCONF').fill('Rec0rdPw!');
  await f.locator('#ICTAB_1').click();                                   // ID tab
  await f.locator('#OPRALIASTYPE_X').selectOption('EMP');
  await f.locator('[id="ATTRVAL_X$0"]').fill('KU0099');
  await f.locator('[id="PSOPRDEFN_DVW_OPRDEFNDESC$0"]').click();         // focus: PS processes the EMPLID
  await page.waitForTimeout(800);
  await f.locator('[id="PSOPRDEFN_DVW_OPRDEFNDESC$0"]').fill('Kumar Raja');
  await f.locator('#ICTAB_2').click();                                   // Roles tab
  await f.locator('[id="PSROLEUSER_VW_ROLENAME$0"]').fill('EMPLOYEE');
  await f.locator('[id="PSROLEUSER_VW$new$0$$0"]').click();
  await f.locator('[id="PSROLEUSER_VW_ROLENAME$1"]').fill('Manager');
  await f.locator('[id="#ICSave"]').click();
  await f.locator('#SAVED_win0').waitFor();
  assert.equal((await saved(page, '#ptifrmtgtframe')).OPRID, 'KUMAR');
  await page.waitForFunction(() => /Saved/.test(document.querySelector('#psa-recbar').textContent));   // seen on the next check
  assert.match(await bar.innerText(), /14 steps on USERMAINT · Saved/);   // PeopleSoft's Saved was seen
  await page.click('#psa-recbar-finish');
  assert.equal(await bar.count(), 0);

  // Review card: Claude's suggestions, one changed by the teacher
  const review = page.locator('.psa-card', { hasText: 'Review the recorded task' });
  await review.waitFor();
  const rtext = await review.innerText();
  for (const s of ['User ID', 'recorded: KUMAR', 'Roles (list)', 'recorded: EMPLOYEE, Manager', 'Password', '14 recorded steps', 'shows Saved'])
    assert.ok(rtext.includes(s), s);
  assert.equal(await review.locator('select[data-key="OPRALIASTYPE_X"]').inputValue(), 'fixed');
  await review.locator('select[data-key="ATTRVAL_X$0"]').selectOption('optional');
  await review.locator('.psa-ok').click();
  await page.locator('.psa-bot', { hasText: 'Saved Create User (quick)' }).waitFor();

  const yml = await (await fetch(`${BASE}/__test/task_file?id=create_user_quick`)).text();
  assert.match(yml, /^# Recorded with PSAgent teach mode by TESTER/);
  assert.match(yml, /when: emplid/);                     // ID tab only runs when an EMPLID is given
  assert.match(yml, /value: Employee/);                  // fixed value kept
  assert.match(yml, /do: open\n  mode: add\n/);            // search page: Add with the typed key
  assert.match(yml, /do: expect_saved/);
  assert.ok(!yml.includes('PT_NAVBAR') && !yml.includes('Portal'), 'portal header or parameters leaked into the task');
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

test('record a process run (search page, Run, Process Scheduler pop-up) -> the agent reruns it', async page => {
  await page.click('#psa-fab');
  await page.locator('#psa-rec').waitFor();
  await page.click('#psa-rec');
  const rc = page.locator('.psa-card').last();
  await rc.locator('[data-k="title"]').fill('Sync portal security');
  await rc.locator('.psa-ok').click();
  const bar = page.locator('#psa-recbar');
  await page.goto(`${BASE}/psp/ps/EMPLOYEE/HRMS/c/PORTAL_ADMIN.PORTAL_SEC_SYNC.GBL`);
  await page.waitForFunction(() => /steps on PORTAL_SEC_SYNC/.test(document.querySelector('#psa-recbar').textContent));
  const f = page.frameLocator('#ptifrmtgtframe');
  await f.locator('#PTS_CFG_CL_WRK_PTS_PAGE_TOGGLE').click();            // Find -> Add a New Value
  await f.locator('#PTS_CFG_CL_WRK_PTS_ADD_BTN').waitFor();
  await f.locator('#PRCSRUNCNTL_RUN_CNTL_ID').fill('SYNC_AGENT');
  await f.locator('#PTS_CFG_CL_WRK_PTS_ADD_BTN').click();
  await f.locator('#PRCSRQSTDLG_WRK_LOADPRCSRQSTDLGPB').click();         // Run
  await page.waitForFunction(() => {                                     // a person takes a moment to read the pop-up
    const fr = document.getElementById('ptModFrame_0');
    return fr && fr.contentDocument && fr.contentDocument._psaRec;
  });
  const dlg = page.frameLocator('#ptModFrame_0');
  await dlg.locator('#PRCSRQSTDLG_WRK_SERVERNAME').selectOption('PSUNX');
  const row = name => dlg.locator('tr', { hasText: name }).locator('input[type="checkbox"]');
  await row('EOAG_NOTIFY').check();                                       // wrong row, corrected
  await row('EOAG_NOTIFY').uncheck();
  await row('PORTAL_CSS').check();
  await dlg.locator('[id="#ICSave"]').click();                           // OK
  await f.locator('#PRCSRQSTDLG_WRK_PRCSINSTANCE', { hasText: '1001' }).waitFor();
  await page.waitForFunction(() => /process 1001 submitted/.test(document.querySelector('#psa-recbar').textContent));
  await f.locator('#PRCSRQSTDLG_WRK_LOADPRCSMONITORPB').click();         // looking at Process Monitor: not the task
  await page.click('#psa-recbar-finish');

  const review = page.locator('.psa-card', { hasText: 'Review the recorded task' });
  await review.waitFor();
  assert.equal(await review.locator('[data-k="open_mode"]').inputValue(), 'add_or_open');   // run controls are reused
  const rtext = await review.innerText();
  for (const s of ['Run Control ID', 'search key', 'recorded: SYNC_AGENT', 'process instance', '1 step after the task'])
    assert.ok(rtext.includes(s), s);
  await review.locator('.psa-ok').click();
  await page.locator('.psa-bot', { hasText: 'Saved Sync Portal Security' }).waitFor();

  const yml = await (await fetch(`${BASE}/__test/task_file?id=sync_portal_security`)).text();
  assert.match(yml, /do: open\n  mode: add_or_open\n  keys:\n  - field:\n      id: PRCSRUNCNTL_RUN_CNTL_ID/);
  assert.match(yml, /popup: true/);
  assert.match(yml, /row:\s+- Portal Cref Security Sync\s+- PORTAL_CSS\s/);      // the Select row, by its text
  assert.ok(!yml.includes('do: grid'), 'a fixed grid became a list');
  assert.ok(!yml.includes('EOAG_NOTIFY'), 'a ticked-and-unticked row stayed in the task');
  assert.match(yml, /- do: expect_process\n/);
  assert.ok(!yml.includes('expect_saved') && !yml.includes('Process Monitor') && !yml.includes('PTS_CFG_CL'), yml);

  // The run control exists now: the agent adds it, PeopleSoft says "already exists", it opens that one
  await page.fill('#psa-input', 'sync portal security');
  await page.press('#psa-input', 'Enter');
  const card = page.locator('.psa-card', { hasText: 'please review' });
  await card.waitFor();
  assert.ok((await card.innerText()).includes('SYNC_AGENT'));
  await card.locator('.psa-ok').click();
  await page.locator('.psa-bot', { hasText: 'portal security sync was submitted' }).waitFor({ timeout: 60000 });
  const run = await page.frameLocator('#psa-ws-frame').locator('body').evaluate(() => window.__mockRun);
  // the pop-up listed the processes in another order: the row was found by its text
  assert.deepEqual(run, { RUN_CNTL_ID: 'SYNC_AGENT', PORTAL: 'EMPLOYEE', SERVER: 'PSUNX', SELECTED: ['PORTAL_CSS'] });
  const result = JSON.parse((await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b).pop());
  assert.equal(result.status, 'success');
  assert.equal(result.message, 'Submitted: process instance 1002');
});

test('runner: a process request counts as submitted when the page shows no instance (AE requests)', async page => {
  await page.evaluate(() => sessionStorage.setItem('mock.noinst', '1'));
  const action = { run_id: 'ae', title: 'Run AE', component: { menu: 'PORTAL_ADMIN', component: 'PORTAL_SEC_SYNC', market: 'GBL' }, steps: [
    { do: 'open', mode: 'add_or_open', keys: [{ field: { id: 'PRCSRUNCNTL_RUN_CNTL_ID', label: 'Run Control ID' }, value: 'NOINST1' }] },
    { do: 'click', button: { id: 'PRCSRQSTDLG_WRK_LOADPRCSRQSTDLGPB', text: 'Run' } },
    { do: 'set', popup: true, field: { id: 'PRCSRQSTDLG_WRK_SELECT_FLAG$0', column: 'Select', row: ['AEMINITEST'] }, value: 'Y' },
    { do: 'click', popup: true, button: { id: '#ICSave', text: 'OK' } },
    { do: 'expect_process' },
  ] };
  const res = await page.evaluate(([a]) => PSAgent.run(a, {}), [action]);
  assert.equal(res.status, 'success', JSON.stringify(res));
  assert.match(res.message, /Submitted to the Process Scheduler/);
  const run = await page.frameLocator('#psa-ws-frame').locator('body').evaluate(() => window.__mockRun);
  assert.deepEqual(run.SELECTED, ['AEMINITEST']);
});

test('live mode: no task fits, so the agent reads each page and decides; Save waits for approval', async page => {
  await page.click('#psa-fab');
  await page.locator('.psa-welcome').waitFor();                          // welcome screen with what I can do
  await page.fill('#psa-input', 'create user LIVE1 with symbolic ID SYSADM1');
  await page.press('#psa-input', 'Enter');
  // the password is asked for on a local card, typed straight into PeopleSoft
  const secret = page.locator('.psa-card', { hasText: 'New Password' });
  await secret.waitFor({ timeout: 60000 });
  await secret.locator('input[type="password"]').fill('L1ve!pw');
  await secret.locator('.psa-ok').click();
  // a question in the chat; the answer goes back to the live run
  await page.locator('.psa-bot', { hasText: 'What description should LIVE1 have?' }).waitFor({ timeout: 60000 });
  assert.match(await page.locator('#psa-status').innerText(), /Waiting for your answer/);
  await page.fill('#psa-input', 'Live One');
  await page.press('#psa-input', 'Enter');
  // Save only after approval
  const approve = page.locator('.psa-card', { hasText: 'Approve: Save' });
  await approve.waitFor({ timeout: 60000 });
  assert.equal(await saved(page), null);                                 // nothing saved before approval
  assert.ok((await approve.innerText()).includes('Live One'));
  await approve.locator('.psa-ok').click();
  await page.locator('.psa-bot', { hasText: 'worked out live' }).waitFor({ timeout: 60000 });
  assert.deepEqual(await saved(page), {
    OPRID: 'LIVE1', DESCR: 'Live One', SYMBOLICID: 'SYSADM1', PWD: 'L1ve!pw', PWD2: 'L1ve!pw',
    OPRCLASS: '', IDTYPE: 'NON', EMPLID: '', ROLES: [''],
  });
  const feed = await page.locator('.psa-feed').last().innerText();
  for (const s of ['Open MAINTAIN_SECURITY.USERMAINT', 'Set User ID', 'Typing New Password (hidden)', 'Asked you a question', 'You approved: Save'])
    assert.ok(feed.includes(s), s + ' missing in\n' + feed);
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).join('\n');
  assert.ok(!bodies.includes('L1ve!pw'), 'password reached the agent service');
  // what it worked out can become a task
  const offer = page.locator('.psa-card', { hasText: 'Make this a task?' });
  await offer.locator('.psa-ok').click();
  const review = page.locator('.psa-card', { hasText: 'Review the recorded task' });
  await review.waitFor();
  assert.equal(await review.locator('[data-k="open_mode"]').inputValue(), 'add');
  assert.match(await review.innerText(), /shows Saved/);
});

test('recovery: the recorded task meets a changed page and is finished live; Save asked again; task update offered', async page => {
  await page.evaluate(() => sessionStorage.setItem('mock.upgraded', '1'));   // Description got a new id and label
  await page.click('#psa-fab');
  await page.fill('#psa-input', 'create user RECOV, Recov User, role EMPLOYEE');
  await page.press('#psa-input', 'Enter');
  const card = page.locator('.psa-card', { hasText: 'please review' });
  await card.waitFor();
  await card.locator('[data-secret="password"]').fill('Rec0v!pw');
  await card.locator('[data-confirm="password"]').fill('Rec0v!pw');
  await card.locator('.psa-ok').click();
  // the recorded steps run until Description, which is not there any more; then Claude takes over
  const approve = page.locator('.psa-card', { hasText: 'Approve: Save' });
  await approve.waitFor({ timeout: 90000 });
  const feed = page.locator('.psa-feed').last();
  assert.match(await feed.innerText(), /did not match the page: working out the rest live/);
  assert.equal(await saved(page), null);                                   // your approval first, again
  await approve.locator('.psa-ok').click();
  await page.locator('.psa-bot', { hasText: 'finished it live' }).waitFor({ timeout: 60000 });
  assert.deepEqual(await saved(page), {
    OPRID: 'RECOV', DESCR: 'Recov User', SYMBOLICID: 'SYSADM1', PWD: 'Rec0v!pw', PWD2: 'Rec0v!pw',
    OPRCLASS: '', IDTYPE: 'NON', EMPLID: '', ROLES: ['EMPLOYEE'],
  });
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).join('\n');
  assert.ok(!bodies.includes('Rec0v!pw'), 'password reached the agent service');
  // the run (recorded part + live part) can replace the task
  const offer = page.locator('.psa-card', { hasText: 'Update the task?' });
  await offer.locator('.psa-ok').click();
  const review = page.locator('.psa-card', { hasText: 'Review the recorded task' });
  await review.waitFor();
  assert.match(await review.innerText(), /Replace the existing task\s*create_user_profile/);
  assert.match(await review.innerText(), /Long Description/);              // the field as it is now
});

test('documents: attach a form, review the people in one table, fix a row, create them one by one', async page => {
  // the form as a PDF, like a scanned or exported request
  const pdfPath = path.join(os.tmpdir(), 'access-request.pdf');
  const printer = await page.context().browser().newPage();
  await printer.goto('file://' + path.join(here, 'access_request.html').replace(/\\/g, '/'));
  await printer.pdf({ path: pdfPath, format: 'A4' });
  await printer.close();

  await page.click('#psa-fab');
  await page.setInputFiles('#psa-file', pdfPath);
  await page.locator('.psa-file', { hasText: 'access-request.pdf' }).waitFor();
  await page.fill('#psa-input', 'create these users');
  await page.press('#psa-input', 'Enter');
  assert.equal(await page.locator('#psa-files .psa-file').count(), 0);     // sent with the message
  const table = page.locator('.psa-card', { hasText: '2 found, please review' });
  await table.waitFor({ timeout: 30000 });
  assert.ok(await table.locator('a', { hasText: 'access-request.pdf' }).count(), 'document link on the card');
  const desc = table.locator('tr[data-i="1"] [data-col="description"]');
  assert.equal(await desc.getAttribute('class'), 'psa-miss');               // needed
  const role = table.locator('tr[data-i="1"] [data-col="roles"]');
  assert.equal(await role.getAttribute('class'), 'psa-unsure');             // please check
  assert.match(await role.getAttribute('title'), /needs manager access/);
  assert.equal(await table.locator('.psa-ok').isDisabled(), true);          // password first
  await desc.fill('Mark Chen');
  await table.locator('[data-secret="password"]').fill('D0c!pw');
  await table.locator('[data-confirm="password"]').fill('D0c!pw');
  assert.equal(await table.locator('.psa-ok').innerText(), 'Create 2');
  await table.locator('.psa-ok').click();
  await page.locator('.psa-bot', { hasText: 'Both users were created from the form' }).waitFor({ timeout: 90000 });
  assert.equal((await saved(page)).OPRID, 'DOCB2');                         // the last one in the workspace
  assert.equal((await saved(page)).DESCR, 'Mark Chen');                     // the value fixed in the table
  const feed = await page.locator('.psa-feed').last().innerText();
  assert.match(feed, /DOCA1 - Anita Desai \(1 of 2\)/);
  assert.match(feed, /DOCB2 - Mark Chen \(2 of 2\)/);
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b);
  const chat = JSON.parse(bodies.find(b => b.includes('"attachment"')));
  assert.equal(chat.attachment.media_type, 'application/pdf');
  assert.ok(Buffer.from(chat.attachment.data, 'base64').toString('latin1').startsWith('%PDF'));
  const results = JSON.parse(bodies.find(b => b.includes('"batch_id"') && b.includes('"results"')));
  assert.deepEqual(results.results.map(r => r.status), ['success', 'success']);
  assert.ok(!bodies.join('').includes('D0c!pw'), 'password reached the agent service');
});

test('troubleshoot: "why did my last process fail?" -> log file, message log and code -> diagnosis card', async page => {
  await page.click('#psa-fab');
  await page.locator('.psa-tile', { hasText: 'Troubleshoot a process' }).click();     // from the welcome screen
  const card = page.locator('.psa-card.psa-diag');
  await card.waitFor({ timeout: 60000 });
  const text = await card.innerText();
  for (const s of ['Process 557060: what went wrong', 'Code issue', 'Confidence: high', 'AEMINITEST.MAIN.Step02 (PeopleCode) - statement 3, line 5',
    'Delete line 5', 'Earlier runs 557051-557053'])
    assert.ok(text.includes(s), s + ' missing in\n' + text);
  assert.match(await card.locator('pre.psa-code').innerText(), /&nTest = &nTest \+ 1;/);   // the suggested code
  const feed = await page.locator('.psa-feed').last().innerText();
  for (const s of ['Troubleshooting process 557060', 'Reading log file AE_AEMINITEST_557060.stdout',
    'Reading the end of log file AE_AEMINITEST_557060.trc (232 MB)',
    'Read process 557060: status, message log, earlier runs', 'Read PeopleCode AEMINITEST.MAIN.Step02', 'Diagnosis ready'])
    assert.ok(feed.includes(s), s + ' missing in\n' + feed);
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b.includes('"run_id":"tbl-'));
  const logs = JSON.parse(bodies[0]).logs, trc = logs.find(l => l.name.endsWith('.trc'));
  assert.match(logs.find(l => l.name.endsWith('.stdout')).text, /ABENDED at Step AEMINITEST\.MAIN\.Step02/);   // log read in the browser, sent once
  assert.match(trc.part, /^last 0\.1 MB of 232 MB$/);                    // big trace: only its end was fetched
  assert.ok(trc.text.endsWith('(trace end)\n') && trc.text.length <= 61000, 'trace end kept, size capped');
  assert.ok(!JSON.parse(bodies[1]).logs);
});

test('security: "why can\'t ABSMITH access Create Job Opening?" -> security chain, smallest fix, links -> Apply live', async page => {
  await page.click('#psa-fab');
  await page.locator('.psa-tile', { hasText: 'Check someone\'s access' }).click();     // security admins only
  assert.equal(await page.inputValue('#psa-input'), "Why can't ");
  await page.type('#psa-input', 'ABSMITH access Create Job Opening?');
  await page.press('#psa-input', 'Enter');
  const card = page.locator('.psa-card.psa-diag', { hasText: 'ABSMITH → Create Job Opening' });
  await card.waitFor({ timeout: 60000 });
  const text = await card.innerText();
  for (const s of ['Missing access', 'Confidence: high', 'HRS_HRPM.HRS_JO_LAUNCH.GBL', 'SECURITY CHAIN', 'Permission lists: No permission list grants',
    'SMALLEST FIX', 'Add role Hiring Manager to ABSMITH.', 'Side effects: Only ABSMITH', 'ABSMITH signs out and in again.'])
    assert.ok(text.toUpperCase().includes(s.toUpperCase()), s + ' missing in\n' + text);
  assert.equal(await card.locator('.psa-chk.missing').count(), 1);
  const hrefs = await card.locator('.psa-links a').evaluateAll(as => as.map(a => a.getAttribute('href')));
  assert.deepEqual(hrefs, ['/psp/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.USERMAINT.GBL?OPRID=ABSMITH',
    '/psp/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.ROLEMAINT.GBL?ROLENAME=Hiring%20Manager',
    '/psp/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.ACCESS_CNTRL_LISTX.GBL?CLASSID=HCCPRS1040',
    '/psp/ps/EMPLOYEE/HRMS/c/HRS_HRPM.HRS_JO_LAUNCH.GBL']);
  const feed = await page.locator('.psa-feed').last().innerText();
  for (const s of ["Checking ABSMITH's access to Create Job Opening", 'Looked up the page "Create Job Opening" in the menu',
    "Checked ABSMITH's roles, permission lists, pages and menu link for HRS_HRPM.HRS_JO_LAUNCH.GBL", 'Diagnosis ready'])
    assert.ok(feed.includes(s), s + ' missing in\n' + feed);
  await card.locator('[data-apply]').click();                                         // nothing changes until this click
  await page.locator('.psa-msg.psa-bot', { hasText: 'now has the role Hiring Manager' }).waitFor({ timeout: 60000 });
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b);
  const apply = bodies.filter(b => b.includes('"run_id":"sec-') && !b.includes('"snapshot"'));
  assert.ok(apply.length >= 2, 'steps and apply sent with the access check id');
  assert.ok(bodies.some(b => b.includes('"run_id":"live-') && b.includes('"snapshot"')), 'the fix ran live');
});

test('approvals: pending absence requests -> Review with AI -> approval status, recommendation, confidence score', async page => {
  await page.click('#psa-fab');
  await page.locator('.psa-tile', { hasText: 'Review absence approvals' }).click();    // from the welcome screen
  const list = page.locator('.psa-card', { hasText: 'Absence requests waiting for you' });
  await list.waitFor({ timeout: 60000 });
  const lt = await list.innerText();
  for (const s of ['Edmund Donahue', 'KU0102', 'Vacation · 31 Jan 2018 · 8 h', 'Waiting for RCHANNING · since 6 Dec 2017'])
    assert.ok(lt.includes(s), s + ' missing in\n' + lt);
  await list.locator('button', { hasText: 'Review with AI' }).click();
  const card = page.locator('.psa-card.psa-diag', { hasText: 'Absence request 1589' });
  await card.waitFor({ timeout: 60000 });
  const text = (await card.innerText()).toUpperCase();
  for (const s of ['Approval status', 'Pending · step 1 of 1 · Waiting for RCHANNING', 'Submitted 6 Dec 2017 by EDONAHUE',
    'Recommend: send back', '88/100 · high', 'Something needs fixing before you approve it', 'Why this confidence',
    'Overlaps: Approved sick leave on 31 Jan', 'Suggested comment', 'please withdraw or change the dates', 'You decide'])
    assert.ok(text.includes(s.toUpperCase()), s + ' missing in\n' + text);
  assert.equal(await card.locator('.psa-chk.missing').count(), 1);                    // the problem check
  assert.equal(await card.locator('.psa-chk.info').count(), 1);                       // unknown status cleaned
  assert.equal(await card.locator('.psa-conf-bar i').evaluate(e => e.style.width), '88%');
  assert.equal(await card.locator('a.psa-btn').getAttribute('href'), '/psc/ps/EMPLOYEE/HRMS/c/EOAWMA_MAIN_FL.EOAWMA_MAIN_FL.GBL');
  const feed = await page.locator('.psa-feed').last().innerText();
  for (const s of ['Reviewing absence request 1589', 'Read absence request 1589: balance, approvals, other absences, team', 'Diagnosis ready'])
    assert.ok(feed.includes(s), s + ' missing in\n' + feed);
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b);
  assert.ok(bodies.some(b => b.includes('"thread":"1589"')), 'review started for request 1589');
  assert.ok(bodies.some(b => b.includes('"run_id":"apr-')), 'steps sent with the review id');
});

test('runbooks: attach the Excel checklist -> draft runbook card -> publish', async page => {
  const xlsx = path.join(here, '..', '..', 'agent-service', 'runbooks', 'templates', 'test_user_setup_checklist.xlsx');
  await page.click('#psa-fab');
  await page.setInputFiles('#psa-file', xlsx);
  await page.locator('.psa-file', { hasText: 'test_user_setup_checklist.xlsx' }).waitFor();
  await page.fill('#psa-input', 'build a runbook from this checklist');
  await page.press('#psa-input', 'Enter');
  const card = page.locator('.psa-card', { hasText: 'Runbook: Set up a test user' });
  await card.waitFor({ timeout: 30000 });
  const t = (await card.innerText()).toUpperCase();      // section headings are shown in capitals
  for (const s of ['Drafted from test_user_setup_checklist.xlsx', 'Please check', 'no Sync Portal Security task yet',
    'User ID', 'PSA_TEST01', '5 steps', 'Today: passes', 'Today: fails', 'tested with user_id = PSA_TEST01',
    'Runs the task Create User Profile with User ID = {{user_id}}, Roles = EMPLOYEE', 'Asks when it runs:',
    'Owner: Security Manager', 'Nothing has run'])
    assert.ok(t.includes(s.toUpperCase()), s + ' missing in\n' + t);
  assert.deepEqual(await card.locator('.psa-rb-t').allInnerTexts(), ['CHECK', 'TASK', 'MANUAL', 'CHECK', 'APPROVAL']);
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b);
  assert.ok(bodies.some(b => b.includes('"media_type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"')),
    'the checklist was sent as an Excel file');
  await card.locator('[data-pub]').click();
  await page.locator('.psa-bot', { hasText: 'as runbooks/set_up_a_test_user.yaml' }).waitFor({ timeout: 10000 });
  assert.equal(await card.locator('[data-pub]').innerText(), 'Published');
});

test('runbooks: run the published runbook from its tile: check, task with confirm card, manual step, check, approval', async page => {
  await page.click('#psa-fab');
  await page.locator('.psa-tile', { hasText: 'Run: Set up a test user' }).click();
  const start = page.locator('.psa-card', { hasText: 'Run: Set up a test user' });
  await start.waitFor({ timeout: 30000 });
  assert.equal(await start.locator('[data-in="user_id"]').inputValue(), 'PSA_TEST01');
  await start.locator('[data-in="user_id"]').fill('RBUSER1');
  await start.locator('[data-go]').click();
  // step 1 (check) ran on the service; step 2 is the task's confirm card
  const task = page.locator('.psa-card', { hasText: 'Step 2: Create the user profile and add the Employee role' });
  await task.waitFor({ timeout: 30000 });
  const tt = await task.innerText();
  for (const s of ['Runs the task Create User Profile', 'RBUSER1', 'Employee', 'Description (needed)'])
    assert.ok(tt.includes(s), s + ' missing in\n' + tt);
  assert.equal(await task.locator('.psa-ok').isDisabled(), true);              // description and password first
  await task.locator('[data-fill="description"]').fill('Runbook User');
  await task.locator('[data-secret="password"]').fill('Rb$ecret1');
  await task.locator('[data-confirm="password"]').fill('Rb$ecret1');
  await task.locator('.psa-ok').click();
  // the task runs in the workspace; then step 3 waits for the person
  const manual = page.locator('.psa-card', { hasText: 'Step 3: Run the portal security sync' });
  await manual.waitFor({ timeout: 60000 });
  await manual.locator('.psa-ok', { hasText: 'Done' }).click();
  const approval = page.locator('.psa-card', { hasText: 'Step 5: Security manager signs off the new user' });
  await approval.waitFor({ timeout: 30000 });
  assert.ok((await approval.innerText()).includes('Approver: Security Manager'));
  await approval.locator('.psa-ok', { hasText: 'Approve' }).click();
  const done = page.locator('.psa-card', { hasText: 'Set up a test user: completed' });
  await done.waitFor({ timeout: 30000 });
  assert.equal(await done.locator('.psa-chk.ok').count(), 5);
  const feed = await page.locator('.psa-feed').last().innerText();
  for (const s of ['1. Make sure the new user ID is not taken', '2. Create the user profile and add the Employee role: Saved',
    '3. Run the portal security sync: Done by TESTER', '5. Security manager signs off the new user: Approved by TESTER'])
    assert.ok(feed.includes(s), s + ' missing in\n' + feed);
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b);
  assert.ok(bodies.some(b => b.includes('"inputs":{"user_id":"RBUSER1"}')), 'started with the input');
  assert.ok(!bodies.some(b => b.includes('Rb$ecret1')), 'the password never reaches the service');
});

test('roles: no PSAGENT role -> no widget; with a role it shows', async page => {
  await page.goto(`${BASE}/__test/norole`);
  await page.waitForTimeout(1200);
  assert.equal(await page.locator('#psa-fab').count(), 0, 'the widget must stay hidden');
  await page.goto(HOME);
  await page.locator('#psa-fab').waitFor({ timeout: 10000 });
});

test('watcher: set up a monitor in the chat -> preview -> save; overnight alert on the welcome screen, in the chat and from the email link', async page => {
  await page.click('#psa-fab');
  const watch = page.locator('.psa-tile', { hasText: 'Watch something for me' });
  await watch.click();
  assert.equal(await page.inputValue('#psa-input'), 'Watch ');
  await page.fill('#psa-input', 'Watch for requests stuck more than 30 minutes and email ops@example.com');
  await page.press('#psa-input', 'Enter');
  const mc = page.locator('.psa-card', { hasText: 'Monitor: Stuck requests' });
  await mc.waitFor({ timeout: 30000 });
  const mt = await mc.innerText();
  for (const s of ['2 REQUESTS ARE STUCK RIGHT NOW', 'PSXPARCHATTR', 'Every 5 minutes', 'ops@example.com', 'Email is not set up'])   // headings are upper case (CSS)
    assert.ok(mt.includes(s), s + ' missing in\n' + mt);
  await mc.locator('[data-save]').click();
  await page.locator('.psa-bot', { hasText: 'monitors/stuck_requests.yaml' }).waitFor({ timeout: 10000 });
  // "overnight": one watcher round
  const ids = await (await fetch(`${BASE}/__test/watch_tick`)).json();
  assert.equal(ids.length, 1);
  await page.click('#psa-new');
  const alert = page.locator('.psa-alert', { hasText: 'from the watcher' });
  await alert.waitFor({ timeout: 10000 });
  await page.locator('#psa-close').click();
  await page.click('#psa-fab');
  await page.locator('.psa-tile', { hasText: 'What happened overnight?' }).click();
  const list = page.locator('.psa-card', { hasText: 'in the last 16 hours' });
  await list.waitFor({ timeout: 30000 });
  await list.locator('[data-n="0"]').click();
  const inc = page.locator('.psa-card.psa-diag', { hasText: '2 requests stuck' });
  await inc.waitFor({ timeout: 10000 });
  const it = await inc.innerText();
  for (const s of ['Every process server is running', 'PRCS8622: Running', '557022', 'ACTION NEEDED'])
    assert.ok(it.includes(s), s + ' missing in\n' + it);
  // the email's "Open in the agent" link opens the panel on the alert
  await page.goto(HOME + '?tab=DEFAULT&psagent=' + ids[0]);
  await page.locator('.psa-card.psa-diag', { hasText: '2 requests stuck' }).waitFor({ timeout: 15000 });
});

test('watch plan: + Features > Watch jobs -> download template -> upload -> overview -> confirm -> manage: pause, delete, download, stop', async page => {
  await page.click('#psa-fab');
  await page.click('#psa-feat');
  await page.locator('#psa-featmenu button', { hasText: 'Watch jobs' }).click();
  const g = page.locator('.psa-card.psa-guide', { hasText: 'Watch jobs' });
  await g.waitFor({ timeout: 10000 });
  const [dl] = await Promise.all([page.waitForEvent('download'), g.locator('[data-dl]').click()]);
  assert.equal(dl.suggestedFilename(), 'watch_plan_template.xlsx');
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), g.locator('[data-up]').click()]);
  await chooser.setFiles(path.join(here, '..', '..', 'agent-service', 'runbooks', 'templates', 'watch_plan_template.xlsx'));
  const wp = page.locator('.psa-card', { hasText: 'Watch plan: Nightly jobs' });
  await wp.waitFor({ timeout: 30000 });
  const wt = await wp.innerText();
  for (const s of ['From watch_plan_template.xlsx', 'AE test program failures', 'Cross-reference report stuck', '2 requests are stuck right now',
                   'Portal security sync by 07:00', 'Succeeded on 3 of the last 30 days', 'Every day at 07:00', 'security@example.com'])
    assert.ok(wt.includes(s), s + ' missing in\n' + wt);
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b.includes('watch_plan_template.xlsx'));
  assert.ok(bodies.some(b => b.includes('"message":"Build a watch plan from this sheet"')), 'the file went with the guide message');
  await wp.locator('[data-go]').click();
  await page.locator('.psa-bot', { hasText: 'monitors/nightly_jobs.yaml' }).waitFor({ timeout: 10000 });
  const pill = page.locator('#psa-dogs');
  assert.match(await pill.innerText(), /◉ 5 watchdogs/);       // 4 here + the stuck monitor from the previous test
  // the manager
  await pill.click();
  const mg = page.locator('.psa-card.psa-mgr');
  await mg.waitFor({ timeout: 10000 });
  const plan = mg.locator('.psa-plan', { hasText: 'Nightly jobs' });
  assert.equal(await plan.locator('.psa-dog').count(), 4);
  await plan.locator('.psa-dog', { hasText: 'Cross-reference report stuck' }).locator('[data-act="pause"]').click();
  await plan.locator('.psa-dog.psa-paused', { hasText: 'Cross-reference report stuck' }).waitFor({ timeout: 10000 });
  assert.match(await pill.innerText(), /\(4 active\)/);
  const del = () => mg.locator('.psa-plan', { hasText: 'Nightly jobs' }).locator('.psa-dog', { hasText: 'Portal security sync' }).locator('[data-act="delete"]');
  await del().click();
  assert.match(await del().innerText(), /click again/);           // asks first
  await del().click();
  await page.waitForFunction(() => document.querySelectorAll('.psa-mgr .psa-plan')[1]?.querySelectorAll('.psa-dog').length === 3
    || [...document.querySelectorAll('.psa-mgr .psa-plan')].some(p => p.innerText.includes('Nightly jobs') && p.querySelectorAll('.psa-dog').length === 3));
  const [x] = await Promise.all([page.waitForEvent('download'), mg.locator('.psa-plan', { hasText: 'Nightly jobs' }).locator('[data-dl]').click()]);
  assert.equal(x.suggestedFilename(), 'nightly_jobs.xlsx');
  const stop = mg.locator('[data-act="stop"]');
  await stop.click();
  await stop.click();
  await page.locator('#psa-dogs.psa-off', { hasText: 'Watching stopped' }).waitFor({ timeout: 10000 });
  await mg.locator('[data-act="start"]').click();
  await page.locator('#psa-dogs:not(.psa-off)').waitFor({ timeout: 10000 });
  assert.ok((await mg.textContent()).includes('stopped all watching'), 'the change is in the history (collapsed)');
});

test('regression: template -> upload -> overview -> save -> run here (passes) -> page changed (passed with changes) -> unattended run on the service', async page => {
  await page.click('#psa-fab');
  await page.click('#psa-feat');
  await page.locator('#psa-featmenu button', { hasText: 'Regression tests' }).click();
  const home = page.locator('.psa-card.psa-guide', { hasText: 'Regression tests' });
  await home.waitFor({ timeout: 10000 });
  assert.ok((await home.innerText()).includes('Unattended runs sign in as TESTER'));
  const [dl] = await Promise.all([page.waitForEvent('download'), home.locator('[data-tpl]').click()]);
  assert.equal(dl.suggestedFilename(), 'regression_test_plan.xlsx');
  const plan = path.join(os.tmpdir(), 'psagent_regression_test_plan.xlsx');
  await dl.saveAs(plan);
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), home.locator('[data-up]').click()]);
  await chooser.setFiles(plan);
  const ov = page.locator('.psa-card', { hasText: 'Test suite: User admin regression' });
  await ov.waitFor({ timeout: 30000 });
  const ot = await ov.innerText();
  for (const s of ['From psagent_regression_test_plan.xlsx', 'New user', 'RT{{n}}', 'Duplicate user is refused', 'Error: "already exists"'])
    assert.ok(ot.includes(s), s + ' missing in\n' + ot);
  await ov.locator('[data-go]').click();
  // run 1, here: both pass (the duplicate is refused, as expected)
  const runNow = page.locator('.psa-card', { hasText: 'Saved User admin regression' });
  await runNow.locator('.psa-ok').click();
  let start = page.locator('.psa-card', { hasText: '{{n}} = 0001' });
  await start.waitFor({ timeout: 10000 });
  await start.locator('[data-sec="password"]').fill('Regr3ss!');
  await start.locator('[data-go]').click();
  let rep = page.locator('.psa-card.psa-diag', { hasText: 'User admin regression: 2 of 2 passed' });
  await rep.waitFor({ timeout: 90000 });
  let bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).join('\n');
  assert.ok(!bodies.includes('Regr3ss!'), 'the password never reaches the service');
  // run 2: the page was upgraded (Description moved): case 1 is finished live -> passed with changes
  await page.evaluate(() => sessionStorage.setItem('mock.upgraded', '1'));
  await page.click('#psa-feat');
  await page.locator('#psa-featmenu button', { hasText: 'Regression tests' }).click();
  const home2 = page.locator('.psa-card.psa-guide', { hasText: 'Regression tests' }).last();
  await home2.locator('.psa-plan', { hasText: 'User admin regression' }).locator('[data-run]').click();
  start = page.locator('.psa-card', { hasText: '{{n}} = 0002' });
  await start.locator('[data-sec="password"]').fill('Regr3ss!');
  await start.locator('[data-go]').click();
  rep = page.locator('.psa-card.psa-diag', { hasText: '1 of 2 passed, 1 passed with changes' });
  await rep.waitFor({ timeout: 120000 });
  const rt = await rep.textContent();
  for (const s of ['Passed with changes', 'was Passed', 'no longer matched the page', 'Live: Description moved: Long Description',
                   'Finished live: Created RT0002', 'compared with the run of'])
    assert.ok(rt.includes(s), s + ' missing in\n' + rt);
  const [x] = await Promise.all([page.waitForEvent('download'), rep.locator('[data-xl]').click()]);
  assert.match(x.suggestedFilename(), /^regression-user_admin_regression-rt-.*\.xlsx$/);
  await page.evaluate(() => sessionStorage.removeItem('mock.upgraded'));
  // run 3: unattended, in the service's own headless browser (signs in as TESTER, runs the same widget code)
  await page.click('#psa-feat');
  await page.locator('#psa-featmenu button', { hasText: 'Regression tests' }).click();
  const home3 = page.locator('.psa-card.psa-guide', { hasText: 'Regression tests' }).last();
  await home3.locator('.psa-plan', { hasText: 'User admin regression' }).locator('[data-una]').click();
  await page.locator('.psa-bot', { hasText: 'Started an unattended run' }).waitFor({ timeout: 10000 });
  let last = null;
  for (let i = 0; i < 120 && !last; i++) {
    const h = await (await fetch(`${BASE}/api/regress`)).json();
    if (!h.unattended.busy && h.runs.length && h.runs[0].mode === 'server') last = h.runs[0];
    else await new Promise(r => setTimeout(r, 1000));
  }
  assert.ok(last, 'the unattended run finished');
  assert.equal(last.headline, '2 of 2 passed');
  const full = (await (await fetch(`${BASE}/api/regress/report?run=${last.run_id}`)).json()).report;
  assert.equal(full.by, 'TESTER');
  assert.equal(full.n, '0003');
});

test('audit: + Features > Audit opens the dashboard beside the chat: overview, usage and cost, the audit log, failures, watcher and tests', async page => {
  await page.setViewportSize({ width: 1500, height: 950 });
  await page.click('#psa-fab');
  await page.locator('.psa-tile', { hasText: 'Audit dashboard' }).waitFor({ timeout: 10000 });
  await page.click('#psa-feat');
  await page.locator('#psa-featmenu button', { hasText: 'Audit' }).click();
  const dash = page.locator('#psa-audit');
  await dash.locator('.psa-au-kpis').waitFor({ timeout: 10000 });
  // beside the chat, not over it
  const [d, p] = [await dash.boundingBox(), await page.locator('#psa-panel').boundingBox()];
  assert.ok(d.x + d.width <= p.x, 'the dashboard is left of the chat');
  let t = await dash.innerText();
  for (const s of ['People', 'Messages', 'Task runs', 'Claude cost', 'of the $25.00 budget', 'Cost by feature', 'Top users', 'VELU', 'Troubleshooter'])
    assert.ok(t.includes(s), s + ' missing in\n' + t);
  assert.ok(await dash.locator('.psa-au-bar').count() >= 3, 'the daily cost bars');
  await dash.locator('.psa-au-hit').last().hover();
  assert.match(await dash.locator('.psa-au-tip').innerText(), /\$[\d.]+, \d+ messages/);
  await snap(page, 'audit_1_overview');
  // 30 days, usage by user: the people of the sample history and this test run's own user
  await dash.locator('[data-days]').selectOption('30');
  await dash.locator('[data-tab="usage"]').click();
  await dash.locator('.psa-au-table').waitFor();
  t = await dash.innerText();
  for (const s of ['VELU', 'ABSMITH', 'TESTER', 'PSAGENT_WATCH (the service)', 'Per conversation'])
    assert.ok(t.includes(s), s + ' missing in\n' + t);
  await snap(page, 'audit_2_usage_by_user');
  await dash.locator('[data-by="feature"]').click();
  await dash.locator('.psa-au-table', { hasText: 'Troubleshooter' }).waitFor();
  const [x] = await Promise.all([page.waitForEvent('download'), dash.locator('[data-xl="usage"]').click()]);
  assert.match(x.suggestedFilename(), /^psagent-usage-by-feature-.*\.xlsx$/);
  // a user's row opens the audit log filtered to them; a run's row shows the confirmed values
  await dash.locator('[data-by="user"]').click();
  await dash.locator('tr[data-user="VELU"]').click();
  await dash.locator('.psa-au-log').waitFor();
  assert.equal(await dash.locator('select[name="user"]').inputValue(), 'VELU');
  const confirmed = dash.locator('tr[data-ev]', { hasText: 'task.confirmed' }).first();
  await confirmed.click();
  assert.match(await dash.locator('.psa-au-detail').first().innerText(), /User ID = NEW\d+/);
  await snap(page, 'audit_3_log_velu');
  // database queries, searched
  await dash.locator('select[name="user"]').selectOption('');
  await dash.locator('select[name="kind"]').selectOption('queries');
  await dash.locator('input[name="q"]').fill('PSPRCSRQST');
  await dash.locator('[data-filters] button[type="submit"]').click();
  await dash.locator('tr[data-ev]', { hasText: 'query' }).first().click();
  assert.match(await dash.locator('.psa-au-detail pre').first().innerText(), /SELECT .*PSPRCSRQST/);
  const [xl] = await Promise.all([page.waitForEvent('download'), dash.locator('[data-xl="events"]').click()]);
  assert.equal(xl.suggestedFilename(), 'psagent-audit-log.xlsx');
  // failures; the watcher and the regression runs (this test run's own included)
  await dash.locator('[data-tab="failures"]').click();
  await dash.locator('.psa-au-chips').waitFor();
  t = await dash.innerText();
  assert.ok(t.includes('Tasks') && t.includes('User ID already exists') && t.includes('API error 529'), t);
  await snap(page, 'audit_4_failures');
  await dash.locator('[data-tab="ops"]').click();
  await dash.locator('.psa-au-cols').waitFor();
  t = await dash.innerText();
  assert.ok(t.includes('User admin regression') && t.includes('watchdogs active'), t);
  // what people typed in the chat is not kept
  const typed = await (await fetch(`${BASE}/api/audit?tab=events&kind=all&days=90&q=${encodeURIComponent('stuck more than 30 minutes')}`)).json();
  assert.deepEqual(typed.events, []);
  const chats = await (await fetch(`${BASE}/api/audit?tab=events&kind=all&days=1&user=TESTER&q=Sent`)).json();
  assert.ok(chats.events.length > 3, 'but each message is counted');
  // ask the agent: the chat on the right
  await dash.locator('[data-ask]').click();
  assert.match(await page.getAttribute('#psa-input', 'placeholder'), /Who spent the most/);
  await dash.locator('[data-close]').click();
  assert.equal(await dash.isVisible(), false);
});

test('excel to CI: + Features > Load with a CI -> pick USER_PROFILE -> template -> fill -> checks -> load through SOAPTOCI -> results; a template dropped in the chat', async page => {
  await page.setViewportSize({ width: 1400, height: 950 });
  await page.click('#psa-fab');
  await page.click('#psa-feat');
  await page.locator('#psa-featmenu button', { hasText: 'Load with a CI' }).click();
  const picker = page.locator('.psa-card.psa-ci', { hasText: 'Load with a Component Interface' });
  await picker.waitFor({ timeout: 10000 });
  await picker.locator('.psa-ci-q').fill('user');
  await picker.locator('[data-ci="USER_PROFILE"]').waitFor({ timeout: 10000 });
  await snap(page, 'ci_1_picker');
  await picker.locator('[data-ci="USER_PROFILE"]').click();
  // the shape, read with GetCIShape through SOAPTOCI in this session
  const shape = page.locator('.psa-card.psa-ci', { hasText: 'USER_PROFILE: User Profile Component' });
  await shape.waitFor({ timeout: 15000 });
  assert.ok((await shape.innerText()).includes('You may Create and Update through this CI'));
  assert.equal(await shape.locator('[data-p="UserID"]').isDisabled(), true, 'the key is always in');
  for (const id of ['UserDescription', 'Roles.RoleName', 'EmailAddresses.EmailAddress']) {
    const box = shape.locator(`[data-p="${id}"]`);
    if (!(await box.isVisible())) await box.evaluate(b => b.closest('details').open = true);
    if (!(await box.isChecked())) await box.check();
  }
  await snap(page, 'ci_2_template');
  const [dl] = await Promise.all([page.waitForEvent('download'), shape.locator('[data-dl]').click()]);
  assert.equal(dl.suggestedFilename(), 'user_profile_template.xlsx');
  const tpl = path.join(os.tmpdir(), 'psagent_ci_template.xlsx');
  await dl.saveAs(tpl);
  // fill it in, as a person would
  const fs = await import('node:fs');
  const rows = {
    USER_PROFILE: [
      { Action: 'Create', UserID: 'CITEST01', UserDescription: 'CI Test One', SymbolicID: 'SYSADM1' },
      { Action: 'Create', UserID: 'CITEST02', UserDescription: 'CI Test Two', SymbolicID: 'SYSADM1' },
      { Action: 'Create', UserID: 'JSMITH', UserDescription: 'Exists already', SymbolicID: 'SYSADM1' },
      { Action: 'Update', UserID: 'VP1', UserDescription: 'Vice President One' }],
    Roles: [{ 'USER_PROFILE.UserID': 'CITEST01', RoleName: 'EMPLOYEE' }, { 'USER_PROFILE.UserID': 'CITEST02', RoleName: 'EMPLOYE' },
            { 'Row action': 'Add', 'USER_PROFILE.UserID': 'VP1', RoleName: 'Manager' }],
    EmailAddresses: [{ 'USER_PROFILE.UserID': 'CITEST01', EmailType: 'Work', EmailAddress: 'citest01@example.com' }],
  };
  const filledRes = await fetch(`${BASE}/__test/ci_fill`, { method: 'POST', body: JSON.stringify({ file: fs.readFileSync(tpl).toString('base64'), rows }) });
  const filled = path.join(os.tmpdir(), 'new users.xlsx');
  fs.writeFileSync(filled, Buffer.from(await filledRes.arrayBuffer()));
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), shape.locator('[data-up]').click()]);
  await chooser.setFiles(filled);
  // every row checked before anything is saved
  const review = page.locator('.psa-ci-load', { hasText: 'new users.xlsx → USER_PROFILE' }).last();
  await review.waitFor({ timeout: 15000 });
  let t = await review.innerText();
  for (const s of ['4 rows', '3 ready', '1 with problems: not loaded', 'User ID JSMITH already exists: use Update to change it', 'Load 3 rows'])
    assert.ok(t.includes(s), s + ' missing in\n' + t);
  assert.deepEqual(await (await fetch(`${BASE}/__test/ci_users`)).json(), {}, 'nothing saved before Load');
  await snap(page, 'ci_3_review');
  await review.locator('[data-go]').click();
  await page.locator('.psa-bot', { hasText: '2 of 3 rows saved through USER_PROFILE' }).waitFor({ timeout: 30000 });
  t = await review.innerText();
  for (const s of ['✓ Saved', '✕ Failed', 'Invalid value -- press the prompt button', '3 of 3 rows loaded: done', 'Download the rows to fix', 'Explain the errors'])
    assert.ok(t.includes(s), s + ' missing in\n' + t);
  await snap(page, 'ci_4_loaded');
  const saved = await (await fetch(`${BASE}/__test/ci_users`)).json();
  assert.deepEqual(saved.CITEST01.roles, ['EMPLOYEE']);
  assert.deepEqual(saved.CITEST01.emails, ['citest01@example.com']);
  assert.equal(saved.CITEST01.values.UserDescription, 'CI Test One');
  assert.deepEqual(saved.VP1.roles, ['Manager'], 'Update with Row action Add inserts the role');
  assert.equal(saved.CITEST02, undefined);
  const [fix] = await Promise.all([page.waitForEvent('download'), review.locator('[data-x="fix"]').click()]);
  assert.equal(fix.suggestedFilename(), 'new users-to-fix.xlsx');
  const [res] = await Promise.all([page.waitForEvent('download'), review.locator('[data-x="results"]').click()]);
  assert.equal(res.suggestedFilename(), 'new users-results.xlsx');
  // each row is in the audit log
  const log = await (await fetch(`${BASE}/api/audit?tab=events&feature=ci&days=1`)).json();
  assert.ok(log.events.filter(e => e.event === 'ci.row').length === 3, 'three rows in the audit log');
  // the fixed rows dropped straight in the chat: checked at once, no Claude call
  const fixFile = path.join(os.tmpdir(), 'new users-to-fix.xlsx');
  await fix.saveAs(fixFile);
  const [att] = await Promise.all([page.waitForEvent('filechooser'), page.click('#psa-attach')]);
  await att.setFiles(fixFile);
  await page.fill('#psa-input', 'load these');
  await page.press('#psa-input', 'Enter');
  const again = page.locator('.psa-ci-load', { hasText: 'new users-to-fix.xlsx → USER_PROFILE' });
  await again.waitFor({ timeout: 15000 });
  t = await again.innerText();
  assert.ok(t.includes('2 rows') && t.includes('CITEST02') && t.includes('JSMITH'), t);
});

test('ask the data: + Features > Ask the data lists the AI_ queries the user may run -> a question -> Claude picks AI_NEW_HIRES -> the browser runs it in PeopleSoft -> table, chart, Excel', async page => {
  await page.setViewportSize({ width: 1400, height: 950 });
  await page.click('#psa-fab');
  await page.click('#psa-feat');
  await page.locator('#psa-featmenu button', { hasText: 'Ask the data' }).click();
  const list = page.locator('.psa-qr-list');
  await list.waitFor({ timeout: 10000 });
  let t = await list.innerText();
  for (const s of ['New Hires', 'AI_NEW_HIRES', 'asks for Start Date, End Date', 'My team', 'private', 'Headcount', 'a prompt has no name'])
    assert.ok(t.includes(s), s + ' missing in\n' + t);
  assert.ok(!t.includes('AI_PAYCHECKS') && !t.includes('AI_THEIRS') && !t.includes('AIXDEPT'), 'only the AI_ queries this user may run');
  await snap(page, 'qr_1_list');
  await page.fill('#psa-input', 'How many new hires this year by department?');
  await page.press('#psa-input', 'Enter');
  const res = page.locator('.psa-card.psa-qr', { hasText: 'AI_NEW_HIRES' }).last();
  await res.waitFor({ timeout: 20000 });
  await page.locator('.psa-bot', { hasText: 'new hires this year: 4 in 10200' }).waitFor({ timeout: 10000 });
  t = await res.innerText();
  for (const s of ['New Hires', 'Start Date: 2026-01-01', 'End Date: 2026-10-31', '7 rows', 'Rows by Department', '10200', 'Mark Chen',
                   '2026-01-01', 'Download Excel', 'Open in Query Viewer'])
    assert.ok(t.includes(s), s + ' missing in\n' + t);
  // the browser ran it in the session, with the dates as YYYY-MM-DD (12/31/2026 would silently give no rows)
  const hits = await (await fetch(`${BASE}/__test/query_hits`)).json();
  assert.deepEqual(hits.at(-1), { action: 'ICQryNameExcelURL', query: 'PUBLIC.AI_NEW_HIRES', BIND1: '2026-01-01', BIND2: '2026-10-31' });
  const [dl] = await Promise.all([page.waitForEvent('download'), res.locator('[data-xl]').click()]);
  assert.equal(dl.suggestedFilename(), 'AI_NEW_HIRES.xlsx');
  assert.ok((await res.locator('a', { hasText: 'Open in Query Viewer' }).getAttribute('href')).includes('ICQryNameURL=PUBLIC.AI_NEW_HIRES&BIND1=2026-01-01'));
  const log = await (await fetch(`${BASE}/api/audit?tab=events&feature=data&days=1`)).json();
  assert.ok(log.events.some(e => e.event === 'query.run' && e.detail === 'AI_NEW_HIRES: 7 rows'), 'the run is in the audit log');
  await snap(page, 'qr_2_result');
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
      await page.screenshot({ path: path.join(os.tmpdir(), 'psa-failure-' + tests.indexOf(t) + '.png') });
    }
    await page.close();
  }
  await browser.close();
} finally {
  server.kill();
}
console.log(failed ? `${failed} failed` : 'all passed');
process.exit(failed ? 1 : 0);
