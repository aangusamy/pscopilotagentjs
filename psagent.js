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
   task, then go to the page as usual and do it once, to the end (Save, or
   Run and OK for a process).
   A red bar follows you across pages; Finish, review, save. Done.

   Console helpers:
     PSAgent.inspect()                         list fields on the current page
     PSAgent.teach('MENU.COMPONENT.GBL', {Action:'A'})
                                               start recording on that page
   ============================================================ */

(function () {

  var VERSION = '2026-10-24.4';   /* shown as PSAgent.version and in the console: check after uploading */

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

  /* Text a grid row shows outside its input fields, e.g. ["Simple AE test program", "AEMINITEST"] */
  function rowTexts(tr) {
    var out = [];
    for (var c = tr && tr.firstElementChild; c && out.length < 4; c = c.nextElementSibling) {
      if (c.querySelector('input:not([type="hidden"]),select,textarea')) continue;
      var t = (c.innerText || c.textContent || '').replace(/\s+/g, ' ').trim();
      if (t && t.length <= 60) out.push(t);
    }
    return out;
  }

  /* A fixed grid (e.g. the Process List on the Process Scheduler Request) by the row's text:
     the row showing "AEMINITEST", wherever it is in the list */
  function byRowText(doc, spec) {
    var base = spec.id.replace(/\$\d+$/, ''), want = spec.row.map(norm);
    for (var k = 0; k < 500; k++) {
      var el = doc.getElementById(base + '$' + k);
      if (!el) { if (k > 0) break; continue; }
      var text = norm(closest(el, 'TR') ? closest(el, 'TR').innerText : '');
      if (isInput(el) && want.every(function (w) { return text.indexOf(w) >= 0; })) return el;
    }
    return null;
  }

  function findField(doc, spec, row) {
    var el = null;
    if (spec.row && spec.row.length && spec.id && row == null) return byRowText(doc, spec);
    if (spec.id) {
      el = row == null ? doc.getElementById(spec.id)
        : doc.getElementById(spec.id + '$' + row) || (row === 0 ? doc.getElementById(spec.id) : null);
      if (!isInput(el)) el = null;
    }
    if (!el && spec.label && row == null) el = byLabel(doc, spec.label);
    if (!el && (spec.column || (spec.label && row != null))) el = byColumn(doc, spec.column || spec.label, row || 0);
    return el;
  }

  function fieldName(spec) { return (spec.label || spec.column || spec.id) + (spec.row && spec.row.length ? ' (' + spec.row.join(' · ') + ')' : ''); }

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

  /* Pop-up pages PeopleSoft opens over the page, e.g. the Process Scheduler Request after Run:
     an iframe in #pt_modals of the top window (or of the page itself) */
  function modalFrames(frame) {
    var out = [];
    [document, frame && frame.contentDocument].forEach(function (d) {
      try {
        if (!d) return;
        [].forEach.call(d.querySelectorAll('#pt_modals iframe,iframe[id^="ptModFrame"]'), function (f) {
          if (out.indexOf(f) < 0 && visible(f)) out.push(f);
        });
      } catch (e) {}
    });
    return out;
  }
  function modalDocs(frame) {
    var out = [];
    modalFrames(frame).forEach(function (f) { try { if (f.contentDocument && f.contentDocument.body) out.push(f.contentDocument); } catch (e) {} });
    return out;
  }
  function modalBusy(frame) {
    return modalFrames(frame).some(function (f) {
      try {
        trackRequests(f.contentWindow);
        var d = f.contentDocument;
        return !d || d.readyState !== 'complete' || isProcessing(d) || requestsBusy(f.contentWindow);
      } catch (e) { return false; }
    });
  }
  /* Every process is submitted through the Process Scheduler Request pop-up (PRCSRQSTDLG) */
  function prcsDialogOpen(frame) {
    return modalDocs(frame).some(function (d) { return !!d.querySelector('[id^="PRCSRQSTDLG_WRK_"]'); });
  }
  function processInstance(doc) {
    var m = doc && doc.body && (doc.body.innerText || '').match(/Process Instance\s*:?\s*(\d+)/i);
    return m && +m[1] > 0 ? m[1] : null;
  }

  /* Where a step works: the pop-up for steps recorded there, otherwise the page */
  function stepDocs(frame, popup) { return popup ? modalDocs(frame) : [frame.contentDocument]; }
  function locate(docs, find) {
    for (var i = 0; i < docs.length; i++) { var el = docs[i] && find(docs[i]); if (el) return el; }
    return null;
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
          || requestsBusy(frame.contentWindow) || modalBusy(frame);
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
    modalFrames(frame).forEach(function (f) { try { trackRequests(f.contentWindow); } catch (e) {} });
    fn();
    return sleep(RUN.afterActionMs)
      .then(function () { return waitIdle(frame, RUN.stepTimeout, leaving); })
      .then(function (doc) { try { win.removeEventListener('beforeunload', onLeave); } catch (e) {} return doc; });
  }

  /* Poll until find() returns something; a PeopleSoft message means it refused the last action */
  function waitFor(frame, find, notFound) {
    var start = Date.now();
    return (function check() {
      var el = find();
      if (el) return Promise.resolve(el);
      var msg = psMessage(frame.contentDocument);
      if (msg) return Promise.reject(new Error(msg));
      if (Date.now() - start > RUN.stepTimeout) return Promise.reject(new Error(notFound));
      return sleep(RUN.pollMs * 2).then(check);
    })();
  }

  function waitForField(frame, spec, row, popup) {
    return waitFor(frame, function () { return locate(stepDocs(frame, popup), function (d) { return findField(d, spec, row); }); },
      'Could not find "' + fieldName(spec) + '" on the ' + (popup ? 'pop-up' : 'page'));
  }

  /* ---- The search page every classic component opens with ---- */

  /* Search framework controls (8.5x+), or an older search page without the Save toolbar */
  function isSearchPage(doc) {
    if (!doc || !doc.body) return false;
    var ctl = doc.querySelectorAll('#PTS_CFG_CL_WRK_PTS_PAGE_TOGGLE,#PTS_CFG_CL_WRK_PTS_ADD_BTN,#PTS_CFG_CL_WRK_PTS_SRCH_BTN');
    if ([].some.call(ctl, visible)) return true;
    var save = doc.getElementById('#ICSave');
    return /\bPSSRCHPAGE\b/.test(doc.body.className) && !(save && visible(save)) && !doc.querySelector('[id^="PSTAB"],[id^="ICTAB"]');
  }

  /* Rows of the search results grid (also shown under "already exists") */
  function resultRows(doc) {
    return [].filter.call(doc.querySelectorAll('tr[id^="trPTS_CFG_CL_STD_RSL"],a[id^="SEARCH_RESULT"]'), visible);
  }
  function resultRow(doc, value) {
    var want = norm(value), rows = resultRows(doc);
    for (var i = 0; i < rows.length; i++) {
      var cells = rows[i].tagName === 'TR' ? rows[i].cells : [rows[i]];
      for (var j = 0; j < cells.length; j++) if (norm(cells[j].textContent) === want) return rows[i];
    }
    return null;
  }
  function rowCells(row) {
    return [].map.call(row.tagName === 'TR' ? row.cells : [row], function (c) { return c.textContent.replace(/\s+/g, ' ').trim(); })
      .filter(Boolean);
  }

  /* Like a person: type the keys, press Add or Search, then deal with what PeopleSoft says */
  function openRecord(frame, step, action) {
    var query = {}, k, keys = step.keys || [], i = 0, mode = step.mode || 'find';
    for (k in (step.query || {})) query[k] = step.query[k];
    query.Action = mode === 'find' ? 'U' : 'A';
    function typeKeys() {
      if (i >= keys.length) return Promise.resolve();
      var key = keys[i++];
      return waitForField(frame, key.field).then(function (el) { return enterValue(frame, el, key.field, null, key.value); }).then(typeKeys);
    }
    return loadUrl(frame, componentUrl(action.component, query)).then(function () {
      if (!isSearchPage(frame.contentDocument)) return null;            /* no search page for this component */
      return typeKeys().then(function () {
        var doc = frame.contentDocument, add = mode !== 'find';
        var btn = findButton(doc, add ? { id: 'PTS_CFG_CL_WRK_PTS_ADD_BTN', text: 'Add' } : { id: 'PTS_CFG_CL_WRK_PTS_SRCH_BTN', text: 'Search' })
          || findButton(doc, { id: '#ICSearch' });
        if (!btn || !visible(btn)) throw new Error('Could not find the ' + (add ? 'Add' : 'Search') + ' button on the search page');
        return act(frame, function () { btn.click(); });
      }).then(function (doc) {
        if (!isSearchPage(doc)) return checkOpened(doc, step);          /* opened (the only match, or the new record) */
        var msg = psMessage(doc), want = step.pick || (keys[0] && keys[0].value);
        var exists = !!msg && /already exists/i.test(msg);
        if (mode === 'add' || (mode === 'add_or_open' && !exists)) throw new Error(msg || 'PeopleSoft stayed on the search page');
        var row = want != null && resultRow(doc, want);
        if (!row) throw new Error(msg || 'No search result is "' + want + '"');
        return act(frame, function () { row.click(); }).then(function (d) {
          if (isSearchPage(d)) throw new Error(psMessage(d) || 'The search result "' + want + '" did not open');
        });
      });
    });
  }

  /* "begins with" search can open a different single match: make sure the record shows its keys */
  function checkOpened(doc, step) {
    if (step.mode !== 'find') return;
    var text = norm(doc.body.innerText);
    (step.keys || []).forEach(function (k) {
      if (k.value && text.indexOf(norm(k.value)) < 0) throw new Error('PeopleSoft opened a record without "' + k.value + '"; it may not exist');
    });
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
      case 'click': return step.result ? 'Open ' + step.result[0] : 'Click ' + (step.button.text || step.button.id);
      case 'tab': return 'Open the ' + step.label + ' tab';
      case 'grid': return 'Fill ' + fieldName(step.field) + ' rows';
      case 'wait': return 'Wait for ' + fieldName(step.field);
      case 'navigate': return step.component ? 'Open ' + step.component.menu + '.' + step.component.component : 'Open the page';
      case 'pick': return 'Open ' + step.value;
      case 'open': return ({ add: 'Add ', find: 'Open ', add_or_open: 'Add or open ' }[step.mode] || 'Open ')
        + ((step.keys || []).map(function (k) { return k.value; }).join(' ') || 'the page');
      case 'expect_saved': return 'Check the save result';
      case 'expect_process': return 'Check that the process was submitted';
      default: return step['do'];
    }
  }

  var STEPS = {
    navigate: function (frame, step, action) {
      return loadUrl(frame, componentUrl(action.component, step.query));
    },
    open: openRecord,
    wait: function (frame, step) {
      return waitForField(frame, step.field, null, step.popup);
    },
    set: function (frame, step) {
      return waitForField(frame, step.field, null, step.popup).then(function (el) {
        return enterValue(frame, el, step.field, null, step.value, step.popup);
      });
    },
    click: function (frame, step) {
      var name = step.button.text || step.button.id;
      return waitFor(frame, function () { return locate(stepDocs(frame, step.popup), function (d) { return findButton(d, step.button); }); },
        'Could not find the "' + name + '" ' + (step.popup ? 'button on the pop-up' : 'button'))
        .then(function (btn) { return act(frame, function () { btn.click(); }); });
    },
    expect_process: function (frame) {
      /* Standard run control pages show the new process instance; others (e.g. AE requests) don't.
         Either way PeopleSoft accepted the request when its pop-up closed without a message. */
      var start = Date.now();
      return waitFor(frame, function () {
        var n = processInstance(frame.contentDocument);
        if (n) return 'Submitted: process instance ' + n;
        if (Date.now() - start > 4000 && !prcsDialogOpen(frame)) return 'Submitted to the Process Scheduler (this page shows no process instance number; see Process Monitor)';
        return null;
      }, prcsDialogOpen(frame) ? 'The Process Scheduler Request did not close after OK' : 'PeopleSoft did not confirm the process request. Check Process Monitor.');
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
  function enterValue(frame, el, spec, row, value, popup) {
    return act(frame, function () { try { el.focus(); } catch (e) {} })
      .then(function (doc) {
        var msg = psMessage(doc);
        if (msg) throw new Error(msg);
        var cur = locate(stepDocs(frame, popup), function (d) { return findField(d, spec, row); }) || el;
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
    var steps = action.steps || [], i = 0, frame = ws.frame, done = null;
    dismissModal();   /* a message box left over from earlier would be mistaken for this run's */
    function fail(message, raw) {
      dismissModal();
      return { status: 'error', message: message, failed_step: describe(raw || {}) };
    }
    function next() {
      if (ws.stopped) return Promise.resolve({ status: 'stopped', message: 'Stopped by the user', failed_step: describe(steps[i] || {}) });
      if (i >= steps.length) return Promise.resolve({ status: 'success', message: done || 'Completed (no save step)' });
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
      return run.then(function (out) {
        if (/^expect_/.test(step['do'])) done = out;
        /* Any PS message after a step means a human would stop and read it */
        if (!/^(navigate|open|expect_)/.test(step['do'])) {
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
        progress: function (n, total, text) { this.status('Step ' + n + '/' + total + ': ' + text); if (this.onStep) this.onStep(n, total, text); },
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
    workspace.onStep = null;
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
  var FRAMEWORK_MENU = /^(NUI_FRAMEWORK|PT_FLDASHBOARD|PTPP_PORTAL)$/i;   /* not PORTAL_ADMIN: Portal Security Sync etc. are real pages */

  function recLoad() { try { return JSON.parse(sessionStorage.getItem(REC_KEY)); } catch (e) { return null; } }
  function recSave(rec) {
    try { if (rec) sessionStorage.setItem(REC_KEY, JSON.stringify(rec)); else sessionStorage.removeItem(REC_KEY); } catch (e) {}
  }
  function isSet(st) { return st['do'] === 'set'; }
  function isAction(st) { return /^(set|click|tab)$/.test(st['do']); }   /* not message / outcome notes */
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

  /* Record what the person does on doc into rec.steps; changed() runs after each step.
     Every step says where it happened (search page, page, pop-up): recorder.py builds the task from that. */
  function attachRecorder(doc, rec, changed, popup) {
    function phase() { return popup ? 'popup' : isSearchPage(doc) ? 'search' : 'page'; }
    if (doc._psaRec) return;
    var snap = {}, orig = {};   /* orig: value before the person first changed the field */
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
      if (/\$\d+$/.test(el.id)) {                  /* grid cell: its column, the row's text, and whether rows can be added */
        var col = columnOf(el), tr = closest(el, 'TR');
        if (col) field.column = col;
        field.row = rowTexts(tr);
        field.addable = !!(tr && tr.querySelector('[id*="$new$"]'));
      }
      var step = { 'do': 'set', phase: phase(), field: field, kind: kindOf(el), value: valueOf(el) };
      var last = rec.steps[rec.steps.length - 1], prev = null, i;
      for (i = rec.steps.length - 1; i >= 0 && !prev; i--) if (isSet(rec.steps[i]) && rec.steps[i].field.id === el.id) prev = rec.steps[i];
      if (prev && prev !== last && prev.value === step.value) return;   /* same value again: nothing new */
      if (!(el.id in orig)) orig[el.id] = el.id in snap ? snap[el.id] : null;
      if (last && last['do'] === 'set' && last.field.id === el.id && last.phase === step.phase) {
        if (step.kind !== 'password' && step.value === orig[el.id]) rec.steps.pop();   /* changed back: undone */
        else rec.steps[rec.steps.length - 1] = step;
      } else rec.steps.push(step);
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
      var ph = phase();
      var row = ph === 'search' && e.target.closest && e.target.closest('tr[id^="trPTS_CFG_CL_STD_RSL"],a[id^="SEARCH_RESULT"]');
      if (row) {                                     /* a row in the search results opens that record */
        diff();
        rec.steps.push({ 'do': 'click', phase: ph, result: rowCells(row) });
        changed();
        return;
      }
      var el = e.target.closest && e.target.closest('a,button,input[type="button"],input[type="submit"]');
      if (!el || ignored(el) || /\$(new|delete|prompt)\$|\$prompt$/i.test(el.id)) return;   /* grid +/- and lookups: replayed by grid steps */
      diff();
      var text = (el.tagName === 'INPUT' ? el.value : el.textContent).replace(/\s+/g, ' ').trim();
      if (/^(ICTAB|PSTAB)/.test(el.id) || /tab/i.test(el.parentElement && el.parentElement.className)) rec.steps.push({ 'do': 'tab', phase: ph, label: text });
      else if (el.id || text) rec.steps.push({ 'do': 'click', phase: ph, button: { id: el.id || undefined, text: text || undefined } });
      else return;
      changed();
      /* Re-baseline once PeopleSoft has redrawn the page, so values it fills in itself are not recorded */
      setTimeout(function () { try { if (!rec.stopped) snapshot(); } catch (e2) {} }, 2500);
    }, true);
  }

  /* What PeopleSoft shows while the person works: messages, and the result of the task
     (Saved, or a process instance after Run). Recorded as notes between the steps. */
  var observed = { doc: null, msg: null, saved: false, instance: null, prcs: false };
  function observe(rec, doc, frame) {
    if (!doc || !doc.body) return false;
    var msg = psMessage(doc) || null, savedEl = doc.querySelector('#SAVED_win0');
    var saved = !!savedEl && visible(savedEl), instance = processInstance(doc), prcs = prcsDialogOpen(frame);
    var fresh = observed.doc !== doc, added = false;
    if (!fresh && rec.steps.some(isAction)) {
      var phase = isSearchPage(doc) ? 'search' : 'page', acts = rec.steps.filter(isAction), last = acts[acts.length - 1];
      if (msg && msg !== observed.msg) { rec.steps.push({ 'do': 'message', phase: phase, text: msg.slice(0, 300) }); added = true; }
      if (saved && !observed.saved) { rec.steps.push({ 'do': 'outcome', phase: phase, kind: 'saved' }); added = true; }
      if (instance && instance !== observed.instance) { rec.steps.push({ 'do': 'outcome', phase: phase, kind: 'process', detail: instance }); added = true; }
      /* The Process Scheduler Request closed after its OK, without a message: the request was accepted */
      else if (observed.prcs && !prcs && !msg && last && last.phase === 'popup' && last.button
        && (last.button.id === '#ICSave' || norm(last.button.text) === 'ok')) {
        rec.steps.push({ 'do': 'outcome', phase: phase, kind: 'process' }); added = true;
      }
    }
    observed = { doc: doc, msg: msg, saved: saved, instance: instance, prcs: prcs };
    return added;
  }

  /* ============================================================
     LIVE MODE — Claude reads the page and decides each step (live.py)
     ============================================================ */

  var SNAP_LIMIT = { fields: 150, buttons: 80, options: 30, results: 30 };

  /* What a person sees on the workspace page, as data for Claude: fields, buttons, tabs,
     messages, the pop-up over it and search results. Ids of pop-up elements start with "popup:". */
  function snapshotPage(frame, userCtx) {
    var snap = { component: '', phase: 'none', fields: [], buttons: [], tabs: [], results: [], messages: [] };
    if (userCtx && userCtx.component && !isFrameworkPage(userCtx)) {
      snap.user_page = userCtx.menu + '.' + userCtx.component + '.' + (userCtx.market || 'GBL') + (userCtx.title ? ' (' + userCtx.title + ')' : '');
    }
    var doc = null, win = null;
    try { doc = frame.contentDocument; win = frame.contentWindow; } catch (e) {}
    if (!doc || !doc.body || doc.URL === 'about:blank') return snap;
    var info = null;
    try { info = parseComponentUrl(win.location.href); } catch (e) {}
    if (info) snap.component = info.component.menu + '.' + info.component.component + '.' + info.component.market;
    snap.title = String((win && (win.szCrefLabel || '')) || doc.title || '').trim().slice(0, 120);
    snap.phase = isSearchPage(doc) ? 'search' : 'page';
    collectElements(doc, '', snap);
    modalDocs(frame).forEach(function (d) { collectElements(d, 'popup:', snap); });
    var title = document.querySelector('#pt_modals [id^="ptModTitle_"]');
    if (title && visible(title) && modalDocs(frame).length) snap.popup_title = title.textContent.trim().slice(0, 80);
    [].forEach.call(doc.querySelectorAll('[id^="ICTAB_"],[id^="PSTAB"]'), function (t) {
      var label = (t.textContent || '').replace(/\s+/g, ' ').trim();
      if (label && visible(t) && label.length < 60) snap.tabs.push({ label: label, active: t.tagName !== 'A' });
    });
    resultRows(doc).slice(0, SNAP_LIMIT.results).forEach(function (r) { snap.results.push(rowCells(r).slice(0, 4)); });
    var msg = psMessage(doc);
    if (msg) snap.messages.push(msg.slice(0, 600));
    var bad = errorFields(doc);
    if (bad.length) snap.messages.push('Fields marked invalid: ' + bad.join(', '));
    var box = modalMessage();                         /* a Yes/No question box: its buttons are choices */
    if (box) {
      var root = (box.el.closest && box.el.closest('[id^="ptMod_"]')) || document;
      [].forEach.call(root.querySelectorAll('input[type="button"],button'), function (b) {
        var t = (b.value || b.textContent || '').trim();
        if (t && visible(b)) snap.buttons.push({ id: 'box:' + t, text: t });
      });
    }
    var savedEl = doc.querySelector('#SAVED_win0');
    if (savedEl && visible(savedEl)) snap.saved = true;
    var n = processInstance(doc);
    if (n) snap.process_instance = n;
    return snap;
  }

  function collectElements(doc, prefix, snap) {
    [].forEach.call(doc.querySelectorAll('input,select,textarea,button,a[id]'), function (el) {
      if (!el.id || !visible(el) || /^(PT_|NEWWIN$|CUSTPAGE$|ICTAB_|PSTAB)|\$prompt|\$srt\d/.test(el.id)) return;
      var isBtn = el.tagName === 'A' || el.tagName === 'BUTTON' || /^(button|submit|image)$/.test(el.type);
      if (isBtn) {
        if (snap.buttons.length >= SNAP_LIMIT.buttons) return;
        var text = (el.tagName === 'INPUT' ? el.value : el.textContent || el.title || '').replace(/\s+/g, ' ').trim();
        if (!text && /\$new\$/.test(el.id)) text = '+ (add row)';
        if (!text && /\$delete\$/.test(el.id)) text = '- (delete row)';
        if (text && text.length <= 60) snap.buttons.push({ id: prefix + el.id, text: text });
        return;
      }
      if (!isInput(el) || snap.fields.length >= SNAP_LIMIT.fields) return;
      var lab = doc.querySelector('label[for="' + el.id.replace(/["\\]/g, '\\$&') + '"]');
      var f = { id: prefix + el.id, label: labelOf(doc, el) || columnOf(el) || '', type: kindOf(el) === 'text' && el.tagName === 'TEXTAREA' ? 'textarea' : kindOf(el),
        value: valueOf(el).slice(0, 200) };
      if (lab && /^\s*\*/.test(lab.textContent)) f.required = true;
      if (el.disabled || el.readOnly) f.readonly = true;
      if (el.tagName === 'SELECT') f.options = [].map.call(el.options, function (o) { return o.text.trim(); }).slice(0, SNAP_LIMIT.options);
      var m = el.id.match(/\$(\d+)$/);
      if (m) {
        f.row = +m[1];
        var col = columnOf(el);
        if (col) f.column = col;
        var rt = rowTexts(closest(el, 'TR'));
        if (rt.length) f.row_text = rt.slice(0, 3).join(' · ');
      }
      snap.fields.push(f);
    });
  }

  function pickResult(frame, value) {
    var row = resultRow(frame.contentDocument, value);
    if (!row) return Promise.reject(new Error('No search result is "' + value + '"'));
    return act(frame, function () { row.click(); });
  }

  /* A button of a PeopleSoft question box (Yes / No), drawn in the top window */
  function clickBox(frame, text) {
    var want = norm(text);
    var b = [].filter.call(document.querySelectorAll('#pt_modals input[type="button"],#pt_modals button'), function (x) {
      return visible(x) && norm(x.value || x.textContent) === want;
    })[0];
    if (!b) return Promise.reject(new Error('The "' + text + '" button is no longer shown'));
    return act(frame, function () { b.click(); });
  }

  /* Do the steps Claude chose, like a person; stop at the first problem and report it */
  function runLiveSteps(frame, steps, onStep) {
    var i = 0;
    function next() {
      if (i >= steps.length) return Promise.resolve({ ok: true });
      var step = steps[i++], item = onStep ? onStep(describe(step)) : null, run;
      try {
        if (step['do'] === 'navigate') run = loadUrl(frame, componentUrl(step.component, step.query));
        else if (step['do'] === 'pick') run = pickResult(frame, step.value);
        else if (step['do'] === 'click' && /^box:/.test(step.button.id)) run = clickBox(frame, step.button.text || step.button.id.slice(4));
        else run = STEPS[step['do']](frame, step, {});
      } catch (e) { run = Promise.reject(e); }
      return run.then(function () {
        if (item) item.end(true);
        var box = modalMessage();
        if (box) {                                   /* PeopleSoft answered with a message box: Claude reads it next */
          dismissModal();
          return { ok: false, step: describe(step), error: box.text };
        }
        return next();
      }, function (e) {
        var msg = (e && e.message) || String(e);
        if (item) item.end(false, msg);
        dismissModal();
        return { ok: false, step: describe(step), error: msg };
      });
    }
    return next();
  }

  /* While Claude works, record it like a person doing the task: a successful live run can be saved as a task */
  function startLiveRecorder(frame, title) {
    var rec = { title: title, steps: [], component: null, query: {} };
    var timer = setInterval(function () {
      var doc, info = null;
      try { doc = frame.contentDocument; info = doc && doc.readyState !== 'loading' && parseComponentUrl(frame.contentWindow.location.href); } catch (e) { return; }
      if (!info || isFrameworkPage(info.component)) return;
      if (!rec.component || rec.component.component !== info.component.component) {
        rec.component = info.component; rec.query = info.query; rec.steps = [];    /* a task covers one component */
      }
      if (!doc._psaRec) attachRecorder(doc, rec, function () {});
      modalFrames(frame).forEach(function (f) {
        try { var d = f.contentDocument; if (d && d.body && d.readyState !== 'loading' && d.URL !== 'about:blank' && !d._psaRec) attachRecorder(d, rec, function () {}, true); } catch (e) {}
      });
      observe(rec, doc, frame);
    }, 300);
    return { rec: rec, stop: function () { clearInterval(timer); rec.stopped = true; } };
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

    var css = [
      '#psa-panel,#psa-fab,#psa-ws,#psa-recbar{--psa-ink:#0f172a;--psa-sub:#64748b;--psa-line:#e6e8f0;--psa-soft:#f6f7fb;--psa-brand:#4f46e5;--psa-brand2:#0ea5e9;--psa-ok:#16a34a;--psa-warn:#d97706;--psa-err:#dc2626;font-family:"Segoe UI",system-ui,-apple-system,sans-serif;}',
      /* launcher */
      '#psa-fab{position:fixed;right:24px;bottom:24px;z-index:2147483646;display:flex;align-items:center;gap:10px;height:52px;padding:0 20px 0 7px!important;margin:0!important;border:none!important;border-radius:26px!important;background:#fff!important;color:var(--psa-ink)!important;box-shadow:0 12px 32px rgba(15,23,42,.2),0 0 0 1px rgba(79,70,229,.18);cursor:pointer;font:600 14px "Segoe UI",system-ui,sans-serif!important;transition:transform .15s,box-shadow .15s;}',
      '#psa-fab:hover{transform:translateY(-2px);box-shadow:0 16px 40px rgba(79,70,229,.3),0 0 0 1px rgba(79,70,229,.35);}',
      'body.psa-docked #psa-fab{display:none;}',
      /* the agent: an avatar with eyes and a status light */
      '.psa-av{position:relative;flex:0 0 auto;display:inline-block;width:38px;height:38px;border-radius:50%;background:radial-gradient(circle at 30% 25%,rgba(255,255,255,.55),transparent 42%),linear-gradient(135deg,var(--psa-brand),var(--psa-brand2));box-shadow:inset 0 -4px 10px rgba(0,0,0,.18);}',
      '.psa-av i{position:absolute;top:38%;width:5px;height:10px;border-radius:3px;background:#fff;animation:psaBlink 5s infinite;}',
      '.psa-av i:first-child{left:31%;}.psa-av i:last-child{right:31%;}',
      '.psa-av:after{content:"";position:absolute;right:-1px;bottom:-1px;width:11px;height:11px;border-radius:50%;background:var(--psa-ok);border:2px solid #fff;}',
      '[data-state="thinking"] .psa-av:after,[data-state="working"] .psa-av:after{background:var(--psa-brand2);animation:psaPulse 1s infinite;}',
      '[data-state="waiting"] .psa-av:after{background:var(--psa-warn);}[data-state="error"] .psa-av:after{background:var(--psa-err);}',
      '[data-state="thinking"] #psa-hdr .psa-av,[data-state="working"] #psa-hdr .psa-av{animation:psaGlow 1.6s ease-in-out infinite;}',
      '[data-state="working"] .psa-av i{animation:psaLook 1.6s ease-in-out infinite;}',
      '@keyframes psaBlink{0%,93%,100%{transform:scaleY(1)}96%{transform:scaleY(.1)}}',
      '@keyframes psaLook{0%,100%{transform:translateX(-2px)}50%{transform:translateX(2px)}}',
      '@keyframes psaPulse{50%{transform:scale(1.35);opacity:.6}}',
      '@keyframes psaGlow{0%,100%{box-shadow:0 0 0 0 rgba(79,70,229,.35)}50%{box-shadow:0 0 0 7px rgba(79,70,229,0)}}',
      '@keyframes psaIn{from{opacity:0;transform:translateX(18px)}to{opacity:1;transform:none}}',
      '@keyframes psaSpin{to{transform:rotate(360deg)}}',
      '@keyframes psaDot{0%,80%,100%{opacity:.25;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}',
      /* docked panel */
      '#psa-panel{position:fixed;top:12px;right:12px;bottom:12px;width:440px;max-width:calc(100vw - 24px);z-index:2147483645;display:none;flex-direction:column;background:#fff;border-radius:20px;box-shadow:0 24px 64px rgba(15,23,42,.24),0 0 0 1px rgba(15,23,42,.06);overflow:hidden;color:var(--psa-ink);font-size:14px;line-height:1.5;text-align:left;}',
      '#psa-panel.psa-open{display:flex;animation:psaIn .22s ease-out;}#psa-panel.psa-wide{width:720px;}',
      '#psa-panel *{box-sizing:border-box;}#psa-panel h2,#psa-panel h4,#psa-panel p,#psa-panel ol,#psa-panel ul{color:inherit;}',
      '#psa-hdr{flex:0 0 auto;display:flex;align-items:center;gap:12px;padding:16px 14px 14px 16px;background:linear-gradient(180deg,#f3f1ff,#fff);border-bottom:1px solid var(--psa-line);}',
      '#psa-hdr .psa-who{flex:1;min-width:0;}#psa-hdr .psa-name{font-weight:700;font-size:15px;letter-spacing:-.01em;}',
      '#psa-status{font-size:12px;color:var(--psa-sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
      '.psa-ib{flex:0 0 auto;width:34px;height:34px;display:inline-flex;align-items:center;justify-content:center;gap:6px;border:none!important;border-radius:10px!important;background:transparent!important;color:var(--psa-sub)!important;cursor:pointer;padding:0!important;margin:0!important;box-shadow:none!important;}',
      '.psa-ib:hover{background:#eef2ff!important;color:var(--psa-brand)!important;}',
      '#psa-rec{width:auto;padding:0 10px!important;font:600 12px "Segoe UI",system-ui,sans-serif!important;color:#b42318!important;}',
      '#psa-ctx{flex:0 0 auto;display:flex;align-items:center;gap:8px;margin:10px 14px 0;padding:8px 12px;border-radius:12px;background:var(--psa-soft);font-size:12px;color:var(--psa-sub);min-width:0;}',
      '#psa-ctx[hidden]{display:none;}#psa-ctx span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}#psa-ctx b{color:var(--psa-ink);font-weight:600;}#psa-ctx .psa-e{color:var(--psa-err);}',
      '#psa-log{flex:1 1 auto;min-height:0;overflow-y:auto;padding:16px 16px 8px;display:flex;flex-direction:column;gap:14px;}',
      /* messages */
      '.psa-msg{max-width:92%;line-height:1.55;word-wrap:break-word;}',
      '.psa-bot{align-self:flex-start;position:relative;padding:3px 0 3px 38px;}',
      '.psa-bot:before{content:"";position:absolute;left:0;top:1px;width:26px;height:26px;border-radius:50%;background:radial-gradient(circle at 30% 25%,rgba(255,255,255,.5),transparent 45%),linear-gradient(135deg,var(--psa-brand),var(--psa-brand2));}',
      '.psa-user{align-self:flex-end;background:linear-gradient(135deg,var(--psa-brand),#6366f1);color:#fff;padding:9px 14px;border-radius:18px 18px 5px 18px;box-shadow:0 4px 14px rgba(79,70,229,.22);}',
      '.psa-msg ul{margin:4px 0;padding-left:18px;}.psa-msg code{background:#eef2ff;color:#3730a3;border-radius:5px;padding:1px 5px;font-size:12px;}',
      '.psa-dots span{display:inline-block;width:7px;height:7px;margin:8px 3px 0 0;border-radius:50%;background:var(--psa-brand);animation:psaDot 1.2s infinite;}',
      '.psa-dots span:nth-child(2){animation-delay:.15s;}.psa-dots span:nth-child(3){animation-delay:.3s;}',
      '.psa-chips{display:flex;flex-wrap:wrap;gap:6px;padding-left:38px;}',
      '.psa-chips button{border:1px solid #c7d2fe!important;background:#fff!important;color:var(--psa-brand)!important;border-radius:999px!important;padding:5px 12px!important;margin:0!important;cursor:pointer;font:500 12px "Segoe UI",system-ui,sans-serif!important;}',
      '.psa-chips button:hover{background:#eef2ff!important;}',
      /* welcome */
      '.psa-welcome h2{margin:4px 0 2px;font-size:24px;font-weight:700;letter-spacing:-.02em;background:linear-gradient(90deg,var(--psa-brand),var(--psa-brand2));-webkit-background-clip:text;background-clip:text;color:transparent!important;}',
      '.psa-welcome p{margin:0 0 6px;color:var(--psa-sub);}',
      '.psa-sec{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--psa-sub);margin:16px 0 8px;}',
      '.psa-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;}',
      '.psa-tile{display:block;width:100%;text-align:left;border:1px solid var(--psa-line)!important;background:#fff!important;border-radius:14px!important;padding:11px 12px!important;margin:0!important;cursor:pointer;color:var(--psa-ink)!important;font:inherit!important;transition:border-color .15s,box-shadow .15s,transform .15s;}',
      '.psa-tile:hover{border-color:#c7d2fe!important;box-shadow:0 8px 20px rgba(79,70,229,.12);transform:translateY(-1px);}',
      '.psa-tile b{display:block;font-size:13px;}.psa-tile small{display:block;color:var(--psa-sub);font-size:12px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      '.psa-tile.psa-new{background:linear-gradient(135deg,#eef2ff,#ecfeff)!important;border-color:#c7d2fe!important;}',
      '.psa-recent{display:flex;align-items:center;gap:10px;width:100%;text-align:left;border:none!important;background:transparent!important;padding:7px 10px!important;margin:0!important;border-radius:10px!important;cursor:pointer;color:var(--psa-ink)!important;font:inherit!important;}',
      '.psa-recent:hover{background:var(--psa-soft)!important;}.psa-recent svg{flex:0 0 auto;color:var(--psa-sub);}.psa-recent span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      /* cards */
      '.psa-card{align-self:stretch;border:1px solid var(--psa-line);border-radius:16px;background:#fff;padding:14px 16px;box-shadow:0 6px 20px rgba(15,23,42,.06);}',
      '.psa-card h4{margin:0 0 8px;font-size:14px;font-weight:700;}',
      '.psa-card table{width:100%;border-collapse:collapse;margin-bottom:8px;font-size:13px;}',
      '.psa-card td{padding:5px 4px;border-bottom:1px solid #f0f2f7;vertical-align:top;}.psa-card td:first-child{color:var(--psa-sub);width:45%;}',
      '.psa-card label{display:block;font-size:12px;margin:6px 0 3px;color:#475569;}',
      '.psa-card input{width:100%;padding:7px 9px!important;border:1px solid #cbd5e1!important;border-radius:9px!important;font:13px "Segoe UI",system-ui,sans-serif!important;background:#fff!important;}',
      '.psa-card input:focus{outline:none;border-color:#818cf8!important;box-shadow:0 0 0 3px rgba(99,102,241,.15);}',
      '.psa-card .psa-actions{display:flex;gap:8px;margin-top:12px;}',
      '.psa-card .psa-actions button{flex:1;padding:9px!important;margin:0!important;border-radius:10px!important;cursor:pointer;font:600 13px "Segoe UI",system-ui,sans-serif!important;}',
      '.psa-ok{background:linear-gradient(135deg,var(--psa-brand),#6366f1)!important;color:#fff!important;border:none!important;}.psa-ok:disabled{opacity:.45;cursor:default!important;}',
      '.psa-no{background:#fff!important;color:var(--psa-ink)!important;border:1px solid var(--psa-line)!important;}',
      '.psa-card select{width:100%;font-size:12px;padding:5px;border:1px solid #cbd5e1;border-radius:8px;}',
      '.psa-card small{color:var(--psa-sub);display:block;margin-top:4px;}',
      '.psa-card details{margin:8px 0;font-size:12px;}.psa-card ol{margin:4px 0;padding-left:20px;max-height:160px;overflow-y:auto;}',
      '.psa-card .psa-check{display:flex;gap:6px;align-items:center;}.psa-card .psa-check input{width:auto;}',
      '.psa-card.psa-approve{border-color:#fcd34d;box-shadow:0 8px 24px rgba(217,119,6,.12);}',
      /* activity feed: what the agent is doing, step by step */
      '.psa-feed{align-self:stretch;border:1px solid var(--psa-line);border-radius:16px;background:var(--psa-soft);padding:10px 12px;font-size:13px;}',
      '.psa-feed-h{display:flex;align-items:center;gap:8px;font-weight:600;}.psa-feed-h small{margin-left:auto;color:var(--psa-sub);font-weight:400;}',
      '.psa-feed ol{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-direction:column;gap:5px;max-height:240px;overflow-y:auto;}',
      '.psa-feed li{display:flex;gap:8px;align-items:flex-start;margin:0;}.psa-feed li em{color:var(--psa-err);font-style:normal;}',
      '.psa-ic{flex:0 0 auto;width:16px;height:16px;margin-top:2px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font:700 10px/1 "Segoe UI",sans-serif;color:#fff;}',
      '.psa-ic.run{border:2px solid #c7d2fe;border-top-color:var(--psa-brand);animation:psaSpin .8s linear infinite;}',
      '.psa-ic.think{border:2px dotted var(--psa-brand2);animation:psaSpin 2s linear infinite;}',
      '.psa-ic.ok{background:var(--psa-ok);}.psa-ic.ok:before{content:"\\2713";}',
      '.psa-ic.err{background:var(--psa-err);}.psa-ic.err:before{content:"!";}',
      '.psa-ic.ask{background:var(--psa-warn);}.psa-ic.ask:before{content:"?";}',
      /* composer */
      '#psa-form{flex:0 0 auto;display:flex!important;align-items:flex-end;gap:6px;margin:6px 12px 4px!important;padding:6px!important;height:auto!important;min-height:0!important;border:1px solid var(--psa-line);border-radius:16px;background:#fff;box-shadow:0 2px 10px rgba(15,23,42,.05);}',
      '#psa-form:focus-within{border-color:#a5b4fc;box-shadow:0 0 0 4px rgba(99,102,241,.12);}',
      '#psa-input{flex:1 1 auto!important;border:none!important;outline:none!important;resize:none!important;height:34px;min-height:34px!important;max-height:140px!important;padding:7px 4px!important;margin:0!important;font:14px/1.45 "Segoe UI",system-ui,sans-serif!important;background:transparent!important;color:var(--psa-ink)!important;box-shadow:none!important;}',
      '#psa-send{flex:0 0 auto;width:34px!important;height:34px!important;min-height:0!important;border-radius:11px!important;border:none!important;background:linear-gradient(135deg,var(--psa-brand),#6366f1)!important;color:#fff!important;display:inline-flex;align-items:center;justify-content:center;padding:0!important;margin:0!important;cursor:pointer;}',
      '#psa-send:disabled{opacity:.4;cursor:default;}',
      '#psa-recentpop{position:absolute;left:12px;right:12px;bottom:92px;max-height:50%;overflow-y:auto;background:#fff;border:1px solid var(--psa-line);border-radius:14px;box-shadow:0 18px 40px rgba(15,23,42,.18);padding:6px;}',
      '#psa-recentpop[hidden]{display:none;}',
      '#psa-hint{flex:0 0 auto;padding:0 18px 10px;font-size:11px;color:#94a3b8;}',
      /* workspace: where the agent works, next to the panel */
      '#psa-ws{position:fixed;top:12px;left:12px;right:12px;bottom:12px;display:none;flex-direction:column;background:#fff;border-radius:18px;overflow:hidden;z-index:2147483644;box-shadow:0 24px 64px rgba(15,23,42,.28),0 0 0 2px rgba(79,70,229,.45);font-size:13px;}',
      'body.psa-docked #psa-ws{right:464px;}body.psa-docked.psa-widebody #psa-ws{right:744px;}',
      '#psa-ws-hdr{display:flex;align-items:center;gap:10px;padding:10px 14px;background:linear-gradient(90deg,#1e1b4b,#312e81);color:#fff;}',
      '#psa-ws-status{flex:1;opacity:.9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
      '#psa-ws-status.psa-err{opacity:1;background:#b91c1c;padding:2px 10px;border-radius:999px;}',
      '#psa-ws-hdr button{background:rgba(255,255,255,.14)!important;border:none!important;color:#fff!important;border-radius:9px!important;cursor:pointer;padding:5px 12px!important;margin:0!important;}',
      '#psa-ws-extra:not(:empty){padding:8px 12px;background:#fff8e1;border-bottom:1px solid #f0d58a;}',
      '#psa-ws-frame{flex:1;border:none;width:100%;}',
      /* recording bar */
      '#psa-recbar{position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:2147483647;display:flex;align-items:center;gap:10px;max-width:calc(100vw - 32px);padding:8px 10px 8px 14px;background:#b42318;color:#fff;border-radius:999px;box-shadow:0 8px 28px rgba(0,0,0,.3);font-size:13px;}',
      '#psa-recbar .psa-recdot{flex:0 0 auto;width:10px;height:10px;border-radius:50%;background:#fff;animation:psaPulse 1.2s infinite;}',
      '#psa-recbar-status{opacity:.95;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
      '#psa-recbar button{flex:0 0 auto;height:auto!important;margin:0!important;padding:5px 12px!important;border:none!important;border-radius:999px!important;cursor:pointer;font:600 12px "Segoe UI",system-ui,sans-serif!important;background:#fff!important;color:#b42318!important;}',
      '#psa-recbar button.psa-rec-cancel{background:rgba(255,255,255,.2)!important;color:#fff!important;}',
      '#psa-panel *:focus-visible,#psa-ws *:focus-visible,#psa-recbar *:focus-visible,#psa-fab:focus-visible{outline:3px solid #818cf8;outline-offset:2px;}',
      /* PeopleSoft pop-ups and message boxes (z-index 9999) must show above the agent's workspace */
      '#pt_modals [id^="ptMod_"],#pt_modals [id^="ptModTable_"]{z-index:2147483646!important;}',
      '@media (max-width:760px){#psa-panel,#psa-panel.psa-wide{width:auto;left:12px;}body.psa-docked #psa-ws{display:none!important;}}'
    ].join('');
    var st = document.createElement('style');
    st.id = 'psa-styles';
    st.textContent = css;
    document.head.appendChild(st);

    var SVG = function (d, w) { return '<svg width="' + (w || 18) + '" height="' + (w || 18) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>'; };
    var ICON = {
      compose: SVG('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'),
      wide: SVG('<path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/>'),
      close: SVG('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
      clock: SVG('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', 16),
      send: SVG('<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>'),
      page: SVG('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>', 15),
      rec: '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#dc2626"></span>'
    };
    var AV = '<span class="psa-av" aria-hidden="true"><i></i><i></i></span>';

    var fab = document.createElement('button');
    fab.id = 'psa-fab';
    fab.type = 'button';
    fab.setAttribute('aria-label', 'Open PeopleSoft Agent');
    fab.setAttribute('aria-expanded', 'false');
    fab.innerHTML = AV + '<span>Ask Agent</span>';
    document.body.appendChild(fab);

    var panel = document.createElement('div');
    panel.id = 'psa-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'PeopleSoft Agent');
    panel.innerHTML =
      '<div id="psa-hdr">' + AV + '<div class="psa-who"><div class="psa-name">PeopleSoft Agent</div><div id="psa-status" aria-live="polite">Ready</div></div>'
      + '<button id="psa-rec" class="psa-ib" type="button" title="Teach me a new task by doing it once" style="display:none">' + ICON.rec + 'Record</button>'
      + '<button id="psa-new" class="psa-ib" type="button" title="New conversation" aria-label="New conversation">' + ICON.compose + '</button>'
      + '<button id="psa-wide" class="psa-ib" type="button" title="Wider panel" aria-label="Wider panel">' + ICON.wide + '</button>'
      + '<button id="psa-close" class="psa-ib" type="button" title="Close (Esc)" aria-label="Close">' + ICON.close + '</button></div>'
      + '<div id="psa-ctx" hidden></div>'
      + '<div id="psa-log" role="log" aria-live="polite"></div>'
      + '<div id="psa-recentpop" role="listbox" aria-label="Recent questions" hidden></div>'
      + '<form id="psa-form"><button id="psa-hist" class="psa-ib" type="button" title="Recent questions" aria-label="Recent questions">' + ICON.clock + '</button>'
      + '<textarea id="psa-input" rows="1" aria-label="Message" placeholder="Ask anything, or tell me what to do..."></textarea>'
      + '<button id="psa-send" type="submit" aria-label="Send">' + ICON.send + '</button></form>'
      + '<div id="psa-hint">Enter to send · Shift+Enter for a new line · Ctrl+/ opens and closes me</div>';
    document.body.appendChild(panel);
    var logEl = panel.querySelector('#psa-log');
    var input = panel.querySelector('#psa-input');
    var state = loadState();
    var busy = false;
    var ready = false;   /* config loaded from the iScript */
    var me = null, canTeach = false;   /* from /api/me: who is signed in, tasks for the welcome screen */
    var liveWaiter = null;             /* a live run waiting for the user's answer in the chat */
    var ctxTimer = null;

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

    /* Questions the user asked before, newest first (this browser, this user) */
    function recentKey() { return 'psagent.recent.' + (cfg.oprid || ''); }
    function recentList() { try { return JSON.parse(localStorage.getItem(recentKey())) || []; } catch (e) { return []; } }
    function remember(q) {
      try {
        var list = recentList().filter(function (x) { return x !== q; });
        list.unshift(q);
        localStorage.setItem(recentKey(), JSON.stringify(list.slice(0, 12)));
      } catch (e) {}
    }

    function greet() {
      var w = document.createElement('div');
      w.className = 'psa-welcome';
      /* the user profile description, when it looks like a name (some are dates or codes) */
      var full = String(cfg.fullname || ''), ctx = pageContext(), recent = recentList();
      var name = /[A-Za-z]{2}/.test(full) && !/\d{3}/.test(full) ? full.split(/\s+/)[0] : (cfg.oprid || '');
      var html = '<h2>Hi' + (name ? ' ' + esc(name) : '') + ', what can I do?</h2>'
        + '<p>Tell me in your own words. I work in PeopleSoft for you, ask when I am not sure, and check with you before anything is saved.</p>';
      if (recent.length) {
        html += '<div class="psa-sec">Recent</div>' + recent.slice(0, 4).map(function (q) {
          return '<button type="button" class="psa-recent" data-q="' + esc(q) + '">' + ICON.clock + '<span>' + esc(q) + '</span></button>';
        }).join('');
      }
      var tiles = [];
      if (ctx.error) tiles.push({ t: 'Help with this error', s: ctx.error, q: 'Help me with this error' });
      ((me && me.tasks) || []).slice(0, 6).forEach(function (t) { tiles.push({ t: t.title, s: t.example, q: t.example }); });
      if (ctx.component && !isFrameworkPage(ctx)) tiles.push({ t: 'Explain this page', s: ctx.title || ctx.component, q: 'What is this page for and how do I use it?' });
      tiles.push({ t: 'Something new', s: 'Describe it and I will work it out on screen', q: '', cls: ' psa-new' });
      html += '<div class="psa-sec">I can</div><div class="psa-grid">' + tiles.map(function (x) {
        return '<button type="button" class="psa-tile' + (x.cls || '') + '" data-q="' + esc(x.q) + '"><b>' + esc(x.t) + '</b><small>' + esc(x.s || '') + '</small></button>';
      }).join('') + '</div>';
      w.innerHTML = html;
      w.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('[data-q]');
        if (!b) return;
        var q = b.getAttribute('data-q');
        if (q) send(q);
        else { input.placeholder = 'e.g. Lock the account of user JSMITH and add the role Manager'; input.focus(); }
      });
      logEl.appendChild(w);
    }

    var STATE_TEXT = { idle: 'Ready', thinking: 'Thinking...', working: 'Working...', waiting: 'Waiting for you', error: 'Stopped: see the chat' };
    function setStatus(st, text) {
      panel.setAttribute('data-state', st);
      fab.setAttribute('data-state', st);
      panel.querySelector('#psa-status').textContent = text || STATE_TEXT[st] || '';
    }

    function typing(text) {
      var d = document.createElement('div');
      d.className = 'psa-msg psa-bot psa-typing';
      d.innerHTML = '<span class="psa-dots" aria-label="Thinking"><span></span><span></span><span></span></span>';
      logEl.appendChild(d);
      logEl.scrollTop = logEl.scrollHeight;
      var before = panel.getAttribute('data-state');
      setStatus('thinking', text);
      return { remove: function () { d.remove(); if (panel.getAttribute('data-state') === 'thinking') setStatus(before === 'working' ? 'working' : 'idle'); } };
    }

    /* The agent's work log, like a terminal of what it is doing: one line per step */
    function activity(title) {
      var box = document.createElement('div');
      box.className = 'psa-feed';
      box.innerHTML = '<div class="psa-feed-h"><span class="psa-ic run"></span><span class="psa-feed-t"></span><small></small></div><ol></ol>';
      box.querySelector('.psa-feed-t').textContent = title;
      logEl.appendChild(box);
      logEl.scrollTop = logEl.scrollHeight;
      var list = box.querySelector('ol'), head = box.querySelector('.psa-feed-h .psa-ic'), t0 = Date.now();
      function tick() { box.querySelector('.psa-feed-h small').textContent = Math.round((Date.now() - t0) / 1000) + 's'; }
      var timer = setInterval(tick, 1000);
      return {
        add: function (text, kind) {
          var li = document.createElement('li');
          li.innerHTML = '<span class="psa-ic ' + (kind || 'run') + '"></span><span></span>';
          li.lastChild.textContent = text;
          list.appendChild(li);
          list.scrollTop = list.scrollHeight;
          logEl.scrollTop = logEl.scrollHeight;
          return {
            end: function (ok, note) {
              li.firstChild.className = 'psa-ic ' + (ok ? 'ok' : 'err');
              if (note) { var em = document.createElement('em'); em.textContent = ' ' + note; li.lastChild.appendChild(em); }
            },
            remove: function () { li.remove(); }
          };
        },
        finish: function (status) { clearInterval(timer); tick(); head.className = 'psa-ic ' + (status === 'success' ? 'ok' : 'err'); }
      };
    }

    function refreshContext() {
      var c = pageContext(), el = panel.querySelector('#psa-ctx');
      if (!c.component || isFrameworkPage(c)) { el.hidden = true; return; }
      el.hidden = false;
      el.innerHTML = ICON.page + '<span>You are on <b>' + esc(c.title || c.component) + '</b></span>'
        + (c.error ? '<span class="psa-e">· ' + esc(c.error.slice(0, 90)) + '</span>' : '');
    }

    function showRecent() {
      var pop = panel.querySelector('#psa-recentpop'), list = recentList();
      if (!pop.hidden) { pop.hidden = true; return; }
      pop.innerHTML = list.length ? list.map(function (q) {
        return '<button type="button" class="psa-recent" role="option" data-q="' + esc(q) + '">' + ICON.clock + '<span>' + esc(q) + '</span></button>';
      }).join('') : '<div style="padding:10px;color:#64748b;font-size:13px">Your questions will show up here.</div>';
      pop.hidden = false;
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
      if (r.live) return runLive(r.live);
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
      if (!text) return;
      panel.querySelector('#psa-recentpop').hidden = true;
      if (liveWaiter) {                              /* the agent asked something during a live run */
        var answer = liveWaiter;
        liveWaiter = null;
        addMsg('user', text);
        answer(text);
        return;
      }
      if (busy) return;
      [].forEach.call(logEl.querySelectorAll('.psa-welcome'), function (w) { w.remove(); });
      addMsg('user', text);
      remember(text);
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
        + 'to the end: Save, or Run and OK for a process. A red bar at the top shows what is recorded; click Finish recording there.</small>'
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
        + ' Do the task once by hand, to the end (Save, or Run and OK for a process), then click **Finish recording** in the red bar at the top.');
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
      var r = recording, acts = r.steps.filter(isAction), last = acts[acts.length - 1];
      var done = r.steps.filter(function (st) { return st['do'] === 'outcome'; }).pop();
      var text = !r.component ? 'Go to the page for this task (NavBar or menu) and do it once, including Save or Run.'
        : r.offPage ? 'You left ' + r.component.component + '. A task covers one page: go back, or finish.'
        : acts.length + ' steps on ' + r.component.component
          + (done ? ' · ' + (done.kind === 'saved' ? 'Saved' : 'process ' + (done.detail ? done.detail + ' ' : '') + 'submitted') + ': click Finish recording'
            : last ? ' · last: ' + describe(last) : ' · waiting for your first entry');
      var st = recBar.querySelector('#psa-recbar-status');
      if (st.textContent !== text) st.textContent = text;
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
        if (!same && r.component && r.steps.some(isAction)) {
          if (!r.offPage) { r.offPage = true; recordingChanged(); }
        } else {
          if (!same) { r.component = info.component; r.query = info.query; r.steps = []; }
          r.offPage = false;
          if (!t.doc._psaRec) { attachRecorder(t.doc, r, recordingChanged); recordingChanged(); }
        }
      }
      if (r.component && !r.offPage) {                 /* pop-ups over the page: Process Scheduler Request */
        modalFrames(t.frame).forEach(function (f) {
          if (!f._psaRecHook) { f._psaRecHook = true; f.addEventListener('load', function () { setTimeout(recordingTick, 0); }); }
          try {
            var d = f.contentDocument;
            if (d && d.body && d.readyState !== 'loading' && d.URL !== 'about:blank' && !d._psaRec) attachRecorder(d, r, recordingChanged, true);
          } catch (e) {}
        });
        if (info && t.doc && t.doc._psaRec && observe(r, t.doc, t.frame)) recSave(r);
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
      var t = workTarget();
      [t.doc].concat(modalDocs(t.frame)).forEach(function (d) { try { if (d && d._psaRec) d._psaRec.diff(); } catch (e) {} });
      stopRecording();
      open();
      if (!r || !r.component || !r.steps.some(isAction)) {
        addMsg('bot', 'Nothing was entered or clicked on a transaction page, so there is nothing to learn. Click **Record** and try again: '
          + 'go to the page and do the task there.', false);
        return;
      }
      reviewRecording({ title: r.title, component: r.component, query: r.query || {}, steps: r.steps });
    }

    function reviewRecording(rec) {
      ensureConfig().then(function () {
        var t = typing('Turning it into a task...');
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
        return '<tr><td>' + esc(f.label) + (f.kind === 'list' ? ' (list)' : '') + (f.search_key ? ' <small>(search key)</small>' : '') + '<small>'
          + (f.mode === 'secret' ? 'secret' : 'recorded: ' + esc(shown || '(blank)')) + '</small></td><td>' + choice + '</td></tr>';
      }).join('');
      var stepList = rec.steps.filter(isAction).map(function (st) {
        return '<li>' + esc(describe(st)) + ({ search: ' <small>(search page)</small>', popup: ' <small>(pop-up)</small>' }[st.phase] || '') + '</li>';
      }).join('');
      var OPEN = { add: 'Add a new one (stop if it already exists)', add_or_open: 'Add a new one, or open it if it already exists',
        find: 'Open an existing one' };
      var opens = draft.open_mode ? '<label>Each time, the record is<select data-k="open_mode">' + Object.keys(OPEN).map(function (m) {
        return '<option value="' + m + '"' + (m === draft.open_mode ? ' selected' : '') + '>' + OPEN[m] + '</option>';
      }).join('') + '</select></label>' : '';
      var result = '<small>' + (draft.outcome === 'saved' ? 'At the end it checks that PeopleSoft shows <b>Saved</b>.'
        : draft.outcome === 'process' ? 'At the end it checks that PeopleSoft <b>accepted the process request</b> (and reports the process instance when the page shows one).'
        : 'No Save or process run was recorded: the task ends after the last step.') + '</small>'
        + (draft.notes || []).map(function (n) { return '<small>' + esc(n) + '</small>'; }).join('');
      var c = card('<h4>Review the recorded task</h4>'
        + '<label>Task name<input data-k="title" value="' + esc(draft.title) + '"></label>'
        + '<label>What it does<input data-k="summary" value="' + esc(draft.summary) + '"></label>'
        + opens
        + '<table>' + rows + '</table>' + result
        + '<details><summary>' + rec.steps.filter(isAction).length + ' recorded steps</summary><ol>' + stepList + '</ol></details>'
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
        if (c.querySelector('[data-k="open_mode"]')) edited.open_mode = c.querySelector('[data-k="open_mode"]').value;
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
      var ws = openWorkspace('Agent: ' + action.title), feed = activity(action.title), cur = null;
      ws.onStep = function (n, total, text) {
        if (cur) cur.end(true);
        cur = feed.add(text);
        setStatus('working', 'Step ' + n + ' of ' + total + ': ' + text);
      };
      setStatus('working');
      return runAction(action, secrets, ws).catch(function (e) {
        return { status: 'error', message: 'The agent hit an unexpected problem: ' + ((e && e.message) || e) };
      }).then(function (res) {
        secrets = null;
        ws.done();
        if (cur) cur.end(res.status === 'success', res.status === 'success' ? null : res.message);
        feed.finish(res.status);
        if (res.status === 'success') ws.status('Done: ' + action.title);
        else if (res.status === 'stopped') ws.status('Stopped', true);
        else ws.status('Stopped at "' + (res.failed_step || 'a step') + '": ' + res.message, true);
        setStatus(res.status === 'success' ? 'idle' : 'error');
        /* Show PeopleSoft's own words right away; the agent's explanation follows */
        if (res.status === 'error') addMsg('bot', '⚠️ Stopped at **' + (res.failed_step || 'a step') + '**. PeopleSoft says:\n\n' + res.message);
        return report(action.run_id, res);
      });
    }

    function report(runId, res) {
      var t = typing();
      return api('/api/result', { session_id: state.sessionId, run_id: runId, status: res.status, message: res.message, failed_step: res.failed_step })
        .then(function (r) { t.remove(); return handleReply(r); },
              function (e) { t.remove(); addMsg('bot', 'I could not report the result to the agent service: ' + e.message, false); });
    }

    /* Live mode: Claude looks at the workspace page, picks the next move, the widget does it, repeat.
       Saves and submits wait for the user's approval; passwords are typed by the user here. */
    function runLive(live) {
      var ws = openWorkspace('Agent: ' + live.goal), feed = activity(live.goal), userCtx = pageContext();
      var lrec = startLiveRecorder(ws.frame, live.goal), payload = {}, secrets = {};
      setStatus('working', 'Working on: ' + live.goal);

      function loop() {
        if (ws.stopped) return finish('stopped', 'Stopped by the user');
        var looking = feed.add('Looking at the page', 'think');
        setStatus('thinking', 'Looking at the page...');
        var body = { session_id: state.sessionId, run_id: live.run_id, snapshot: snapshotPage(ws.frame, userCtx) };
        for (var k in payload) body[k] = payload[k];
        payload = {};
        return api('/api/live/step', body).then(function (r) { looking.remove(); return handle(r.action || {}); },
          function (e) { looking.remove(); return finish('error', 'I lost contact with the agent service: ' + e.message); });
      }

      function handle(a) {
        if (a.type === 'do') {
          setStatus('working', a.note || 'Working...');
          ws.status(a.note || describe(a.steps[0]));
          return runLiveSteps(ws.frame, a.steps || [], function (text) { return feed.add(text); })
            .then(function (res) { payload = { result: res }; return loop(); });
        }
        if (a.type === 'ask') {
          feed.add('Asked you a question', 'ask').end(true);
          addMsg('bot', a.question);
          return waitForUser('Waiting for your answer').then(function (text) { payload = { answer: text }; return loop(); });
        }
        if (a.type === 'secret') {
          return askSecret(a).then(function (res) { payload = { result: res }; return loop(); });
        }
        if (a.type === 'confirm') {
          return askApproval(a).then(function (yes) {
            feed.add((yes ? 'You approved: ' : 'You declined: ') + a.button, yes ? 'ok' : 'err');
            payload = yes ? { approved: a.confirm_id } : { declined: true };
            return loop();
          });
        }
        if (a.type === 'done') return finish('success', a.summary || 'Done');
        return finish('error', a.summary || 'The agent stopped.');
      }

      function waitForUser(status) {
        setStatus('waiting', status);
        setBusy(false);
        input.focus();
        return new Promise(function (resolve) { liveWaiter = resolve; }).then(function (text) { setBusy(true); return text; });
      }

      function askSecret(a) {
        var label = a.field.label || 'Password', key = norm(label).replace(/^(confirm|re-?enter|repeat|verify|new)\s+/, '');
        function type(value) {
          var item = feed.add('Typing ' + label + ' (hidden)');
          return STEPS.set(ws.frame, { 'do': 'set', field: a.field, value: value, popup: a.popup })
            .then(function () { item.end(true); return { ok: true, note: 'the user typed ' + label }; },
                  function (e) { item.end(false, e.message); return { ok: false, step: 'Set ' + label, error: e.message }; });
        }
        if (secrets[key]) return type(secrets[key]);   /* "Confirm Password": the one typed a moment ago */
        setStatus('waiting', 'Waiting for you to type ' + label);
        return new Promise(function (resolve) {
          var c = card('<h4>' + esc(label) + '</h4><small>Type it here. It goes straight into PeopleSoft; I never see it.</small>'
            + '<label>' + esc(label) + '<input type="password" autocomplete="new-password" data-s="1"></label>'
            + '<div class="psa-actions"><button type="button" class="psa-ok" disabled>Type it in</button><button type="button" class="psa-no">Skip</button></div>');
          var inp = c.querySelector('[data-s]'), ok = c.querySelector('.psa-ok');
          inp.addEventListener('input', function () { ok.disabled = !inp.value; });
          inp.addEventListener('keydown', function (e) { if (e.key === 'Enter' && inp.value) ok.click(); });
          setTimeout(function () { inp.focus(); }, 50);
          ok.addEventListener('click', function () {
            var v = inp.value;
            inp.value = '';
            disable(c);
            secrets[key] = v;
            resolve(type(v));
          });
          c.querySelector('.psa-no').addEventListener('click', function () { disable(c); resolve({ ok: false, error: 'the user skipped typing ' + label }); });
        });
      }

      function askApproval(a) {
        setStatus('waiting', 'Waiting for your approval');
        return new Promise(function (resolve) {
          var rows = (a.rows || []).map(function (r) { return '<tr><td>' + esc(r.label) + '</td><td><strong>' + esc(r.value) + '</strong></td></tr>'; }).join('');
          var c = card('<h4>Ready to ' + esc(String(a.button || 'save').toLowerCase()) + ': ' + esc(a.title) + '</h4>'
            + (rows ? '<table>' + rows + '</table>' : '')
            + '<small>I will click <b>' + esc(a.button) + '</b> in PeopleSoft only if you approve. You can check the workspace first.</small>'
            + '<div class="psa-actions"><button type="button" class="psa-ok">Approve: ' + esc(a.button) + '</button><button type="button" class="psa-no">Not now</button></div>');
          c.classList.add('psa-approve');
          c.querySelector('.psa-ok').addEventListener('click', function () { disable(c); resolve(true); });
          c.querySelector('.psa-no').addEventListener('click', function () { disable(c); resolve(false); });
        });
      }

      function finish(status, summary) {
        lrec.stop();
        liveWaiter = null;
        secrets = null;
        ws.done();
        feed.finish(status);
        ws.status(status === 'success' ? 'Done: ' + live.goal : 'Stopped: ' + summary, status !== 'success');
        setStatus(status === 'success' ? 'idle' : 'error');
        return report(live.run_id, { status: status, message: summary }).then(function () {
          var rec = lrec.rec;
          if (status !== 'success' || !canTeach || !rec.component || !rec.steps.some(isAction)) return;
          var c = card('<h4>Make this a task?</h4><small>I worked this out step by step. Saved as a task, I can do it again in one go, '
            + 'with new values each time.</small><div class="psa-actions"><button type="button" class="psa-ok">Save as a task</button>'
            + '<button type="button" class="psa-no">No thanks</button></div>');
          c.querySelector('.psa-no').addEventListener('click', function () { disable(c); });
          c.querySelector('.psa-ok').addEventListener('click', function () {
            disable(c);
            reviewRecording({ title: live.goal, component: rec.component, query: rec.query || {}, steps: rec.steps });
          });
        });
      }

      return loop();
    }

    function open() {
      panel.classList.add('psa-open');
      document.body.classList.add('psa-docked');
      fab.setAttribute('aria-expanded', 'true');
      refreshContext();
      clearInterval(ctxTimer);
      ctxTimer = setInterval(refreshContext, 3000);
      if (!ready) {                       /* first open, or the last attempt failed: (re)connect */
        logEl.innerHTML = '';
        ensureConfig().then(function () {
          ready = true;
          return apiGet('/api/me').then(function (m) {
            me = m;
            canTeach = !!m.can_teach;
            if (canTeach) panel.querySelector('#psa-rec').style.display = '';
          }, function (e) {
            addMsg('bot', 'I cannot reach the agent service at ' + (cfg.agentUrl || '(no URL)') + ': ' + e.message
              + '. Check that it is running and that this PeopleSoft address is in PSAGENT_ALLOWED_ORIGINS.', false);
          }).then(function () {
            if (state.history.length) state.history.forEach(function (h) { addMsg(h.who, h.text, false); });
            else greet();
          });
        }, function (e) {
          console.warn('[PSAgent] config failed:', e);
          addMsg('bot', 'Sorry, the agent is not available right now: ' + e.message + '.', false);
        });
      }
      setTimeout(function () { input.focus(); }, 100);
    }
    function close() {
      panel.classList.remove('psa-open');
      document.body.classList.remove('psa-docked');
      fab.setAttribute('aria-expanded', 'false');
      clearInterval(ctxTimer);
      fab.focus();
    }
    function setWide(on) {
      panel.classList.toggle('psa-wide', on);
      document.body.classList.toggle('psa-widebody', on);
      try { localStorage.setItem('psagent.wide', on ? '1' : ''); } catch (e) {}
    }
    try { if (localStorage.getItem('psagent.wide')) setWide(true); } catch (e) {}

    function submit() {
      var t = input.value;
      if (!String(t).trim() || (busy && !liveWaiter)) return;
      input.value = '';
      grow();
      send(t);
    }
    function grow() { input.style.height = 'auto'; input.style.height = Math.min(Math.max(input.scrollHeight, 34), 140) + 'px'; }

    fab.addEventListener('click', function () { panel.classList.contains('psa-open') ? close() : open(); });
    panel.querySelector('#psa-close').addEventListener('click', close);
    panel.querySelector('#psa-wide').addEventListener('click', function () { setWide(!panel.classList.contains('psa-wide')); });
    panel.querySelector('#psa-rec').addEventListener('click', function () { showRecordCard({ title: '' }); });
    panel.querySelector('#psa-hist').addEventListener('click', showRecent);
    panel.querySelector('#psa-recentpop').addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-q]');
      if (b) send(b.getAttribute('data-q'));
    });
    panel.querySelector('#psa-new').addEventListener('click', function () {
      state = { sessionId: null, history: [] };
      saveState();
      logEl.innerHTML = '';
      setStatus('idle');
      greet();
    });
    panel.querySelector('#psa-form').addEventListener('submit', function (e) { e.preventDefault(); submit(); });
    input.addEventListener('input', grow);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
      if (e.key === 'ArrowUp' && !input.value) { var r = recentList()[0]; if (r) { e.preventDefault(); input.value = r; grow(); } }
    });
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === '/') { e.preventDefault(); panel.classList.contains('psa-open') ? close() : open(); }
      else if (e.key === 'Escape' && panel.classList.contains('psa-open')) close();
    });
    setStatus('idle');

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
    /* what live mode sends to Claude for the page in the workspace (or the page you are on) */
    snapshot: function () { return snapshotPage(workspace && workspace.el.style.display !== 'none' ? workspace.frame : getContentFrameEl() || { contentDocument: document, contentWindow: window }); },
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
