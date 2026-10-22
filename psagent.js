/* ============================================================
   PEOPLESOFT AGENT WIDGET — chat + on-screen task automation
   Paste at the BOTTOM of PTNUI_MENU_JS in App Designer
   (instead of pscopilotagent.js, or alongside it).

   Talks to the Python agent service (agent-service/). The agent
   collects the values by chat, the user confirms, and this widget
   performs the task on the real PeopleSoft component inside a
   visible workspace frame, the way a person would.

   Console helpers:
     PSAgent.inspect()                         list fields on the workspace page
     PSAgent.teach('MENU.COMPONENT.GBL', {Action:'A'})
                                               record a task while you do it
   ============================================================ */

(function () {

  /* iScript URL — update WEBLIB name to match your App Designer record */
  var CONFIG_ISCRIPT_URL = '/psc/ps/EMPLOYEE/HRMS/s/WEBLIB_PSAGENT.ISCRIPT1.FieldFormula.IScript_PSAgentConfig';

  var RUN = { stepTimeout: 30000, settleMs: 400, pollMs: 150, afterActionMs: 300 };

  /* Populated at runtime from the iScript (or window.PSAGENT_CONFIG for local testing) */
  var cfg = { agentUrl: '', token: '', oprid: '', fullname: '' };

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function norm(t) { return String(t == null ? '' : t).replace(/\s+/g, ' ').replace(/[\s:*]+$/, '').trim().toLowerCase(); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function visible(el) {
    if (!el || !el.ownerDocument) return false;
    var st = el.ownerDocument.defaultView.getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }
  function closest(el, tag) {
    while (el && el.tagName !== tag) el = el.parentElement;
    return el;
  }

  /* ============================================================
     PLAYBOOK RUNNER — performs steps in the workspace iframe
     ============================================================ */

  var PROCESSING_SELECTORS = ['#WAIT_win0', '#processing', '#pt_processing', '.ps_box-processing', '.ps-processing'];
  var MESSAGE_SELECTORS = [
    '#alertmsg', '#PTMSGBOXTEXT', '.PTMSGBOXTEXT', '#win0divPT_MSGBOX', '[class*="PTMSGBOX"]',
    '#PSPAGE_MSGTEXT', '#ERRMSGTEXT', '#PSWARNINGMSGTEXT', '.PSWARNINGMSGTEXT', '.PSERROREDITEMTEXT',
    '.ps-exception-msg', '.ps_box-error', '.ps-alert-error'
  ];

  function isInput(el) {
    return !!el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) && el.type !== 'hidden';
  }

  function byLabel(doc, label) {
    var want = norm(label), i, el;
    var labels = doc.getElementsByTagName('label');
    for (i = 0; i < labels.length; i++) {
      if (norm(labels[i].textContent) === want && labels[i].htmlFor) {
        el = doc.getElementById(labels[i].htmlFor);
        if (isInput(el)) return el;
      }
    }
    var inputs = doc.querySelectorAll('input,select,textarea');
    for (i = 0; i < inputs.length; i++) {
      if (isInput(inputs[i]) && (norm(inputs[i].getAttribute('aria-label')) === want || norm(inputs[i].title) === want)) return inputs[i];
    }
    return null;
  }

  /* Grid cell by column header text, e.g. column "Role Name", row 0 */
  function byColumn(doc, column, row) {
    var want = norm(column);
    var heads = doc.querySelectorAll('th,[role="columnheader"],td[class*="GRIDCOLUMNHDR"],td[class*="GridColumnHdr"]');
    for (var h = 0; h < heads.length; h++) {
      if (norm(heads[h].textContent) !== want) continue;
      var headRow = closest(heads[h], 'TR'), table = closest(heads[h], 'TABLE');
      if (!headRow || !table) continue;
      var col = 0, c = headRow.firstElementChild;
      while (c && c !== heads[h]) { col += c.colSpan || 1; c = c.nextElementSibling; }
      var rows = table.rows, start = -1;
      for (var r = 0; r < rows.length; r++) if (rows[r] === headRow) { start = r; break; }
      var target = rows[start + 1 + row];
      if (!target) return null;
      var pos = 0;
      for (c = target.firstElementChild; c; c = c.nextElementSibling) {
        if (pos === col) {
          var inp = c.querySelector('input:not([type="hidden"]),select,textarea');
          if (inp) return inp;
        }
        pos += c.colSpan || 1;
      }
    }
    return null;
  }

  function findField(doc, spec, row) {
    var el = null;
    if (spec.id) {
      el = row == null ? doc.getElementById(spec.id)
        : doc.getElementById(spec.id + '$' + row) || (row === 0 ? doc.getElementById(spec.id) : null);
      if (!isInput(el)) el = null;
    }
    if (!el && spec.label && row == null) el = byLabel(doc, spec.label);
    if (!el && (spec.column || (spec.label && row != null))) el = byColumn(doc, spec.column || spec.label, row || 0);
    return el;
  }

  function fieldName(spec) { return spec.label || spec.column || spec.id; }

  function findButton(doc, spec) {
    var el = spec.id ? doc.getElementById(spec.id) : null;
    if (el && visible(el)) return el;
    if (spec.text) {
      var want = norm(spec.text);
      var cands = doc.querySelectorAll('a,button,input[type="button"],input[type="submit"],[role="button"]');
      for (var i = 0; i < cands.length; i++) {
        var t = cands[i].tagName === 'INPUT' ? cands[i].value : (cands[i].textContent || cands[i].title);
        if (norm(t) === want && visible(cands[i])) return cands[i];
      }
    }
    return el;
  }

  function fire(el, type) {
    var W = el.ownerDocument.defaultView;
    el.dispatchEvent(new W.Event(type, { bubbles: true }));
  }

  function setValue(el, value, name) {
    if (el.disabled || el.readOnly) throw new Error(name + ' is read-only on this page');
    var v = String(value == null ? '' : value);
    if (el.tagName === 'SELECT') {
      var opts = el.options, idx = -1, i;
      for (i = 0; i < opts.length && idx < 0; i++) if (opts[i].value === v) idx = i;
      for (i = 0; i < opts.length && idx < 0; i++) if (norm(opts[i].text) === norm(v)) idx = i;
      for (i = 0; i < opts.length && idx < 0; i++) if (norm(opts[i].value) === norm(v)) idx = i;
      if (idx < 0) throw new Error('"' + v + '" is not an option for ' + name);
      el.selectedIndex = idx;
    } else if (el.type === 'checkbox' || el.type === 'radio') {
      var on = /^(y|yes|true|1|on)$/i.test(v);
      if (el.checked !== on) el.click();   /* click fires PS onclick + change */
      return;
    } else {
      el.focus();
      el.value = v;
      fire(el, 'input');
    }
    fire(el, 'change');
    try { el.blur(); } catch (e) {}
  }

  function psMessage(doc) {
    for (var i = 0; i < MESSAGE_SELECTORS.length; i++) {
      var els = doc.querySelectorAll(MESSAGE_SELECTORS[i]);
      for (var j = 0; j < els.length; j++) {
        var t = (els[j].innerText || els[j].textContent || '').trim();
        if (t && visible(els[j])) return t;
      }
    }
    return null;
  }

  function isProcessing(doc) {
    for (var i = 0; i < PROCESSING_SELECTORS.length; i++) {
      var el = doc.querySelector(PROCESSING_SELECTORS[i]);
      if (el && visible(el)) return true;
    }
    return false;
  }

  /* Resolves once the page is loaded, no PS processing indicator is shown and the
     DOM has been quiet for settleMs. Handles both AJAX updates and full reloads. */
  function waitIdle(frame, timeout, leaving) {
    var start = Date.now(), quietSince = Date.now(), doc = null, obs = null;
    return new Promise(function (resolve, reject) {
      (function poll() {
        var d;
        try { d = frame.contentDocument; } catch (e) { return reject(new Error('Workspace page is not accessible (different origin?)')); }
        if (d !== doc) {
          if (obs) obs.disconnect();
          doc = d; obs = null; quietSince = Date.now();
          if (leaving) leaving.done = leaving.done || leaving.doc !== d;
          if (d && d.documentElement) {
            obs = new MutationObserver(function () { quietSince = Date.now(); });
            obs.observe(d.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
          }
        }
        var navigating = leaving && leaving.started && !leaving.done;
        var busy = navigating || !d || d.readyState !== 'complete' || d.URL === 'about:blank' || isProcessing(d);
        if (!busy && Date.now() - quietSince >= RUN.settleMs) { if (obs) obs.disconnect(); return resolve(d); }
        if (Date.now() - start > timeout) { if (obs) obs.disconnect(); return reject(new Error('PeopleSoft did not finish processing in time')); }
        setTimeout(poll, RUN.pollMs);
      })();
    });
  }

  /* Run fn (which may trigger a PS round trip) and wait until the page settles. */
  function act(frame, fn) {
    var win = frame.contentWindow, leaving = { doc: frame.contentDocument, started: false, done: false };
    function onLeave() { leaving.started = true; }
    try { win.addEventListener('beforeunload', onLeave); } catch (e) {}
    fn();
    return sleep(RUN.afterActionMs)
      .then(function () { return waitIdle(frame, RUN.stepTimeout, leaving); })
      .then(function (doc) { try { win.removeEventListener('beforeunload', onLeave); } catch (e) {} return doc; });
  }

  function waitForField(frame, spec, row) {
    var start = Date.now();
    return (function check() {
      var el = frame.contentDocument && findField(frame.contentDocument, spec, row);
      if (el) return Promise.resolve(el);
      if (Date.now() - start > RUN.stepTimeout) return Promise.reject(new Error('Could not find "' + fieldName(spec) + '" on the page'));
      return sleep(RUN.pollMs * 2).then(check);
    })();
  }

  function componentUrl(comp, query) {
    var m = location.pathname.match(/\/ps[cp]\/([^\/]+)\/([^\/]+)\/([^\/]+)\//);
    var site = m ? m[1].replace(/_newwin$/i, '').replace(/_\d+$/, '') : 'ps';
    var portal = m ? m[2] : 'EMPLOYEE';
    var node = comp.node || (m ? m[3] : 'HRMS');
    var qs = [];
    for (var k in (query || {})) qs.push(encodeURIComponent(k) + '=' + encodeURIComponent(query[k]));
    return '/psc/' + site + '/' + portal + '/' + node + '/c/' + comp.menu + '.' + comp.component + '.' + (comp.market || 'GBL')
      + (qs.length ? '?' + qs.join('&') : '');
  }

  function loadUrl(frame, url) {
    return new Promise(function (resolve) {
      function done() { frame.removeEventListener('load', done); resolve(); }
      frame.addEventListener('load', done);
      frame.src = url;
    }).then(function () { return waitIdle(frame, RUN.stepTimeout); });
  }

  function fillSecrets(v, secrets) {
    if (Array.isArray(v)) return v.map(function (x) { return fillSecrets(x, secrets); });
    if (typeof v !== 'string') return v;
    return v.replace(/\{\{\s*secret:([a-z0-9_]+)\s*\}\}/g, function (_, n) {
      if (!(n in secrets)) throw new Error('Missing secret value for ' + n);
      return secrets[n];
    });
  }

  function describe(step) {
    if (step.say) return step.say;
    switch (step['do']) {
      case 'set': return 'Set ' + fieldName(step.field);
      case 'click': return 'Click ' + (step.button.text || step.button.id);
      case 'tab': return 'Open the ' + step.label + ' tab';
      case 'grid': return 'Fill ' + fieldName(step.field) + ' rows';
      case 'wait': return 'Wait for ' + fieldName(step.field);
      case 'expect_saved': return 'Check the save result';
      default: return step['do'];
    }
  }

  var STEPS = {
    navigate: function (frame, step, action) {
      return loadUrl(frame, componentUrl(action.component, step.query));
    },
    wait: function (frame, step) {
      return waitForField(frame, step.field);
    },
    set: function (frame, step) {
      return waitForField(frame, step.field).then(function (el) {
        return act(frame, function () { setValue(el, step.value, fieldName(step.field)); });
      });
    },
    click: function (frame, step) {
      var btn = findButton(frame.contentDocument, step.button);
      if (!btn) return Promise.reject(new Error('Could not find the "' + (step.button.text || step.button.id) + '" button'));
      return act(frame, function () { btn.click(); });
    },
    tab: function (frame, step) {
      var doc = frame.contentDocument, want = norm(step.label), links = doc.querySelectorAll('a'), i;
      for (i = 0; i < links.length; i++) {
        if (norm(links[i].textContent) === want && visible(links[i])) {
          var link = links[i];
          return act(frame, function () { link.click(); });
        }
      }
      /* Active tab is rendered as plain text, not a link */
      var all = doc.querySelectorAll('[id^="PSTAB"],[id^="ICTAB"],[class*="TAB"] span,[class*="TAB"] td,[role="tab"]');
      for (i = 0; i < all.length; i++) if (norm(all[i].textContent) === want) return Promise.resolve();
      return Promise.reject(new Error('Could not find the "' + step.label + '" tab'));
    },
    grid: function (frame, step) {
      var values = step.values || [], row = 0, k = 0;
      function next() {
        if (k >= values.length) return Promise.resolve();
        var doc = frame.contentDocument, value = values[k];
        var el = findField(doc, step.field, row);
        if (el && el.value && el.value !== value) { row++; return next(); }        /* row already used */
        if (el && el.value === value) { row++; k++; return next(); }               /* already there */
        var ready = el ? Promise.resolve(el) : addRow(frame, step.field, row);
        return ready.then(function (cell) {
          return act(frame, function () { setValue(cell, value, fieldName(step.field)); });
        }).then(function (d) {
          var msg = psMessage(d);
          if (msg) throw new Error(msg);
          row++; k++;
          return next();
        });
      }
      return next();
    },
    expect_saved: function (frame) {
      var doc = frame.contentDocument, msg = psMessage(doc);
      if (msg && !/saved/i.test(msg)) return Promise.reject(new Error(msg));
      var saved = doc.querySelector('#SAVED_win0,[id^="SAVED_"]');
      if (saved && visible(saved)) return Promise.resolve('Saved');
      return Promise.reject(new Error('No PeopleSoft error appeared, but the "Saved" confirmation was not seen. Please check the page.'));
    }
  };

  function addRow(frame, spec, row) {
    var prev = findField(frame.contentDocument, spec, row - 1);
    var tr = prev && closest(prev, 'TR');
    var btn = tr && tr.querySelector('[id*="$new$"]');
    if (!btn) return Promise.reject(new Error('Could not add a row for ' + fieldName(spec)));
    return act(frame, function () { btn.click(); }).then(function () { return waitForField(frame, spec, row); });
  }

  /* action = {run_id, title, component, steps}; secrets = {password: '...'}; ws = workspace */
  function runAction(action, secrets, ws) {
    var steps = action.steps || [], i = 0, frame = ws.frame;
    function next() {
      if (ws.stopped) return Promise.resolve({ status: 'stopped', message: 'Stopped by the user', failed_step: describe(steps[i] || {}) });
      if (i >= steps.length) return Promise.resolve({ status: 'success', message: 'Saved' });
      var raw = steps[i], step;
      ws.progress(i + 1, steps.length, describe(raw));
      try {
        step = JSON.parse(JSON.stringify(raw));
        step.value = fillSecrets(step.value, secrets);
      } catch (e) { return Promise.resolve({ status: 'error', message: e.message, failed_step: describe(raw) }); }
      var handler = STEPS[step['do']];
      if (!handler) return Promise.resolve({ status: 'error', message: 'Unknown step ' + step['do'], failed_step: describe(raw) });
      return handler(frame, step, action).then(function () {
        /* Any PS message box after a step means a human would stop and read it */
        if (step['do'] !== 'navigate' && step['do'] !== 'expect_saved') {
          var msg = frame.contentDocument && psMessage(frame.contentDocument);
          if (msg) return { status: 'error', message: msg, failed_step: describe(raw) };
        }
        i++;
        return next();
      }, function (err) {
        return { status: 'error', message: (err && err.message) || String(err), failed_step: describe(raw) };
      });
    }
    return next();
  }

  /* ============================================================
     WORKSPACE — visible frame where the agent works
     ============================================================ */

  var workspace = null;

  function openWorkspace(title) {
    if (!workspace) {
      var el = document.createElement('div');
      el.id = 'psa-ws';
      el.setAttribute('role', 'region');
      el.setAttribute('aria-label', 'Agent workspace');
      el.innerHTML =
        '<div id="psa-ws-hdr"><strong id="psa-ws-title"></strong><span id="psa-ws-status" aria-live="polite"></span>'
        + '<button id="psa-ws-stop" type="button">Stop</button><button id="psa-ws-close" type="button" aria-label="Close workspace">&#x2715;</button></div>'
        + '<div id="psa-ws-extra"></div>'
        + '<iframe id="psa-ws-frame" title="Agent workspace"></iframe>';
      document.body.appendChild(el);
      workspace = {
        el: el,
        frame: el.querySelector('#psa-ws-frame'),
        stopped: false,
        progress: function (n, total, text) { this.status('Step ' + n + '/' + total + ': ' + text); },
        status: function (text) { el.querySelector('#psa-ws-status').textContent = text; },
        done: function () { el.querySelector('#psa-ws-stop').style.display = 'none'; },
        extra: el.querySelector('#psa-ws-extra')
      };
      el.querySelector('#psa-ws-stop').addEventListener('click', function () { workspace.stopped = true; workspace.status('Stopping after this step...'); });
      el.querySelector('#psa-ws-close').addEventListener('click', function () { workspace.stopped = true; el.style.display = 'none'; });
    }
    workspace.stopped = false;
    workspace.el.style.display = 'flex';
    workspace.el.querySelector('#psa-ws-stop').style.display = '';
    workspace.el.querySelector('#psa-ws-title').textContent = title;
    workspace.extra.innerHTML = '';
    workspace.status('');
    return workspace;
  }

  /* ============================================================
     TEACH MODE + INSPECT — build playbooks by doing the task once
     ============================================================ */

  function labelOf(doc, el) {
    if (el.id) {
      var l = doc.querySelector('label[for="' + el.id.replace(/["\\]/g, '\\$&') + '"]');
      if (l) return l.textContent.replace(/\s+/g, ' ').replace(/[\s:*]+$/, '').trim();
    }
    return el.getAttribute('aria-label') || el.title || '';
  }

  function inspect() {
    var frame = workspace && workspace.el.style.display !== 'none' ? workspace.frame : getContentFrameEl();
    var doc = frame ? frame.contentDocument : document;
    var rows = [];
    doc.querySelectorAll('input,select,textarea').forEach(function (el) {
      if (!isInput(el) || !el.id) return;
      rows.push({ id: el.id, label: labelOf(doc, el), type: el.tagName === 'SELECT' ? 'select' : el.type,
        value: el.type === 'password' ? '***' : el.value });
    });
    doc.querySelectorAll('a[id],input[type="button"],button[id]').forEach(function (el) {
      var t = (el.tagName === 'INPUT' ? el.value : el.textContent).trim();
      if (t && visible(el)) rows.push({ id: el.id, label: t, type: 'button', value: '' });
    });
    if (console.table) console.table(rows);
    return rows;
  }

  function teach(component, query) {
    var parts = String(component).split('.');
    var ws = openWorkspace('Teaching: ' + component);
    var steps = [{ 'do': 'navigate', query: query || {} }];
    ws.done();
    ws.extra.innerHTML = '<span>Do the task by hand below, then </span><button type="button" id="psa-teach-done">Finish teaching</button>';

    function record(step) {
      var last = steps[steps.length - 1];
      if (step['do'] === 'set' && last['do'] === 'set' && last.field.id === step.field.id) steps[steps.length - 1] = step;
      else steps.push(step);
      ws.status(steps.length + ' steps recorded');
    }
    function attach() {
      var doc = ws.frame.contentDocument;
      if (!doc || doc._psaTeach) return;
      doc._psaTeach = true;
      doc.addEventListener('change', function (e) {
        var el = e.target;
        if (!isInput(el) || !el.id || el.type === 'button') return;
        var value = el.type === 'password' ? '{{secret:password}}'
          : el.type === 'checkbox' ? (el.checked ? 'Y' : 'N')
          : el.tagName === 'SELECT' ? (el.options[el.selectedIndex] || {}).text : el.value;
        record({ 'do': 'set', field: { id: el.id, label: labelOf(doc, el) }, value: value });
      }, true);
      doc.addEventListener('click', function (e) {
        var el = e.target.closest && e.target.closest('a,button,input[type="button"],input[type="submit"]');
        if (!el) return;
        var text = (el.tagName === 'INPUT' ? el.value : el.textContent).replace(/\s+/g, ' ').trim();
        if (/^(ICTAB|PSTAB)/.test(el.id) || /tab/i.test(el.parentElement && el.parentElement.className)) record({ 'do': 'tab', label: text });
        else if (el.id || text) record({ 'do': 'click', button: { id: el.id || undefined, text: text || undefined } });
      }, true);
    }
    ws.frame.addEventListener('load', attach);
    ws.frame.src = componentUrl({ menu: parts[0], component: parts[1], market: parts[2] || 'GBL' }, query);

    return new Promise(function (resolve) {
      ws.extra.querySelector('#psa-teach-done').addEventListener('click', function () {
        ws.frame.removeEventListener('load', attach);
        var json = JSON.stringify(steps, null, 2);
        ws.extra.innerHTML = '<div>Recorded playbook. Paste it under <code>playbook:</code> in the task YAML and replace typed values with <code>{{field}}</code>:</div>'
          + '<textarea readonly rows="10" style="width:100%;font:12px monospace">' + esc(json) + '</textarea>';
        try { navigator.clipboard.writeText(json); } catch (e) {}
        console.log(json);
        resolve(steps);
      });
    });
  }

  /* ============================================================
     PAGE CONTEXT — what the user is looking at
     ============================================================ */

  function getContentFrameEl() {
    return document.getElementById('ptifrmtgtframe') || document.getElementById('ptifrmcontent')
      || document.querySelector('iframe[name="TargetContent"]');
  }

  function pageContext() {
    var ctx = { title: '', component: '', menu: '', error: '' };
    var fr = getContentFrameEl(), w = window, d = document;
    try { if (fr && fr.contentWindow) { w = fr.contentWindow; d = fr.contentDocument; } } catch (e) {}
    try {
      var m = w.location.href.match(/\/ps[cp][^/]*\/[^/]+\/[^/]+\/[^/]+\/[ch]\/([^?#/]+)/i);
      if (m) { var p = m[1].split('.'); ctx.menu = p[0] || ''; ctx.component = p[1] || ''; }
      ctx.title = (w.szCrefLabel || w.szPinCrefLabel || (d && d.title) || '').trim();
      ctx.error = (d && psMessage(d)) || psMessage(document) || '';
    } catch (e) {}
    return ctx;
  }

  /* ============================================================
     CHAT WIDGET
     ============================================================ */

  var STORE_KEY = 'psagent.chat';

  function initWidget() {
    if (document.getElementById('psa-fab')) return;

    var css = '#psa-fab{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg,#003865,#0078d4);border:none;cursor:pointer;box-shadow:0 4px 16px rgba(0,56,101,.45);z-index:2147483647;display:flex;align-items:center;justify-content:center;padding:0;}'
      + '#psa-panel{position:fixed;bottom:92px;right:24px;width:380px;height:580px;max-height:calc(100vh - 110px);border-radius:16px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.22);border:1px solid #d0dce8;display:none;flex-direction:column;background:#fff;z-index:2147483645;font-family:Segoe UI,sans-serif;font-size:13px;color:#222;}'
      + '#psa-panel.psa-open{display:flex;}'
      + '#psa-hdr{background:linear-gradient(135deg,#003865,#0078d4);color:#fff;padding:12px 14px;display:flex;align-items:center;gap:8px;}'
      + '#psa-hdr h3{margin:0;font-size:13px;font-weight:600;flex:1;}'
      + '#psa-hdr button{background:rgba(255,255,255,.2);border:none;color:#fff;border-radius:6px;cursor:pointer;padding:4px 8px;font-size:12px;}'
      + '#psa-log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#fafbfc;}'
      + '.psa-msg{max-width:85%;padding:8px 11px;border-radius:12px;line-height:1.45;word-wrap:break-word;}'
      + '.psa-bot{background:#f0f4f8;border:1px solid #d0dce8;align-self:flex-start;}'
      + '.psa-user{background:#003865;color:#fff;align-self:flex-end;}'
      + '.psa-msg ul{margin:4px 0;padding-left:18px;}'
      + '.psa-chips{display:flex;flex-wrap:wrap;gap:6px;}'
      + '.psa-chips button{border:1px solid #0078d4;background:#fff;color:#0078d4;border-radius:14px;padding:4px 10px;cursor:pointer;font-size:12px;}'
      + '.psa-card{align-self:stretch;border:1px solid #0078d4;border-radius:10px;background:#fff;padding:10px 12px;}'
      + '.psa-card h4{margin:0 0 6px;font-size:13px;}'
      + '.psa-card table{width:100%;border-collapse:collapse;margin-bottom:8px;}'
      + '.psa-card td{padding:3px 4px;border-bottom:1px solid #eef2f6;vertical-align:top;}'
      + '.psa-card td:first-child{color:#555;width:45%;}'
      + '.psa-card label{display:block;font-size:12px;margin:4px 0 2px;color:#444;}'
      + '.psa-card input{width:100%;box-sizing:border-box;padding:5px 7px;border:1px solid #bbb;border-radius:5px;}'
      + '.psa-card .psa-actions{display:flex;gap:8px;margin-top:10px;}'
      + '.psa-card .psa-actions button{flex:1;padding:7px;border-radius:6px;border:1px solid #0078d4;cursor:pointer;font-size:12px;}'
      + '.psa-ok{background:#0078d4;color:#fff;}.psa-ok:disabled{opacity:.5;cursor:default;}'
      + '.psa-no{background:#fff;color:#0078d4;}'
      + '.psa-typing{color:#888;font-style:italic;}'
      + '#psa-form{display:flex;gap:6px;padding:8px;border-top:1px solid #e0e0e0;background:#f8f9fa;}'
      + '#psa-input{flex:1;resize:none;height:38px;padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit;}'
      + '#psa-send{background:#0078d4;color:#fff;border:none;border-radius:8px;padding:0 14px;cursor:pointer;}'
      + '#psa-ws{position:fixed;top:16px;left:16px;right:420px;bottom:16px;display:none;flex-direction:column;background:#fff;border:2px solid #0078d4;border-radius:12px;overflow:hidden;z-index:2147483644;box-shadow:0 8px 40px rgba(0,0,0,.25);font-family:Segoe UI,sans-serif;font-size:13px;}'
      + '#psa-ws-hdr{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#003865;color:#fff;}'
      + '#psa-ws-status{flex:1;opacity:.9;}'
      + '#psa-ws-hdr button{background:rgba(255,255,255,.2);border:none;color:#fff;border-radius:6px;cursor:pointer;padding:4px 10px;}'
      + '#psa-ws-extra:not(:empty){padding:8px 12px;background:#fff8e1;border-bottom:1px solid #f0d58a;}'
      + '#psa-ws-frame{flex:1;border:none;width:100%;}'
      + '#psa-panel *:focus,#psa-ws *:focus{outline:3px solid #003865;outline-offset:2px;}';
    var st = document.createElement('style');
    st.id = 'psa-styles';
    st.textContent = css;
    document.head.appendChild(st);

    var fab = document.createElement('button');
    fab.id = 'psa-fab';
    fab.type = 'button';
    fab.setAttribute('aria-label', 'Open PeopleSoft Agent');
    fab.setAttribute('aria-expanded', 'false');
    fab.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke="white" stroke-width="2" stroke-linejoin="round"/><circle cx="9" cy="10" r="1" fill="white"/><circle cx="12" cy="10" r="1" fill="white"/><circle cx="15" cy="10" r="1" fill="white"/></svg>';
    document.body.appendChild(fab);

    var panel = document.createElement('div');
    panel.id = 'psa-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'PeopleSoft Agent');
    panel.innerHTML =
      '<div id="psa-hdr"><h3>PeopleSoft Agent</h3><button id="psa-new" type="button" title="Start a new conversation">New</button>'
      + '<button id="psa-close" type="button" aria-label="Close">&#x2715;</button></div>'
      + '<div id="psa-log" role="log" aria-live="polite"></div>'
      + '<form id="psa-form"><textarea id="psa-input" aria-label="Message" placeholder="Ask or tell me what to do..."></textarea>'
      + '<button id="psa-send" type="submit">Send</button></form>';
    document.body.appendChild(panel);

    var logEl = panel.querySelector('#psa-log');
    var input = panel.querySelector('#psa-input');
    var state = loadState();
    var busy = false;

    function loadState() {
      try { return JSON.parse(sessionStorage.getItem(STORE_KEY)) || { sessionId: null, history: [] }; }
      catch (e) { return { sessionId: null, history: [] }; }
    }
    function saveState() {
      try { state.history = state.history.slice(-60); sessionStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) {}
    }

    function md(t) {
      var lines = esc(t).split('\n'), out = [], inList = false;
      lines.forEach(function (l) {
        var item = l.match(/^\s*[-*]\s+(.*)$/);
        if (item) { if (!inList) { out.push('<ul>'); inList = true; } out.push('<li>' + item[1] + '</li>'); return; }
        if (inList) { out.push('</ul>'); inList = false; }
        out.push(l + '<br>');
      });
      if (inList) out.push('</ul>');
      return out.join('').replace(/(<br>)+$/, '')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, '$1<em>$2</em>')
        .replace(/`([^`]+)`/g, '<code>$1</code>');
    }

    function addMsg(who, text, keep) {
      var div = document.createElement('div');
      div.className = 'psa-msg ' + (who === 'user' ? 'psa-user' : 'psa-bot');
      div.innerHTML = who === 'user' ? esc(text).replace(/\n/g, '<br>') : md(text);
      logEl.appendChild(div);
      logEl.scrollTop = logEl.scrollHeight;
      if (keep !== false) { state.history.push({ who: who, text: text }); saveState(); }
      return div;
    }

    function addChips(items) {
      var wrap = document.createElement('div');
      wrap.className = 'psa-chips';
      items.forEach(function (label) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        b.addEventListener('click', function () { wrap.remove(); send(label); });
        wrap.appendChild(b);
      });
      logEl.appendChild(wrap);
    }

    function greet() {
      addMsg('bot', 'Hi **' + (cfg.fullname || cfg.oprid || 'there') + '**! I can do PeopleSoft tasks for you on screen, '
        + 'asking for anything I need and showing you a summary before I save.\n\n- Create a user profile\n- Explain an error or page', false);
      addChips(['Create a user profile', 'What is this page for?', 'Help with this error']);
    }

    function typing() {
      var d = document.createElement('div');
      d.className = 'psa-msg psa-bot psa-typing';
      d.textContent = 'Thinking...';
      logEl.appendChild(d);
      logEl.scrollTop = logEl.scrollHeight;
      return d;
    }

    function setBusy(b) {
      busy = b;
      panel.querySelector('#psa-send').disabled = b;
    }

    function handleReply(r) {
      if (r.session_id) { state.sessionId = r.session_id; saveState(); }
      (r.messages || []).forEach(function (m) { addMsg('bot', m); });
      if (r.confirm) showConfirm(r.confirm);
      if (r.action) return runAndReport(r.action);
    }

    function call(path, payload) {
      var t = typing();
      setBusy(true);
      return api(path, payload)
        .then(function (r) { t.remove(); return handleReply(r); })
        .catch(function (e) { t.remove(); addMsg('bot', 'Sorry, I could not reach the agent service (' + e.message + ').', false); })
        .then(function () { setBusy(false); });
    }

    function send(text) {
      text = String(text || '').trim();
      if (!text || busy) return;
      addMsg('user', text);
      call('/api/chat', { session_id: state.sessionId, message: text, page: pageContext() });
    }

    function showConfirm(c) {
      var card = document.createElement('div');
      card.className = 'psa-card';
      var rows = (c.rows || []).map(function (r) { return '<tr><td>' + esc(r.label) + '</td><td><strong>' + esc(r.value) + '</strong></td></tr>'; }).join('');
      var secrets = (c.secrets || []).map(function (s) {
        return '<label>' + esc(s.label) + '<input type="password" autocomplete="new-password" data-secret="' + esc(s.name) + '"></label>'
          + '<label>Confirm ' + esc(s.label) + '<input type="password" autocomplete="new-password" data-confirm="' + esc(s.name) + '"></label>';
      }).join('');
      card.innerHTML = '<h4>' + esc(c.title) + ': please review</h4><table>' + rows + '</table>' + secrets
        + '<div class="psa-actions"><button type="button" class="psa-ok">Confirm</button><button type="button" class="psa-no">Cancel</button></div>';
      logEl.appendChild(card);
      logEl.scrollTop = logEl.scrollHeight;

      var ok = card.querySelector('.psa-ok'), no = card.querySelector('.psa-no');
      function values() {
        var out = {}, valid = true;
        card.querySelectorAll('[data-secret]').forEach(function (inp) {
          var conf = card.querySelector('[data-confirm="' + inp.getAttribute('data-secret') + '"]');
          if (!inp.value || inp.value !== conf.value) valid = false;
          out[inp.getAttribute('data-secret')] = inp.value;
        });
        return valid ? out : null;
      }
      function refresh() { ok.disabled = !values(); }
      card.querySelectorAll('input').forEach(function (i) { i.addEventListener('input', refresh); });
      refresh();

      function close() { ok.disabled = no.disabled = true; card.querySelectorAll('input').forEach(function (i) { i.value = ''; i.disabled = true; }); }
      ok.addEventListener('click', function () {
        var secretValues = values();
        if (!secretValues) return;
        close();
        pendingSecrets[c.run_id] = secretValues;
        call('/api/confirm', { session_id: state.sessionId, run_id: c.run_id, approved: true });
      });
      no.addEventListener('click', function () {
        close();
        call('/api/confirm', { session_id: state.sessionId, run_id: c.run_id, approved: false });
      });
    }

    var pendingSecrets = {};  /* run_id -> secrets, kept only in memory until the run ends */

    function runAndReport(action) {
      var secrets = pendingSecrets[action.run_id] || {};
      delete pendingSecrets[action.run_id];
      var ws = openWorkspace('Agent: ' + action.title);
      return runAction(action, secrets, ws).then(function (res) {
        secrets = null;
        ws.done();
        ws.status(res.status === 'success' ? 'Done: ' + action.title : (res.status === 'stopped' ? 'Stopped' : 'Stopped: ' + res.message));
        return api('/api/result', { session_id: state.sessionId, run_id: action.run_id, status: res.status, message: res.message, failed_step: res.failed_step });
      }).then(function (r) { return handleReply(r); });
    }

    function open() {
      panel.classList.add('psa-open');
      fab.setAttribute('aria-expanded', 'true');
      if (!logEl.childNodes.length) {
        ensureConfig().then(function () {
          if (state.history.length) state.history.forEach(function (h) { addMsg(h.who, h.text, false); });
          else greet();
        }, function () { addMsg('bot', 'Sorry, the agent is not available right now.', false); });
      }
      setTimeout(function () { input.focus(); }, 100);
    }
    function close() { panel.classList.remove('psa-open'); fab.setAttribute('aria-expanded', 'false'); fab.focus(); }

    fab.addEventListener('click', function () { panel.classList.contains('psa-open') ? close() : open(); });
    panel.querySelector('#psa-close').addEventListener('click', close);
    panel.querySelector('#psa-new').addEventListener('click', function () {
      state = { sessionId: null, history: [] };
      saveState();
      logEl.innerHTML = '';
      greet();
    });
    panel.querySelector('#psa-form').addEventListener('submit', function (e) {
      e.preventDefault();
      var t = input.value;
      input.value = '';
      send(t);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); panel.querySelector('#psa-send').click(); }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && panel.classList.contains('psa-open')) close();
    });
  }

  /* ============================================================
     CONFIG + API
     ============================================================ */

  var configPromise = null;

  function ensureConfig(force) {
    if (configPromise && !force) return configPromise;
    if (window.PSAGENT_CONFIG && !force) {
      for (var k in window.PSAGENT_CONFIG) cfg[k] = window.PSAGENT_CONFIG[k];
      return (configPromise = Promise.resolve(cfg));
    }
    configPromise = fetch(CONFIG_ISCRIPT_URL, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (c) {
        cfg.agentUrl = c.agentUrl; cfg.token = c.token; cfg.oprid = c.oprid; cfg.fullname = c.fullname;
        return cfg;
      });
    configPromise.catch(function () { configPromise = null; });
    return configPromise;
  }

  function api(path, payload, retried) {
    return ensureConfig().then(function () {
      return fetch(cfg.agentUrl.replace(/\/$/, '') + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg.token },
        body: JSON.stringify(payload)
      });
    }).then(function (r) {
      if (r.status === 401 && !retried && !window.PSAGENT_CONFIG) {
        return ensureConfig(true).then(function () { return api(path, payload, true); });
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  window.PSAgent = {
    inspect: inspect,
    teach: teach,
    run: function (action, secrets) { return runAction(action, secrets || {}, openWorkspace('Agent: ' + (action.title || 'test'))); }
  };

  function start() {
    if (document.body) initWidget();
    else setTimeout(start, 250);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

})();
