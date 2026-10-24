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
    'Read process 557060: status, message log, earlier runs', 'Read PeopleCode AEMINITEST.MAIN.Step02', 'Diagnosis ready'])
    assert.ok(feed.includes(s), s + ' missing in\n' + feed);
  const bodies = (await (await fetch(`${BASE}/__test/bodies`)).json()).filter(b => b.includes('"run_id":"tbl-'));
  assert.match(JSON.parse(bodies[0]).logs[0].text, /ABENDED at Step AEMINITEST\.MAIN\.Step02/);   // log read in the browser, sent once
  assert.ok(!JSON.parse(bodies[1]).logs);
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
