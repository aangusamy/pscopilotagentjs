/* ============================================================
   PEOPLESOFT AGENT WIDGET — chat + on-screen task automation
   Load it on every page: Branding Object (JavaScript) added under
   Branding System Options > Additional JavaScript Objects.

   Talks to the Python agent service (agent-service/). The agent
   collects the values by chat, the user confirms, and this widget
   performs the task on the real PeopleSoft component inside a
   visible workspace frame, the way a person would.

   Teaching a new task: click "Record" in the chat header (shown to
   users in PSAGENT_TEACHERS) or say "let me teach you ...". Name the
   task, then go to the page as usual and do it once, including Save.
   A red bar follows you across pages; Finish, review, save. Done.

   Console helpers:
     PSAgent.inspect()                         list fields on the current page
     PSAgent.teach('MENU.COMPONENT.GBL', {Action:'A'})
                                               start recording on that page
   ============================================================ */

(function () {

  var VERSION = '2026-10-23.3';   /* shown as PSAgent.version and in the console: check after uploading */

  /* iScript that returns the agent URL + token. Site, portal and node come from the
     current page URL; change the WEBLIB part if you named the record differently. */
  var CONFIG_ISCRIPT = 's/WEBLIB_PSAGENT.ISCRIPT1.FieldFormula.IScript_PSAgentConfig';
  function configIscriptUrl() {
    var m = location.pathname.match(/\/ps[cp]\/([^\/]+)\/([^\/]+)\/([^\/]+)\//);
    var site = m ? m[1].replace(/_newwin$/i, '').replace(/_\d+$/, '') : 'ps';
    return '/psc/' + site + '/' + (m ? m[2] : 'EMPLOYEE') + '/' + (m ? m[3] : 'HRMS') + '/' + CONFIG_ISCRIPT;
  }

  var RUN = { stepTimeout: 30000, settleMs: 400, pollMs: 150, afterActionMs: 300 };

  /* Populated at runtime from the iScript (or window.PSAGENT_CONFIG for local testing) */
  var cfg = { agentUrl: '', token: '', oprid: '', fullname: '' };

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function norm(t) { return String(t == null ? '' : t).replace(/\s+/g, ' ').replace(/^[\s*]+|[\s:*]+$/g, '').trim().toLowerCase(); }
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
    '.ps-exception-msg', '.ps_box-error', '.ps-alert-error',
    '#PTS_CRITTEXT_DISP'            /* 8.5x search page, e.g. "The value you tried to add already exists." */
  ];
  /* PeopleSoft draws message boxes in the TOP window (#pt_modals), also for pages inside a frame */
  var MODAL_SELECTORS = ['#pt_modals #alertmsg', '#pt_modals .popupText'];

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

  function firstVisibleText(doc, selectors) {
    for (var i = 0; i < selectors.length; i++) {
      var els = doc.querySelectorAll(selectors[i]);
      for (var j = 0; j < els.length; j++) {
        var t = (els[j].innerText || els[j].textContent || '').trim();
        if (t && visible(els[j])) return { text: t, el: els[j] };
      }
    }
    return null;
  }

  /* A PeopleSoft message box open in the top window */
  function modalMessage() { return firstVisibleText(document, MODAL_SELECTORS); }

  /* Message PeopleSoft shows for the page in doc: on the page itself, or as a box in the top window */
  function psMessage(doc) {
    var m = (doc && firstVisibleText(doc, MESSAGE_SELECTORS)) || modalMessage();
    return m ? m.text : null;
  }

  /* Close a message box whose only button is OK (after its text was captured).
     Yes/No questions are left open for the person to answer. */
  function dismissModal() {
    var m = modalMessage();
    if (!m) return false;
    var box = (m.el.closest && (m.el.closest('[id^="ptMod_"]') || m.el.closest('#pt_modals'))) || document;
    var btns = [].filter.call(box.querySelectorAll('input[type="button"],input[type="submit"],button'), function (b) {
      return visible(b) && (b.value || b.textContent || '').trim();
    });
    if (btns.length !== 1 || !/^ok$/i.test((btns[0].value || btns[0].textContent).trim())) return false;
    btns[0].click();
    return true;
  }

  /* Fields PeopleSoft marked as wrong (red) */
  function errorFields(doc) {
    var out = [];
    [].forEach.call(doc.querySelectorAll('input[class*="PSERROR"],select[class*="PSERROR"],textarea[class*="PSERROR"]'), function (el) {
      if (visible(el)) out.push(labelOf(doc, el) || columnOf(el) || el.id);
    });
    return out;
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
  /* Count the page's in-flight server requests (PeopleSoft AJAX round trips). */
  function trackRequests(win) {
    try {
      if (!win || win.__psaReq) return;
      var X = win.XMLHttpRequest && win.XMLHttpRequest.prototype;
      if (!X) return;
      win.__psaReq = { inflight: 0, last: Date.now() };
      var send = X.send;
      X.send = function () {
        var st = win.__psaReq, xhr = this, ended = false;
        function end() { if (!ended) { ended = true; st.inflight--; st.last = Date.now(); } }
        st.inflight++; st.last = Date.now();
        xhr.addEventListener('loadend', end);
        try { return send.apply(xhr, arguments); } catch (e) { end(); throw e; }
      };
    } catch (e) {}
  }

  function requestsBusy(win) {
    try {
      var st = win && win.__psaReq;
      return !!st && (st.inflight > 0 || Date.now() - st.last < RUN.settleMs);
    } catch (e) { return false; }
  }

  function waitIdle(frame, timeout, leaving) {
    var start = Date.now(), quietSince = Date.now(), doc = null, obs = null;
    return new Promise(function (resolve, reject) {
      (function poll() {
        var d;
        try { d = frame.contentDocument; } catch (e) { return reject(new Error('Workspace page is not accessible (different origin?)')); }
        if (d !== doc) {
          if (obs) obs.disconnect();
          doc = d; obs = null; quietSince = Date.now();
          trackRequests(frame.contentWindow);
          if (leaving) leaving.done = leaving.done || leaving.doc !== d;
          if (d && d.documentElement) {
            obs = new MutationObserver(function () { quietSince = Date.now(); });
            obs.observe(d.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
          }
        }
        var navigating = leaving && leaving.started && !leaving.done;
        var busy = navigating || !d || d.readyState !== 'complete' || d.URL === 'about:blank' || isProcessing(d)
          || requestsBusy(frame.contentWindow);
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
    trackRequests(win);
    fn();
    return sleep(RUN.afterActionMs)
      .then(function () { return waitIdle(frame, RUN.stepTimeout, leaving); })
      .then(function (doc) { try { win.removeEventListener('beforeunload', onLeave); } catch (e) {} return doc; });
  }

  function waitForField(frame, spec, row) {
    var start = Date.now();
    return (function check() {
      var doc = frame.contentDocument;
      var el = doc && findField(doc, spec, row);
      if (el) return Promise.resolve(el);
      var msg = doc && psMessage(doc);   /* PeopleSoft refused the last action: report its message, don't wait */
      if (msg) return Promise.reject(new Error(msg));
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
        return enterValue(frame, el, step.field, null, step.value);
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
          return enterValue(frame, cell, step.field, row, value);
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
      var bad = errorFields(doc);
      if (bad.length) return Promise.reject(new Error('PeopleSoft marked these fields as invalid: ' + bad.join(', ')));
      return Promise.reject(new Error('No PeopleSoft error appeared, but the "Saved" confirmation was not seen. Please check the page.'));
    }
  };

  /* Like a person: move into the field, let PeopleSoft process the previous field's
     change (it does that on focus and may redraw this field), then type. */
  function enterValue(frame, el, spec, row, value) {
    return act(frame, function () { try { el.focus(); } catch (e) {} })
      .then(function (doc) {
        var msg = psMessage(doc);
        if (msg) throw new Error(msg);
        var cur = findField(doc, spec, row) || el;
        return act(frame, function () { setValue(cur, value, fieldName(spec)); });
      });
  }

  function addRow(frame, spec, row) {
    var prev = findField(frame.contentDocument, spec, row - 1);
    var tr = prev && closest(prev, 'TR');
    var btn = tr && tr.querySelector('[id*="$new$"]');
    if (!btn) return Promise.reject(new Error('Could not add a row for ' + fieldName(spec)));
    return act(frame, function () { btn.click(); }).then(function () { return waitForField(frame, spec, row); });
  }

  /* action = {run_id, title, component, steps}; secrets = {password: '...'}; ws = workspace */
  function runAction(action, secrets, ws) {
    var steps = action.steps || [], i = 0, frame = ws.frame, saved = false;
    dismissModal();   /* a message box left over from earlier would be mistaken for this run's */
    function fail(message, raw) {
      dismissModal();
      return { status: 'error', message: message, failed_step: describe(raw || {}) };
    }
    function next() {
      if (ws.stopped) return Promise.resolve({ status: 'stopped', message: 'Stopped by the user', failed_step: describe(steps[i] || {}) });
      if (i >= steps.length) return Promise.resolve({ status: 'success', message: saved ? 'Saved' : 'Completed (no save step)' });
      var raw = steps[i], step;
      ws.progress(i + 1, steps.length, describe(raw));
      try {
        step = JSON.parse(JSON.stringify(raw));
        step.value = fillSecrets(step.value, secrets);
      } catch (e) { return Promise.resolve(fail(e.message, raw)); }
      var handler = STEPS[step['do']];
      if (!handler) return Promise.resolve(fail('Unknown step ' + step['do'], raw));
      var run;
      try { run = handler(frame, step, action); } catch (e) { run = Promise.reject(e); }
      return run.then(function () {
        if (step['do'] === 'expect_saved') saved = true;
        /* Any PS message after a step means a human would stop and read it */
        if (step['do'] !== 'navigate' && step['do'] !== 'expect_saved') {
          var msg = psMessage(frame.contentDocument);
          if (msg) return fail(msg, raw);
        }
        i++;
        return next();
      }, function (err) {
        return fail((err && err.message) || String(err), raw);
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
        status: function (text, isError) {
          var st = el.querySelector('#psa-ws-status');
          st.textContent = text;
          st.className = isError ? 'psa-err' : '';
        },
        done: function () { el.querySelector('#psa-ws-stop').style.display = 'none'; },
        extra: el.querySelector('#psa-ws-extra')
      };
      el.querySelector('#psa-ws-stop').addEventListener('click', function () { workspace.stopped = true; workspace.status('Stopping after this step...'); });
      el.querySelector('#psa-ws-close').addEventListener('click', function () {
        workspace.stopped = true;
        el.style.display = 'none';
        if (workspace.onClose) workspace.onClose();
      });
    }
    workspace.stopped = false;
    workspace.onClose = null;
    workspace.el.style.display = 'flex';
    workspace.el.querySelector('#psa-ws-stop').style.display = '';
    workspace.el.querySelector('#psa-ws-title').textContent = title;
    workspace.extra.innerHTML = '';
    workspace.status('');
    return workspace;
  }

  /* ============================================================
     RECORDER + INSPECT — learn a task by watching a person do it once
     ============================================================ */

  function labelOf(doc, el) {
    if (el.id) {
      var l = doc.querySelector('label[for="' + el.id.replace(/["\\]/g, '\\$&') + '"]');
      if (l) return l.textContent.replace(/\s+/g, ' ').replace(/^[\s*]+|[\s:*]+$/g, '').trim();
    }
    return el.getAttribute('aria-label') || el.title || '';
  }

  /* Header text of the grid column an input sits in ("Role Name") */
  function columnOf(el) {
    var td = closest(el, 'TD'), tr = td && closest(td, 'TR'), table = tr && closest(tr, 'TABLE');
    if (!table) return '';
    var col = 0, c;
    for (c = tr.firstElementChild; c && c !== td; c = c.nextElementSibling) col += c.colSpan || 1;
    for (var r = 0; r < table.rows.length && table.rows[r] !== tr; r++) {
      var row = table.rows[r];
      if (!row.querySelector('th,[role="columnheader"],td[class*="GRIDCOLUMNHDR"],td[class*="GridColumnHdr"]')) continue;
      var pos = 0;
      for (c = row.firstElementChild; c; c = c.nextElementSibling) {
        if (pos === col) return c.textContent.replace(/\s+/g, ' ').replace(/^[\s*]+/, '').trim();   /* '*' marks required fields */
        pos += c.colSpan || 1;
      }
    }
    return '';
  }

  function inspect() {
    var frame = workspace && workspace.el.style.display !== 'none' ? workspace.frame : getContentFrameEl();
    var doc = frame ? frame.contentDocument : document;
    var rows = [];
    doc.querySelectorAll('input,select,textarea').forEach(function (el) {
      if (!isInput(el) || !el.id) return;
      rows.push({ id: el.id, label: labelOf(doc, el) || columnOf(el), type: el.tagName === 'SELECT' ? 'select' : el.type,
        value: el.type === 'password' ? '***' : el.value });
    });
    doc.querySelectorAll('a[id],input[type="button"],button[id]').forEach(function (el) {
      var t = (el.tagName === 'INPUT' ? el.value : el.textContent).trim();
      if (t && visible(el)) rows.push({ id: el.id, label: t, type: 'button', value: '' });
    });
    if (console.table) console.table(rows);
    return rows;
  }

  function kindOf(el) {
    if (el.type === 'password') return 'password';
    if (el.tagName === 'SELECT') return 'select';
    if (el.type === 'checkbox' || el.type === 'radio') return 'checkbox';
    return 'text';
  }
  function valueOf(el) {
    var k = kindOf(el);
    if (k === 'password') return '';
    if (k === 'select') return el.selectedIndex >= 0 ? el.options[el.selectedIndex].text.trim() : '';
    if (k === 'checkbox') return el.checked ? 'Y' : 'N';
    return el.value;
  }
  function recordable(el) {
    return isInput(el) && el.id && !/^(button|submit|reset|image|file)$/.test(el.type);
  }

  /* Recording follows the person through normal PeopleSoft navigation (NavBar, menus,
     search pages). The widget is loaded on every page, so the recording lives in
     sessionStorage and resumes after each page load. Password values are never
     captured, only that a password was typed. */

  var REC_KEY = 'psagent.rec';
  /* Homepages, NavBar and portal pages are navigation only: nothing to record there */
  var FRAMEWORK_MENU = /^(NUI_FRAMEWORK|PT_FLDASHBOARD|PTPP_PORTAL|PORTAL_ADMIN)$/i;

  function recLoad() { try { return JSON.parse(sessionStorage.getItem(REC_KEY)); } catch (e) { return null; } }
  function recSave(rec) {
    try { if (rec) sessionStorage.setItem(REC_KEY, JSON.stringify(rec)); else sessionStorage.removeItem(REC_KEY); } catch (e) {}
  }
  function isSet(st) { return st['do'] === 'set'; }
  function isFrameworkPage(comp) { return !!comp && FRAMEWORK_MENU.test(comp.menu); }

  /* {component, query} of a PeopleSoft component URL; portal and internal parameters dropped */
  function parseComponentUrl(href) {
    var m = String(href || '').match(/\/ps[cp]\/[^\/]+\/[^\/]+\/[^\/]+\/c\/([^?#\/]+)(?:\?([^#]*))?/i);
    if (!m) return null;
    var p = m[1].split('.');
    if (p.length < 2 || !/^[A-Z0-9_]+$/i.test(p[0]) || !/^[A-Z0-9_]+$/i.test(p[1])) return null;
    var query = {};
    (m[2] || '').split('&').forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf('='), k, v;
      try { k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i)); v = i < 0 ? '' : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); }
      catch (e) { return; }
      if (!/^(Portal|NoCrumbs|IC|cmd$|nav|FolderPath|IsFolder)/i.test(k)) query[k] = v;
    });
    return { component: { menu: p[0].toUpperCase(), component: p[1].toUpperCase(), market: (p[2] || 'GBL').toUpperCase() }, query: query };
  }

  /* Where the transaction is shown: the classic content frame when the page has one (also
     while it is still loading), otherwise the page itself (Fluid) */
  function workTarget() {
    var fr = getContentFrameEl();
    if (fr) {
      try { return { win: fr.contentWindow, doc: fr.contentDocument, frame: fr }; } catch (e) { return { win: null, doc: null, frame: fr }; }
    }
    return { win: window, doc: document, frame: null };
  }

  /* Record what the person does on doc into rec.steps; changed() runs after each step */
  function attachRecorder(doc, rec, changed) {
    if (doc._psaRec) return;
    var snap = {};
    function ignored(el) {
      /* the agent's own UI, PeopleSoft's header/NavBar and message boxes are not part of the task */
      return /^PT_/.test(el.id || '')
        || !!(el.closest && el.closest('[id^="psa-"],#pt_modals,#PT_HEADER,#pthdr2container,#PT_NAVBAR,.ps_header'));
    }
    function snapshot() {
      snap = {};
      doc.querySelectorAll('input,select,textarea').forEach(function (el) {
        if (recordable(el) && kindOf(el) !== 'password' && !ignored(el)) snap[el.id] = valueOf(el);
      });
    }
    function setStep(el) {
      /* Only what a person can do: visible, editable fields. PeopleSoft also sets fields itself,
         e.g. the search key again after Add; those are not steps (replaying them fails). */
      if (!visible(el) || el.disabled || el.readOnly) return;
      var field = { id: el.id, label: labelOf(doc, el) };
      if (/\$\d+$/.test(el.id)) { var col = columnOf(el); if (col) field.column = col; }
      var step = { 'do': 'set', field: field, kind: kindOf(el), value: valueOf(el) };
      var last = rec.steps[rec.steps.length - 1], prev = null, i;
      for (i = rec.steps.length - 1; i >= 0 && !prev; i--) if (isSet(rec.steps[i]) && rec.steps[i].field.id === el.id) prev = rec.steps[i];
      if (prev && prev !== last && prev.value === step.value) return;   /* same value again: nothing new */
      if (last && last['do'] === 'set' && last.field.id === el.id) rec.steps[rec.steps.length - 1] = step;
      else rec.steps.push(step);
      if (step.kind !== 'password') snap[el.id] = step.value;
      changed();
    }
    /* Values set by script (lookup prompts, calendars) fire no change event: diff instead */
    function diff() {
      if (rec.stopped) return;
      doc.querySelectorAll('input,select,textarea').forEach(function (el) {
        if (recordable(el) && kindOf(el) !== 'password' && el.id in snap && valueOf(el) !== snap[el.id]) setStep(el);
      });
    }
    doc._psaRec = { diff: diff };
    snapshot();
    doc.addEventListener('change', function (e) {
      if (!rec.stopped && recordable(e.target) && !ignored(e.target)) setStep(e.target);
    }, true);
    doc.addEventListener('click', function (e) {
      if (rec.stopped) return;
      var el = e.target.closest && e.target.closest('a,button,input[type="button"],input[type="submit"]');
      if (!el || ignored(el) || /\$(new|delete|prompt)\$|\$prompt$/i.test(el.id)) return;   /* grid +/- and lookups: replayed by grid steps */
      diff();
      var text = (el.tagName === 'INPUT' ? el.value : el.textContent).replace(/\s+/g, ' ').trim();
      /* The search page's mode switch becomes the task's start mode */
      if (!rec.steps.some(isSet) && /^(add a new value|find an existing value)$/i.test(text)) {
        rec.query = rec.query || {};
        rec.query.Action = /^add/i.test(text) ? 'A' : 'U';
        changed();
        return;
      }
      if (/^(ICTAB|PSTAB)/.test(el.id) || /tab/i.test(el.parentElement && el.parentElement.className)) rec.steps.push({ 'do': 'tab', label: text });
      else if (el.id || text) rec.steps.push({ 'do': 'click', button: { id: el.id || undefined, text: text || undefined } });
      changed();
      /* Re-baseline once PeopleSoft has redrawn the page, so values it fills in itself are not recorded */
      setTimeout(function () { try { if (!rec.stopped) snapshot(); } catch (e2) {} }, 2500);
    }, true);
  }

  /* ============================================================
     PAGE CONTEXT — what the user is looking at
     ============================================================ */

  function getContentFrameEl() {
    return document.getElementById('ptifrmtgtframe') || document.getElementById('ptifrmcontent')
      || document.querySelector('iframe[name="TargetContent"]');
  }

  function pageContext() {
    var ctx = { title: '', component: '', menu: '', market: '', error: '' };
    var fr = getContentFrameEl(), w = window, d = document;
    try { if (fr && fr.contentWindow) { w = fr.contentWindow; d = fr.contentDocument; } } catch (e) {}
    try {
      var m = w.location.href.match(/\/ps[cp][^/]*\/[^/]+\/[^/]+\/[^/]+\/[ch]\/([^?#/]+)/i);
      if (m) { var p = m[1].split('.'); ctx.menu = p[0] || ''; ctx.component = p[1] || ''; ctx.market = p[2] || ''; }
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
      + '#psa-panel{position:fixed;bottom:92px;right:24px;width:380px;height:580px!important;max-height:calc(100vh - 110px);border-radius:16px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.22);border:1px solid #d0dce8;display:none;flex-direction:column;background:#fff;z-index:2147483645;font-family:Segoe UI,sans-serif;font-size:13px;color:#222;}'
      + '#psa-panel.psa-open{display:flex;}'
      + '#psa-hdr{flex:0 0 auto!important;background:linear-gradient(135deg,#003865,#0078d4);color:#fff;padding:12px 14px;display:flex;align-items:center;gap:8px;}'
      + '#psa-hdr h3{margin:0;font-size:13px;font-weight:600;flex:1;}'
      + '#psa-hdr button{background:rgba(255,255,255,.2);border:none;color:#fff;border-radius:6px;cursor:pointer;padding:4px 8px;font-size:12px;}'
      + '#psa-log{flex:1 1 auto!important;min-height:0!important;height:auto!important;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#fafbfc;}'
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
      + '.psa-card select{width:100%;font-size:12px;padding:3px;}'
      + '.psa-card small{color:#777;display:block;}'
      + '.psa-card details{margin:6px 0;font-size:12px;}'
      + '.psa-card ol{margin:4px 0;padding-left:20px;max-height:140px;overflow-y:auto;}'
      + '.psa-card .psa-check{display:flex;gap:6px;align-items:center;}'
      + '.psa-card .psa-check input{width:auto;}'
      /* PeopleSoft styles every form/textarea/button: pin the chat's layout with !important */
      + '#psa-form{display:flex!important;flex:0 0 auto!important;height:auto!important;min-height:0!important;max-height:none!important;margin:0!important;gap:6px;padding:8px;border-top:1px solid #e0e0e0;background:#f8f9fa;align-items:stretch;}'
      + '#psa-input{flex:1 1 auto!important;resize:none;height:40px!important;min-height:40px!important;max-height:40px!important;margin:0!important;padding:8px!important;border:1px solid #ccc;border-radius:8px;font:13px Segoe UI,sans-serif!important;box-sizing:border-box!important;}'
      + '#psa-send{flex:0 0 auto!important;height:40px!important;min-height:0!important;margin:0!important;background:#0078d4!important;color:#fff!important;border:none;border-radius:8px;padding:0 14px!important;cursor:pointer;}'
      + '#psa-ws{position:fixed;top:16px;left:16px;right:420px;bottom:16px;display:none;flex-direction:column;background:#fff;border:2px solid #0078d4;border-radius:12px;overflow:hidden;z-index:2147483644;box-shadow:0 8px 40px rgba(0,0,0,.25);font-family:Segoe UI,sans-serif;font-size:13px;}'
      + '#psa-ws-hdr{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#003865;color:#fff;}'
      + '#psa-ws-status{flex:1;opacity:.9;}'
      + '#psa-ws-status.psa-err{opacity:1;background:#c0392b;padding:2px 8px;border-radius:4px;}'
      + '#psa-ws-hdr button{background:rgba(255,255,255,.2);border:none;color:#fff;border-radius:6px;cursor:pointer;padding:4px 10px;}'
      + '#psa-ws-extra:not(:empty){padding:8px 12px;background:#fff8e1;border-bottom:1px solid #f0d58a;}'
      + '#psa-ws-frame{flex:1;border:none;width:100%;}'
      + '#psa-recbar{position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:2147483647;display:flex;align-items:center;gap:10px;'
      + 'max-width:calc(100vw - 32px);padding:8px 12px;background:#b42318;color:#fff;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.3);font:13px Segoe UI,sans-serif;}'
      + '#psa-recbar .psa-recdot{flex:0 0 auto;width:10px;height:10px;border-radius:50%;background:#fff;animation:psaBlink 1.2s infinite;}'
      + '@keyframes psaBlink{50%{opacity:.25;}}'
      + '#psa-recbar-status{opacity:.95;}'
      + '#psa-recbar button{flex:0 0 auto;height:auto!important;margin:0!important;padding:4px 10px!important;border:none;border-radius:6px;cursor:pointer;font:600 12px Segoe UI,sans-serif;background:#fff!important;color:#b42318!important;}'
      + '#psa-recbar button.psa-rec-cancel{background:rgba(255,255,255,.2)!important;color:#fff!important;}'
      + '#psa-panel *:focus,#psa-ws *:focus,#psa-recbar *:focus{outline:3px solid #003865;outline-offset:2px;}';
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
      '<div id="psa-hdr"><h3>PeopleSoft Agent</h3>'
      + '<button id="psa-rec" type="button" title="Teach me a new task by doing it once" style="display:none">&#9210; Record</button>'
      + '<button id="psa-new" type="button" title="Start a new conversation">New</button>'
      + '<button id="psa-close" type="button" aria-label="Close">&#x2715;</button></div>'
      + '<div id="psa-log" role="log" aria-live="polite"></div>'
      + '<form id="psa-form"><textarea id="psa-input" aria-label="Message" placeholder="Ask or tell me what to do..."></textarea>'
      + '<button id="psa-send" type="submit">Send</button></form>';
    document.body.appendChild(panel);

    var logEl = panel.querySelector('#psa-log');
    var input = panel.querySelector('#psa-input');
    var state = loadState();
    var busy = false;
    var ready = false;   /* config loaded from the iScript */

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
      if (r.record) showRecordCard(r.record);
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

    /* ---- Recording: name the task, then the person does it in PeopleSoft as usual ---- */

    function parsePage(text) {
      var t = String(text || '').trim(), m = t.match(/\/c\/([^?#\/]+)/i) || [null, t];
      var p = m[1].split('.');
      if (p.length < 2 || !/^[A-Z0-9_]+$/i.test(p[0]) || !/^[A-Z0-9_]+$/i.test(p[1])) return null;
      return { menu: p[0].toUpperCase(), component: p[1].toUpperCase(), market: (p[2] || 'GBL').toUpperCase() };
    }

    function card(html) {
      var c = document.createElement('div');
      c.className = 'psa-card';
      c.innerHTML = html;
      logEl.appendChild(c);
      logEl.scrollTop = logEl.scrollHeight;
      return c;
    }

    function disable(c) { c.querySelectorAll('input,select,button,textarea').forEach(function (i) { i.disabled = true; }); }

    function showRecordCard(offer) {
      var pg = pageContext();
      var here = pg.menu && pg.component && !isFrameworkPage(pg) ? pg.menu + '.' + pg.component + '.' + (pg.market || 'GBL') : '';
      var c = card('<h4>Record a new task</h4>'
        + '<label>Task name<input data-k="title" placeholder="e.g. Create Requisition" value="' + esc(offer && offer.title || '') + '"></label>'
        + '<label>PeopleSoft page (optional)<input data-k="page" placeholder="Leave empty and navigate there yourself" value="' + esc(here) + '"></label>'
        + '<label>Start in<select data-k="mode"><option value="A">Add a New Value</option><option value="U">Find an Existing Value</option>'
        + '<option value="">The page as it opens</option></select></label>'
        + '<small data-k="hint">After Start, go to the page the way you normally do (NavBar, menu, search) and do the task once, '
        + 'including Save. A red bar at the top shows what is recorded; click Finish recording there.</small>'
        + '<div class="psa-actions"><button type="button" class="psa-ok">Start recording</button><button type="button" class="psa-no">Cancel</button></div>');
      var ok = c.querySelector('.psa-ok'), hint = c.querySelector('[data-k="hint"]'), hintText = hint.textContent;
      var mode = c.querySelector('[data-k="mode"]');
      function get(k) { return c.querySelector('[data-k="' + k + '"]').value; }
      function refresh() {
        var text = get('page').trim(), comp = parsePage(text);
        hint.textContent = !text ? hintText
          : !comp ? 'Type MENU.COMPONENT.GBL (e.g. MAINTAIN_SECURITY.USERMAINT.GBL), paste the page URL, or leave it empty.'
          : isFrameworkPage(comp) ? 'That is a homepage or navigation page. Leave this empty and navigate to the transaction yourself after Start.'
          : hintText;
        mode.disabled = !comp || isFrameworkPage(comp);
        ok.disabled = !get('title').trim() || (!!text && (!comp || isFrameworkPage(comp)));
      }
      c.querySelectorAll('input').forEach(function (i) { i.addEventListener('input', refresh); });
      refresh();
      c.querySelector('.psa-no').addEventListener('click', function () { disable(c); });
      ok.addEventListener('click', function () {
        var comp = parsePage(get('page'));
        var rec = { title: get('title').trim(), steps: [], component: null, query: {} };
        if (comp && !isFrameworkPage(comp)) {
          rec.component = comp;
          if (get('mode')) rec.query.Action = get('mode');
        }
        disable(c);
        startRecording(rec);
      });
    }

    var recBar = null, recording = null, recTimer = null;

    function startRecording(rec) {
      recSave(rec);
      addMsg('bot', 'Recording **' + rec.title + '**. '
        + (rec.component ? 'Opening the page.' : 'Go to the page for this task the way you normally do.')
        + ' Do the task once by hand, including Save, then click **Finish recording** in the red bar at the top.');
      close();
      resumeRecording();
      if (rec.component) location.href = componentUrl(rec.component, rec.query).replace('/psc/', '/psp/');
    }

    function resumeRecording() {
      recording = recLoad();
      if (!recording) return;
      if (!recBar) {
        recBar = document.createElement('div');
        recBar.id = 'psa-recbar';
        recBar.setAttribute('role', 'status');
        recBar.innerHTML = '<span class="psa-recdot" aria-hidden="true"></span><strong id="psa-recbar-title"></strong>'
          + '<span id="psa-recbar-status"></span>'
          + '<button type="button" id="psa-recbar-finish">Finish recording</button>'
          + '<button type="button" id="psa-recbar-cancel" class="psa-rec-cancel">Cancel</button>';
        document.body.appendChild(recBar);
        recBar.querySelector('#psa-recbar-finish').addEventListener('click', finishRecording);
        recBar.querySelector('#psa-recbar-cancel').addEventListener('click', function () {
          stopRecording();
          open();
          addMsg('bot', 'Recording cancelled.', false);
        });
      }
      recBar.querySelector('#psa-recbar-title').textContent = 'Recording: ' + recording.title;
      recordingTick();
      clearInterval(recTimer);
      recTimer = setInterval(recordingTick, 400);
    }

    function recordingChanged() { recSave(recording); updateRecBar(); }

    function updateRecBar() {
      if (!recBar || !recording) return;
      var r = recording, last = r.steps[r.steps.length - 1];
      recBar.querySelector('#psa-recbar-status').textContent =
        !r.component ? 'Go to the page for this task (NavBar or menu) and do it once, including Save.'
        : r.offPage ? 'You left ' + r.component.component + '. A task covers one page: go back, or finish.'
        : r.steps.length + ' steps on ' + r.component.component + (last ? ' · last: ' + describe(last) : ' · waiting for your first entry');
    }

    /* Follow the person: find the transaction page and attach to each new document */
    function recordingTick() {
      var r = recording;
      if (!r) return;
      var t = workTarget(), info = null;
      if (t.frame && !t.frame._psaRecHook) {           /* attach the moment each new page loads in the frame */
        t.frame._psaRecHook = true;
        t.frame.addEventListener('load', function () { setTimeout(recordingTick, 0); });
      }
      try { info = t.doc && t.doc.readyState !== 'loading' && parseComponentUrl(t.win.location.href); } catch (e) {}
      if (info && !isFrameworkPage(info.component)) {
        var same = !!r.component && r.component.menu === info.component.menu && r.component.component === info.component.component;
        if (!same && r.component && r.steps.some(isSet)) {
          if (!r.offPage) { r.offPage = true; recordingChanged(); }
        } else {
          if (!same) { r.component = info.component; r.query = info.query; r.steps = []; }
          r.offPage = false;
          if (!t.doc._psaRec) { attachRecorder(t.doc, r, recordingChanged); recordingChanged(); }
        }
      }
      updateRecBar();
    }

    function stopRecording() {
      if (recording) recording.stopped = true;
      recording = null;
      recSave(null);
      clearInterval(recTimer);
      if (recBar) { recBar.remove(); recBar = null; }
    }

    function finishRecording() {
      var r = recording;
      try { var d = workTarget().doc; if (d._psaRec) d._psaRec.diff(); } catch (e) {}
      stopRecording();
      open();
      if (!r || !r.component || !r.steps.some(isSet)) {
        addMsg('bot', 'Nothing was entered on a transaction page, so there is nothing to learn. Click **Record** and try again: '
          + 'go to the page and type the values there.', false);
        return;
      }
      var rec = { title: r.title, component: r.component, query: r.query || {}, steps: r.steps };
      ensureConfig().then(function () {
        var t = typing();
        return api('/api/recordings/draft', { recording: rec }).then(function (res) { t.remove(); showReviewCard(rec, res.draft); },
          function (e) { t.remove(); addMsg('bot', 'I could not build a template from that recording: ' + e.message, false); });
      });
    }

    var MODE_LABELS = { required: 'Ask every time', optional: 'Ask, can be blank', 'default': 'Ask, default = recorded', fixed: 'Always the recorded value' };

    function showReviewCard(rec, draft) {
      var rows = draft.fields.map(function (f) {
        var shown = Array.isArray(f.value) ? f.value.join(', ') : f.value;
        var choice = f.mode === 'secret' ? '<small>Typed on the confirm card each time</small>'
          : '<select data-key="' + esc(f.key) + '">' + Object.keys(MODE_LABELS).map(function (m) {
            return '<option value="' + m + '"' + (m === f.mode ? ' selected' : '') + '>' + MODE_LABELS[m] + '</option>';
          }).join('') + '</select>';
        return '<tr><td>' + esc(f.label) + (f.kind === 'list' ? ' (list)' : '') + '<small>'
          + (f.mode === 'secret' ? 'secret' : 'recorded: ' + esc(shown || '(blank)')) + '</small></td><td>' + choice + '</td></tr>';
      }).join('');
      var stepList = rec.steps.map(function (st) { return '<li>' + esc(describe(st)) + '</li>'; }).join('');
      var c = card('<h4>Review the recorded task</h4>'
        + '<label>Task name<input data-k="title" value="' + esc(draft.title) + '"></label>'
        + '<label>What it does<input data-k="summary" value="' + esc(draft.summary) + '"></label>'
        + '<table>' + rows + '</table>'
        + '<details><summary>' + rec.steps.length + ' recorded steps</summary><ol>' + stepList + '</ol></details>'
        + (draft.exists ? '<label class="psa-check"><input type="checkbox" data-k="overwrite"> Replace the existing task <code>' + esc(draft.task_id) + '</code></label>' : '')
        + '<small>Task ID: ' + esc(draft.task_id) + (draft.suggested_by === 'claude' ? ' · suggestions by Claude' : '') + '</small>'
        + '<div class="psa-actions"><button type="button" class="psa-ok">Save task</button><button type="button" class="psa-no">Discard</button></div>');
      var ok = c.querySelector('.psa-ok'), over = c.querySelector('[data-k="overwrite"]');
      function refresh() { ok.disabled = !c.querySelector('[data-k="title"]').value.trim() || (over && !over.checked); }
      c.querySelectorAll('input').forEach(function (i) { i.addEventListener('input', refresh); i.addEventListener('change', refresh); });
      refresh();
      c.querySelector('.psa-no').addEventListener('click', function () { disable(c); addMsg('bot', 'Recording discarded.', false); });
      ok.addEventListener('click', function () {
        var edited = JSON.parse(JSON.stringify(draft));
        edited.title = c.querySelector('[data-k="title"]').value.trim();
        edited.summary = c.querySelector('[data-k="summary"]').value.trim();
        c.querySelectorAll('select[data-key]').forEach(function (sel) {
          edited.fields.forEach(function (f) { if (f.key === sel.getAttribute('data-key')) f.mode = sel.value; });
        });
        disable(c);
        var t = typing();
        api('/api/recordings/save', { recording: rec, draft: edited, overwrite: !!(over && over.checked) }).then(function (r) {
          t.remove();
          addMsg('bot', 'Saved **' + r.title + '**. From now on I can do it for you'
            + (r.fields.length ? '; I will ask for: ' + r.fields.join(', ') + '.' : '.'));
          if (r.examples && r.examples.length) addChips(r.examples);
        }, function (e) {
          t.remove();
          addMsg('bot', 'Saving failed: ' + e.message, false);
          c.querySelectorAll('input,select,button').forEach(function (i) { i.disabled = false; });
          refresh();
        });
      });
    }

    function runAndReport(action) {
      var secrets = pendingSecrets[action.run_id] || {};
      delete pendingSecrets[action.run_id];
      var ws = openWorkspace('Agent: ' + action.title);
      return runAction(action, secrets, ws).catch(function (e) {
        return { status: 'error', message: 'The agent hit an unexpected problem: ' + ((e && e.message) || e) };
      }).then(function (res) {
        secrets = null;
        ws.done();
        if (res.status === 'success') ws.status('Done: ' + action.title);
        else if (res.status === 'stopped') ws.status('Stopped', true);
        else ws.status('Stopped at "' + (res.failed_step || 'a step') + '": ' + res.message, true);
        /* Show PeopleSoft's own words right away; the agent's explanation follows */
        if (res.status === 'error') addMsg('bot', '⚠️ Stopped at **' + (res.failed_step || 'a step') + '**. PeopleSoft says:\n\n' + res.message);
        var t = typing();
        return api('/api/result', { session_id: state.sessionId, run_id: action.run_id, status: res.status, message: res.message, failed_step: res.failed_step })
          .then(function (r) { t.remove(); return handleReply(r); },
                function (e) { t.remove(); addMsg('bot', 'I could not report the result to the agent service: ' + e.message, false); });
      });
    }

    function open() {
      panel.classList.add('psa-open');
      fab.setAttribute('aria-expanded', 'true');
      if (!ready) {                       /* first open, or the last attempt failed: (re)connect */
        logEl.innerHTML = '';
        ensureConfig().then(function () {
          ready = true;
          apiGet('/api/me').then(function (me) {
            if (me.can_teach) panel.querySelector('#psa-rec').style.display = '';
          }, function (e) {
            addMsg('bot', 'I cannot reach the agent service at ' + (cfg.agentUrl || '(no URL)') + ': ' + e.message
              + '. Check that it is running and that this PeopleSoft address is in PSAGENT_ALLOWED_ORIGINS.', false);
          });
          if (state.history.length) state.history.forEach(function (h) { addMsg(h.who, h.text, false); });
          else greet();
        }, function (e) {
          console.warn('[PSAgent] config failed:', e);
          addMsg('bot', 'Sorry, the agent is not available right now: ' + e.message + '.', false);
        });
      }
      setTimeout(function () { input.focus(); }, 100);
    }
    function close() { panel.classList.remove('psa-open'); fab.setAttribute('aria-expanded', 'false'); fab.focus(); }

    fab.addEventListener('click', function () { panel.classList.contains('psa-open') ? close() : open(); });
    panel.querySelector('#psa-close').addEventListener('click', close);
    panel.querySelector('#psa-rec').addEventListener('click', function () { showRecordCard({ title: '' }); });
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

    window.PSAgent.teach = function (component, query) {
      var comp = parsePage(component);
      if (!comp) throw new Error('Use MENU.COMPONENT.GBL');
      startRecording({ title: comp.component, steps: [], component: comp, query: query || {} });
    };
    resumeRecording();   /* a recording started on an earlier page continues here */
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
    configPromise = fetch(configIscriptUrl(), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error('iScript returned HTTP ' + r.status);
        return r.text().then(function (t) {
          try { return JSON.parse(t); } catch (e) {
            if (/Authorization Error/i.test(t)) throw new Error('you have no access to WEBLIB_PSAGENT (grant IScript_PSAgentConfig in a permission list)');
            throw new Error('the iScript did not return JSON (check WEBLIB_PSAGENT.ISCRIPT1 FieldFormula)');
          }
        });
      })
      .then(function (c) {
        cfg.agentUrl = c.agentUrl; cfg.token = c.token; cfg.oprid = c.oprid; cfg.fullname = c.fullname;
        return cfg;
      });
    configPromise.catch(function () { configPromise = null; });
    return configPromise;
  }

  /* POST payload as JSON (GET when payload is undefined); errors carry the service's message */
  function api(path, payload, retried) {
    return ensureConfig().then(function () {
      var opts = { method: payload === undefined ? 'GET' : 'POST', headers: { 'Authorization': 'Bearer ' + cfg.token } };
      if (payload !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(payload); }
      return fetch(cfg.agentUrl.replace(/\/$/, '') + path, opts);
    }).then(function (r) {
      if (r.status === 401 && !retried && !window.PSAGENT_CONFIG) {
        return ensureConfig(true).then(function () { return api(path, payload, true); });
      }
      if (!r.ok) {
        return r.json().then(function (j) { return j.error; }, function () { return null; })
          .then(function (msg) { throw new Error(msg || 'HTTP ' + r.status); });
      }
      return r.json();
    });
  }
  function apiGet(path) { return api(path, undefined); }

  window.PSAgent = {
    version: VERSION,
    inspect: inspect,
    run: function (action, secrets) { return runAction(action, secrets || {}, openWorkspace('Agent: ' + (action.title || 'test'))); }
  };

  function start() {
    /* Branding loads this file on every page, including the PeopleSoft content frame
       and the agent's own workspace frame: only the top window gets the widget. */
    try { if (window.top !== window && window.top.document) return; } catch (e) { /* cross-origin parent: show it */ }
    if (document.body) { initWidget(); try { console.info('[PSAgent] widget ' + VERSION); } catch (e) {} }
    else setTimeout(start, 250);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

})();
