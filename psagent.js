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

  var VERSION = '2026-11-04.2';   /* shown as PSAgent.version and in the console: check after uploading */
  var NAME = 'Pia', NAME_LONG = 'PeopleSoft Intelligent Assistant';   /* also in agent-service SYSTEM_PROMPT */

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
  var cfg = { agentUrl: '', token: '', oprid: '', fullname: '', roles: null };
  /* an alert email's "Open in the agent" link: ...?psagent=inc-... opens the panel on that alert */
  var INC_LINK = (String(location.search + location.hash).match(/[?&#]psagent=(inc-[\w-]+)/) || [])[1] || null;

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

  /* The page is not what the recording expects (field, button or tab missing, no response):
     a recorded task can be finished live from there. PeopleSoft's own messages are not this. */
  function mismatch(msg) { var e = new Error(msg); e.mismatch = true; return e; }

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
    if (el.disabled || el.readOnly) throw mismatch(name + ' is read-only on this page');
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

  function modalButtons(m) {
    var box = (m.el.closest && (m.el.closest('[id^="ptMod_"]') || m.el.closest('#pt_modals'))) || document;
    return [].filter.call(box.querySelectorAll('input[type="button"],input[type="submit"],button'), function (b) {
      return visible(b) && (b.value || b.textContent || '').trim();
    });
  }

  /* The Cancel or No button of an open question box (never OK or Yes, which could save) */
  function modalNoButton() {
    var m = modalMessage();
    return m ? modalButtons(m).filter(function (b) { return /^(cancel|no)$/i.test((b.value || b.textContent).trim()); })[0] : null;
  }

  /* Close a message box whose only button is OK (after its text was captured).
     Yes/No questions are left open for the person to answer. */
  function dismissModal() {
    var m = modalMessage();
    if (!m) return false;
    var btns = modalButtons(m);
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
        if (Date.now() - start > timeout) { if (obs) obs.disconnect(); return reject(mismatch('PeopleSoft did not finish processing in time')); }
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
      if (Date.now() - start > RUN.stepTimeout) return Promise.reject(mismatch(notFound));
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
        if (!btn || !visible(btn)) throw mismatch('Could not find the ' + (add ? 'Add' : 'Search') + ' button on the search page');
        return act(frame, function () { btn.click(); });
      }).then(function (doc) {
        if (!isSearchPage(doc)) return checkOpened(doc, step);          /* opened (the only match, or the new record) */
        var msg = psMessage(doc), want = step.pick || (keys[0] && keys[0].value);
        var exists = !!msg && /already exists/i.test(msg);
        if (mode === 'add' || (mode === 'add_or_open' && !exists)) throw msg ? new Error(msg) : mismatch('PeopleSoft stayed on the search page');
        var row = want != null && resultRow(doc, want);
        if (!row) throw new Error(msg || 'No search result is "' + want + '"');
        return act(frame, function () { row.click(); }).then(function (d) {
          if (isSearchPage(d)) throw psMessage(d) ? new Error(psMessage(d)) : mismatch('The search result "' + want + '" did not open');
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
      return Promise.reject(mismatch('Could not find the "' + step.label + '" tab'));
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
      return Promise.reject(mismatch('No PeopleSoft error appeared, but the "Saved" confirmation was not seen. Please check the page.'));
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
    if (!btn) return Promise.reject(mismatch('Could not add a row for ' + fieldName(spec)));
    return act(frame, function () { btn.click(); }).then(function () { return waitForField(frame, spec, row); });
  }

  /* action = {run_id, title, component, steps}; secrets = {password: '...'}; ws = workspace */
  function runAction(action, secrets, ws) {
    var steps = action.steps || [], i = 0, frame = ws.frame, done = null;
    dismissModal();   /* a message box left over from earlier would be mistaken for this run's */
    function fail(message, raw, isMismatch) {
      dismissModal();
      return { status: 'error', message: message, failed_step: describe(raw || {}), failed_index: i, mismatch: !!isMismatch };
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
        return fail((err && err.message) || String(err), raw, err && err.mismatch);
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

  /* {menu, component, market} of MENU.COMPONENT.GBL. Real names hold brackets and hyphens: ADMINISTER_WORKFORCE_(GBL), RUN_PER006-- */
  function parseComponentName(text) {
    var p = String(text || '').replace(/%28/gi, '(').replace(/%29/gi, ')').split('.');
    if (p.length < 2 || !/^[A-Z0-9_()-]+$/i.test(p[0]) || !/^[A-Z0-9_()-]+$/i.test(p[1])) return null;
    return { menu: p[0].toUpperCase(), component: p[1].toUpperCase(), market: (p[2] || 'GBL').toUpperCase() };
  }

  /* {component, query} of a PeopleSoft component URL; portal and internal parameters dropped */
  function parseComponentUrl(href) {
    var m = String(href || '').match(/\/ps[cp]\/[^\/]+\/[^\/]+\/[^\/]+\/c\/([^?#\/]+)(?:\?([^#]*))?/i);
    if (!m) return null;
    var comp = parseComponentName(m[1]);
    if (!comp) return null;
    var query = {};
    (m[2] || '').split('&').forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf('='), k, v;
      try { k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i)); v = i < 0 ? '' : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); }
      catch (e) { return; }
      if (!/^(Portal|NoCrumbs|IC|cmd$|nav|FolderPath|IsFolder)/i.test(k)) query[k] = v;
    });
    return { component: comp, query: query };
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

  /* A regression run: the service hands out one case at a time; it runs here like any task and the result goes back
     to be judged. When the page no longer matched a recorded step, the case is finished live: Save is approved at
     once (regression tests only run on test environments) and passwords come from the run's secrets. Used by the
     panel and by the headless runner on the service (PSAgent.runSuite), so both behave the same.
     ui: {workspace(ws), caseStart(c), caseEnd(c, verdict), step(text)} */
  function suiteLoop(runId, secrets, ui) {
    ui = ui || {};
    secrets = secrets || {};
    var ws = openWorkspace('Regression tests');
    if (ui.workspace) ui.workspace(ws);
    function secretFor(label) {
      var key = norm(label).replace(/^(confirm|re-?enter|repeat|verify|new)\s+/, '').replace(/\s+/g, '_');
      return secrets[key] || secrets.password || secrets[Object.keys(secrets)[0]] || '';
    }
    function live(c) {
      function step(payload) {
        var body = { run_id: runId, snapshot: snapshotPage(ws.frame, {}) };
        for (var k in payload) body[k] = payload[k];
        return api('/api/regress/live', body).then(function (r) {
          if (r.verdict) return r;
          var a = r.action || {};
          if (ui.step && a.note) ui.step('Live: ' + a.note);
          if (a.type === 'do') {
            return runLiveSteps(ws.frame, a.steps || [], function (t) { if (ui.step) ui.step(t); return { end: function () {} }; })
              .then(function (res) { return step({ result: res }); });
          }
          if (a.type === 'secret') {
            return STEPS.set(ws.frame, { 'do': 'set', field: a.field, value: secretFor(a.field.label), popup: a.popup }).then(
              function () { return step({ result: { ok: true, note: 'typed ' + a.field.label } }); },
              function (e) { return step({ result: { ok: false, step: 'Set ' + a.field.label, error: e.message } }); });
          }
          if (a.type === 'confirm') return step({ approved: a.confirm_id });
          return step({});
        });
      }
      return step({});
    }
    /* A question box the case stopped at would be read by the next case as its own: answer no, so nothing is saved */
    function closeBox() {
      var no = modalNoButton();
      if (!no) return Promise.resolve();
      return act(ws.frame, function () { no.click(); }).catch(function () {});
    }
    function next() {
      if (ws.stopped) return api('/api/regress/stop', { run_id: runId }).then(function (r) { ws.done(); return r.report; });
      return api('/api/regress/next', { run_id: runId }).then(function (r) {
        if (r.done) { ws.done(); ws.status('Regression run finished: ' + r.report.headline); return r.report; }
        var c = r['case'];
        if (ui.caseStart) ui.caseStart(c);
        ws.onStep = function (n, total, text) { if (ui.step) ui.step('Case ' + c.n + ' of ' + c.of + ' · step ' + n + '/' + total + ': ' + text); };
        return runAction(c.action, secrets, ws).catch(function (e) {
          return { status: 'error', message: 'The runner hit an unexpected problem: ' + ((e && e.message) || e) };
        }).then(function (res) {
          return api('/api/regress/result', { run_id: runId, n: c.n, result: res });
        }).then(function (v) {
          if (v.recover) { if (ui.step) ui.step('The page changed: finishing case ' + c.n + ' live'); return live(c); }
          return v;
        }).then(function (v) {
          if (ui.caseEnd) ui.caseEnd(c, v.verdict);
          if (v.stopped) { ws.done(); return api('/api/regress/next', { run_id: runId }).then(function (x) { return x.report; }); }
          return closeBox().then(next);
        });
      });
    }
    return next();
  }

  function initWidget() {
    if (document.getElementById('psa-fab')) return;

    var css = [
      '#psa-panel,#psa-fab,#psa-ws,#psa-recbar,#psa-audit{--psa-ink:#0f172a;--psa-sub:#64748b;--psa-line:#e6e8f0;--psa-soft:#f6f7fb;--psa-brand:#4f46e5;--psa-brand2:#0ea5e9;--psa-ok:#16a34a;--psa-warn:#d97706;--psa-err:#dc2626;font-family:"Segoe UI",system-ui,-apple-system,sans-serif;}',
      /* launcher */
      '#psa-fab{position:fixed;right:24px;bottom:24px;z-index:2147483646;display:flex;align-items:center;gap:10px;height:52px;padding:0 20px 0 7px!important;margin:0!important;border:none!important;border-radius:26px!important;background:#fff!important;color:var(--psa-ink)!important;box-shadow:0 12px 32px rgba(15,23,42,.2),0 0 0 1px rgba(79,70,229,.18);cursor:pointer;font:600 14px "Segoe UI",system-ui,sans-serif!important;transition:transform .15s,box-shadow .15s;}',
      '#psa-fab:hover{transform:translateY(-2px);box-shadow:0 16px 40px rgba(79,70,229,.3),0 0 0 1px rgba(79,70,229,.35);}',
      'body.psa-docked #psa-fab{display:none;}',
      /* the agent: an avatar with eyes and a status light */
      '.psa-av{position:relative;flex:0 0 auto;display:inline-block;width:38px;height:38px;border-radius:50%;background:radial-gradient(circle at 30% 25%,rgba(255,255,255,.55),transparent 42%),linear-gradient(135deg,var(--psa-brand),var(--psa-brand2));box-shadow:inset 0 -4px 10px rgba(0,0,0,.18);}',
      '.psa-av i{position:absolute;top:30%;width:6px;height:7px;border-radius:50%;background:#fff;animation:psaBlink 5s infinite;}',
      '.psa-av i:first-child{left:27%;}.psa-av i:last-child{right:27%;}',
      '.psa-av:before{content:"";position:absolute;left:50%;top:52%;box-sizing:border-box;width:16px;height:8px;margin-left:-8px;border:2.5px solid #fff;border-top:none;border-radius:0 0 16px 16px;}',
      '.psa-av:after{content:"";position:absolute;right:-1px;bottom:-1px;width:11px;height:11px;border-radius:50%;background:var(--psa-ok);border:2px solid #fff;}',
      '[data-state="thinking"] .psa-av:after,[data-state="working"] .psa-av:after{background:var(--psa-brand2);animation:psaPulse 1s infinite;}',
      '[data-state="waiting"] .psa-av:after{background:var(--psa-warn);}[data-state="error"] .psa-av:after{background:var(--psa-err);}',
      '[data-state="thinking"] #psa-hdr .psa-av,[data-state="working"] #psa-hdr .psa-av{animation:psaGlow 1.6s ease-in-out infinite;}',
      '[data-state="working"] .psa-av i{animation:psaLook 1.6s ease-in-out infinite;}',
      '@keyframes psaBlink{0%,93%,100%{transform:scaleY(1)}96%{transform:scaleY(.1)}}',
      '@keyframes psaLook{0%,100%{transform:translateX(-2px)}50%{transform:translateX(2px)}}',
      '#psa-hdr .psa-av.psa-wave{animation:psaWave 1.2s ease-in-out 1;transform-origin:70% 90%;}',
      '@keyframes psaWave{0%,100%{transform:rotate(0)}15%{transform:rotate(-16deg)}30%{transform:rotate(12deg)}45%{transform:rotate(-12deg)}60%{transform:rotate(8deg)}75%{transform:rotate(-4deg)}}',
      '@media (prefers-reduced-motion:reduce){#psa-panel .psa-av,#psa-panel .psa-av i,#psa-fab .psa-av i{animation:none!important;}}',
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
      '#psa-hdr .psa-who{flex:1;min-width:0;}#psa-hdr .psa-name{font-weight:700;font-size:15px;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
      '#psa-hdr .psa-name small{display:none;font-weight:500;font-size:12px;color:var(--psa-sub);letter-spacing:0;margin-left:4px;}#psa-panel.psa-wide #psa-hdr .psa-name small{display:inline;}',
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
      '.psa-tile{display:flex;gap:10px;align-items:flex-start;width:100%;text-align:left;border:1px solid var(--psa-line)!important;background:#fff!important;border-radius:14px!important;padding:11px 12px!important;margin:0!important;cursor:pointer;color:var(--psa-ink)!important;font:inherit!important;transition:border-color .15s,box-shadow .15s,transform .15s;}',
      '.psa-tile:hover{border-color:#c7d2fe!important;box-shadow:0 8px 20px rgba(79,70,229,.12);transform:translateY(-1px);}',
      '.psa-tile b{display:block;font-size:13px;}.psa-tile small{display:block;color:var(--psa-sub);font-size:12px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      '.psa-tile.psa-new{background:linear-gradient(135deg,#eef2ff,#ecfeff)!important;border-color:#c7d2fe!important;}',
      '.psa-tic{flex:0 0 auto;width:30px;height:30px;border-radius:9px;display:inline-flex;align-items:center;justify-content:center;}',
      '.psa-tt{flex:1;min-width:0;}.psa-fit{font-style:normal;font-weight:600;font-size:10px;color:#047857;background:#d1fae5;border-radius:999px;padding:1px 6px;margin-left:2px;vertical-align:1px;white-space:nowrap;}',
      '.psa-alert{display:flex;gap:10px;align-items:flex-start;margin:10px 0 2px;padding:10px 12px;border:1px solid #fecaca;background:#fef2f2;border-radius:14px;color:#7f1d1d;font-size:13px;}',
      '.psa-alert>svg{flex:0 0 auto;margin-top:2px;color:#dc2626;}',
      '.psa-alert-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px;}',
      '.psa-alert-list button{border:1px solid #fca5a5!important;background:#fff!important;color:#991b1b!important;border-radius:999px!important;padding:4px 11px!important;margin:0!important;cursor:pointer;font:600 12px "Segoe UI",system-ui,sans-serif!important;}',
      '.psa-alert-list button:hover{background:#fee2e2!important;}',
      '.psa-news{position:relative;margin:10px 0 2px;padding:10px 34px 10px 12px;border:1px solid #c7d2fe;background:linear-gradient(135deg,#eef2ff,#f5f3ff);border-radius:14px;font-size:13px;}',
      '.psa-news>b{color:var(--psa-brand);}.psa-news ul{margin:4px 0 0;padding-left:18px;}.psa-news li{margin:2px 0;}',
      '.psa-news-x{position:absolute;top:6px;right:6px;width:24px;height:24px;border:none!important;background:transparent!important;color:var(--psa-sub)!important;cursor:pointer;font-size:18px;line-height:1;padding:0!important;margin:0!important;border-radius:6px!important;}',
      '.psa-news-x:hover{background:#e0e7ff!important;}',
      '.psa-recent{display:flex;align-items:center;gap:10px;width:100%;text-align:left;border:none!important;background:transparent!important;padding:7px 10px!important;margin:0!important;border-radius:10px!important;cursor:pointer;color:var(--psa-ink)!important;font:inherit!important;}',
      '.psa-recent:hover{background:var(--psa-soft)!important;}.psa-recent svg{flex:0 0 auto;color:var(--psa-sub);}.psa-recent span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      /* cards */
      '.psa-card{align-self:stretch;border:1px solid var(--psa-line);border-radius:16px;background:#fff;padding:14px 16px;box-shadow:0 6px 20px rgba(15,23,42,.06);}',
      '.psa-card h4{margin:0 0 8px;font-size:14px;font-weight:700;}',
      '.psa-qr-name{font-weight:400;color:var(--psa-sub);font-size:12px;}.psa-qr-chart{margin:4px 0 10px;}.psa-qr-chart h5{margin:0 0 6px;font-size:12.5px;}',
      '.psa-qr .psa-actions a{flex:1;display:inline-flex;align-items:center;justify-content:center;padding:9px;border-radius:10px;font:600 13px "Segoe UI",system-ui,sans-serif;text-decoration:none!important;color:var(--psa-ink)!important;}',
      '.psa-qr .psa-ci-rows td{white-space:nowrap;}',
      '.psa-ci .psa-sub{color:var(--psa-sub);font-size:12.5px;margin:0 0 8px;}.psa-ci h5{margin:10px 0 4px;font-size:12.5px;}',
      '.psa-ci-q{width:100%;box-sizing:border-box;border:1px solid var(--psa-line);border-radius:9px;padding:7px 10px;font:13px "Segoe UI",system-ui,sans-serif;margin-bottom:6px;}',
      '.psa-ci-list{max-height:260px;overflow:auto;display:flex;flex-direction:column;gap:2px;}',
      '.psa-ci-list button{display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:baseline;text-align:left;border:0;background:none;border-radius:8px;padding:6px 8px;cursor:pointer;font:13px "Segoe UI",system-ui,sans-serif;color:var(--psa-ink);}',
      '.psa-ci-list button:hover,.psa-ci-list button:focus{background:var(--psa-soft);outline:none;}.psa-ci-list span{color:var(--psa-sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}.psa-ci-list small,.psa-ci small{color:var(--psa-sub);font-size:11.5px;}',
      '.psa-ci-ld{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:4px 0;border-top:1px solid var(--psa-line);}.psa-ci-ld span{display:flex;flex-direction:column;min-width:0;}',
      '.psa-ci-ld button{border:1px solid var(--psa-line);background:#fff;border-radius:8px;padding:3px 10px;cursor:pointer;font:12.5px "Segoe UI",system-ui,sans-serif;}',
      '.psa-ci-tree{max-height:320px;overflow:auto;border:1px solid var(--psa-line);border-radius:10px;padding:4px 8px;margin-bottom:6px;}',
      '.psa-ci-coll summary{cursor:pointer;padding:4px 0;}.psa-ci-coll ul{list-style:none;margin:0 0 4px;padding:0 0 0 2px;}.psa-ci-coll li{display:block;padding:3px 0;margin:0;}',
      '.psa-ci-coll li small{display:block;padding-left:22px;line-height:1.3;}.psa-ci-coll input[type=checkbox]{width:14px;height:14px;margin:0;padding:0;flex:0 0 auto;}',
      '.psa-ci-coll li label{display:flex;gap:8px;align-items:center;cursor:pointer;font-weight:400;margin:0;}.psa-ci-coll em{font-style:normal;color:var(--psa-brand);font-size:11px;}.psa-ci-sec span{color:var(--psa-sub);}',
      '.psa-ci-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;}.psa-ci-chips span{background:var(--psa-soft);border-radius:999px;padding:2px 10px;font-size:12px;}',
      '.psa-ci .psa-good{color:#15803d;}.psa-ci .psa-bad{color:#b91c1c;}.psa-ci .psa-warnt{color:#b45309;}.psa-ci-chips .psa-good{background:#dcfce7;}.psa-ci-chips .psa-bad{background:#fee2e2;}',
      '.psa-ci-fp{margin:0 0 8px;padding-left:18px;color:#b91c1c;font-size:12.5px;}',
      '.psa-ci-rows{max-height:300px;overflow:auto;border:1px solid var(--psa-line);border-radius:10px;margin-bottom:8px;}',
      '.psa-ci-rows table{width:100%;border-collapse:collapse;font-size:12.5px;}.psa-ci-rows th{position:sticky;top:0;background:var(--psa-soft);text-align:left;font-weight:600;color:var(--psa-sub);padding:4px 6px;}',
      '.psa-ci-rows td{border-top:1px solid #f1f3f8;padding:4px 6px;vertical-align:top;}.psa-ci-rows td:first-child{width:auto;}',
      '.psa-ci-bar{height:8px;border-radius:4px;background:#eef0f6;overflow:hidden;margin:4px 0;}.psa-ci-bar i{display:block;height:100%;background:var(--psa-brand);border-radius:4px;transition:width .3s;}',
      '.psa-ci-secrets label{display:flex;flex-direction:column;gap:2px;font-size:12.5px;margin-bottom:4px;}.psa-ci-secrets input{border:1px solid var(--psa-line);border-radius:8px;padding:6px 8px;}',
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
      '.psa-ic.live{background:var(--psa-brand2);}.psa-ic.live:before{content:"\\21BB";}',
      /* composer */
      '#psa-form{flex:0 0 auto;display:flex!important;align-items:flex-end;gap:6px;margin:6px 12px 4px!important;padding:6px!important;height:auto!important;min-height:0!important;border:1px solid var(--psa-line);border-radius:16px;background:#fff;box-shadow:0 2px 10px rgba(15,23,42,.05);}',
      '#psa-form:focus-within{border-color:#a5b4fc;box-shadow:0 0 0 4px rgba(99,102,241,.12);}',
      '#psa-input{flex:1 1 auto!important;border:none!important;outline:none!important;resize:none!important;height:34px;min-height:34px!important;max-height:140px!important;padding:7px 4px!important;margin:0!important;font:14px/1.45 "Segoe UI",system-ui,sans-serif!important;background:transparent!important;color:var(--psa-ink)!important;box-shadow:none!important;}',
      '#psa-send{flex:0 0 auto;width:34px!important;height:34px!important;min-height:0!important;border-radius:11px!important;border:none!important;background:linear-gradient(135deg,var(--psa-brand),#6366f1)!important;color:#fff!important;display:inline-flex;align-items:center;justify-content:center;padding:0!important;margin:0!important;cursor:pointer;}',
      '#psa-send:disabled{opacity:.4;cursor:default;}',
      '#psa-recentpop{position:absolute;left:12px;right:12px;bottom:92px;max-height:50%;overflow-y:auto;background:#fff;border:1px solid var(--psa-line);border-radius:14px;box-shadow:0 18px 40px rgba(15,23,42,.18);padding:6px;}',
      '#psa-recentpop[hidden]{display:none;}',
      '#psa-bar{flex:0 0 auto;position:relative;display:flex;align-items:center;gap:8px;padding:0 12px 8px;}',
      '#psa-hint{flex:1 1 auto;min-width:0;font-size:11px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
      '#psa-feat,#psa-dogs{flex:0 0 auto;border:1px solid var(--psa-line);background:#fff;color:#334155;border-radius:999px;padding:3px 10px;cursor:pointer;font:600 12px "Segoe UI",system-ui,sans-serif;}',
      '#psa-feat:hover,#psa-dogs:hover{border-color:#a5b4fc;background:#eef2ff;}',
      '#psa-dogs{color:#0f766e;border-color:#99f6e4;background:#f0fdfa;}#psa-dogs.psa-off{color:#92400e;border-color:#fcd34d;background:#fffbeb;}#psa-dogs.psa-alert{color:#b91c1c;border-color:#fca5a5;background:#fef2f2;}',
      '#psa-featmenu{position:absolute;left:12px;bottom:34px;z-index:5;min-width:250px;background:#fff;border:1px solid var(--psa-line);border-radius:12px;box-shadow:0 10px 30px rgba(15,23,42,.18);padding:6px;}',
      '#psa-featmenu button{display:flex;align-items:center;gap:10px;width:100%;text-align:left;border:0;background:none;border-radius:8px;padding:7px 8px;cursor:pointer;font:13px "Segoe UI",system-ui,sans-serif;color:#0f172a;}',
      '#psa-featmenu button:hover,#psa-featmenu button:focus{background:#f1f5f9;outline:none;}#psa-featmenu small{display:block;color:#64748b;font-size:11.5px;}',
      '#psa-featmenu .psa-tic{flex:0 0 28px;height:28px;border-radius:8px;display:flex;align-items:center;justify-content:center;}',
      '.psa-guide ol{margin:6px 0;padding-left:20px;}.psa-guide li{margin:3px 0;}',
      '.psa-plan{border:1px solid var(--psa-line);border-radius:10px;margin:8px 0;overflow:hidden;}',
      '.psa-plan-h{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:7px 10px;background:#f8fafc;border-bottom:1px solid var(--psa-line);}',
      '.psa-plan-h b{flex:1 1 auto;}.psa-plan-h small{color:#64748b;}',
      '.psa-mini{border:1px solid var(--psa-line);background:#fff;border-radius:7px;padding:2px 8px;cursor:pointer;font:12px "Segoe UI",system-ui,sans-serif;color:#334155;}',
      '.psa-mini:hover{background:#f1f5f9;}.psa-mini.psa-danger{color:#b91c1c;border-color:#fecaca;}.psa-mini:disabled{opacity:.5;cursor:default;}',
      '.psa-dog{display:flex;gap:8px;align-items:flex-start;padding:7px 10px;border-top:1px solid #f1f5f9;font-size:12.5px;}.psa-dog:first-of-type{border-top:0;}',
      '.psa-dog .psa-dot{flex:0 0 8px;height:8px;border-radius:50%;margin-top:5px;background:#10b981;}.psa-dog.psa-paused .psa-dot{background:#cbd5e1;}.psa-dog.psa-failing .psa-dot{background:#ef4444;}',
      '.psa-dog-b{flex:1 1 auto;min-width:0;}.psa-dog-b small{display:block;color:#64748b;}.psa-dog-a{flex:0 0 auto;display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end;}',
      '.psa-dog a{color:#b91c1c;cursor:pointer;}',
      '.psa-card .psa-mon-t td:first-child{width:auto;}.psa-card .psa-wp td:first-child{width:18px;}.psa-wp td:nth-child(2){min-width:150px;}.psa-wp small{display:block;color:#64748b;}',
      '.psa-rt{list-style:none;margin:6px 0;padding:0;font-size:13px;}.psa-rt li{padding:4px 0;border-top:1px solid #f1f5f9;}.psa-rt li:first-child{border-top:0;}',
      '.psa-rt-i{display:inline-block;width:18px;font-weight:700;color:#94a3b8;}.psa-rt-on{background:#f8fafc;}.psa-rt-d{margin-left:18px;}.psa-rt-now{display:block;color:#64748b;}',
      '.psa-chg{margin:6px 0;padding:7px 10px;border-radius:10px;background:#eff6ff;color:#1e3a8a;font-size:12.5px;}.psa-chg ul{margin:2px 0 0;padding-left:18px;}',
      /* documents: attached file, drop target, preview, review table */
      '#psa-files{flex:0 0 auto;display:flex;flex-wrap:wrap;gap:6px;margin:4px 14px 0;}#psa-files[hidden]{display:none;}',
      '.psa-file{display:inline-flex;align-items:center;gap:6px;max-width:100%;padding:4px 6px 4px 10px;border:1px solid #c7d2fe;background:#eef2ff;border-radius:999px;font-size:12px;color:#3730a3;}',
      '.psa-file span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      '.psa-file button{border:none!important;background:transparent!important;color:#6366f1!important;cursor:pointer;padding:0 4px!important;margin:0!important;font-size:14px;line-height:1;}',
      '#psa-panel.psa-drop{box-shadow:0 0 0 4px #818cf8,0 24px 64px rgba(15,23,42,.24);}',
      '.psa-doc{display:flex;align-items:center;gap:10px;margin:2px 0 10px;font-size:12px;}',
      '.psa-doc img{max-height:72px;max-width:120px;border:1px solid var(--psa-line);border-radius:8px;}',
      '.psa-doc a{color:var(--psa-brand);font-weight:600;text-decoration:none;}',
      '.psa-card tr.psa-unsure-row td{background:#fffbeb;}.psa-card .psa-note{display:block;color:#b45309;font-size:11px;font-weight:400;}',
      '.psa-tablewrap{overflow-x:auto;margin:6px 0 4px;}',
      '.psa-card table.psa-batch{border-collapse:separate;border-spacing:3px;}',
      '.psa-batch th{font-size:11px;font-weight:600;text-align:left;color:var(--psa-sub);padding:2px 4px;white-space:nowrap;}',
      '.psa-card .psa-batch td,.psa-card .psa-batch td:first-child{padding:0;border:none;width:auto;}',
      '.psa-card .psa-batch input{min-width:96px;padding:5px 7px!important;font-size:12px!important;}',
      '.psa-card .psa-batch input[type="checkbox"]{min-width:0;width:16px;height:16px;}',
      '.psa-card input.psa-unsure{background:#fffbeb!important;border-color:#f59e0b!important;}',
      '.psa-card input.psa-miss{background:#fef2f2!important;border-color:#f87171!important;}',
      '.psa-legend{font-size:11px;color:var(--psa-sub);}.psa-legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin:0 4px 0 8px;vertical-align:-1px;}',
      /* troubleshooting diagnosis */
      '.psa-diag-top{display:flex;align-items:center;gap:10px;margin:2px 0 8px;}',
      '.psa-badge{display:inline-block;padding:3px 10px;border-radius:999px;font-weight:700;font-size:12px;}',
      '.psa-diag-sum{margin:0 0 10px;font-size:14px;line-height:1.5;}',
      '.psa-diag-sec{margin:8px 0;font-size:13px;}.psa-diag-sec>b{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--psa-sub);margin-bottom:3px;}',
      '.psa-diag-sec ol,.psa-card details ul{margin:2px 0;padding-left:20px;}.psa-diag-sec li,.psa-card details li{margin:2px 0;}',
      '.psa-card pre.psa-code{margin:4px 0;padding:10px 12px;max-height:260px;overflow:auto;background:#0f172a;color:#e2e8f0;border-radius:10px;font:12px/1.5 Consolas,"Courier New",monospace;white-space:pre-wrap;word-break:break-word;}',
      '.psa-card code.psa-where{display:inline-block;padding:2px 8px;background:#eef2ff;color:#3730a3;border-radius:6px;font:12px Consolas,monospace;}',
      '.psa-q{margin:6px 0;font-size:12px;}',
      '.psa-chk{display:flex;gap:8px;align-items:flex-start;margin:5px 0;font-size:13px;line-height:1.4;}',
      '.psa-chk>i{font-style:normal;flex:none;width:18px;height:18px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;margin-top:1px;}',
      '.psa-chk.ok>i{background:#16a34a;}.psa-chk.missing>i{background:#dc2626;}.psa-chk.warning>i{background:#d97706;}.psa-chk.info>i{background:#64748b;}',
      '.psa-chk b{font-weight:600;}.psa-fix{border:1px solid #c7d2fe;background:#eef2ff;border-radius:10px;padding:8px 10px;}',
      '.psa-links{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;}.psa-links a{font-size:12px;padding:3px 9px;border-radius:999px;border:1px solid var(--psa-line);color:#3730a3!important;text-decoration:none!important;background:#fff;}',
      '.psa-links a:hover{border-color:#a5b4fc;}',
      /* absence approvals: the list and the review's confidence score */
      '.psa-apr-list{display:flex;flex-direction:column;gap:6px;margin-top:4px;}',
      '.psa-apr{display:flex;align-items:center;gap:10px;justify-content:space-between;padding:8px 10px;border:1px solid var(--psa-line);border-radius:10px;font-size:13px;line-height:1.45;}',
      '.psa-card .psa-apr small{display:inline;margin:0;}.psa-card .psa-apr .psa-apr-who{display:block;font-size:11px;margin-top:2px;}',
      '.psa-card .psa-apr button{flex:none;white-space:nowrap;padding:7px 12px!important;margin:0!important;border-radius:9px!important;cursor:pointer;font:600 12px "Segoe UI",system-ui,sans-serif!important;}',
      '.psa-apr-status{margin:0 0 10px;padding:8px 10px;background:#f8fafc;border:1px solid var(--psa-line);border-radius:10px;font-size:12.5px;line-height:1.5;}',
      '.psa-apr-status>b:first-child{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--psa-sub);margin-right:6px;}',
      '.psa-conf{flex:1;min-width:140px;}.psa-conf-h{display:flex;justify-content:space-between;font-size:12px;color:var(--psa-sub);}',
      '.psa-conf-bar{height:8px;border-radius:999px;background:#e2e8f0;overflow:hidden;margin-top:3px;}.psa-conf-bar i{display:block;height:100%;border-radius:999px;}',
      '.psa-card a.psa-btn{display:inline-block;padding:7px 14px;border-radius:9px;background:#4f46e5;color:#fff!important;text-decoration:none!important;font-weight:600;font-size:13px;}',
      /* runbook draft: the steps the agent will run, by type */
      '.psa-rb-steps{list-style:none;margin:6px 0;padding:0;display:flex;flex-direction:column;gap:6px;}',
      '.psa-rb-steps>li{border:1px solid var(--psa-line);border-radius:10px;padding:8px 10px;font-size:13px;line-height:1.45;}',
      '.psa-rb-h{display:flex;align-items:center;gap:8px;}.psa-rb-h b{flex:1;min-width:0;}',
      '.psa-rb-n{flex:none;width:20px;height:20px;border-radius:50%;background:#eef2ff;color:#3730a3;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;}',
      '.psa-rb-t{flex:none;font-size:10.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;border-radius:999px;padding:2px 8px;}',
      '.psa-rb-d{margin:4px 0 0 28px;color:#334155;font-size:12.5px;}.psa-card .psa-rb-d small{display:inline;margin:0;}',
      '.psa-rb-test{display:inline-block;margin-top:3px;padding:1px 8px;border-radius:999px;font-size:11.5px;font-weight:600;}',
      '.psa-card .psa-rb-d details{margin:3px 0 0;}',
      '.psa-rb-warn{margin:8px 0;padding:8px 10px;border-radius:10px;font-size:12.5px;}.psa-rb-warn ul{margin:2px 0 0;padding-left:18px;}',
      /* monitors and the watcher's alerts */
      '.psa-mon-facts{border-collapse:collapse;margin:6px 0;font-size:12.5px;}.psa-mon-facts td{padding:2px 10px 2px 0;vertical-align:top;}.psa-mon-facts td:first-child{color:#64748b;white-space:nowrap;}',
      '.psa-mon-t{border-collapse:collapse;margin:6px 0;font-size:12px;width:100%;}.psa-mon-t th{text-align:left;color:#64748b;font-weight:600;border-bottom:1px solid #e2e8f0;padding:3px 6px;}.psa-mon-t td{border-bottom:1px solid #f1f5f9;padding:3px 6px;}',
      '.psa-inc-list{list-style:none;margin:6px 0 0;padding:0;}.psa-inc-list li{margin:0 0 6px;}',
      '.psa-inc-list button{display:block;width:100%;text-align:left;border:1px solid #e2e8f0;background:#fff;border-radius:10px;padding:7px 10px;cursor:pointer;font:13px "Segoe UI",system-ui,sans-serif;color:#0f172a;}',
      '.psa-inc-list button:hover{border-color:#5eead4;background:#f0fdfa;}.psa-inc-list small{display:block;color:#64748b;}.psa-inc-list span{display:block;font-size:12px;color:#334155;margin-top:2px;}',
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
      '#psa-audit{position:fixed;top:12px;left:12px;right:12px;bottom:12px;display:none;flex-direction:column;background:#fff;border-radius:18px;overflow:hidden;z-index:2147483643;box-shadow:0 24px 64px rgba(15,23,42,.24),0 0 0 1px rgba(15,23,42,.08);color:var(--psa-ink);font-size:13px;line-height:1.45;text-align:left;}',
      'body.psa-docked #psa-audit{right:464px;}body.psa-docked.psa-widebody #psa-audit{right:744px;}',
      '.psa-au-hdr{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;padding:12px 16px;border-bottom:1px solid var(--psa-line);background:var(--psa-soft);}',
      '.psa-au-title b{font-size:16px;display:block;}.psa-au-title span{color:var(--psa-sub);font-size:12px;}',
      '.psa-au-tabs{display:flex;gap:2px;flex-wrap:wrap;}.psa-au-tabs button,.psa-au-seg button{border:0;background:none;padding:6px 10px;border-radius:8px;cursor:pointer;font:600 13px "Segoe UI",system-ui,sans-serif;color:var(--psa-sub);}',
      '.psa-au-tabs button.psa-on,.psa-au-seg button.psa-on{background:#fff;color:var(--psa-brand);box-shadow:0 1px 2px rgba(15,23,42,.12);}',
      '.psa-au-tools{margin-left:auto;display:flex;align-items:center;gap:6px;}.psa-au-tools select,.psa-au-filters select,.psa-au-filters input{font:13px "Segoe UI",system-ui,sans-serif;border:1px solid var(--psa-line);border-radius:7px;padding:4px 6px;background:#fff;color:var(--psa-ink);}',
      '.psa-au-tools button,.psa-au-filters button,.psa-au-xl,.psa-au-more{border:1px solid var(--psa-line);background:#fff;border-radius:8px;padding:5px 10px;cursor:pointer;font:13px "Segoe UI",system-ui,sans-serif;color:var(--psa-ink);}',
      '.psa-au-tools button:hover,.psa-au-filters button:hover,.psa-au-xl:hover,.psa-au-more:hover{border-color:var(--psa-brand);color:var(--psa-brand);}',
      '.psa-au-body{flex:1;overflow:auto;padding:14px 16px 24px;}.psa-au-wait,.psa-au-err{color:var(--psa-sub);}',
      '.psa-au-period{color:var(--psa-sub);margin:0 0 10px;font-size:12.5px;}',
      '.psa-au-kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:12px;}',
      '.psa-au-kpi{border:1px solid var(--psa-line);border-radius:12px;padding:10px 12px;}.psa-au-kpi span{display:block;color:var(--psa-sub);font-size:12px;}.psa-au-kpi b{display:block;font-size:22px;font-weight:600;margin:2px 0;}.psa-au-kpi small{color:var(--psa-sub);font-size:11.5px;}',
      '.psa-au-kbad b{color:var(--psa-err);}',
      '.psa-au-meter{border:1px solid var(--psa-line);border-radius:12px;padding:10px 12px;margin-bottom:12px;}.psa-au-meter>span{display:block;height:8px;border-radius:4px;background:#eef0f6;margin-top:6px;overflow:hidden;}.psa-au-meter i{display:block;height:100%;border-radius:4px;background:var(--psa-brand);}',
      '.psa-au-mwarn i{background:var(--psa-warn);}.psa-au-mbad i{background:var(--psa-err);}',
      '.psa-au-sec{margin:6px 0 14px;}.psa-au-sec h5{margin:0 0 6px;font-size:13px;}.psa-au-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;}',
      '.psa-au-chart{width:100%;height:auto;max-height:200px;display:block;}.psa-au-bar{fill:var(--psa-brand);}.psa-au-hit{fill:transparent;cursor:default;}.psa-au-hit:hover{fill:rgba(79,70,229,.07);}',
      '.psa-au-grid{stroke:#eef0f6;stroke-width:1;}.psa-au-base{stroke:#cbd5e1;stroke-width:1;}.psa-au-ax{fill:#64748b;font-size:10px;font-family:"Segoe UI",system-ui,sans-serif;}',
      '.psa-au-tip{position:absolute;z-index:3;background:#0f172a;color:#fff;font-size:12px;padding:4px 8px;border-radius:6px;pointer-events:none;white-space:nowrap;}',
      '.psa-au-hrow{display:grid;grid-template-columns:minmax(90px,140px) 1fr auto;align-items:center;gap:8px;padding:3px 0;}.psa-au-hrow[data-user]{cursor:pointer;}.psa-au-hrow[data-user]:hover .psa-au-hl{color:var(--psa-brand);}',
      '.psa-au-hl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}.psa-au-hv{font-variant-numeric:tabular-nums;color:var(--psa-ink);}',
      '.psa-au-ht{display:inline-block;height:8px;background:transparent;vertical-align:middle;}.psa-au-hrow .psa-au-ht{display:block;}.psa-au-ht i{display:block;height:8px;border-radius:0 4px 4px 0;background:var(--psa-brand);}',
      '.psa-au-wide .psa-au-ht{width:90px;}.psa-au-wide{white-space:nowrap;}',
      '.psa-au-bar2{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px;}.psa-au-bar2 .psa-au-period{margin:0;}.psa-au-seg{display:inline-flex;background:var(--psa-soft);border-radius:9px;padding:2px;}.psa-au-bar2 .psa-au-xl{margin-left:auto;}',
      '.psa-au-table{width:100%;border-collapse:collapse;font-size:12.5px;}.psa-au-table th{text-align:left;font-weight:600;color:var(--psa-sub);border-bottom:1px solid var(--psa-line);padding:5px 8px;white-space:nowrap;}',
      '.psa-au-table td{border-bottom:1px solid #f1f3f8;padding:5px 8px;vertical-align:top;}.psa-au-table tr[data-ev],.psa-au-table tr[data-user],.psa-au-table tr[data-feature]{cursor:pointer;}.psa-au-table tbody tr:hover{background:#fafbff;}',
      '.psa-au-detail td{background:var(--psa-soft);}.psa-au-detail pre{white-space:pre-wrap;word-break:break-word;margin:0 0 6px;font:12px Consolas,monospace;background:#fff;border:1px solid var(--psa-line);border-radius:6px;padding:6px 8px;}',
      '.psa-au-nowrap{white-space:nowrap;font-variant-numeric:tabular-nums;}.psa-au-muted{color:var(--psa-sub);}.psa-au-ok{color:#15803d;white-space:nowrap;}.psa-au-bad{color:#b91c1c;white-space:nowrap;}.psa-au-warn{color:#b45309;white-space:nowrap;}',
      '.psa-au-filters{display:flex;flex-wrap:wrap;align-items:flex-end;gap:8px;margin-bottom:10px;}.psa-au-filters label{display:flex;flex-direction:column;font-size:11.5px;color:var(--psa-sub);gap:2px;}.psa-au-q input{width:180px;}.psa-au-filters .psa-au-xl{margin-left:auto;}',
      '.psa-au-more{margin:10px 0;}.psa-au-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;}.psa-au-chips span{background:#fef2f2;color:#991b1b;border-radius:999px;padding:3px 10px;font-size:12px;}',
      '.psa-au-list{margin:0;padding-left:18px;}.psa-au-list li{margin-bottom:6px;}',
      '@media (max-width:760px){#psa-panel,#psa-panel.psa-wide{width:auto;left:12px;}body.psa-docked #psa-ws{display:none!important;}#psa-audit,body.psa-docked #psa-audit{left:6px;right:6px;top:6px;bottom:6px;z-index:2147483646;}}'
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
      clip: SVG('<path d="m21.4 11-9.2 9.2a6 6 0 0 1-8.5-8.5l8.6-8.6a4 4 0 1 1 5.7 5.7l-8.6 8.6a2 2 0 1 1-2.8-2.8l8.5-8.5"/>'),
      send: SVG('<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>'),
      page: SVG('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>', 15),
      rec: '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#dc2626"></span>'
    };
    var AV = '<span class="psa-av" aria-hidden="true"><i></i><i></i></span>';

    var fab = document.createElement('button');
    fab.id = 'psa-fab';
    fab.type = 'button';
    fab.setAttribute('aria-label', 'Open ' + NAME + ', ' + NAME_LONG);
    fab.setAttribute('aria-expanded', 'false');
    fab.innerHTML = AV + '<span>Ask ' + NAME + '</span>';
    document.body.appendChild(fab);

    var panel = document.createElement('div');
    panel.id = 'psa-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', NAME + ', ' + NAME_LONG);
    panel.innerHTML =
      '<div id="psa-hdr">' + AV + '<div class="psa-who"><div class="psa-name" title="' + NAME_LONG + '">' + NAME + ' <small>' + NAME_LONG + '</small></div><div id="psa-status" aria-live="polite">Ready</div></div>'
      + '<button id="psa-rec" class="psa-ib" type="button" title="Teach me a new task by doing it once" style="display:none">' + ICON.rec + 'Record</button>'
      + '<button id="psa-new" class="psa-ib" type="button" title="New conversation" aria-label="New conversation">' + ICON.compose + '</button>'
      + '<button id="psa-wide" class="psa-ib" type="button" title="Wider panel" aria-label="Wider panel">' + ICON.wide + '</button>'
      + '<button id="psa-close" class="psa-ib" type="button" title="Close (Esc)" aria-label="Close">' + ICON.close + '</button></div>'
      + '<div id="psa-ctx" hidden></div>'
      + '<div id="psa-log" role="log" aria-live="polite"></div>'
      + '<div id="psa-recentpop" role="listbox" aria-label="Recent questions" hidden></div>'
      + '<div id="psa-files" hidden></div>'
      + '<form id="psa-form"><button id="psa-hist" class="psa-ib" type="button" title="Recent questions" aria-label="Recent questions">' + ICON.clock + '</button>'
      + '<button id="psa-attach" class="psa-ib" type="button" title="Attach a PDF, image or Excel checklist (or drop it here)" aria-label="Attach a document">' + ICON.clip + '</button>'
      + '<input id="psa-file" type="file" accept="application/pdf,image/png,image/jpeg,image/gif,image/webp,.xlsx,.csv" hidden>'
      + '<textarea id="psa-input" rows="1" aria-label="Message" placeholder="Ask anything, or tell me what to do..."></textarea>'
      + '<button id="psa-send" type="submit" aria-label="Send">' + ICON.send + '</button></form>'
      + '<div id="psa-bar"><button id="psa-feat" type="button" aria-haspopup="menu" aria-expanded="false">＋ Features <span aria-hidden="true">▾</span></button>'
      + '<div id="psa-featmenu" role="menu" hidden></div>'
      + '<span id="psa-hint">Enter to send · Shift+Enter new line · Ctrl+/</span>'
      + '<button id="psa-dogs" type="button" hidden title="Watchdogs: open the list to pause, run, delete or download them"></button></div>';
    document.body.appendChild(panel);
    var logEl = panel.querySelector('#psa-log');
    var input = panel.querySelector('#psa-input');
    var state = loadState();
    var busy = false;
    var ready = false;   /* config loaded from the iScript */
    var me = null, canTeach = false;   /* from /api/me: who is signed in, tasks for the welcome screen */
    var liveWaiter = null;             /* a live run waiting for the user's answer in the chat */
    var attached = null, lastDoc = null; /* file to send with the next message; the one the cards refer to */
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

    /* What's new: shown once, until dismissed or the first message. Change NEWS.id for the next announcement. */
    var NEWS = { id: '2026-10-27', items: [
      'Meet **' + NAME + '**: the PeopleSoft Agent has a name and a new look.',
      'Processes of yours that failed in the last 24 hours show up here: one click to find out why.',
      'Cards that fit the page you are on come first.'
    ] };
    function newsSeen(set) {
      try { if (set) localStorage.setItem('psagent.news', NEWS.id); return localStorage.getItem('psagent.news') === NEWS.id; } catch (e) { return true; }
    }

    /* Welcome cards: what kind of help, with its icon and colours */
    var TILE = {
      error: [SVG('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>', 16), '#b91c1c', '#fee2e2'],
      task: [SVG('<path d="M7 4v16l13-8Z"/>', 16), '#4f46e5', '#eef2ff'],
      trouble: [SVG('<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4Z"/>', 16), '#b45309', '#fef3c7'],
      security: [SVG('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>', 16), '#be123c', '#ffe4e6'],
      explain: [SVG('<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>', 16), '#0369a1', '#e0f2fe'],
      approve: [SVG('<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/><path d="m9 15 2 2 4-4"/>', 16), '#047857', '#d1fae5'],
      runbook: [SVG('<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3 6 1 1 2-2M3 12l1 1 2-2M3 18l1 1 2-2"/>', 16), '#6b21a8', '#f3e8ff'],
      monitor: [SVG('<path d="M3 12h4l3-8 4 16 3-8h4"/>', 16), '#0f766e', '#ccfbf1'],
      test: [SVG('<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3"/><path d="M7.5 15h9"/>', 16), '#9d174d', '#fce7f3'],
      'new': [SVG('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.5 2.5M15.2 15.2l2.5 2.5M6.3 17.7l2.5-2.5M15.2 8.8l2.5-2.5"/>', 16), '#0e7490', '#cffafe'],
      audit: [SVG('<path d="M4 20V11M10 20V5M16 20v-6M21 20H3"/>', 16), '#1d4ed8', '#dbeafe'],
      ci: [SVG('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 4v16"/>', 16), '#15803d', '#dcfce7'],
      data: [SVG('<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6"/>', 16), '#6d28d9', '#ede9fe']
    };

    function greet() {
      var w = document.createElement('div');
      w.className = 'psa-welcome';
      /* the user profile description, when it looks like a name (some are dates or codes) */
      var full = String(cfg.fullname || ''), ctx = pageContext(), m = me || {};
      var name = /[A-Za-z]{2}/.test(full) && !/\d{3}/.test(full) ? full.split(/\s+/)[0] : (cfg.oprid || '');
      var hour = new Date().getHours(), hello = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
      var html = '<h2>' + hello + (name ? ', ' + esc(name) : '') + '</h2>'
        + '<p>I\'m ' + NAME + ', your ' + NAME_LONG + '. Tell me in your own words what you need: I work in PeopleSoft for you, '
        + 'ask when I am not sure, and check with you before anything is saved.</p>';
      if (!newsSeen()) {
        html += '<div class="psa-news"><button type="button" class="psa-news-x" aria-label="Dismiss what\'s new">&times;</button><b>What\'s new</b><ul>'
          + NEWS.items.map(function (x) { return '<li>' + md(x) + '</li>'; }).join('') + '</ul></div>';
      }
      var failed = m.failed || [];
      if (failed.length) {
        html += '<div class="psa-alert" role="status">' + TILE.error[0] + '<div><b>' + (failed.length === 1 ? 'Your process ' + esc(failed[0].name) + ' failed'
          : failed.length + ' of your processes failed') + ' in the last 24 hours.</b> Pick one and I will find out why.<div class="psa-alert-list">'
          + failed.slice(0, 3).map(function (f) {
            return '<button type="button" data-q="Why did process ' + esc(f.instance) + ' fail?">' + esc(f.name) + ' · ' + esc(f.instance)
              + ' · ' + esc(f.status) + '</button>';
          }).join('') + '</div></div></div>';
      }
      var night = m.overnight;
      if (night && night.count) {
        html += '<div class="psa-alert" role="status">' + TILE.monitor[0] + '<div><b>' + (night.count === 1 ? '1 alert' : night.count + ' alerts')
          + ' from the watcher in the last 24 hours.</b> Each one is already analysed.<div class="psa-alert-list">'
          + night.items.map(function (i) {
            return '<button type="button" data-inc="' + esc(i.id) + '">' + esc(i.title) + ' · ' + esc(String(i.found).slice(5, 16)) + '</button>';
          }).join('') + (night.count > night.items.length ? '<button type="button" data-q="What happened in the last 24 hours?">All ' + night.count + '</button>' : '')
          + '</div></div></div>';
      }
      /* Cards; the ones for the page you are on come first */
      var here = String(ctx.component || '').toUpperCase(), menu = String(ctx.menu || '').toUpperCase(), tiles = [];
      ((m.tasks) || []).forEach(function (t) {
        tiles.push({ k: 'task', t: t.title, s: t.example, q: t.example, fit: !!here && String(t.component || '').toUpperCase() === here });
      });
      if (m.troubleshoot) tiles.push({ k: 'trouble', t: 'Troubleshoot a process', s: 'Why did my last process fail?', q: 'Why did my last process fail?', fit: menu === 'PROCESSMONITOR' });
      if (m.security) tiles.push({ k: 'security', t: 'Check someone\'s access', s: 'Why can\'t a user open a page?', q: '', fill: 'Why can\'t ', fit: menu === 'MAINTAIN_SECURITY' });
      else if (m.own_access) tiles.push({ k: 'security', t: 'Check my access', s: 'Why can\'t I open a page?', q: '', fill: 'Why can\'t I open ' });
      if (m.approvals) tiles.push({ k: 'approve', t: 'Review absence approvals', s: 'I check each request and recommend', q: 'Show my pending absence approvals', fit: /EOAW|ABS/.test(here) });
      if (m.can_monitor && m.watcher) {
        tiles.push({ k: 'monitor', t: 'Watch something for me', s: 'Failed or stuck processes, data checks: I alert by email', q: '', fill: 'Watch ', fit: menu === 'PROCESSMONITOR' });
        tiles.push({ k: 'monitor', t: 'What happened overnight?', s: (m.overnight ? m.overnight.monitors : 0) + ' monitors running', q: 'What happened overnight?' });
      }
      if (m.ask_data) tiles.push({ k: 'data', t: 'Ask the data', s: 'How many, who, which: from your PS Queries', q: '', fill: 'How many ' });
      if (m.can_audit) tiles.push({ k: 'audit', t: 'Audit dashboard', s: 'Who used the agent, what it cost, what failed', q: '', act: 'audit' });
      (m.runbooks || []).forEach(function (r) {
        tiles.push({ k: 'runbook', t: 'Run: ' + r.title, s: r.steps + ' steps' + (r.summary ? ' · ' + r.summary : ''), q: 'Run the runbook ' + r.title });
      });
      /* the page's own cards first, then runbooks (what the person published to run), the watcher's cards and the
         other features the user has; recorded tasks (there can be dozens) fill the rest, at least two */
      var rank = function (x) { return x.fit ? 0 : x.k === 'runbook' ? 1 : x.k === 'monitor' ? 2 : x.k === 'task' ? 4 : 3; };
      tiles = tiles.map(function (x, i) { x.i = i; return x; }).sort(function (a, b) { return rank(a) - rank(b) || a.i - b.i; });
      var runbooks = 0;
      tiles = tiles.filter(function (x) { return x.fit || x.k !== 'runbook' || ++runbooks <= 3; });
      var room = Math.max(8 - tiles.filter(function (x) { return x.fit || x.k !== 'task'; }).length, 2), tasks = 0;
      tiles = tiles.filter(function (x) { return x.fit || x.k !== 'task' || ++tasks <= room; });
      if (ctx.error) tiles.unshift({ k: 'error', t: 'Help with this error', s: ctx.error, q: 'Help me with this error' });
      if (ctx.component && !isFrameworkPage(ctx)) tiles.push({ k: 'explain', t: 'Explain this page', s: ctx.title || ctx.component, q: 'What is this page for and how do I use it?' });
      tiles.push({ k: 'new', t: 'Something new', s: 'Describe it and I will work it out on screen', q: '', cls: ' psa-new' });
      html += '<div class="psa-sec">I can</div><div class="psa-grid">' + tiles.map(function (x) {
        var ic = TILE[x.k];
        return '<button type="button" class="psa-tile' + (x.cls || '') + '" data-q="' + esc(x.q) + '"' + (x.fill ? ' data-fill="' + esc(x.fill) + '"' : '')
          + (x.act ? ' data-act="' + x.act + '"' : '')
          + '><span class="psa-tic" style="color:' + ic[1] + ';background:' + ic[2] + '">' + ic[0] + '</span><span class="psa-tt"><b>' + esc(x.t)
          + (x.fit ? ' <em class="psa-fit">This page</em>' : '') + '</b><small>' + esc(x.s || '') + '</small></span></button>';
      }).join('') + '</div>';
      w.innerHTML = html;
      w.addEventListener('click', function (e) {
        if (e.target.closest && e.target.closest('.psa-news-x')) { newsSeen(true); w.querySelector('.psa-news').remove(); return; }
        var inc = e.target.closest && e.target.closest('[data-inc]');
        if (inc) { openIncident(inc.getAttribute('data-inc')); return; }
        var b = e.target.closest && e.target.closest('[data-q]');
        if (!b) return;
        if (b.getAttribute('data-act') === 'audit') { openAudit(); return; }
        var q = b.getAttribute('data-q');
        if (q) send(q);
        else if (b.getAttribute('data-fill')) {
          input.value = b.getAttribute('data-fill');
          hint(/^Watch/.test(input.value) ? 'e.g. Watch payroll processes overnight and email payroll@company.com'
            : /^How many/.test(input.value) ? 'e.g. How many new hires this year by department?'
            : / I open $/.test(input.value) ? 'e.g. Why can\'t I open Create Job Opening?'
            : 'e.g. Why can\'t ABSMITH access Create Job Opening?');
          input.focus();
        } else { hint('e.g. Lock the account of user JSMITH and add the role Manager'); input.focus(); }
      });
      logEl.appendChild(w);
    }

    /* The message box shows a different example every few seconds while the welcome screen is up */
    var PLACEHOLDER = 'Ask anything, or tell me what to do...', phHold = false, phTimer = null, phIndex = 1;
    function hint(text) { input.placeholder = text; phHold = true; }   /* a specific hint stays until the next message */
    function examples() {
      var m = me || {}, out = [];
      (m.tasks || []).forEach(function (t) { (t.examples && t.examples.length ? t.examples : [t.example]).forEach(function (x) { if (x) out.push(x); }); });
      if (m.troubleshoot) out.push('Why did my last process fail?');
      if (m.security) out.push('Why can\'t ABSMITH open Job Data?');
      else if (m.own_access) out.push('Why can\'t I open Job Data?');
      if (m.approvals) out.push('Show my pending absence approvals');
      if (m.ask_data) out.push('How many new hires this year by department?');
      (m.runbooks || []).slice(0, 2).forEach(function (r) { out.push('Run ' + r.title); });
      out.push('What is this page for?');
      return out;
    }
    function rotatePlaceholder() {
      if (phHold || input.value || !logEl.querySelector('.psa-welcome')) return;
      var list = examples();
      input.placeholder = phIndex % (list.length + 1) === 0 ? PLACEHOLDER : 'Try: "' + list[(phIndex % (list.length + 1)) - 1] + '"';
      phIndex++;
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

    /* ---- Documents: read in the browser, sent with the next message, kept only in memory ---- */
    var XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    var DOC_LIMIT = { 'application/pdf': 10, 'image/png': 5, 'image/jpeg': 5, 'image/gif': 5, 'image/webp': 5, 'text/csv': 5 };   /* MB */
    DOC_LIMIT[XLSX] = 5;
    var uploadAs = null;   /* message to send with the next file picked from a guide card */
    var reading = null;    /* a file still being read (a Promise): a message sent meanwhile waits for it */
    function takeFile(file, autoSend) {
      if (!file) return;
      /* Windows reports CSV files as application/vnd.ms-excel (or nothing): go by the extension for spreadsheets */
      var type = /\.xlsx$/i.test(file.name) ? XLSX : /\.csv$/i.test(file.name) ? 'text/csv' : file.type, mb = DOC_LIMIT[type];
      if (!mb) { addMsg('bot', 'I can read **PDF**, **image** (PNG, JPEG, GIF, WebP) and **Excel** (.xlsx, .csv) files. "' + file.name + '" is not one of those.', false); return; }
      if (file.size > mb * 1024 * 1024) { addMsg('bot', '"' + file.name + '" is too large: up to ' + mb + ' MB for this type.', false); return; }
      var reader = new FileReader();
      reading = new Promise(function (done) {
        reader.onload = function () {
          reading = null;
          done();
          attached = { name: file.name, media_type: type, size: file.size, data: String(reader.result).split(',')[1],
            url: URL.createObjectURL(file) };
          showChip();
          if (autoSend) return send(autoSend);
          hint('What should I do with it? (or just send)');
          input.focus();
        };
        reader.onerror = function () { reading = null; done(); addMsg('bot', 'I could not read "' + file.name + '".', false); };
      });
      reader.readAsDataURL(file);
    }
    function showChip() {
      var box = panel.querySelector('#psa-files');
      box.hidden = !attached;
      box.innerHTML = attached ? '<span class="psa-file">' + ICON.page + '<span>' + esc(attached.name) + ' · '
        + Math.max(1, Math.round(attached.size / 1024)) + ' KB</span><button type="button" aria-label="Remove attachment">&times;</button></span>' : '';
      var x = box.querySelector('button');
      if (x) x.addEventListener('click', function () { attached = null; showChip(); });
    }
    function docPreview() {
      if (!lastDoc) return '';
      return '<div class="psa-doc">' + (/^image\//.test(lastDoc.media_type) ? '<img src="' + lastDoc.url + '" alt="">' : ICON.page)
        + '<span>From <a href="' + lastDoc.url + '" target="_blank" rel="noopener">' + esc(lastDoc.name) + '</a> (opens the document)</span></div>';
    }

    function handleReply(r) {
      if (r.session_id) { state.sessionId = r.session_id; saveState(); }
      if (r.query_result) showQueryResult(r.query_result, r.query_chart);
      (r.messages || []).forEach(function (m) { addMsg('bot', m); });
      if (r.query_chart && !r.query_result) card('<h4>' + esc(r.query_chart.query) + '</h4>' + queryChart(r.query_chart)).classList.add('psa-qr');
      if (r.query_rows) showQueryRows(r.query_rows);
      if (r.confirm) showConfirm(r.confirm);
      if (r.record) showRecordCard(r.record);
      if (r.batch) showBatch(r.batch);
      if (r.approvals) showApprovals(r.approvals);
      if (r.runbook) showRunbook(r.runbook);
      if (r.runbook_run) showRunbookStart(r.runbook_run);
      if (r.monitor) showMonitor(r.monitor);
      if (r.watch_plan) showWatchPlan(r.watch_plan);
      if (r.test_suite) showTestSuite(r.test_suite);
      if (r.incidents) showIncidents(r.incidents);
      if (r.ci_load) { if (r.ci_load.load) showCILoad(r.ci_load.load); else ciPicker(r.ci_load.ci); }
      if (r.query_run) return runQuery(r.query_run);
      if (r.action) return runAndReport(r.action);
      if (r.live) return runLive(r.live);
      if (r.batch_action) return runBatch(r.batch_action);
      if (r.troubleshoot) return runTroubleshoot(r.troubleshoot);
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
      if (!text && !(attached && !liveWaiter)) return;
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
      newsSeen(true);
      phHold = false;
      input.placeholder = PLACEHOLDER;
      var att = attached;
      attached = null;
      showChip();
      addMsg('user', (text || 'Here is a document.') + (att ? '\n📎 ' + att.name : ''));
      if (text) remember(text);
      var body = { session_id: state.sessionId, message: text, page: pageContext() };
      if (att) { lastDoc = att; body.attachment = { name: att.name, media_type: att.media_type, data: att.data }; }
      call('/api/chat', body);
    }

    function showConfirm(c) {
      var card = document.createElement('div');
      card.className = 'psa-card';
      var rows = (c.rows || []).map(function (r) {
        return '<tr' + (r.unsure != null ? ' class="psa-unsure-row"' : '') + '><td>' + esc(r.label) + '</td><td><strong>' + esc(r.value) + '</strong>'
          + (r.unsure != null ? '<span class="psa-note">⚠ Please check: ' + esc(r.unsure) + '</span>' : '') + '</td></tr>';
      }).join('');
      var secrets = (c.secrets || []).map(function (s) {
        return '<label>' + esc(s.label) + '<input type="password" autocomplete="new-password" data-secret="' + esc(s.name) + '"></label>'
          + '<label>Confirm ' + esc(s.label) + '<input type="password" autocomplete="new-password" data-confirm="' + esc(s.name) + '"></label>';
      }).join('');
      card.innerHTML = '<h4>' + esc(c.title) + ': please review</h4>' + docPreview() + '<table>' + rows + '</table>' + secrets
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
      return parseComponentName(m[1]);
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
      var lrec = startLiveRecorder(ws.frame, action.title);   /* if the page changed, the recovered run can update the task */
      ws.onStep = function (n, total, text) {
        if (cur) cur.end(true);
        cur = feed.add(text);
        setStatus('working', 'Step ' + n + ' of ' + total + ': ' + text);
      };
      setStatus('working');
      return runAction(action, secrets, ws).catch(function (e) {
        return { status: 'error', message: 'The agent hit an unexpected problem: ' + ((e && e.message) || e) };
      }).then(function (res) {
        if (cur) cur.end(res.status === 'success', res.status === 'success' ? null : res.message);
        if (res.status === 'error' && res.mismatch && !ws.stopped) return recoverLive(action, res, ws, feed, lrec, secrets);
        lrec.stop();
        secrets = null;
        ws.done();
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

    /* The recorded steps no longer match the page: Claude finishes the task live from that step,
       in the same workspace, with the password from the confirm card. Save waits for approval again. */
    function recoverLive(action, res, ws, feed, lrec, secrets) {
      feed.add('Step ' + (res.failed_index + 1) + ' did not match the page: working out the rest live', 'live');
      ws.status('Working out the rest live from step ' + (res.failed_index + 1));
      var t = typing('The page changed: working it out live...');
      return api('/api/live/recover', { session_id: state.sessionId, run_id: action.run_id, failed_index: res.failed_index, error: res.message })
        .then(function (r) {
          t.remove();
          var known = {};
          for (var k in secrets) known[norm(String(k).replace(/_/g, ' '))] = secrets[k];
          return runLive(r.live, { ws: ws, feed: feed, recorder: lrec, secrets: known,
            update: canTeach && action.task_id ? { title: action.title, task_id: action.task_id } : null });
        }, function (e) {
          t.remove();
          lrec.stop();
          ws.done();
          feed.finish('error');
          ws.status('Stopped at "' + res.failed_step + '": ' + res.message, true);
          setStatus('error');
          addMsg('bot', '⚠️ Stopped at **' + res.failed_step + '**: ' + res.message + '\n\n(I could not switch to working it out live: ' + e.message + ')');
          return report(action.run_id, res);
        });
    }

    /* Several items from one document: one table to check, fix or untick, then one run per row */
    function showBatch(b) {
      if (!panel.classList.contains('psa-wide')) setWide(true);
      var cols = b.columns || [];
      var head = '<tr><th title="Include"></th>' + cols.map(function (c) { return '<th>' + esc(c.label) + (c.required ? ' *' : '') + '</th>'; }).join('') + '</tr>';
      var body = (b.rows || []).map(function (r, i) {
        return '<tr data-i="' + i + '"><td><input type="checkbox" data-inc checked aria-label="Include ' + esc(r.label) + '"></td>'
          + cols.map(function (c) {
            var note = (r.unsure || {})[c.name], miss = (r.missing || []).indexOf(c.name) >= 0;
            return '<td><input data-col="' + esc(c.name) + '" value="' + esc(r.values[c.name] || '') + '" aria-label="' + esc(c.label) + ' for ' + esc(r.label) + '"'
              + (miss ? ' class="psa-miss" placeholder="needed"' : note != null ? ' class="psa-unsure" title="Please check: ' + esc(note) + '"' : '') + '></td>';
          }).join('') + '</tr>';
      }).join('');
      var secrets = (b.secrets || []).map(function (s) {
        return '<label>' + esc(s.label) + ' (used for every row)<input type="password" autocomplete="new-password" data-secret="' + esc(s.name) + '"></label>'
          + '<label>Confirm ' + esc(s.label) + '<input type="password" autocomplete="new-password" data-confirm="' + esc(s.name) + '"></label>';
      }).join('');
      var c = card('<h4>' + esc(b.title) + ': ' + b.rows.length + ' found, please review</h4>' + docPreview()
        + '<div class="psa-tablewrap"><table class="psa-batch">' + head + body + '</table></div>'
        + '<div class="psa-legend"><i style="background:#fde68a"></i>please check (hover for why)<i style="background:#fecaca"></i>needed · untick a row to skip it</div>'
        + secrets
        + '<div class="psa-actions"><button type="button" class="psa-ok">Create</button><button type="button" class="psa-no">Cancel</button></div>');
      var ok = c.querySelector('.psa-ok');
      function picked() { return [].filter.call(c.querySelectorAll('[data-inc]'), function (x) { return x.checked; }).length; }
      function secretValues() {
        var out = {}, valid = true;
        c.querySelectorAll('[data-secret]').forEach(function (inp) {
          var conf = c.querySelector('[data-confirm="' + inp.getAttribute('data-secret') + '"]');
          if (!inp.value || inp.value !== conf.value) valid = false;
          out[inp.getAttribute('data-secret')] = inp.value;
        });
        return valid ? out : null;
      }
      function refresh() {
        var n = picked();
        ok.textContent = 'Create ' + n;
        ok.disabled = !n || !secretValues();
      }
      c.querySelectorAll('input').forEach(function (x) {
        x.addEventListener('input', function () { if (x.classList.contains('psa-miss') && x.value) x.classList.remove('psa-miss'); refresh(); });
        x.addEventListener('change', refresh);
      });
      refresh();
      function close() { c.querySelectorAll('input,button').forEach(function (x) { x.disabled = true; }); c.querySelectorAll('input[type="password"]').forEach(function (x) { x.value = ''; }); }
      ok.addEventListener('click', function () {
        var sv = secretValues();
        if (!sv) return;
        var rows = [].map.call(c.querySelectorAll('tr[data-i]'), function (tr) {
          var i = +tr.getAttribute('data-i'), row = b.rows[i], values = {};
          tr.querySelectorAll('[data-col]').forEach(function (inp) {
            var name = inp.getAttribute('data-col');
            if (inp.value !== (row.values[name] || '')) values[name] = inp.value;
          });
          return { index: i, include: tr.querySelector('[data-inc]').checked, values: values };
        });
        close();
        pendingSecrets[b.batch_id] = sv;
        call('/api/confirm', { session_id: state.sessionId, run_id: b.batch_id, approved: true, rows: rows });
      });
      c.querySelector('.psa-no').addEventListener('click', function () {
        close();
        call('/api/confirm', { session_id: state.sessionId, run_id: b.batch_id, approved: false });
      });
    }

    function runBatch(ba) {
      var secrets = pendingSecrets[ba.batch_id] || {};
      delete pendingSecrets[ba.batch_id];
      for (var k in pendingSecrets) if (/^batch-/.test(k)) delete pendingSecrets[k];   /* from tables shown again after a fix */
      var items = ba.items || [], ws = openWorkspace('Agent: ' + ba.title), results = [], i = 0;
      var feed = activity(ba.title + ' · ' + items.length + (items.length === 1 ? ' entry' : ' entries'));
      setStatus('working');
      function next() {
        if (i >= items.length || ws.stopped) return finish();
        var it = items[i++], line = feed.add(it.label + ' (' + i + ' of ' + items.length + ')');
        ws.onStep = function (n, total, text) { setStatus('working', it.label + ' · step ' + n + ' of ' + total + ': ' + text); };
        return runAction({ run_id: it.run_id, title: ba.title, component: ba.component, steps: it.steps }, secrets, ws)
          .catch(function (e) { return { status: 'error', message: (e && e.message) || String(e) }; })
          .then(function (res) {
            line.end(res.status === 'success', res.status === 'success' ? null : res.message);
            results.push({ label: it.label, status: res.status, message: res.message, failed_step: res.failed_step });
            return next();
          });
      }
      function finish() {
        for (; i < items.length; i++) results.push({ label: items[i].label, status: 'stopped', message: 'Not started: stopped by the user' });
        secrets = null;
        ws.done();
        var ok = results.filter(function (r) { return r.status === 'success'; }).length;
        feed.finish(ok === results.length ? 'success' : 'error');
        ws.status(ok + ' of ' + results.length + ' done', ok !== results.length);
        setStatus(ok === results.length ? 'idle' : 'error');
        var t = typing();
        return api('/api/batch/result', { session_id: state.sessionId, batch_id: ba.batch_id, results: results })
          .then(function (r) { t.remove(); return handleReply(r); },
                function (e) { t.remove(); addMsg('bot', 'I could not report the results to the agent service: ' + e.message, false); });
      }
      return next();
    }

    /* Troubleshoot a failed process: read its posted log files in the user's session, then the
       investigator takes one step per call (each shown in the feed) until the diagnosis is ready. */
    var BIG_LOG = 2000000, LOG_PART = 150000, LOG_SEND = 60000;
    function mb(n) { return (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + ' MB'; }
    function headTail(text, n) {
      return text.length <= n ? text : text.slice(0, n / 4) + '\n…(middle of the file left out)…\n' + text.slice(-n * 3 / 4);
    }
    /* A log file as {text, part}. Big files (traces can be hundreds of MB): only the end, where the
       error usually is, or the start when the web server ignores Range. */
    function readLog(lf) {
      var size = +lf.size || 0, big = size > BIG_LOG;
      return fetch(lf.path, { credentials: 'same-origin', headers: big ? { Range: 'bytes=-' + LOG_PART } : {} }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        if (/html/i.test(r.headers.get('content-type') || '') && !/\.html?$/i.test(lf.name)) throw new Error('not readable (sign-on page)');
        if (!big) return r.text().then(function (text) { return { text: text, part: '' }; });
        if (r.status === 206) return r.text().then(function (text) { return { text: text, part: 'last ' + mb(text.length) + ' of ' + mb(size) }; });
        var reader = r.body && r.body.getReader(), dec = new TextDecoder(), text = '';
        if (!reader) throw new Error('too large to read (' + mb(size) + ')');
        function more() {
          return reader.read().then(function (c) {
            if (!c.done) text += dec.decode(c.value, { stream: true });
            if (c.done || text.length >= LOG_PART) {
              try { reader.cancel(); } catch (e) {}
              return { text: text.slice(0, LOG_PART), part: 'first ' + mb(Math.min(text.length, LOG_PART)) + ' of ' + mb(size) };
            }
            return more();
          });
        }
        return more();
      });
    }

    function runTroubleshoot(t) {
      var title = t.title || ('Troubleshooting process ' + t.instance);
      var what = t.kind === 'security' ? 'Checking access' : t.kind === 'approval' ? 'Reviewing absence request ' + t.thread : 'Investigating process ' + t.instance;
      var feed = activity(title), logs = [], first = true;
      setStatus('working', what);
      var reads = (t.logs || []).map(function (lf) {
        var item = feed.add((+lf.size > BIG_LOG ? 'Reading the end of log file ' + lf.name + ' (' + mb(+lf.size) + ')' : 'Reading log file ' + lf.name));
        return readLog(lf).then(function (got) {
          item.end(true);
          logs.push({ name: lf.name, text: headTail(got.text, LOG_SEND), part: got.part });
        }, function (e) { item.end(false, e.message); });
      });
      function next() {
        var looking = feed.add('Deciding what to check next', 'think');
        setStatus('thinking', what + '...');
        var body = { session_id: state.sessionId, run_id: t.run_id };
        if (first) { body.logs = logs; first = false; }
        return api('/api/troubleshoot/step', body).then(function (r) {
          looking.remove();
          (r.steps || []).forEach(function (text) { feed.add(text).end(true); });
          if (!r.done) { setStatus('working', what); return next(); }
          feed.finish(r.diagnosis ? 'success' : 'error');
          setStatus(r.diagnosis ? 'idle' : 'error');
          if (r.diagnosis) ({ security: showAccess, approval: showReview }[r.diagnosis.kind] || showDiagnosis)(r.diagnosis);
          else addMsg('bot', '⚠️ ' + (r.error || 'I could not finish the investigation.'));
        }, function (e) {
          looking.remove();
          feed.finish('error');
          setStatus('error');
          addMsg('bot', '⚠️ I lost contact with the agent service: ' + e.message, false);
        });
      }
      return Promise.all(reads).then(next);
    }

    var CATEGORY = {
      data: ['Data issue', '#92400e', '#fef3c7'], code: ['Code issue', '#5b21b6', '#ede9fe'],
      setup: ['Setup issue', '#075985', '#e0f2fe'], security: ['Security issue', '#9f1239', '#ffe4e6'],
      environment: ['Environment issue', '#065f46', '#d1fae5'], unknown: ['Not conclusive', '#334155', '#f1f5f9']
    };
    function showDiagnosis(d) {
      var cat = CATEGORY[d.category] || CATEGORY.unknown;
      var list = function (items, tag) { return '<' + tag + '>' + (items || []).map(function (x) { return '<li>' + md(x) + '</li>'; }).join('') + '</' + tag + '>'; };
      var c = card('<h4>Process ' + esc(d.instance) + ': what went wrong</h4>'
        + '<div class="psa-diag-top"><span class="psa-badge" style="color:' + cat[1] + ';background:' + cat[2] + '">' + cat[0] + '</span>'
        + '<small>Confidence: ' + esc(d.confidence || '?') + '</small></div>'
        + '<p class="psa-diag-sum">' + md(d.summary || '') + '</p>'
        + (d.where ? '<div class="psa-diag-sec"><b>Where</b><code class="psa-where">' + esc(d.where) + '</code></div>' : '')
        + '<div class="psa-diag-sec"><b>Root cause</b>' + md(d.root_cause || '') + '</div>'
        + ((d.evidence || []).length ? '<details open><summary>Evidence</summary>' + list(d.evidence, 'ul') + '</details>' : '')
        + '<div class="psa-diag-sec"><b>How to fix</b>' + list(d.fix_steps, 'ol') + '</div>'
        + (d.code_fix ? '<div class="psa-diag-sec"><b>Suggested code</b><pre class="psa-code">' + esc(d.code_fix) + '</pre></div>' : '')
        + (d.rerun ? '<div class="psa-diag-sec"><b>Then</b>' + md(d.rerun) + '</div>' : '')
        + ((d.queries || []).length ? '<details><summary>' + d.queries.length + ' read-only ' + (d.queries.length === 1 ? 'query' : 'queries') + ' I ran</summary>'
          + d.queries.map(function (q) { return '<div class="psa-q"><b>' + esc(q.purpose) + '</b> (' + q.rows + ' rows)<pre class="psa-code">' + esc(q.sql) + '</pre></div>'; }).join('')
          + '</details>' : '')
        + '<div class="psa-actions"><button type="button" class="psa-no" data-copy>Copy report</button></div>');
      c.classList.add('psa-diag');
      c.querySelector('[data-copy]').addEventListener('click', function (e) {
        var text = ['Process ' + d.instance + ' (' + cat[0] + ', confidence ' + d.confidence + ')', d.summary, '', 'Where: ' + (d.where || '-'),
          'Root cause: ' + d.root_cause, '', 'Evidence:'].concat((d.evidence || []).map(function (x) { return '- ' + x; }), ['', 'Fix:'],
          (d.fix_steps || []).map(function (x, i) { return (i + 1) + '. ' + x; }), d.code_fix ? ['', 'Suggested code:', d.code_fix] : [],
          d.rerun ? ['', 'Then: ' + d.rerun] : []).join('\n');
        var btn = e.target;
        (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () { btn.textContent = 'Copied'; },
          function () { btn.textContent = 'Copy not allowed here'; });
      });
    }

    var VERDICT = {
      missing_access: ['Missing access', '#991b1b', '#fee2e2'], partial_access: ['Partial access', '#92400e', '#fef3c7'],
      account_locked: ['Account locked', '#991b1b', '#fee2e2'], no_user: ['No such user', '#334155', '#f1f5f9'],
      row_security: ['Row security', '#92400e', '#fef3c7'], navigation: ['Menu link missing', '#075985', '#e0f2fe'],
      has_access: ['Has access', '#065f46', '#d1fae5'], unknown: ['Not conclusive', '#334155', '#f1f5f9']
    };
    var MARK = { ok: '✓', missing: '✕', warning: '!', info: 'i' };

    /* Access check result: the security chain, what is missing, the smallest fix, links to the security pages,
       and Apply (security admins): the agent makes the change on screen and stops for approval before Save. */
    function showAccess(d) {
      var v = VERDICT[d.verdict] || VERDICT.unknown, f = d.fix || {};
      function link(label, menu, comp, query) {
        return '<a target="_blank" rel="noopener" href="' + esc(componentUrl({ menu: menu, component: comp }, query).replace('/psc/', '/psp/')) + '">' + esc(label) + '</a>';
      }
      var links = [link('User profile ' + d.user, 'MAINTAIN_SECURITY', 'USERMAINT', { OPRID: d.user })];
      if (f.role) links.push(link('Role ' + f.role, 'MAINTAIN_SECURITY', 'ROLEMAINT', { ROLENAME: f.role }));
      if (f.permission_list) links.push(link('Permission list ' + f.permission_list, 'MAINTAIN_SECURITY', 'ACCESS_CNTRL_LISTX', { CLASSID: f.permission_list }));
      var pm = String(d.page || '').match(/([A-Z0-9_#$][A-Z0-9_#$()-]*)\.([A-Z0-9_#$][A-Z0-9_#$()-]*)\.([A-Z0-9_#$]+)/);
      if (pm) links.push('<a target="_blank" rel="noopener" href="' + esc(componentUrl({ menu: pm[1], component: pm[2], market: pm[3] }).replace('/psc/', '/psp/')) + '">The page</a>');
      var c = card('<h4>' + esc(d.user) + ' → ' + esc(d.target) + '</h4>'
        + '<div class="psa-diag-top"><span class="psa-badge" style="color:' + v[1] + ';background:' + v[2] + '">' + v[0] + '</span>'
        + '<small>Confidence: ' + esc(d.confidence || '?') + '</small></div>'
        + '<p class="psa-diag-sum">' + md(d.summary || '') + '</p>'
        + (d.page ? '<div class="psa-diag-sec"><b>Page</b><code class="psa-where">' + esc(d.page) + '</code></div>' : '')
        + ((d.checks || []).length ? '<div class="psa-diag-sec"><b>Security chain</b>' + d.checks.map(function (x) {
          return '<div class="psa-chk ' + esc(x.status) + '"><i>' + (MARK[x.status] || 'i') + '</i><span><b>' + esc(x.check) + '</b>: ' + md(x.detail) + '</span></div>';
        }).join('') + '</div>' : '')
        + (d.missing ? '<div class="psa-diag-sec"><b>Missing</b>' + md(d.missing) + '</div>' : '')
        + (f.kind && f.kind !== 'none' ? '<div class="psa-diag-sec"><b>Smallest fix</b><div class="psa-fix">' + md(f.description || '')
          + (f.side_effects ? '<div style="margin-top:4px;color:var(--psa-sub);font-size:12px">' + md('Side effects: ' + f.side_effects) + '</div>' : '') + '</div></div>' : '')
        + ((d.alternatives || []).length ? '<details><summary>Alternatives</summary><ul>' + d.alternatives.map(function (x) { return '<li>' + md(x) + '</li>'; }).join('') + '</ul></details>' : '')
        + (d.after_fix ? '<div class="psa-diag-sec"><b>Then</b>' + md(d.after_fix) + '</div>' : '')
        + '<div class="psa-links">' + links.join('') + '</div>'
        + ((d.queries || []).length ? '<details><summary>' + d.queries.length + ' extra read-only ' + (d.queries.length === 1 ? 'query' : 'queries') + '</summary>'
          + d.queries.map(function (q) { return '<div class="psa-q"><b>' + esc(q.purpose) + '</b> (' + q.rows + ' rows)<pre class="psa-code">' + esc(q.sql) + '</pre></div>'; }).join('')
          + '</details>' : '')
        + '<div class="psa-actions">' + (d.can_apply ? '<button type="button" class="psa-ok" data-apply>Apply (you approve the save)</button>' : '')
        + '<button type="button" class="psa-no" data-copy>Copy report</button></div>');
      c.classList.add('psa-diag');
      var apply = c.querySelector('[data-apply]');
      if (apply) apply.addEventListener('click', function () {
        apply.disabled = true;
        apply.textContent = 'Starting...';
        api('/api/security/apply', { session_id: state.sessionId, run_id: d.run_id }).then(function (r) {
          apply.textContent = 'Applying on screen';
          return runLive(r.live);
        }, function (e) { apply.disabled = false; apply.textContent = 'Apply (you approve the save)'; addMsg('bot', '⚠️ ' + e.message, false); });
      });
      c.querySelector('[data-copy]').addEventListener('click', function (e) {
        var text = [d.user + ' → ' + d.target + ' (' + v[0] + ', confidence ' + d.confidence + ')', d.summary, '', 'Page: ' + (d.page || '-'), '']
          .concat((d.checks || []).map(function (x) { return '[' + x.status + '] ' + x.check + ': ' + x.detail; }),
            d.missing ? ['', 'Missing: ' + d.missing] : [], f.description ? ['', 'Fix: ' + f.description, 'Side effects: ' + (f.side_effects || '-')] : [],
            (d.alternatives || []).length ? ['', 'Alternatives:'].concat(d.alternatives.map(function (x) { return '- ' + x; })) : [],
            d.after_fix ? ['', 'Then: ' + d.after_fix] : []).join('\n');
        var btn = e.target;
        (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () { btn.textContent = 'Copied'; },
          function () { btn.textContent = 'Copy not allowed here'; });
      });
    }

    /* Absence requests waiting for approval: Review with AI starts a review (the same step loop as troubleshooting) */
    function fmtDay(d) {
      var m = String(d || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
      return m ? +m[3] + ' ' + 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ')[m[2] - 1] + ' ' + m[1] : esc(d);
    }
    function showApprovals(a) {
      var items = a.items || [];
      if (!items.length) return;
      var c = card('<h4>' + (a.everyone ? 'Absence requests waiting for approval' : 'Absence requests waiting for you') + '</h4>'
        + '<div class="psa-apr-list">' + items.map(function (x, i) {
          var days = x.from === x.to ? fmtDay(x.from) : fmtDay(x.from) + ' – ' + fmtDay(x.to);
          var who = x.waiting_for_you ? 'Waiting for you' : 'Waiting for ' + esc(x.approver) + (x.approvers > 1 ? ' and ' + (x.approvers - 1) + ' more' : '');
          return '<div class="psa-apr"><div><b>' + esc(x.name) + '</b> <small>' + esc(x.employee) + '</small><br>' + esc(x.absence) + ' · ' + days
            + (x.hours != null ? ' · ' + esc(x.hours) + ' h' : '') + '<small class="psa-apr-who">' + who + ' · since ' + fmtDay(x.since) + '</small></div>'
            + '<button type="button" class="psa-ok" data-i="' + i + '">Review with AI</button></div>';
        }).join('') + '</div>');
      c.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('[data-i]');
        if (!b || busy) return;
        var x = items[+b.getAttribute('data-i')];
        b.disabled = true;
        b.textContent = 'Reviewing...';
        addMsg('user', 'Review the absence request of ' + x.name + ' (' + x.absence + ', ' + fmtDay(x.from) + ')');
        setBusy(true);
        api('/api/approvals/review', { session_id: state.sessionId, thread: x.thread })
          .then(function (r) { b.textContent = 'Reviewed'; return runTroubleshoot(r.troubleshoot); },
                function (err) { b.disabled = false; b.textContent = 'Review with AI'; addMsg('bot', '⚠️ ' + err.message, false); })
          .then(function () { setBusy(false); });
      });
    }

    var RECOMMEND = {
      approve: ['Recommend: approve', '#065f46', '#d1fae5', 'I reviewed this request. Everything checks out: please approve it.'],
      send_back: ['Recommend: send back', '#92400e', '#fef3c7', 'I reviewed this request. Something needs fixing before you approve it.'],
      deny: ['Recommend: deny', '#991b1b', '#fee2e2', 'I reviewed this request. I recommend you deny it.'],
      needs_review: ['Needs your review', '#334155', '#f1f5f9', 'I reviewed this request but the data cannot settle it: please look at it yourself.']
    };
    var CONF_COLOR = { high: '#16a34a', medium: '#d97706', low: '#dc2626' };
    var CHECK_MARK = { ok: ['ok', '✓'], warning: ['warning', '!'], problem: ['missing', '✕'], info: ['info', 'i'] };

    /* Absence review result: the approval status (from PeopleSoft), the recommendation and its confidence score,
       the checks behind it and a comment to send. The approver approves or denies in PeopleSoft. */
    function showReview(d) {
      var r = RECOMMEND[d.recommendation] || RECOMMEND.needs_review, st = d.status || {}, score = +d.confidence_score || 0;
      var conf = d.confidence || 'low', col = CONF_COLOR[conf] || CONF_COLOR.low;
      var waiting = st.waiting_count ? (st.reviewer_is_approver ? 'Waiting for <b>you</b>' : 'Waiting for <b>' + esc((st.waiting_for || []).join(', ')) + '</b>')
        + (st.waiting_count > (st.waiting_for || []).length ? ' and ' + (st.waiting_count - st.waiting_for.length) + ' more' : '') : 'Not waiting for anyone';
      var decided = (st.decided || []).map(function (x) { return esc(x.status) + ' by ' + esc(x.by) + (x.on ? ' on ' + fmtDay(x.on) : ''); }).join('; ');
      var approvalsUrl = componentUrl({ menu: 'EOAWMA_MAIN_FL', component: 'EOAWMA_MAIN_FL' });
      var c = card('<h4>Absence request ' + esc(d.thread) + (d.request ? ': ' + esc(d.request) : '') + '</h4>'
        + '<div class="psa-apr-status"><b>Approval status</b> ' + esc(st.thread_status || '?') + (st.step ? ' · step ' + esc(st.step) : '')
        + ' · ' + waiting + (decided ? '<br>' + decided : '') + (st.submitted ? '<br><small>Submitted ' + fmtDay(st.submitted) + ' by ' + esc(st.requested_by) + '</small>' : '') + '</div>'
        + '<div class="psa-diag-top"><span class="psa-badge" style="color:' + r[1] + ';background:' + r[2] + '">' + r[0] + '</span>'
        + '<div class="psa-conf" title="' + esc(d.confidence_reason || '') + '"><div class="psa-conf-h"><span>Confidence</span><b style="color:' + col + '">' + score + '/100 · '
        + esc(conf) + '</b></div><div class="psa-conf-bar"><i style="width:' + Math.max(2, Math.min(100, score)) + '%;background:' + col + '"></i></div></div></div>'
        + '<p class="psa-diag-sum"><b>' + r[3] + '</b><br>' + md(d.summary || '') + '</p>'
        + (d.confidence_reason ? '<div class="psa-diag-sec"><b>Why this confidence</b>' + md(d.confidence_reason) + '</div>' : '')
        + ((d.checks || []).length ? '<div class="psa-diag-sec"><b>What I checked</b>' + d.checks.map(function (x) {
          var m = CHECK_MARK[x.status] || CHECK_MARK.info;
          return '<div class="psa-chk ' + m[0] + '"><i>' + m[1] + '</i><span><b>' + esc(x.check) + '</b>: ' + md(x.detail) + '</span></div>';
        }).join('') + '</div>' : '')
        + (d.comment ? '<div class="psa-diag-sec"><b>Suggested comment</b><div class="psa-fix">' + md(d.comment) + '</div></div>' : '')
        + ((d.not_checked || []).length ? '<details><summary>Not checked</summary><ul>' + d.not_checked.map(function (x) { return '<li>' + md(x) + '</li>'; }).join('') + '</ul></details>' : '')
        + ((d.queries || []).length ? '<details><summary>' + d.queries.length + ' extra read-only ' + (d.queries.length === 1 ? 'query' : 'queries') + '</summary>'
          + d.queries.map(function (q) { return '<div class="psa-q"><b>' + esc(q.purpose) + '</b> (' + q.rows + ' rows)<pre class="psa-code">' + esc(q.sql) + '</pre></div>'; }).join('')
          + '</details>' : '')
        + '<small>You decide: approve or deny in PeopleSoft. I never approve anything myself.</small>'
        + '<div class="psa-actions"><a class="psa-btn" target="_blank" rel="noopener" href="' + esc(approvalsUrl) + '">Open Approvals in PeopleSoft</a>'
        + (d.comment ? '<button type="button" class="psa-no" data-comment>Copy comment</button>' : '')
        + '<button type="button" class="psa-no" data-copy>Copy report</button></div>');
      c.classList.add('psa-diag');
      function copy(btn, text) {
        (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () { btn.textContent = 'Copied'; },
          function () { btn.textContent = 'Copy not allowed here'; });
      }
      var cm = c.querySelector('[data-comment]');
      if (cm) cm.addEventListener('click', function () { copy(cm, d.comment); });
      c.querySelector('[data-copy]').addEventListener('click', function (e) {
        copy(e.target, ['Absence request ' + d.thread + ': ' + (d.request || ''), r[0] + ' (confidence ' + score + '/100, ' + conf + ')',
          'Approval status: ' + (st.thread_status || '?') + ', ' + waiting.replace(/<[^>]+>/g, ''), '', d.summary, '', 'Why this confidence: ' + (d.confidence_reason || '-'), '']
          .concat((d.checks || []).map(function (x) { return '[' + x.status + '] ' + x.check + ': ' + x.detail; }),
            d.comment ? ['', 'Suggested comment: ' + d.comment] : [],
            (d.not_checked || []).length ? ['', 'Not checked:'].concat(d.not_checked.map(function (x) { return '- ' + x; })) : []).join('\n'));
      });
    }

    /* Runbook drafted from a checklist: every step by type, what each check query returns today,
       the author's open questions; Publish saves it (nothing runs). */
    var RB_TYPE = {
      check: ['Check', '#075985', '#e0f2fe'], task: ['Task', '#3730a3', '#e0e7ff'], process: ['Process', '#6b21a8', '#f3e8ff'],
      approval: ['Approval', '#92400e', '#fef3c7'], manual: ['Manual', '#334155', '#f1f5f9']
    };
    function showRunbook(rb) {
      if (!panel.classList.contains('psa-wide')) setWide(true);
      var steps = (rb.steps || []).map(function (st) {
        var t = RB_TYPE[st.type] || RB_TYPE.manual, d = [];
        if (st.task) {
          d.push('Runs the task <b>' + esc(st.task) + '</b>' + (st.values && st.values.length ? ' with ' + st.values.map(function (v) {
            return esc(v.label) + ' = <code>' + esc(v.value) + '</code>';
          }).join(', ') : ''));
          if (st.asks && st.asks.length) d.push('<small>Asks when it runs: ' + esc(st.asks.join(', ')) + '</small>');
          if (st.type === 'process') d.push('<small>Then waits for Success in Process Monitor.</small>');
        }
        if (st.rule) {
          var x = st.test || {}, rows = x.rows ? ' (' + x.rows + (x.more ? '+' : '') + ' rows)' : ' (no rows)';
          var test = x.error ? ['Query error: ' + x.error, '#991b1b', '#fee2e2']
            : x.skipped ? ['Not tested: ' + x.skipped, '#334155', '#f1f5f9']
            : x.passes ? ['Today: passes' + rows, '#065f46', '#d1fae5'] : ['Today: fails' + rows, '#92400e', '#fef3c7'];
          var tried = x['with'] ? Object.keys(x['with']).map(function (k) { return k + ' = ' + x['with'][k]; }).join(', ') : '';
          d.push(esc(st.rule) + ' <small>(' + (st.expect === 'rows' ? 'passes when the query finds rows' : 'passes when the query finds nothing') + ')</small>');
          d.push('<span class="psa-rb-test" style="color:' + test[1] + ';background:' + test[2] + '">' + esc(test[0]) + '</span>'
            + (tried ? ' <small>tested with ' + esc(tried) + '</small>' : ''));
          d.push('<details><summary>Query</summary><pre class="psa-code">' + esc(st.sql) + '</pre></details>');
        }
        if (st.instructions) d.push(esc(st.instructions));
        if (st.warn_only) d.push('<small>If it fails: reported, and the run goes on.</small>');
        return '<li><div class="psa-rb-h"><span class="psa-rb-n">' + st.n + '</span><b>' + esc(st.title) + '</b>'
          + '<span class="psa-rb-t" style="color:' + t[1] + ';background:' + t[2] + '">' + t[0] + '</span></div>'
          + '<div class="psa-rb-d">' + d.join('<br>') + (st.owner ? '<br><small>Owner: ' + esc(st.owner) + '</small>' : '') + '</div></li>';
      }).join('');
      var inputs = (rb.inputs || []).map(function (i) {
        return '<tr><td>' + esc(i.label) + '</td><td>' + (i['default'] ? '<code>' + esc(i['default']) + '</code> <small>(default)</small>' : '<small>asked each run</small>')
          + (i.hint ? '<small>' + esc(i.hint) + '</small>' : '') + '</td></tr>';
      }).join('');
      var c = card('<h4>Runbook: ' + esc(rb.title) + '</h4>'
        + (rb.source ? '<small>Drafted from ' + esc(rb.source) + '</small>' : '')
        + (rb.summary ? '<p class="psa-diag-sum">' + esc(rb.summary) + '</p>' : '')
        + ((rb.problems || []).length ? '<div class="psa-rb-warn" style="background:#fef2f2;color:#7f1d1d"><b>Problems I could not fix</b><ul>'
          + rb.problems.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + ((rb.questions || []).length ? '<div class="psa-rb-warn" style="background:#fffbeb;color:#78350f"><b>Please check</b><ul>'
          + rb.questions.map(function (x) { return '<li>' + md(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + (inputs ? '<div class="psa-diag-sec"><b>Inputs (each run)</b><table>' + inputs + '</table></div>' : '')
        + '<div class="psa-diag-sec"><b>' + (rb.steps || []).length + ' steps</b><ol class="psa-rb-steps">' + steps + '</ol></div>'
        + '<small>Nothing has run. To change a step, tell me in the chat and I will redraft the runbook.</small>'
        + '<label class="psa-check" style="display:none"><input type="checkbox" data-k="overwrite"> Replace the existing runbook</label>'
        + '<div class="psa-actions"><button type="button" class="psa-ok" data-pub>Publish runbook</button><button type="button" class="psa-no" data-x>Discard</button></div>');
      var pub = c.querySelector('[data-pub]'), over = c.querySelector('[data-k="overwrite"]');
      c.querySelector('[data-x]').addEventListener('click', function () { disable(c); addMsg('bot', 'Runbook draft discarded.', false); });
      pub.addEventListener('click', function () {
        pub.disabled = true;
        pub.textContent = 'Publishing...';
        api('/api/runbooks/save', { session_id: state.sessionId, draft_id: rb.draft_id, overwrite: over.checked }).then(function (r) {
          disable(c);
          pub.textContent = 'Published';
          addMsg('bot', 'Published **' + r.title + '** (' + r.steps + ' steps) as `' + r.path + '`.');
        }, function (e) {
          pub.disabled = false;
          pub.textContent = 'Publish runbook';
          var exists = /already exists/.test(e.message);
          if (exists) over.parentNode.style.display = '';
          addMsg('bot', '⚠️ ' + e.message + (exists ? '. Tick "Replace the existing runbook" to overwrite it.' : ''), false);
        });
      });
    }

    /* Monitor drafted in the chat: what it watches, how often, who gets the email, and what it would have caught
       (failures in the last 30 days, what is stuck now, what the check returns now); Save starts it. */
    function previewTable(pv) {
      if (!pv || !(pv.sample || []).length) return '';
      return '<table class="psa-mon-t"><tr>' + (pv.columns || []).map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr>'
        + pv.sample.map(function (r) { return '<tr>' + r.map(function (x) { return '<td>' + esc(x) + '</td>'; }).join('') + '</tr>'; }).join('') + '</table>';
    }
    function showMonitor(mo) {
      var pv = mo.preview || {}, label = pv.error ? 'Query error: ' + pv.error : pv.skipped ? 'Not tested: ' + pv.skipped : pv.label || '';
      var c = card('<h4>Monitor: ' + esc(mo.title) + '</h4>'
        + (mo.summary ? '<p class="psa-diag-sum">' + esc(mo.summary) + '</p>' : '')
        + ((mo.problems || []).length ? '<div class="psa-rb-warn" style="background:#fef2f2;color:#7f1d1d"><b>Problems I could not fix</b><ul>'
          + mo.problems.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + '<table class="psa-mon-facts"><tr><td>Watches</td><td>' + esc(mo.what) + '</td></tr><tr><td>When</td><td>' + esc(mo.when) + '</td></tr>'
        + '<tr><td>Email</td><td>' + (mo.notify && mo.notify.length ? esc(mo.notify.join(', ')) : '<small>default recipients</small>') + '</td></tr></table>'
        + (label ? '<div class="psa-diag-sec"><b>' + esc(label) + '</b>' + previewTable(pv) + '</div>' : '')
        + ((mo.warnings || []).length ? '<div class="psa-rb-warn" style="background:#fffbeb;color:#78350f"><ul>'
          + mo.warnings.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + '<small>It only reads. Each alert is analysed and emailed with the action needed; nothing is rerun or changed.'
        + (mo.exists ? ' Saving replaces the monitor with the same name.' : '') + '</small>'
        + '<div class="psa-actions"><button type="button" class="psa-ok" data-save>Save and start</button><button type="button" class="psa-no" data-x>Discard</button></div>');
      var save = c.querySelector('[data-save]');
      c.querySelector('[data-x]').addEventListener('click', function () { disable(c); addMsg('bot', 'Monitor discarded.', false); });
      save.addEventListener('click', function () {
        save.disabled = true;
        save.textContent = 'Saving...';
        api('/api/monitors/save', { session_id: state.sessionId, draft_id: mo.draft_id }).then(function (r) {
          disable(c);
          save.textContent = 'Running';
          addMsg('bot', 'Saved **' + r.title + '** (`' + r.path + '`). It runs ' + r.when.toLowerCase() + ', starting now.');
        }, function (e) {
          save.disabled = false;
          save.textContent = 'Save and start';
          addMsg('bot', '⚠️ ' + e.message, false);
        });
      });
    }

    /* ＋ Features: the fixed things the agent does, by role. Picking one starts a guided flow (Watch jobs, Build a
       runbook: download the template, fill it, upload it) or asks the question for you. */
    function features() {
      var m = me || {}, f = [];
      if (m.can_monitor && m.watcher) {
        f.push({ k: 'monitor', t: 'Watch jobs', s: 'Upload a watch plan: I watch and email the analysis', go: guideWatch });
        f.push({ k: 'monitor', t: 'Manage watchdogs', s: 'Pause, run, delete, download', go: showManager });
      }
      if (m.regress) f.push({ k: 'test', t: 'Regression tests', s: 'Replay the tasks after a patch: what broke?', go: showRegress });
      if (m.can_teach) f.push({ k: 'runbook', t: 'Build a runbook', s: 'Upload your checklist', go: guideRunbook });
      if ((m.runbooks || []).length) f.push({ k: 'runbook', t: 'Run a runbook', s: m.runbooks.length + ' published', go: pickRunbook });
      if (m.troubleshoot) f.push({ k: 'trouble', t: 'Troubleshoot a process', s: 'Why did my last process fail?', go: function () { send('Why did my last process fail?'); } });
      if (m.security) f.push({ k: 'security', t: 'Check someone\'s access', s: 'Why can\'t a user open a page?', go: function () {
        input.value = 'Why can\'t '; hint('e.g. Why can\'t ABSMITH access Create Job Opening?'); input.focus(); } });
      else if (m.own_access) f.push({ k: 'security', t: 'Check my access', s: 'Why can\'t I open a page?', go: function () {
        input.value = 'Why can\'t I open '; hint('e.g. Why can\'t I open Create Job Opening?'); input.focus(); } });
      if (m.approvals) f.push({ k: 'approve', t: 'Review absence approvals', s: 'I check each request and recommend', go: function () { send('Show my pending absence approvals'); } });
      if (m.can_teach) f.push({ k: 'task', t: 'Record a task', s: 'Do it once by hand, I learn it', go: function () { showRecordCard({ title: '' }); } });
      if (m.ask_data) f.push({ k: 'data', t: 'Ask the data', s: 'How many, who, which: from your AI_ queries', go: showQueries });
      if (m.ci) f.push({ k: 'ci', t: 'Load with a CI', s: 'Excel to CI: upload rows, I check and load them', go: function () { ciPicker(); } });
      if (m.can_audit) f.push({ k: 'audit', t: 'Audit', s: 'Usage, cost, failures and the audit log', go: function () { openAudit(); } });
      return f;
    }
    function toggleFeatures(show) {
      var menu = panel.querySelector('#psa-featmenu'), btn = panel.querySelector('#psa-feat');
      show = show === undefined ? menu.hidden : show;
      if (show) {
        var f = features();
        menu.innerHTML = f.length ? f.map(function (x, i) {
          var ic = TILE[x.k];
          return '<button type="button" role="menuitem" data-f="' + i + '"><span class="psa-tic" style="color:' + ic[1] + ';background:' + ic[2] + '">'
            + ic[0] + '</span><span><b>' + esc(x.t) + '</b><small>' + esc(x.s) + '</small></span></button>';
        }).join('') : '<small style="padding:6px;display:block">Connecting to the agent...</small>';
        menu.onclick = function (e) {
          var b = e.target.closest && e.target.closest('[data-f]');
          if (!b) return;
          toggleFeatures(false);
          [].forEach.call(logEl.querySelectorAll('.psa-welcome'), function (w) { w.remove(); });
          f[+b.getAttribute('data-f')].go();
        };
      }
      menu.hidden = !show;
      btn.setAttribute('aria-expanded', show ? 'true' : 'false');
      if (show) { var first = menu.querySelector('button'); if (first) first.focus(); }
    }

    /* A file the service sends as {name, data (base64)}: saved through the browser's download */
    function download(path) {
      return apiGet(path).then(function (r) {
        var bin = atob(r.data), bytes = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        var a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([bytes], { type: XLSX }));
        a.download = r.name;
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        return r.name;
      });
    }
    function guide(title, steps, template, uploadText, extra) {
      var c = card('<h4>' + esc(title) + '</h4><ol>' + steps.map(function (x) { return '<li>' + md(x) + '</li>'; }).join('') + '</ol>'
        + '<div class="psa-actions"><button type="button" class="psa-ok" data-up>Upload the filled template</button>'
        + '<button type="button" class="psa-no" data-dl>Download the template</button>' + (extra || '') + '</div>');
      c.classList.add('psa-guide');
      c.querySelector('[data-dl]').addEventListener('click', function (e) {
        var b = e.target;
        b.disabled = true;
        download('/api/templates/' + template).then(function (name) { b.disabled = false; b.textContent = 'Downloaded ' + name; },
          function (err) { b.disabled = false; addMsg('bot', '⚠️ ' + err.message, false); });
      });
      c.querySelector('[data-up]').addEventListener('click', function () { uploadAs = uploadText; panel.querySelector('#psa-file').click(); });
      return c;
    }
    function guideWatch() {
      var c = guide('Watch jobs', [
        '**Download the watch plan template.** It comes with examples: AEMINITEST and XRFWIN failures, XRFWIN stuck, PORTAL_CSS must succeed by 07:00.',
        '**Fill one row per job:** the process, what to watch for (Failure, Stuck, Must succeed by, Data check), when, and who gets the email.',
        '**Upload it here.** I check every row against PeopleSoft and give you an overview with what each watchdog would have caught. Nothing runs until you confirm.'
      ], 'watch_plan', 'Build a watch plan from this sheet', '<button type="button" class="psa-no" data-mg>Manage watchdogs</button>');
      c.querySelector('[data-mg]').addEventListener('click', showManager);
    }
    function guideRunbook() {
      guide('Build a runbook', [
        '**Download the checklist template**, or use the checklist you already have (Excel, CSV or PDF).',
        '**One row per step:** what to do, the process or page, what must be true afterwards, who owns it.',
        '**Upload it here.** I map every row to a check, task, process, approval or manual step and test the checks. You review it and publish.'
      ], 'runbook', 'Build a runbook from this checklist');
    }
    function pickRunbook() {
      var rbs = (me && me.runbooks) || [];
      var c = card('<h4>Run a runbook</h4><ul class="psa-inc-list">' + rbs.map(function (r, i) {
        return '<li><button type="button" data-r="' + i + '"><b>' + esc(r.title) + '</b><small>' + r.steps + ' steps</small>'
          + (r.summary ? '<span>' + esc(r.summary) + '</span>' : '') + '</button></li>';
      }).join('') + '</ul>');
      c.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('[data-r]');
        if (b) send('Run the runbook ' + rbs[+b.getAttribute('data-r')].title);
      });
    }

    /* The overview of an uploaded watch plan: every watchdog with what it would have caught, what changes when the
       plan already exists; Confirm and schedule saves it and the watcher starts. */
    function showWatchPlan(wp) {
      if (!panel.classList.contains('psa-wide')) setWide(true);
      var ch = wp.changes, list = function (title, items) { return items.length ? '<li><b>' + title + ':</b> ' + items.map(esc).join(', ') + '</li>' : ''; };
      var rows = (wp.watchdogs || []).map(function (d) {
        var pv = d.preview || {}, label = pv.error ? 'Query error: ' + pv.error : pv.skipped ? 'Not tested: ' + pv.skipped : pv.label || '';
        return '<tr><td>' + d.n + '</td><td><b>' + esc(d.title) + '</b>' + (d.paused ? ' <small>(paused)</small>' : '') + '<small>' + esc(d.what) + '</small>'
          + ((d.problems || []).length ? '<small style="color:#b91c1c">' + d.problems.map(esc).join('<br>') + '</small>' : '') + '</td>'
          + '<td>' + esc(d.when) + '</td><td>' + (d.notify && d.notify.length ? esc(d.notify.join(', ')) : '<small>default</small>') + '</td>'
          + '<td>' + esc(label) + (pv.sample && pv.sample.length ? '<details><summary>Examples</summary>' + previewTable(pv) + '</details>' : '') + '</td></tr>';
      }).join('');
      var c = card('<h4>Watch plan: ' + esc(wp.title) + '</h4>'
        + (wp.source ? '<small>From ' + esc(wp.source) + '</small>' : '')
        + (wp.summary ? '<p class="psa-diag-sum">' + esc(wp.summary) + '</p>' : '')
        + ((wp.problems || []).length ? '<div class="psa-rb-warn" style="background:#fef2f2;color:#7f1d1d"><b>Problems I could not fix</b><ul>'
          + wp.problems.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + (ch ? '<div class="psa-chg"><b>This replaces the saved plan ' + esc(wp.title) + '</b><ul>' + list('Added', ch.added) + list('Removed', ch.removed)
          + list('Changed', ch.changed.map(function (x) { return x.title + ' (' + x.fields.join(', ').replace(/_/g, ' ') + ')'; }))
          + '<li>' + ch.unchanged + ' unchanged</li></ul></div>' : '')
        + ((wp.questions || []).length ? '<div class="psa-rb-warn" style="background:#fffbeb;color:#78350f"><b>Please check</b><ul>'
          + wp.questions.map(function (x) { return '<li>' + md(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + '<table class="psa-mon-t psa-wp"><tr><th>#</th><th>Watchdog</th><th>When</th><th>Email</th><th>What it would have caught</th></tr>' + rows + '</table>'
        + ((wp.warnings || []).length ? '<div class="psa-rb-warn" style="background:#fffbeb;color:#78350f"><ul>'
          + wp.warnings.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + '<small>Watchdogs only read. Each alert is analysed and emailed with the action needed; nothing is rerun or changed.</small>'
        + '<div class="psa-actions"><button type="button" class="psa-ok" data-go>Confirm and schedule</button><button type="button" class="psa-no" data-x>Discard</button></div>');
      var go = c.querySelector('[data-go]');
      c.querySelector('[data-x]').addEventListener('click', function () { disable(c); addMsg('bot', 'Watch plan discarded. Nothing was scheduled.', false); });
      go.addEventListener('click', function () {
        go.disabled = true;
        go.textContent = 'Scheduling...';
        api('/api/watchplans/save', { session_id: state.sessionId, draft_id: wp.draft_id }).then(function (r) {
          disable(c);
          go.textContent = 'Scheduled';
          setDogs(r.counts);
          addMsg('bot', 'Scheduled **' + r.title + '**: ' + r.watchdogs + ' watchdogs (`' + r.path + '`). They start now; open **◉ watchdogs** below to pause, run, delete or download them.');
        }, function (e) {
          go.disabled = false;
          go.textContent = 'Confirm and schedule';
          addMsg('bot', '⚠️ ' + e.message, false);
        });
      });
    }

    /* ＋ Features › Load with a CI (Excel to CI): pick a Component Interface, download its template, upload it filled
       in; the service checks every row, then this browser posts each row to WEBLIB_SOAPTOCI in the user's own session
       (as ExcelToCI does), so PeopleSoft saves it as them. */
    var ciUpload = null;
    function ciUrl() {
      var m = location.pathname.match(/\/ps[cp]\/([^\/]+)\/([^\/]+)\/([^\/]+)\//);
      var site = m ? m[1].replace(/_newwin$/i, '').replace(/_\d+$/, '') : 'ps';
      return '/psc/' + site + '/' + (m ? m[2] : 'EMPLOYEE') + '/' + (m ? m[3] : 'HRMS') + '/s/WEBLIB_SOAPTOCI.SOAPTOCI.FieldFormula.IScript_SOAPToCI';
    }
    function soapToCI(xml) {
      // postDataBin=y: without it the servlet doesn't pass the body on (%Request.GetContentBody() is empty).
      // Not disconnect=y as ExcelToCI sends: that ends the user's session.
      return fetch(ciUrl() + '?postDataBin=y', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'text/xml; charset=UTF-8' }, body: xml })
        .then(function (r) { return r.text(); });
    }
    function xmlEsc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
    function ciPicker(name) {
      if (name) return ciPick(name);
      var c = card('<h4>Load with a Component Interface</h4><p class="psa-sub">Like ExcelToCI, without the macro workbook: pick a CI, '
        + 'download its template, fill it in and upload it. I check every row before anything is saved; then each row is saved '
        + 'through the CI as you.</p><input type="search" class="psa-ci-q" placeholder="Search CIs: user, job, position..." aria-label="Search Component Interfaces">'
        + '<div class="psa-ci-list" role="listbox" aria-label="Component Interfaces"><small>Loading...</small></div><div class="psa-ci-loads"></div>');
      c.classList.add('psa-ci');
      var q = c.querySelector('.psa-ci-q'), list = c.querySelector('.psa-ci-list'), timer = null;
      function search() {
        apiGet('/api/ci?q=' + encodeURIComponent(q.value.trim())).then(function (r) {
          list.innerHTML = r.cis.length ? r.cis.slice(0, 40).map(function (x) {
            return '<button type="button" role="option" data-ci="' + esc(x.name) + '"><b>' + esc(x.name) + '</b><span>' + esc(x.descr)
              + '</span><small>' + [x.create ? 'Create' : '', x.update ? 'Update' : ''].filter(Boolean).join(' · ') + '</small></button>';
          }).join('') + (r.more || r.cis.length > 40 ? '<small>More match: type more of the name.</small>' : '')
            : '<small>No CI you may load matches "' + esc(q.value) + '".</small>';
        }, function (e) { list.innerHTML = '<small>⚠️ ' + esc(e.message) + '</small>'; });
      }
      q.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(search, 250); });
      list.addEventListener('click', function (e) { var b = e.target.closest('[data-ci]'); if (b) ciPick(b.getAttribute('data-ci')); });
      search();
      apiGet('/api/ci/loads').then(function (r) {
        if (!r.loads.length) return;
        c.querySelector('.psa-ci-loads').innerHTML = '<h5>Your recent loads</h5>' + r.loads.slice(0, 5).map(function (l) {
          return '<div class="psa-ci-ld"><span><b>' + esc(l.file) + '</b> → ' + esc(l.ci) + '<small>' + esc(ciStatus(l)) + '</small></span>'
            + '<button type="button" data-open="' + esc(l.load_id) + '">' + (/paused|checked|running/.test(l.status) && l.processed < l.total ? 'Resume' : 'Open') + '</button></div>';
        }).join('');
        c.querySelector('.psa-ci-loads').addEventListener('click', function (e) {
          var b = e.target.closest('[data-open]');
          if (b) apiGet('/api/ci/load?load=' + encodeURIComponent(b.getAttribute('data-open'))).then(function (x) { showCILoad(x.load); });
        });
      }, function () {});
      setTimeout(function () { q.focus(); }, 50);
    }
    function ciStatus(l) {
      var d = l.done || {};
      if (l.status === 'checked') return l.created.slice(5, 16) + ': checked, ' + l.counts.ready + ' ready, ' + l.counts.problems + ' with problems';
      return (l.started || l.created).slice(5, 16) + ': ' + l.status + ', ' + l.processed + ' of ' + l.total + ' loaded'
        + (d.error ? ', ' + d.error + ' failed' : '');
    }
    function ciPick(name) {
      var wait = addMsg('bot', 'Reading **' + name + '** from PeopleSoft...', false);
      return apiGet('/api/ci/shape?ci=' + encodeURIComponent(name)).then(function (r) {
        if (r.shape) return r;
        /* the CI's shape, as ExcelToCI's New Template reads it: GetCIShape through SOAPTOCI, in this session */
        return soapToCI(r.request).then(function (xml) { return api('/api/ci/shape', { ci: name, xml: xml }); });
      }).then(function (r) { if (wait) wait.remove(); showCIShape(r.shape); },
        function (e) { if (wait) wait.remove(); addMsg('bot', '⚠️ ' + e.message, false); });
    }
    function showCIShape(s) {
      function propRow(cpath, p) {
        var id = cpath ? cpath + '.' + p.name : p.name, locked = p.key || (p.required && !p.readonly);
        if (p.readonly && !p.key) return '';
        if (p.secret) return '<li class="psa-ci-sec"><span>🔒 ' + esc(p.label) + '</span><small>typed on the load card, never in the file</small></li>';
        return '<li><label><input type="checkbox" data-p="' + esc(id) + '"' + (p['default'] ? ' checked' : '') + (locked ? ' disabled' : '') + '> '
          + esc(p.label) + (p.required && !p.readonly ? ' <em>*</em>' : '') + (p.key ? ' <em>key</em>' : '') + '</label><small>' + esc(p.name) + ' · '
          + esc(p.type_label) + (p.length ? ' ' + p.length : '') + (p.values ? ' · ' + p.values + ' values' : '') + '</small></li>';
      }
      function coll(c) {
        var props = c.props.map(function (p) { return propRow(c.path, p); }).join('');
        return '<details' + (c.level === 0 ? ' open' : '') + ' class="psa-ci-coll" style="margin-left:' + (c.level * 12) + 'px"><summary><b>'
          + esc(c.level === 0 ? s.ci : c.name) + '</b> <small>' + (c.level === 0 ? 'one row per record' : 'a list in each record: its own sheet')
          + '</small></summary><ul>' + props + '</ul></details>' + c.collections.map(coll).join('');
      }
      var can = [s.can.create ? 'Create' : '', s.can.update ? 'Update' : ''].filter(Boolean).join(' and ');
      var c = card('<h4>' + esc(s.ci) + ': ' + esc(s.descr) + '</h4><p class="psa-sub">You may <b>' + esc(can || 'not load') + '</b> through this CI. '
        + 'Tick the columns you want in the template (keys and required ones are always in). Lists like Roles get their own sheet.</p>'
        + '<div class="psa-ci-tree">' + coll(s.root) + '</div><div class="psa-actions"><button type="button" class="psa-ok" data-dl>Download template</button>'
        + '<button type="button" class="psa-no" data-up>Upload the filled template</button></div>');
      c.classList.add('psa-ci');
      c.querySelector('[data-dl]').addEventListener('click', function (e) {
        var b = e.target, props = [].map.call(c.querySelectorAll('[data-p]:checked'), function (x) { return x.getAttribute('data-p'); });
        b.disabled = true;
        api('/api/ci/template', { ci: s.ci, props: props }).then(function (r) { saveB64(r); b.disabled = false; b.textContent = 'Downloaded ' + r.name; },
          function (err) { b.disabled = false; addMsg('bot', '⚠️ ' + err.message, false); });
      });
      c.querySelector('[data-up]').addEventListener('click', function () { ciUpload = { ci: s.ci }; panel.querySelector('#psa-file').click(); });
      c.scrollIntoView({ block: 'nearest' });
    }
    function saveB64(r) {
      var bin = atob(r.data), bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      var a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([bytes], { type: XLSX }));
      a.download = r.name;
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    }
    function ciUploadFile(file, cu) {
      if (!file) return;
      if (!/\.xlsx$/i.test(file.name)) { addMsg('bot', 'Upload the template as an Excel file (.xlsx).', false); return; }
      var reader = new FileReader();
      reader.onload = function () {
        addMsg('user', '📎 ' + file.name, false);
        var wait = addMsg('bot', 'Checking every row of **' + file.name + '** against PeopleSoft...', false);
        api('/api/ci/check', { ci: cu.ci, name: file.name, data: String(reader.result).split(',')[1] }).then(function (r) {
          if (wait) wait.remove();
          showCILoad(r.load);
        }, function (e) { if (wait) wait.remove(); addMsg('bot', '⚠️ ' + e.message, false); });
      };
      reader.readAsDataURL(file);
    }
    var CI_STATUS = { ready: ['Ready', ''], problem: ['Problem', 'psa-bad'], ok: ['✓ Saved', 'psa-good'], warning: ['✓ Saved, warning', 'psa-warnt'], error: ['✕ Failed', 'psa-bad'] };
    function ciRows(l) {
      return '<div class="psa-ci-rows"><table><thead><tr><th>Row</th><th>Action</th><th>Key</th><th>Lists</th><th>Status</th></tr></thead><tbody>'
        + l.rows.map(function (r) {
          var st = CI_STATUS[r.status] || [r.status, ''];
          return '<tr data-n="' + r.n + '"><td>' + r.row + '</td><td>' + esc(r.action) + '</td><td><b>' + esc(r.label) + '</b></td><td>' + (r.children || '')
            + '</td><td><span class="' + st[1] + '">' + st[0] + '</span>' + (r.problems.length ? '<br><small>' + r.problems.map(esc).join('<br>') + '</small>'
            : r.detail && r.status !== 'ok' ? '<br><small>' + esc(r.detail) + '</small>' : '') + '</td></tr>';
        }).join('') + '</tbody></table>' + (l.more_rows ? '<small>and ' + l.more_rows + ' more rows (all in the results file)</small>' : '') + '</div>';
    }
    function showCILoad(l) {
      var c = card('');
      c.classList.add('psa-ci', 'psa-ci-load');
      c.setAttribute('data-load', l.load_id);
      renderCILoad(c, l);
      c.scrollIntoView({ block: 'nearest' });
      return c;
    }
    function renderCILoad(c, l) {
      var d = l.done || {}, finished = /done|stopped/.test(l.status), left = l.total - l.processed;
      var head = '<h4>' + esc(l.file) + ' → ' + esc(l.ci) + '</h4>';
      var chips = '<div class="psa-ci-chips"><span>' + l.counts.rows + ' rows</span><span class="psa-good">' + l.counts.ready + ' ready</span>'
        + (l.counts.problems ? '<span class="psa-bad">' + l.counts.problems + ' with problems: not loaded</span>' : '')
        + (l.processed ? '<span class="psa-good">' + (d.ok + d.warning) + ' saved</span>' + (d.error ? '<span class="psa-bad">' + d.error + ' failed</span>' : '') : '') + '</div>';
      var fileProblems = (l.file_problems || []).length ? '<ul class="psa-ci-fp">' + l.file_problems.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '';
      var secrets = !finished && left > 0 && (l.secrets || []).length ? '<div class="psa-ci-secrets">' + l.secrets.map(function (s) {
        return '<label>' + esc(s.label) + ' for the new rows <input type="password" autocomplete="new-password" data-sec="' + esc(s.name) + '"></label>';
      }).join('') + '<small>Typed here, filled in by this browser: it never reaches the agent service.</small></div>' : '';
      var progress = l.processed || l.status === 'running' ? '<div class="psa-ci-bar" role="progressbar" aria-valuemin="0" aria-valuemax="' + l.total + '" aria-valuenow="'
        + l.processed + '"><i style="width:' + (l.total ? 100 * l.processed / l.total : 0) + '%"></i></div><p class="psa-sub psa-ci-now">' + l.processed + ' of ' + l.total
        + ' rows loaded' + (finished ? (l.status === 'stopped' ? ': stopped' : ': done') : '') + '</p>' : '';
      var actions = '';
      if (!finished && left > 0) {
        actions = l.status === 'running' ? '<button type="button" class="psa-no" data-pause>Pause</button><button type="button" class="psa-no" data-stop>Stop</button>'
          : '<button type="button" class="psa-ok" data-go>' + (l.processed ? 'Resume: load the other ' + left : 'Load ' + left + (left === 1 ? ' row' : ' rows')) + '</button>'
            + '<button type="button" class="psa-no" data-stop>' + (l.processed ? 'Stop here' : 'Cancel') + '</button>';
      }
      if (l.processed || l.counts.problems) actions += '<button type="button" class="psa-no" data-x="results">Download results</button>';
      if (l.counts.problems || d.error) actions += '<button type="button" class="psa-no" data-x="fix">Download the rows to fix</button>';
      if (finished && d.error) actions += '<button type="button" class="psa-no" data-explain>Explain the errors</button>';
      c.innerHTML = head + chips + fileProblems + progress + ciRows(l) + secrets + '<div class="psa-actions">' + actions + '</div>';
      c.onclick = function (e) {
        var b = e.target.closest && e.target.closest('button');
        if (!b) return;
        if (b.hasAttribute('data-go')) {
          var secrets = {}, missing = false;
          [].forEach.call(c.querySelectorAll('[data-sec]'), function (i) { secrets[i.getAttribute('data-sec')] = i.value; if (!i.value) missing = true; });
          if (missing) { addMsg('bot', 'Type the password for the new rows first.', false); return; }
          runCILoad(c, l, secrets);
        } else if (b.hasAttribute('data-pause') || b.hasAttribute('data-stop')) {
          c.ciHalt = b.hasAttribute('data-stop') ? 'stop' : 'pause';
          if (l.status !== 'running') api('/api/ci/' + c.ciHalt, { load_id: l.load_id }).then(function (r) { renderCILoad(c, r.load); });
          b.disabled = true;
        } else if (b.hasAttribute('data-x')) {
          download('/api/ci/export?load=' + encodeURIComponent(l.load_id) + '&what=' + b.getAttribute('data-x')).then(function (name) { b.textContent = 'Downloaded ' + name; },
            function (err) { addMsg('bot', '⚠️ ' + err.message, false); });
        } else if (b.hasAttribute('data-explain')) {
          var bad = l.rows.filter(function (r) { return r.status === 'error'; }).slice(0, 15);
          send('These rows failed when I loaded ' + l.file + ' through the CI ' + l.ci + '. Explain why in plain words and what to change in the file:\n'
            + bad.map(function (r) { return '- row ' + r.row + ' (' + r.label + '): ' + r.detail; }).join('\n'));
        }
      };
    }
    /* the load: next row from the service -> SOAPTOCI in this session -> its answer back to the service, until done */
    function runCILoad(c, l, secrets) {
      c.ciHalt = null;
      return api('/api/ci/start', { load_id: l.load_id }).then(function (r) {
        renderCILoad(c, r.load);
        var t0 = Date.now(), n0 = r.load.processed, last = r.load;
        function step() {
          if (c.ciHalt) return api('/api/ci/' + c.ciHalt, { load_id: l.load_id }).then(function (x) { renderCILoad(c, x.load); });
          return api('/api/ci/next', { load_id: l.load_id }).then(function (x) {
            if (x.done || x.paused) { renderCILoad(c, x.load); if (x.done) ciDone(x.load); return; }
            var xml = x.row.xml.replace(/\{\{secret:(\w+)\}\}/g, function (m, k) { return xmlEsc(secrets[k] || ''); });
            var now = c.querySelector('.psa-ci-now');
            if (now) now.textContent = 'Loading ' + x.row.label + '... (' + last.processed + ' of ' + last.total + ')';
            return soapToCI(xml).then(null, function (e) { return 'network error: ' + e.message; }).then(function (text) {
              return api('/api/ci/result', { load_id: l.load_id, n: x.row.n, response: text });
            }).then(function (res) {
              last = res.load;
              var bar = c.querySelector('.psa-ci-bar i'), done = last.processed - n0, per = (Date.now() - t0) / Math.max(1, done);
              if (bar) bar.style.width = (100 * last.processed / Math.max(1, last.total)) + '%';
              var row = c.querySelector('tr[data-n="' + x.row.n + '"] td:last-child'), st = CI_STATUS[res.result.status];
              if (row) row.innerHTML = '<span class="' + st[1] + '">' + st[0] + '</span>' + (res.result.status !== 'ok' ? '<br><small>' + esc(res.result.detail) + '</small>' : '');
              if (now) now.textContent = last.processed + ' of ' + last.total + ' rows loaded'
                + (last.total > last.processed ? ', about ' + Math.max(1, Math.round(per * (last.total - last.processed) / 60000)) + ' min left' : '');
              return step();
            });
          });
        }
        return step();
      }).then(null, function (e) { addMsg('bot', '⚠️ The load stopped: ' + e.message + '. Open it again from Load with a CI to resume.', false); });
    }
    function ciDone(l) {
      var d = l.done || {};
      addMsg('bot', '**' + l.file + '**: ' + (d.ok + d.warning) + ' of ' + l.total + ' rows saved through ' + l.ci
        + (d.warning ? ' (' + d.warning + ' with warnings)' : '') + (d.error ? ', **' + d.error + ' failed**: download the rows to fix, correct them and upload that file.' : '.')
        + (l.counts.problems ? ' ' + plural(l.counts.problems, 'row') + ' had problems and ' + (l.counts.problems === 1 ? 'was' : 'were') + ' not loaded.' : ''), false);
    }

    /* ＋ Features › Ask the data: answers from the user's curated PS Queries (named AI_...). Claude picks the query and
       its prompt values; this browser runs it with Query Viewer's Run to Excel link in the user's own session (PeopleSoft
       applies their row security); the service reads the file, Claude gets only counts and totals. */
    var queryFiles = {};       /* run_id -> PeopleSoft's Excel file, for Download Excel (memory only) */
    function qryUrl(action, q) {
      var m = location.pathname.match(/\/ps[cp]\/([^\/]+)\/([^\/]+)\/([^\/]+)\//);
      var site = m ? m[1].replace(/_newwin$/i, '').replace(/_\d+$/, '') : 'ps';
      return '/psc/' + site + '/' + (m ? m[2] : 'EMPLOYEE') + '/' + (m ? m[3] : 'HRMS') + '/q/?ICAction=' + action + '='
        + (q.private ? 'PRIVATE.' : 'PUBLIC.') + q.name
        + (q.params || []).map(function (x) { return '&' + encodeURIComponent(x[0]) + '=' + encodeURIComponent(x[1]); }).join('');
    }
    function blobB64(blob) {
      return new Promise(function (resolve, reject) {
        var fr = new FileReader();
        fr.onload = function () { resolve(String(fr.result).split(',')[1]); };
        fr.onerror = reject;
        fr.readAsDataURL(blob);
      });
    }
    function pageText(html) {   /* what PeopleSoft showed instead of the file (DOMParser runs no script, loads nothing) */
      var d = new DOMParser().parseFromString(String(html), 'text/html');
      [].forEach.call(d.querySelectorAll('script,style'), function (x) { x.remove(); });
      return (d.body ? d.body.textContent : '').replace(/\s+/g, ' ').trim().slice(0, 1500);
    }
    function saveBlob(blob, name) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    }
    function runQuery(q) {
      var feed = activity('Running ' + q.name + ' in PeopleSoft');
      var line = feed.add(q.title + (q.shown.length ? ': ' + q.shown.map(function (x) { return x.label + ' ' + x.value; }).join(', ') : ''));
      setStatus('working', 'Running ' + q.name);
      return fetch(qryUrl('ICQryNameExcelURL', q), { credentials: 'same-origin' }).then(function (r) {
        var ct = r.headers.get('content-type') || '';
        if (r.ok && /spreadsheet|ms-excel/i.test(ct)) {
          return r.blob().then(function (blob) {
            queryFiles[q.run_id] = { blob: blob, name: q.name + '.xlsx' };
            return blobB64(blob).then(function (b64) { return { data: b64 }; });
          });
        }
        return r.text().then(function (t) { return { error: pageText(t) || 'HTTP ' + r.status }; });
      }, function (e) { return { error: 'network error: ' + e.message }; }).then(function (out) {
        line.end(!out.error, out.error ? out.error.slice(0, 140) : null);
        feed.finish(out.error ? 'error' : 'success');
        var t = typing('Reading the answer...');
        out.session_id = state.sessionId;
        out.run_id = q.run_id;
        return api('/api/query/result', out).then(function (r) { t.remove(); setStatus('idle'); return handleReply(r); },
          function (e) { t.remove(); setStatus('error'); addMsg('bot', '⚠️ ' + e.message, false); });
      });
    }
    function queryChart(ch) {
      return '<div class="psa-qr-chart"><h5>' + esc(ch.value.charAt(0).toUpperCase() + ch.value.slice(1) + ' by ' + ch.by.join(' and ')) + '</h5>'
        + hbars(ch.rows.slice(0, 15), function (r) { return r.slice(0, -1).join(' · '); }, function (r) { return +r[r.length - 1] || 0; }, num)
        + (ch.rows.length > 15 || ch.more ? '<small>and ' + ((ch.more || 0) + Math.max(0, ch.rows.length - 15)) + ' more</small>' : '') + '</div>';
    }
    function rowsTable(x) {
      return x.rows.length ? '<div class="psa-ci-rows"><table><thead><tr>' + x.headings.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('')
          + '</tr></thead><tbody>' + x.rows.map(function (r) { return '<tr>' + r.map(function (v) { return '<td>' + esc(v) + '</td>'; }).join('') + '</tr>'; }).join('')
          + '</tbody></table></div>' + (x.count > x.rows.length ? '<small>The first ' + x.rows.length + ' of ' + x.count + ' rows: all of them are in the Excel file.</small>' : '')
        : '<p class="psa-sub">No rows.</p>';
    }
    function showQueryRows(x) {   /* the rows matching a filter ("who has role X"), from the whole result */
      card('<h4>' + esc(x.title) + ' <small class="psa-qr-name">' + esc(x.query) + '</small></h4>'
        + '<p class="psa-sub">' + x.filters.map(function (f) { return esc(f[0]) + ': <b>' + esc(f[1]) + '</b>'; }).join(' · ') + '</p>'
        + '<div class="psa-ci-chips"><span>' + plural(x.count, 'matching row') + '</span></div>' + rowsTable(x)).classList.add('psa-ci', 'psa-qr');
    }
    function showQueryResult(x, chart) {
      var c = card('<h4>' + esc(x.title) + ' <small class="psa-qr-name">' + esc(x.name) + (x.private ? ' · private' : '') + '</small></h4>'
        + (x.shown.length ? '<p class="psa-sub">' + x.shown.map(function (v) { return esc(v.label) + ': <b>' + esc(v.value) + '</b>'; }).join(' · ') + '</p>' : '')
        + '<div class="psa-ci-chips"><span>' + plural(x.count, 'row') + '</span></div>'
        + (chart ? queryChart(chart) : '')
        + rowsTable(x)
        + '<div class="psa-actions"><button type="button" class="psa-no" data-xl>Download Excel</button>'
        + '<a class="psa-no" target="_blank" rel="noopener" href="' + esc(qryUrl('ICQryNameURL', x)) + '">Open in Query Viewer</a></div>');
      c.classList.add('psa-ci', 'psa-qr');
      c.querySelector('[data-xl]').addEventListener('click', function () {
        var f = queryFiles[x.run_id];
        if (f) saveBlob(f.blob, f.name); else window.open(qryUrl('ICQryNameExcelURL', x), '_blank');
      });
    }
    function showQueries() {
      apiGet('/api/queries').then(function (r) {
        var qs = r.queries;
        var c = card('<h4>Ask the data</h4><p class="psa-sub">Ask in your own words, e.g. <i>"how many new hires this year by department?"</i> '
          + 'I answer only from these PS Queries (named ' + esc(r.prefix) + '...), run in PeopleSoft as you.</p>'
          + (qs.length ? '<ul class="psa-inc-list">' + qs.map(function (q, i) {
            return '<li><button type="button" data-q="' + i + '"' + (q.problem ? ' disabled' : '') + '><b>' + esc(q.title) + '</b><small>' + esc(q.name)
              + (q.private ? ' · private' : '') + (q.prompts.length ? ' · asks for ' + q.prompts.map(function (p) { return p.label; }).join(', ') : '') + '</small>'
              + (q.description ? '<span>' + esc(q.description) + '</span>' : '') + (q.problem ? '<span>⚠️ ' + esc(q.problem) + '</span>' : '') + '</button></li>';
          }).join('') + '</ul>'
            : '<p class="psa-sub">No ' + esc(r.prefix) + ' query is available to you yet. An administrator creates them in Query Manager, named '
              + esc(r.prefix) + '...; you see the ones your query security allows.</p>'));
        c.classList.add('psa-qr-list');
        c.addEventListener('click', function (e) {
          var b = e.target.closest && e.target.closest('[data-q]');
          if (b) { var q = qs[+b.getAttribute('data-q')]; send('Run ' + q.name + ' (' + q.title + ')'); }
        });
      }, function (e) { addMsg('bot', '⚠️ ' + e.message, false); });
    }

    /* ＋ Features › Audit (auditors): the admin dashboard, on the left beside the chat. Five tabs read /api/audit;
       the chat stays open on the right, so the auditor can ask about what they see. */
    var auditEl = null, audit = { tab: 'overview', days: 7, by: 'user', f: { kind: 'actions' }, events: [], more: false };
    var AU_TABS = [['overview', 'Overview'], ['usage', 'Usage & cost'], ['events', 'Audit log'], ['failures', 'Failures'], ['ops', 'Watcher & tests']];
    function money(x) { x = +x || 0; return '$' + (x >= 100 ? x.toFixed(0) : x >= 1 ? x.toFixed(2) : x.toFixed(x ? 3 : 2)); }
    function num(x) { return (+x || 0).toLocaleString(); }
    function openAudit(tab, filters) {
      if (!auditEl) {
        auditEl = document.createElement('div');
        auditEl.id = 'psa-audit';
        auditEl.setAttribute('role', 'region');
        auditEl.setAttribute('aria-label', 'Agent audit dashboard');
        auditEl.innerHTML = '<div class="psa-au-hdr"><div class="psa-au-title"><b>Audit</b><span>PeopleSoft Agent: usage, cost and the audit log</span></div>'
          + '<div class="psa-au-tabs" role="tablist">' + AU_TABS.map(function (t) {
            return '<button type="button" role="tab" data-tab="' + t[0] + '">' + t[1] + '</button>'; }).join('') + '</div>'
          + '<div class="psa-au-tools"><label>Period <select data-days><option value="1">Today</option><option value="7">7 days</option>'
          + '<option value="30">30 days</option><option value="90">90 days</option></select></label>'
          + '<button type="button" data-ask title="Ask the agent about this">Ask the agent</button>'
          + '<button type="button" data-refresh aria-label="Refresh" title="Refresh">&#x21bb;</button>'
          + '<button type="button" data-close aria-label="Close the dashboard" title="Close">&#x2715;</button></div></div>'
          + '<div class="psa-au-body" aria-live="polite"></div><div class="psa-au-tip" role="tooltip" hidden></div>';
        document.body.appendChild(auditEl);
        auditEl.querySelector('.psa-au-tabs').addEventListener('click', function (e) {
          var b = e.target.closest('[data-tab]');
          if (b) { audit.tab = b.getAttribute('data-tab'); loadAudit(); }
        });
        auditEl.querySelector('[data-days]').addEventListener('change', function (e) { audit.days = +e.target.value; loadAudit(); });
        auditEl.querySelector('[data-refresh]').addEventListener('click', function () { loadAudit(); });
        auditEl.querySelector('[data-close]').addEventListener('click', function () { auditEl.style.display = 'none'; });
        auditEl.querySelector('[data-ask]').addEventListener('click', function () {
          if (!panel.classList.contains('psa-open')) open();
          hint('e.g. Who spent the most this week? Why did DEMO1\'s runs fail?');
          input.focus();
        });
        /* bar tooltips: one shared box, positioned over the hovered mark */
        auditEl.addEventListener('mouseover', function (e) {
          var t = e.target.closest && e.target.closest('[data-tip]'), tip = auditEl.querySelector('.psa-au-tip');
          if (!t) { tip.hidden = true; return; }
          tip.textContent = t.getAttribute('data-tip');
          tip.hidden = false;
          var r = t.getBoundingClientRect(), box = auditEl.getBoundingClientRect();
          tip.style.left = Math.max(4, Math.min(box.width - tip.offsetWidth - 4, r.left - box.left + r.width / 2 - tip.offsetWidth / 2)) + 'px';
          tip.style.top = Math.max(4, r.top - box.top - tip.offsetHeight - 6) + 'px';
        });
      }
      if (tab) audit.tab = tab;
      if (filters) { audit.f = Object.assign({ kind: 'actions' }, filters); }
      if (auditEl.style.display !== 'flex' && panel.classList.contains('psa-open'))
        addMsg('bot', 'The audit dashboard is open on the left. Ask me about it here, e.g. *Who spent the most this week?* or *What failed for VELU yesterday?*', false);
      auditEl.style.display = 'flex';
      auditEl.querySelector('[data-days]').value = String(audit.days);
      loadAudit();
    }
    function auditQuery(extra) {
      var q = { tab: audit.tab, days: audit.days };
      if (audit.tab === 'usage') q.by = audit.by;
      if (audit.tab === 'events') Object.keys(audit.f).forEach(function (k) { if (audit.f[k]) q[k] = audit.f[k]; });
      Object.keys(extra || {}).forEach(function (k) { q[k] = extra[k]; });
      return Object.keys(q).map(function (k) { return k + '=' + encodeURIComponent(q[k]); }).join('&');
    }
    function loadAudit(more) {
      var body = auditEl.querySelector('.psa-au-body');
      [].forEach.call(auditEl.querySelectorAll('[data-tab]'), function (b) {
        var on = b.getAttribute('data-tab') === audit.tab;
        b.classList.toggle('psa-on', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      var before = more && audit.events.length ? audit.events[audit.events.length - 1].id : null;
      if (!more) body.innerHTML = '<p class="psa-au-wait">Loading...</p>';
      return apiGet('/api/audit?' + auditQuery(before ? { before: before } : null)).then(function (d) {
        if (audit.tab === 'events') {
          audit.events = more ? audit.events.concat(d.events) : d.events;
          audit.more = d.more;
          d.events = audit.events;
        }
        body.innerHTML = AU_VIEW[audit.tab](d);
        body.scrollTop = more ? body.scrollTop : 0;
        wireAudit(body, d);
      }, function (e) { body.innerHTML = '<p class="psa-au-err">⚠️ ' + esc(e.message) + '</p>'; });
    }
    /* one series: a single hue, thin bars with rounded tops on the baseline, a 2px gap, recessive grid */
    function barChart(points, valueOf, labelOf, tipOf) {
      var W = 640, H = 150, pad = 22, n = points.length, max = Math.max.apply(null, points.map(valueOf).concat([0])) || 1;
      var step = (W - pad) / n, bw = Math.max(2, Math.min(28, step - 2)), out = [];
      [0.5, 1].forEach(function (g) {
        var y = H - 18 - (H - 30) * g;
        out.push('<line x1="' + pad + '" x2="' + W + '" y1="' + y + '" y2="' + y + '" class="psa-au-grid"/>'
          + '<text x="0" y="' + (y + 4) + '" class="psa-au-ax">' + esc(money(max * g)) + '</text>');
      });
      out.push('<line x1="' + pad + '" x2="' + W + '" y1="' + (H - 18) + '" y2="' + (H - 18) + '" class="psa-au-base"/>');
      points.forEach(function (p, i) {
        var v = valueOf(p), h = v ? Math.max(2, (H - 30) * v / max) : 0, x = pad + i * step + (step - bw) / 2, y = H - 18 - h, r = Math.min(4, bw / 2, h);
        if (h) out.push('<path class="psa-au-bar" d="M' + x + ' ' + (H - 18) + 'V' + (y + r) + 'Q' + x + ' ' + y + ' ' + (x + r) + ' ' + y
          + 'H' + (x + bw - r) + 'Q' + (x + bw) + ' ' + y + ' ' + (x + bw) + ' ' + (y + r) + 'V' + (H - 18) + 'Z"/>');
        out.push('<rect class="psa-au-hit" x="' + (pad + i * step) + '" y="0" width="' + step + '" height="' + (H - 18) + '" data-tip="' + esc(tipOf(p)) + '"/>');
        if (n <= 10 || i % Math.ceil(n / 8) === 0 || i === n - 1)
          out.push('<text x="' + (x + bw / 2) + '" y="' + (H - 4) + '" class="psa-au-ax" text-anchor="middle">' + esc(labelOf(p)) + '</text>');
      });
      return '<svg viewBox="0 0 ' + W + ' ' + H + '" class="psa-au-chart" role="img" aria-label="' + esc(points.map(function (p) {
        return labelOf(p) + ' ' + money(valueOf(p)); }).join(', ')) + '">' + out.join('') + '</svg>';
    }
    function hbars(rows, label, value, fmt, attr) {
      var max = Math.max.apply(null, rows.map(value).concat([0])) || 1;
      return '<div class="psa-au-hbars">' + rows.map(function (r) {
        return '<div class="psa-au-hrow"' + (attr ? ' ' + attr(r) : '') + '><span class="psa-au-hl">' + esc(label(r)) + '</span>'
          + '<span class="psa-au-ht"><i style="width:' + Math.max(value(r) ? 1.5 : 0, 100 * value(r) / max) + '%"></i></span>'
          + '<span class="psa-au-hv">' + esc(fmt(value(r))) + '</span></div>';
      }).join('') + '</div>';
    }
    function kpi(label, value, sub, cls) {
      return '<div class="psa-au-kpi' + (cls ? ' ' + cls : '') + '"><span>' + esc(label) + '</span><b>' + esc(value) + '</b>' + (sub ? '<small>' + sub + '</small>' : '') + '</div>';
    }
    function shortDay(d) { var p = d.split('-'); return +p[2] + ' ' + ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+p[1] - 1]; }
    function period(d) { return d.from === d.to ? shortDay(d.to) : shortDay(d.from) + ' to ' + shortDay(d.to); }
    function outcome(o) {
      return o === 'ok' ? '<span class="psa-au-ok">✓ OK</span>' : o === 'failed' || o === 'error' ? '<span class="psa-au-bad">✕ ' + (o === 'error' ? 'Error' : 'Failed') + '</span>'
        : o === 'alert' ? '<span class="psa-au-warn">! Alert</span>' : o === 'denied' ? '<span class="psa-au-bad">⛔ Refused</span>'
        : '<span class="psa-au-muted">' + esc(o) + '</span>';
    }
    function eventRows(list) {
      if (!list.length) return '<p class="psa-au-muted">Nothing recorded for this period and filter.</p>';
      return '<table class="psa-au-table psa-au-log"><thead><tr><th>Time</th><th>User</th><th>Feature</th><th>What happened</th><th>Outcome</th></tr></thead><tbody>'
        + list.map(function (e, i) {
          return '<tr data-ev="' + i + '" tabindex="0"><td class="psa-au-nowrap">' + esc(e.ts.slice(5, 16)) + '</td><td>' + esc(e.oprid) + '</td><td>'
            + esc(e.feature_label) + '</td><td><b>' + esc(e.event) + '</b> ' + esc(e.detail || '') + (e.ref ? ' <span class="psa-au-muted">' + esc(e.ref) + '</span>' : '')
            + '</td><td>' + outcome(e.outcome) + '</td></tr>';
        }).join('') + '</tbody></table>';
    }
    function eventDetail(e) {
      var d = e.data || {}, out = [];
      if (d.sql) out.push('<pre>' + esc(d.sql) + '</pre>');
      if (d.values) out.push('<div>' + d.values.map(function (v) { return esc(v.label) + ' = <b>' + esc(v.value) + '</b>'; }).join('<br>') + '</div>');
      if (d.error) out.push('<div class="psa-au-bad">' + esc(d.error) + '</div>');
      var rest = Object.keys(d).filter(function (k) { return ['sql', 'values', 'error'].indexOf(k) < 0; });
      if (rest.length) out.push('<pre>' + esc(JSON.stringify(rest.reduce(function (o, k) { o[k] = d[k]; return o; }, {}), null, 1)) + '</pre>');
      var facts = [];
      if (e.n !== null && e.n !== undefined) facts.push(num(e.n) + ' rows');
      if (e.ms !== null && e.ms !== undefined) facts.push((e.ms / 1000).toFixed(1) + ' s');
      if (e.model) facts.push(e.model + ': ' + num(e.tin) + ' in, ' + num(e.tout) + ' out, ' + num(e.tcr) + ' cached, ' + money(e.cost));
      if (e.session) facts.push('conversation ' + e.session.slice(0, 8));
      if (facts.length) out.push('<div class="psa-au-muted">' + esc(facts.join(' · ')) + '</div>');
      return out.join('') || '<div class="psa-au-muted">No more details.</div>';
    }
    var AU_VIEW = {
      overview: function (d) {
        var t = d.totals, pct = d.cost_before ? Math.round((t.cost - d.cost_before) / d.cost_before * 100) : null, m = d.month, h = [];
        h.push('<p class="psa-au-period">' + esc(period(d)) + '</p><div class="psa-au-kpis">'
          + kpi('People', num(t.users), plural(t.conversations, 'conversation'))
          + kpi('Messages', num(t.messages), num(t.claude_calls) + ' Claude calls')
          + kpi('Task runs', num(t.runs), num(t.runs_ok) + ' succeeded' + (t.runs_failed ? ', <span class="psa-au-bad">' + num(t.runs_failed) + ' failed</span>' : ''))
          + kpi('Claude cost', money(t.cost), pct === null ? 'estimate from tokens' : (pct >= 0 ? '+' : '') + pct + '% on the period before')
          + kpi('Failures', num(t.failures), 'runs, errors, failed steps', t.failures ? 'psa-au-kbad' : '')
          + kpi('Refused', num(t.denied), 'attempts without the role or right', t.denied ? 'psa-au-kbad' : '')
          + kpi('Watcher alerts', num(t.incidents), plural(t.test_runs, 'regression run') + (t.test_runs ? ', ' + num(t.test_runs_ok) + ' all passed' : ''))
          + '</div>');
        if (m.budget) {
          var share = Math.min(1, m.share || 0), cls = m.share >= 1 ? 'psa-au-mbad' : m.share >= 0.8 ? 'psa-au-mwarn' : '';
          h.push('<div class="psa-au-meter ' + cls + '"><div><b>' + esc(m.name) + '</b>: ' + money(m.cost) + ' of the ' + money(m.budget) + ' budget ('
            + Math.round((m.share || 0) * 100) + '%)' + (m.share >= 1 ? ' <span class="psa-au-bad">✕ over budget</span>' : m.share >= 0.8 ? ' <span class="psa-au-warn">! over 80%</span>' : '')
            + '</div><span role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + Math.round(share * 100) + '"><i style="width:' + share * 100 + '%"></i></span></div>');
        } else {
          h.push('<p class="psa-au-muted">' + esc(m.name) + ' so far: ' + money(m.cost) + '. Set PSAGENT_BUDGET for a monthly budget and alerts.</p>');
        }
        h.push('<div class="psa-au-sec"><h5>Claude cost per day</h5>' + (d.daily.length > 1 ? barChart(d.daily, function (p) { return p.cost; },
          function (p) { return shortDay(p.day); },
          function (p) { return shortDay(p.day) + ': ' + money(p.cost) + ', ' + p.messages + ' messages, ' + p.runs + ' task runs' + (p.failures ? ', ' + p.failures + ' failures' : ''); })
          : '<p class="psa-au-muted">Pick 7 days or more to see the trend.</p>') + '</div>');
        h.push('<div class="psa-au-cols"><div class="psa-au-sec"><h5>Cost by feature</h5>' + (d.features.length ? hbars(d.features, function (f) { return f.label; },
          function (f) { return f.cost; }, money) : '<p class="psa-au-muted">No Claude calls yet.</p>') + '</div>'
          + '<div class="psa-au-sec"><h5>Top users</h5>' + (d.top_users.length ? hbars(d.top_users, function (u) { return u.oprid; }, function (u) { return u.cost; },
            money, function (u) { return 'data-user="' + esc(u.oprid) + '" tabindex="0" title="Show what ' + esc(u.oprid) + ' did"'; }) : '<p class="psa-au-muted">No one yet.</p>') + '</div></div>');
        return h.join('');
      },
      usage: function (d) {
        var rows = d.rows.filter(function (r) { return r.cost || r.messages || r.runs || r.calls; });
        return '<div class="psa-au-bar2"><span class="psa-au-seg" role="group" aria-label="Group by">'
          + '<button type="button" data-by="user"' + (d.by === 'user' ? ' class="psa-on"' : '') + '>By user</button>'
          + '<button type="button" data-by="feature"' + (d.by === 'feature' ? ' class="psa-on"' : '') + '>By feature</button></span>'
          + '<span class="psa-au-period">' + esc(period(d)) + ': ' + money(d.total) + '</span><button type="button" class="psa-au-xl" data-xl="usage">Download Excel</button></div>'
          + (rows.length ? '<table class="psa-au-table"><thead><tr><th>' + (d.by === 'user' ? 'User' : 'Feature') + '</th><th class="psa-au-wide">Cost</th>'
            + '<th>Messages</th><th>Conversations</th><th>Claude calls</th><th>Per conversation</th><th>Task runs</th><th>Failures</th><th>Queries</th></tr></thead><tbody>'
            + rows.map(function (r) {
              var max = rows[0].cost || 1;
              return '<tr' + (d.by === 'user' ? ' data-user="' + esc(r.k) + '" tabindex="0" title="Show what ' + esc(r.k) + ' did"' : ' data-feature="' + esc(r.k) + '" tabindex="0"') + '><td><b>'
                + esc(r.label) + '</b>' + (r.system ? ' <span class="psa-au-muted">(the service)</span>' : '') + '</td><td class="psa-au-wide"><span class="psa-au-ht"><i style="width:'
                + Math.max(r.cost ? 1.5 : 0, 100 * r.cost / max) + '%"></i></span> ' + money(r.cost) + '</td><td>' + num(r.messages) + '</td><td>' + num(r.conversations) + '</td><td>'
                + num(r.calls) + '</td><td>' + (r.per_conversation === null ? '' : money(r.per_conversation)) + '</td><td>' + num(r.runs) + '</td><td>'
                + (r.failures ? '<span class="psa-au-bad">' + num(r.failures) + '</span>' : '0') + '</td><td>' + num(r.queries) + '</td></tr>';
            }).join('') + '</tbody></table><p class="psa-au-muted">Costs are estimates from the tokens of each Claude call at list prices; the Anthropic invoice is the real figure.</p>'
            : '<p class="psa-au-muted">No usage in this period.</p>');
      },
      events: function (d) {
        var f = audit.f, opt = function (v, t, cur) { return '<option value="' + esc(v) + '"' + (v === (cur || '') ? ' selected' : '') + '>' + esc(t) + '</option>'; };
        return '<form class="psa-au-filters" data-filters>'
          + '<label>User <select name="user">' + opt('', 'Everyone', f.user) + d.users.map(function (u) { return opt(u, u, f.user); }).join('') + '</select></label>'
          + '<label>Feature <select name="feature">' + opt('', 'All', f.feature) + Object.keys(d.features).map(function (k) { return opt(k, d.features[k], f.feature); }).join('') + '</select></label>'
          + '<label>Show <select name="kind">' + opt('actions', 'Actions', f.kind) + opt('queries', 'Database queries', f.kind) + opt('claude', 'Claude calls', f.kind) + opt('all', 'Everything', f.kind) + '</select></label>'
          + '<label>Outcome <select name="outcome">' + opt('', 'Any', f.outcome) + opt('failed', 'Failed', f.outcome) + opt('denied', 'Refused', f.outcome) + '</select></label>'
          + '<label>From <input type="date" name="since" value="' + esc(f.since || '') + '"></label><label>To <input type="date" name="until" value="' + esc(f.until || '') + '"></label>'
          + '<label class="psa-au-q">Search <input type="search" name="q" value="' + esc(f.q || '') + '" placeholder="process, user, SQL..."></label>'
          + '<button type="submit">Apply</button><button type="button" class="psa-au-xl" data-xl="events">Download Excel</button></form>'
          + eventRows(d.events) + (audit.more ? '<button type="button" class="psa-au-more" data-more>Load more</button>' : '')
          + '<p class="psa-au-muted">What users type in the chat is not kept, only what the agent did. Passwords never reach the service.</p>';
      },
      failures: function (d) {
        return '<p class="psa-au-period">' + esc(period(d)) + ': ' + d.failures.length + (d.failures.length === 1 ? ' failure' : ' failures') + '</p>'
          + (d.by_feature.length ? '<div class="psa-au-chips">' + d.by_feature.map(function (x) { return '<span>' + esc(x[0]) + ' <b>' + x[1] + '</b></span>'; }).join('') + '</div>' : '')
          + eventRows(d.failures);
      },
      ops: function (d) {
        var h = ['<div class="psa-au-cols"><div class="psa-au-sec"><h5>Watcher</h5>'];
        if (!d.watcher) h.push('<p class="psa-au-muted">The watcher is not running on this agent service.</p>');
        else {
          var c = d.watcher.counts || {};
          h.push('<p>' + (c.stopped ? '<span class="psa-au-warn">⏸ Watching is stopped</span>' : '◉ ' + num(c.active) + ' of ' + num(c.watchdogs) + ' watchdogs active')
            + ' in ' + num(d.watcher.plans) + ' plans.</p>' + (d.watcher.incidents.length ? '<ul class="psa-au-list">' + d.watcher.incidents.map(function (i) {
              return '<li><b>' + esc(i.title) + '</b> <span class="psa-au-muted">' + esc(i.found || '') + '</span><br>' + esc(i.summary || i.note || '') + '</li>'; }).join('') + '</ul>'
              : '<p class="psa-au-muted">No alerts in this period.</p>'));
        }
        h.push('</div><div class="psa-au-sec"><h5>Regression tests</h5>');
        if (!d.regression) h.push('<p class="psa-au-muted">Regression tests are off here (only on test environments).</p>');
        else h.push('<p>' + num(d.regression.suites) + ' suites.</p>' + (d.regression.runs.length ? '<ul class="psa-au-list">' + d.regression.runs.map(function (r) {
          var bad = r.counts && r.counts.failed;
          return '<li>' + (bad ? '<span class="psa-au-bad">✕</span>' : '<span class="psa-au-ok">✓</span>') + ' <b>' + esc(r.title) + '</b>: ' + esc(r.headline)
            + ' <span class="psa-au-muted">' + esc((r.started || '').slice(0, 16)) + ', ' + esc(r.mode === 'server' ? 'unattended' : r.by || '') + '</span>'
            + (r.newly_failing && r.newly_failing.length ? '<br><span class="psa-au-bad">Failing since the last run: ' + esc(r.newly_failing.join(', ')) + '</span>' : '') + '</li>';
        }).join('') + '</ul>' : '<p class="psa-au-muted">No runs yet.</p>'));
        h.push('</div></div>');
        return h.join('');
      }
    };
    function wireAudit(body, d) {
      body.onclick = function (e) {
        var x = e.target.closest ? e.target : null;
        if (!x) return;
        var u = x.closest('[data-user]'), fe = x.closest('[data-feature]'), by = x.closest('[data-by]'), ev = x.closest('[data-ev]');
        if (x.closest('[data-more]')) return loadAudit(true);
        if (x.closest('[data-xl]')) {
          var b = x.closest('[data-xl]');
          b.disabled = true;
          return download('/api/audit/export?' + auditQuery({ tab: b.getAttribute('data-xl') })).then(function (name) {
            b.disabled = false; b.textContent = 'Downloaded ' + name; }, function (err) { b.disabled = false; b.textContent = '⚠️ ' + err.message; });
        }
        if (by) { audit.by = by.getAttribute('data-by'); return loadAudit(); }
        if (u) { audit.tab = 'events'; audit.f = { kind: 'actions', user: u.getAttribute('data-user') }; return loadAudit(); }
        if (fe) { audit.tab = 'events'; audit.f = { kind: 'all', feature: fe.getAttribute('data-feature') }; return loadAudit(); }
        if (ev) {
          var next = ev.nextElementSibling;
          if (next && next.classList.contains('psa-au-detail')) { next.remove(); return; }
          var list = audit.tab === 'events' ? audit.events : d.failures, tr = document.createElement('tr');
          tr.className = 'psa-au-detail';
          tr.innerHTML = '<td colspan="5">' + eventDetail(list[+ev.getAttribute('data-ev')]) + '</td>';
          ev.parentNode.insertBefore(tr, ev.nextSibling);
        }
      };
      body.onkeydown = function (e) { if (e.key === 'Enter' && e.target.matches('[data-ev],[data-user],[data-feature]')) { e.preventDefault(); e.target.click(); } };
      var form = body.querySelector('[data-filters]');
      if (form) form.addEventListener('submit', function (e) {
        e.preventDefault();
        audit.f = {};
        [].forEach.call(form.elements, function (el) { if (el.name) audit.f[el.name] = el.value; });
        loadAudit();
      });
    }

    /* ◉ N watchdogs: the count in the feature bar, and the manager it opens */
    var dogsTimer = null, managerCard = null;
    function setDogs(n) {
      var b = panel.querySelector('#psa-dogs');
      if (!n || !(me && me.can_monitor && me.watcher)) { b.hidden = true; return; }
      b.hidden = false;
      b.className = n.stopped ? 'psa-off' : n.alerts ? 'psa-alert' : '';
      b.textContent = n.stopped ? '⏸ Watching stopped' : '◉ ' + plural(n.watchdogs, 'watchdog')
        + (n.active < n.watchdogs ? ' (' + n.active + ' active)' : '') + (n.alerts ? ' · ' + plural(n.alerts, 'alert') : '');
    }
    function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
    function refreshDogs() {
      if (!(me && me.can_monitor && me.watcher)) return;
      apiGet('/api/watchdogs').then(function (v) { setDogs(v.counts); if (managerCard && document.body.contains(managerCard)) renderManager(v); }, function () {});
    }
    function showManager() {
      apiGet('/api/watchdogs').then(function (v) {
        if (managerCard) managerCard.remove();
        managerCard = card('');
        managerCard.classList.add('psa-mgr');
        renderManager(v);
        managerCard.scrollIntoView({ block: 'nearest' });
      }, function (e) { addMsg('bot', '⚠️ ' + e.message, false); });
    }
    function renderManager(v) {
      var n = v.counts || {}, c = managerCard;
      setDogs(n);
      var plans = (v.plans || []).map(function (p) {
        var allPaused = p.watchdogs.every(function (d) { return d.paused; });
        return '<div class="psa-plan"><div class="psa-plan-h"><b>' + esc(p.title) + '</b><small>' + plural(p.watchdogs.length, 'watchdog')
          + (p.by ? ' · by ' + esc(p.by) + (p.on ? ' on ' + esc(p.on) : '') : '') + '</small>'
          + '<button type="button" class="psa-mini" data-dl="' + esc(p.id) + '">Download as Excel</button>'
          + '<button type="button" class="psa-mini" data-act="' + (allPaused ? 'resume_plan' : 'pause_plan') + '" data-plan="' + esc(p.id) + '">' + (allPaused ? 'Resume all' : 'Pause all') + '</button>'
          + '<button type="button" class="psa-mini psa-danger" data-act="delete_plan" data-plan="' + esc(p.id) + '" data-ask="Delete the plan ' + esc(p.title) + '?">Delete plan</button></div>'
          + p.watchdogs.map(function (d) {
            return '<div class="psa-dog' + (d.paused ? ' psa-paused' : d.failing ? ' psa-failing' : '') + '"><span class="psa-dot"></span><div class="psa-dog-b"><b>' + esc(d.title) + '</b>'
              + (d.paused ? ' <small style="display:inline">(paused)</small>' : '') + '<small>' + esc(d.what) + '</small><small>' + esc(d.when)
              + ' · last run ' + esc(d.last_run || 'not yet') + (d.notify && d.notify.length ? ' · ' + esc(d.notify.join(', ')) : '') + '</small>'
              + (d.last_incident ? '<small>Last alert: <a data-inc="' + esc(d.last_incident.id) + '">' + esc(d.last_incident.title) + '</a> · '
                + esc(String(d.last_incident.found).slice(5, 16)) + '</small>' : '') + '</div><div class="psa-dog-a">'
              + '<button type="button" class="psa-mini" data-act="' + (d.paused ? 'resume' : 'pause') + '" data-id="' + esc(d.id) + '">' + (d.paused ? 'Resume' : 'Pause') + '</button>'
              + '<button type="button" class="psa-mini" data-act="run" data-id="' + esc(d.id) + '">Run now</button>'
              + '<button type="button" class="psa-mini psa-danger" data-act="delete" data-id="' + esc(d.id) + '" data-ask="Delete ' + esc(d.title) + '?">Delete</button></div></div>';
          }).join('') + '</div>';
      }).join('');
      c.innerHTML = '<h4>Watchdogs</h4><div class="psa-diag-top"><small>' + plural(n.watchdogs || 0, 'watchdog') + ' in ' + plural(n.plans || 0, 'plan') + ' · '
        + (n.active || 0) + ' active · ' + plural(n.alerts || 0, 'alert') + ' in 24 hours' + (n.stopped ? ' · <b style="color:#92400e">watching is stopped</b>' : '') + '</small>'
        + '<button type="button" class="psa-mini' + (n.stopped ? '' : ' psa-danger') + '" data-act="' + (n.stopped ? 'start' : 'stop') + '"'
        + (n.stopped ? '' : ' data-ask="Stop all watching? Nothing runs until you start it again."') + '>' + (n.stopped ? 'Start watching' : 'Stop all watching') + '</button></div>'
        + (plans || '<p>No watchdogs yet. Choose <b>＋ Features › Watch jobs</b> to upload a watch plan.</p>')
        + ((v.history || []).length ? '<details><summary>Recent changes</summary><ul>' + v.history.map(function (h) {
          return '<li><small>' + esc(h.at) + ' · ' + esc(h.who || '?') + '</small> ' + esc(h.what) + '</li>'; }).join('') + '</ul></details>' : '')
        + '<small>To change a plan: Download as Excel, edit it, and upload it again with ＋ Features › Watch jobs; I show what changes before you confirm.</small>';
      c.onclick = function (e) {
        var t = e.target, a = t.closest && t.closest('[data-act],[data-dl],[data-inc]');
        if (!a) return;
        if (a.hasAttribute('data-inc')) return openIncident(a.getAttribute('data-inc'));
        if (a.hasAttribute('data-dl')) return download('/api/watchplans/export?plan=' + encodeURIComponent(a.getAttribute('data-dl'))).then(
          function (name) { a.textContent = 'Downloaded ' + name; }, function (err) { addMsg('bot', '⚠️ ' + err.message, false); });
        var ask = a.getAttribute('data-ask');
        if (ask && !a.hasAttribute('data-sure')) {          /* ask once, on the button itself */
          a.setAttribute('data-sure', '1');
          a.textContent = ask.replace(/\?$/, '') + ': click again';
          setTimeout(function () { if (document.body.contains(a)) { a.removeAttribute('data-sure'); refreshDogs(); } }, 5000);
          return;
        }
        var act = a.getAttribute('data-act'), body = { action: act, id: a.getAttribute('data-id') || '', plan: a.getAttribute('data-plan') || '' };
        [].forEach.call(c.querySelectorAll('button'), function (b) { b.disabled = true; });
        if (act === 'run') a.textContent = 'Running...';
        api('/api/watchdogs/action', body).then(function (r) {
          renderManager(r);
          if (act === 'run') {
            if (r.ran && r.ran.length) r.ran.forEach(showIncident);
            else addMsg('bot', 'Ran it now: nothing to report.', false);
          }
        }, function (err) { addMsg('bot', '⚠️ ' + err.message, false); refreshDogs(); });
      };
    }

    /* Regression tests: the smoke test, the saved suites (run here or unattended on the service), the last runs */
    var VERD = { passed: ['Passed', '#065f46', '#d1fae5', '✔'], changed: ['Passed with changes', '#92400e', '#fef3c7', '◐'],
      failed: ['Failed', '#b91c1c', '#fee2e2', '✖'], stopped: ['Stopped', '#475569', '#f1f5f9', '■'] };
    function verdictTag(v) { var x = VERD[v] || VERD.stopped; return '<span class="psa-badge" style="color:' + x[1] + ';background:' + x[2] + '">' + x[0] + '</span>'; }
    function showRegress() {
      apiGet('/api/regress').then(function (h) {
        var u = h.unattended || {}, sm = h.smoke || {};
        var last = function (l) { return l ? '<small>Last run ' + esc(String(l.started).slice(5, 16)) + (l.mode === 'server' ? ' (unattended)' : '') + ': '
          + '<a data-rep="' + esc(l.run_id) + '">' + esc(l.headline) + '</a></small>' : '<small>Not run yet</small>'; };
        var runBtns = function (key, val) {
          return '<button type="button" class="psa-mini" data-run="' + esc(val) + '" data-k="' + key + '">Run here</button>'
            + (u.available ? '<button type="button" class="psa-mini" data-una="' + esc(val) + '" data-k="' + key + '">Run unattended</button>' : '');
        };
        var c = card('<h4>Regression tests</h4><small>Each case runs a recorded task with set values and checks the result. '
          + '<b>Test environment:</b> the cases really save data.</small>'
          + '<div class="psa-plan"><div class="psa-plan-h"><b>Smoke test</b><small>' + plural(sm.cases || 0, 'case') + ': every task once, with its defaults</small>'
          + runBtns('smoke', 'smoke_test') + '<button type="button" class="psa-mini" data-dl="smoke_test">Download as Excel</button></div>'
          + '<div class="psa-dog">' + last(sm.last) + ((sm.skipped || []).length ? '<small>Not in the smoke test: ' + sm.skipped.map(esc).join('; ') + '</small>' : '') + '</div></div>'
          + (h.suites || []).map(function (x) {
            return '<div class="psa-plan"><div class="psa-plan-h"><b>' + esc(x.title) + '</b><small>' + plural(x.cases, 'case')
              + (x.run_at ? ' · unattended daily at ' + esc(x.run_at) : '') + (x.after_patch ? ' · after every patch' : '') + '</small>'
              + runBtns('suite', x.id) + '<button type="button" class="psa-mini" data-dl="' + esc(x.id) + '">Download as Excel</button>'
              + '<button type="button" class="psa-mini psa-danger" data-del="' + esc(x.id) + '">Delete</button></div>'
              + '<div class="psa-dog">' + last(x.last) + '</div></div>';
          }).join('')
          + (u.available ? '<small>Unattended runs sign in as <b>' + esc(u.user) + '</b> in a browser on the agent service and email the report.'
            + (u.busy ? ' A run is in progress (' + esc(u.busy) + ').' : '') + '</small>' : '<small>Unattended runs are off on the agent service (PSAGENT_TEST_USER, Playwright).</small>')
          + '<div class="psa-actions"><button type="button" class="psa-ok" data-up>Upload a test plan</button><button type="button" class="psa-no" data-tpl>Download the test plan template</button></div>');
        c.classList.add('psa-guide');
        c.addEventListener('click', function (e) {
          var b = e.target.closest && e.target.closest('button,a');
          if (!b) return;
          if (b.hasAttribute('data-up')) { uploadAs = 'Build a regression test suite from this test plan'; panel.querySelector('#psa-file').click(); }
          else if (b.hasAttribute('data-tpl')) download('/api/templates/regression').then(function (n) { b.textContent = 'Downloaded ' + n; }, function (er) { addMsg('bot', '⚠️ ' + er.message, false); });
          else if (b.hasAttribute('data-dl')) download('/api/regress/export?suite=' + encodeURIComponent(b.getAttribute('data-dl'))).then(function (n) { b.textContent = 'Downloaded ' + n; }, function (er) { addMsg('bot', '⚠️ ' + er.message, false); });
          else if (b.hasAttribute('data-rep')) apiGet('/api/regress/report?run=' + encodeURIComponent(b.getAttribute('data-rep'))).then(function (r) { showTestReport(r.report); });
          else if (b.hasAttribute('data-run')) startTestRun(b.getAttribute('data-k') === 'smoke' ? { smoke: true } : { suite: b.getAttribute('data-run') });
          else if (b.hasAttribute('data-una')) {
            b.disabled = true;
            api('/api/regress/unattended', b.getAttribute('data-k') === 'smoke' ? { smoke: true } : { suite: b.getAttribute('data-una') }).then(function (r) {
              b.textContent = 'Started';
              addMsg('bot', 'Started an unattended run (`' + r.run_id + '`) as **' + r.user + '** on the agent service. The report is emailed and shows here under Regression tests when it ends.');
            }, function (er) { b.disabled = false; addMsg('bot', '⚠️ ' + er.message, false); });
          } else if (b.hasAttribute('data-del')) {
            if (!b.hasAttribute('data-sure')) { b.setAttribute('data-sure', '1'); b.textContent = 'Delete: click again'; return; }
            api('/api/regress/delete', { suite: b.getAttribute('data-del') }).then(function () { c.remove(); showRegress(); }, function (er) { addMsg('bot', '⚠️ ' + er.message, false); });
          }
        });
      }, function (e) { addMsg('bot', '⚠️ ' + e.message, false); });
    }

    function showTestSuite(ts) {
      if (!panel.classList.contains('psa-wide')) setWide(true);
      var ch = ts.changes, list = function (title, items) { return items.length ? '<li><b>' + title + ':</b> ' + items.map(esc).join(', ') + '</li>' : ''; };
      var rows = (ts.cases || []).map(function (x) {
        return '<tr><td>' + x.n + '</td><td><b>' + esc(x.title) + '</b>' + ((x.problems || []).length ? '<small style="color:#b91c1c">' + x.problems.map(esc).join('<br>') + '</small>' : '')
          + '</td><td>' + esc(x.task_title || x.task) + '</td><td><small>' + (x.values || []).map(function (v) { return esc(v.label) + ' = <code>' + esc(v.value) + '</code>'; }).join('<br>')
          + '</small></td><td>' + esc(x.expect) + '</td></tr>';
      }).join('');
      var c = card('<h4>Test suite: ' + esc(ts.title) + '</h4>' + (ts.source ? '<small>From ' + esc(ts.source) + '</small>' : '')
        + ((ts.problems || []).length ? '<div class="psa-rb-warn" style="background:#fef2f2;color:#7f1d1d"><b>Problems I could not fix</b><ul>'
          + ts.problems.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + (ch ? '<div class="psa-chg"><b>This replaces the saved suite ' + esc(ts.title) + '</b><ul>' + list('Added', ch.added) + list('Removed', ch.removed)
          + list('Changed', ch.changed) + '<li>' + ch.unchanged + ' unchanged</li></ul></div>' : '')
        + ((ts.questions || []).length ? '<div class="psa-rb-warn" style="background:#fffbeb;color:#78350f"><b>Please check</b><ul>'
          + ts.questions.map(function (x) { return '<li>' + md(x) + '</li>'; }).join('') + '</ul></div>' : '')
        + '<table class="psa-mon-t psa-wp"><tr><th>#</th><th>Test case</th><th>Task</th><th>Values</th><th>Expected</th></tr>' + rows + '</table>'
        + '<small>' + [ts.run_at ? 'Unattended every day at ' + esc(ts.run_at) : '', ts.after_patch ? 'unattended after every patch' : '',
          (ts.notify || []).length ? 'report to ' + esc(ts.notify.join(', ')) : ''].filter(Boolean).join(' · ')
        + ((ts.run_at || ts.after_patch) && !ts.unattended ? ' (the headless runner is not set up on the agent service yet)' : '') + '</small>'
        + '<div class="psa-actions"><button type="button" class="psa-ok" data-go>Save test suite</button><button type="button" class="psa-no" data-x>Discard</button></div>');
      var go = c.querySelector('[data-go]');
      c.querySelector('[data-x]').addEventListener('click', function () { disable(c); addMsg('bot', 'Test suite discarded.', false); });
      go.addEventListener('click', function () {
        go.disabled = true;
        api('/api/regress/save', { session_id: state.sessionId, draft_id: ts.draft_id }).then(function (r) {
          disable(c);
          go.textContent = 'Saved';
          var m = card('<h4>Saved ' + esc(r.title) + '</h4><small>' + plural(r.cases, 'case') + ' in <code>' + esc(r.path) + '</code>.</small>'
            + '<div class="psa-actions"><button type="button" class="psa-ok">Run it now</button><button type="button" class="psa-no">Later</button></div>');
          m.querySelector('.psa-ok').addEventListener('click', function () { disable(m); startTestRun({ suite: r.suite_id }); });
          m.querySelector('.psa-no').addEventListener('click', function () { disable(m); });
        }, function (e) { go.disabled = false; addMsg('bot', '⚠️ ' + e.message, false); });
      });
    }

    function startTestRun(which) {
      api('/api/regress/start', which).then(function (st) {
        var sec = (st.secrets || []).map(function (x) {
          return '<label>' + esc(x.label) + ' for the cases<input type="password" autocomplete="new-password" data-sec="' + esc(x.name) + '"></label>';
        }).join('');
        var c = card('<h4>Run ' + esc(st.title) + '</h4><small>' + plural(st.cases.length, 'case') + ', run ' + esc(st.n) + ' ({{n}} = ' + esc(st.n) + '). '
          + 'This is a test environment: the cases really save data.</small>' + sec
          + (sec ? '<small>Typed once for the whole run; it goes straight into PeopleSoft, I never see it.</small>' : '')
          + '<div class="psa-actions"><button type="button" class="psa-ok" data-go>Start</button><button type="button" class="psa-no" data-x>Cancel</button></div>');
        c.querySelector('[data-x]').addEventListener('click', function () { disable(c); api('/api/regress/stop', { run_id: st.run_id }); });
        c.querySelector('[data-go]').addEventListener('click', function () {
          var secrets = {};
          [].forEach.call(c.querySelectorAll('[data-sec]'), function (i) { secrets[i.getAttribute('data-sec')] = i.value; i.value = ''; });
          disable(c);
          runTestsHere(st, secrets);
        });
      }, function (e) { addMsg('bot', '⚠️ ' + e.message, false); });
    }

    function runTestsHere(st, secrets) {
      var ws = null, items = {};
      var c = card('<h4>Running ' + esc(st.title) + '</h4><ul class="psa-rt">' + st.cases.map(function (x) {
        return '<li data-n="' + x.n + '"><span class="psa-rt-i">○</span><b>' + esc(x.title) + '</b> <small>' + esc(x.task) + ' · expects ' + esc(x.expect) + '</small><div class="psa-rt-d"></div></li>';
      }).join('') + '</ul><small class="psa-rt-now"></small><div class="psa-actions"><button type="button" class="psa-no" data-stop>Stop the run</button></div>');
      [].forEach.call(c.querySelectorAll('[data-n]'), function (li) { items[li.getAttribute('data-n')] = li; });
      var now = c.querySelector('.psa-rt-now');
      c.querySelector('[data-stop]').addEventListener('click', function (e) { e.target.disabled = true; e.target.textContent = 'Stopping after this step...'; if (ws) ws.stopped = true; });
      setStatus('working', 'Regression run');
      suiteLoop(st.run_id, secrets, {
        workspace: function (w) { ws = w; },
        caseStart: function (x) { var li = items[x.n]; if (li) { li.querySelector('.psa-rt-i').textContent = '◌'; li.classList.add('psa-rt-on'); } },
        step: function (t) { now.textContent = t; setStatus('working', t); },
        caseEnd: function (x, v) {
          var li = items[x.n];
          if (!li || !v) return;
          var m = VERD[v.verdict] || VERD.stopped;
          li.classList.remove('psa-rt-on');
          li.querySelector('.psa-rt-i').textContent = m[3];
          li.querySelector('.psa-rt-i').style.color = m[1];
          li.querySelector('.psa-rt-d').innerHTML = '<small>' + esc(v.detail || '') + '</small>';
        }
      }).then(function (report) {
        secrets = null;
        disable(c);
        c.querySelector('.psa-actions').remove();
        now.textContent = '';
        setStatus(report && report.counts && report.counts.failed ? 'error' : 'idle');
        if (report) showTestReport(report);
      }, function (e) {
        secrets = null;
        setStatus('error');
        addMsg('bot', '⚠️ The regression run stopped: ' + e.message, false);
      });
    }

    function showTestReport(rep) {
      if (!panel.classList.contains('psa-wide')) setWide(true);
      var rows = (rep.results || []).map(function (r) {
        var prev = r.previous ? (VERD[r.previous] || VERD.stopped)[0] : '';
        return '<tr><td>' + r.n + '</td><td><b>' + esc(r.title) + '</b><small>' + esc(r.task_title) + ' · expects ' + esc(r.expect) + '</small></td><td>' + verdictTag(r.verdict)
          + (prev && r.previous !== r.verdict ? '<small>was ' + esc(prev) + '</small>' : '') + '</td><td><small>' + esc(r.detail || '') + '</small>'
          + ((r.changes || []).length ? '<details><summary>What changed</summary><ul>' + r.changes.map(function (x) { return '<li><small>' + esc(x) + '</small></li>'; }).join('') + '</ul></details>' : '')
          + '</td></tr>';
      }).join('');
      var c = card('<h4>' + esc(rep.title) + ': ' + esc(rep.headline) + '</h4><small>Run ' + esc(rep.run_id) + ' by ' + esc(rep.by) + (rep.mode === 'server' ? ' (unattended)' : '')
        + ', ' + esc(String(rep.started).slice(0, 16)) + (rep.previous_started ? ' · compared with the run of ' + esc(String(rep.previous_started).slice(0, 16)) : '') + '</small>'
        + ((rep.newly_failing || []).length ? '<div class="psa-rb-warn" style="background:#fef2f2;color:#7f1d1d"><b>Failing since the last run:</b> ' + rep.newly_failing.map(esc).join(', ') + '</div>' : '')
        + ((rep.fixed || []).length ? '<div class="psa-rb-warn" style="background:#ecfdf5;color:#065f46"><b>Fixed since the last run:</b> ' + rep.fixed.map(esc).join(', ') + '</div>' : '')
        + (rep.note ? '<div class="psa-rb-warn" style="background:#fffbeb;color:#78350f">' + esc(rep.note) + '</div>' : '')
        + '<table class="psa-mon-t psa-wp"><tr><th>#</th><th>Test case</th><th>Result</th><th>Details</th></tr>' + rows + '</table>'
        + '<div class="psa-actions"><button type="button" class="psa-no" data-xl>Download the report (Excel)</button></div>');
      c.classList.add('psa-diag');
      c.querySelector('[data-xl]').addEventListener('click', function (e) {
        download('/api/regress/report?xlsx=1&run=' + encodeURIComponent(rep.run_id)).then(function (n) { e.target.textContent = 'Downloaded ' + n; },
          function (er) { addMsg('bot', '⚠️ ' + er.message, false); });
      });
    }

    /* The watcher's alerts: a list, each opening its analysis and the action needed */
    function showIncidents(x) {
      var items = x.items || [];
      if (!items.length) return;
      var c = card('<h4>' + (items.length === 1 ? '1 alert' : items.length + ' alerts') + ' in the last ' + x.hours + ' hours</h4><ul class="psa-inc-list">'
        + items.map(function (i, n) {
          return '<li><button type="button" data-n="' + n + '"><b>' + esc(i.title) + '</b><small>' + esc(String(i.found).slice(5, 16)) + ' · ' + esc(i.monitor_title)
            + (i.cleared ? ' · cleared' : '') + '</small><span>' + esc(String(i.summary || i.note || '').slice(0, 180)) + '</span></button></li>';
        }).join('') + '</ul>');
      c.addEventListener('click', function (e) {
        var b = e.target.closest && e.target.closest('[data-n]');
        if (b) showIncident(items[+b.getAttribute('data-n')]);
      });
    }
    function openIncident(id) {
      apiGet('/api/incidents?id=' + encodeURIComponent(id)).then(function (r) { showIncident(r.incidents[0]); },
        function (e) { addMsg('bot', '⚠️ I could not open that alert: ' + e.message, false); });
    }
    function showIncident(inc) {
      var rq = inc.request, found = 'Found by the watcher (' + inc.monitor_title + ') on ' + String(inc.found).slice(0, 16)
        + (inc.occurrences > 1 ? '; failed ' + inc.occurrences + ' times: ' + inc.instances.join(', ') : '')
        + (inc.log_files && inc.log_files.length ? '; read ' + inc.log_files.join(', ') : '') + (inc.emailed ? '; emailed' : '');
      if (inc.diagnosis) {
        addMsg('bot', '**' + inc.title + '**' + (rq ? ' (' + rq.status + ', user ' + rq.oprid + ', run control ' + (rq.runcntl || '-') + ', ended ' + rq.ended + ')' : '')
          + '\n' + found + '.', false);
        return showDiagnosis(inc.diagnosis);
      }
      var c = card('<h4>' + esc(inc.title) + '</h4><small>' + esc(found) + '</small>'
        + (inc.summary ? '<p class="psa-diag-sum">' + md(inc.summary) + '</p>' : '')
        + ((inc.servers || []).length ? '<div class="psa-diag-sec"><b>Process servers</b><ul>' + inc.servers.map(function (sv) {
          return '<li>' + esc(sv.name) + ': ' + esc(sv.status) + ' <small>(last heartbeat ' + esc(sv.heartbeat) + ')</small></li>'; }).join('') + '</ul></div>' : '')
        + ((inc.stuck || []).length ? previewTable({ columns: ['Instance', 'Process', 'User', 'Status', 'Run time', 'Server'],
          sample: inc.stuck.slice(0, 10).map(function (r) { return [r.instance, r.process, r.oprid, r.status, r.run_time, r.server]; }) }) : '')
        + (inc.check ? previewTable({ columns: inc.check.columns, sample: inc.check.rows }) : '')
        + (inc.note ? '<p><small>' + esc(inc.note) + '</small></p>' : '')
        + '<div class="psa-diag-sec"><b>Action needed</b><ol>' + (inc.actions || []).map(function (a) { return '<li>' + md(a) + '</li>'; }).join('') + '</ol></div>'
        + (rq ? '<div class="psa-actions"><button type="button" class="psa-no" data-q="Why did process ' + esc(rq.instance) + ' fail?">Investigate again</button></div>' : ''));
      c.classList.add('psa-diag');
      c.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('[data-q]'); if (b) send(b.getAttribute('data-q')); });
    }

    /* Running a published runbook: the start card with its inputs, then one step at a time. Checks run on the
       service; a task shows its confirm card (the password stays here) and runs in the workspace; a process is
       followed until Process Monitor shows it ended; approvals and manual steps wait for a click. */
    var RB_MARK = { ok: 'ok', warn: 'err', failed: 'err', stopped: 'err' };
    function showRunbookStart(rb) {
      var inputs = (rb.inputs || []).map(function (i) {
        return '<label>' + esc(i.label) + '<input data-in="' + esc(i.name) + '" value="' + esc(i['default']) + '"></label>'
          + (i.hint ? '<small>' + esc(i.hint) + '</small>' : '');
      }).join('');
      var steps = (rb.steps || []).map(function (st) {
        var t = RB_TYPE[st.type] || RB_TYPE.manual;
        return '<li><div class="psa-rb-h"><span class="psa-rb-n">' + st.n + '</span><b>' + esc(st.title) + '</b><span class="psa-rb-t" style="color:'
          + t[1] + ';background:' + t[2] + '">' + t[0] + '</span></div></li>';
      }).join('');
      var c = card('<h4>Run: ' + esc(rb.title) + '</h4>' + (rb.summary ? '<p class="psa-diag-sum">' + esc(rb.summary) + '</p>' : '')
        + inputs + '<details><summary>' + (rb.steps || []).length + ' steps</summary><ol class="psa-rb-steps">' + steps + '</ol></details>'
        + '<small>' + (rb.auto_run ? 'When you click Start, tasks and processes save and submit without asking again.' : 'Tasks still show their confirm card.')
        + ' Approvals wait for you. The run stops at the first problem.</small>'
        + '<div class="psa-actions"><button type="button" class="psa-ok" data-go>Start</button><button type="button" class="psa-no" data-x>Cancel</button></div>');
      c.querySelector('[data-x]').addEventListener('click', function () {
        disable(c);
        api('/api/runbooks/step', { session_id: state.sessionId, run_id: rb.run_id, cancel: true }).catch(function () {});
      });
      c.querySelector('[data-go]').addEventListener('click', function () {
        var values = {};
        c.querySelectorAll('[data-in]').forEach(function (x) { values[x.getAttribute('data-in')] = x.value; });
        disable(c);
        runRunbook(rb, values, c);
      });
    }

    function runRunbook(rb, inputs, startCard) {
      var feed = activity('Runbook: ' + rb.title), waitLine = null, ws = null;
      setStatus('working', 'Running ' + rb.title);
      function call(req) {
        req.session_id = state.sessionId;
        req.run_id = rb.run_id;
        return api('/api/runbooks/step', req);
      }
      function handle(r) {
        if (waitLine && (r.events || []).length) { waitLine.remove(); waitLine = null; }
        (r.events || []).forEach(function (ev) {
          var ok = ev.status === 'ok', line = feed.add(ev.n + '. ' + ev.title + (ok ? ': ' + ev.detail : ''));
          line.end(ok, ok ? null : (ev.status === 'warn' ? 'warning: ' : '') + ev.detail);
        });
        if (r.done) return finish(r);
        var nx = r.next || {};
        if (nx.type === 'task') return askTask(nx);
        if (nx.type === 'run') return runTask(nx);
        if (nx.type === 'wait') {
          setStatus('working', nx.text);
          if (!waitLine) waitLine = feed.add(nx.n + '. ' + nx.title + ': ' + nx.text, 'run');
          return sleep(4000).then(function () { return call({ poll: true }).then(handle, lost); });
        }
        if (nx.type === 'approval' || nx.type === 'manual') return askClick(nx);
        return finish({ status: 'failed', results: [] });
      }
      function lost(e) {
        feed.finish('error');
        setStatus('error');
        addMsg('bot', '⚠️ The runbook stopped: ' + e.message, false);
      }
      /* the task's confirm card: values from the runbook, anything still missing, the password */
      function askTask(nx) {
        setStatus('waiting', 'Step ' + nx.n + ': waiting for you');
        var rows = (nx.rows || []).map(function (x) { return '<tr><td>' + esc(x.label) + '</td><td><strong>' + esc(x.value) + '</strong></td></tr>'; }).join('');
        var miss = (nx.missing || []).map(function (f) {
          return '<label>' + esc(f.label) + ' (needed)<input data-fill="' + esc(f.name) + '"></label>' + (f.hint ? '<small>' + esc(f.hint) + '</small>' : '');
        }).join('');
        var sec = (nx.secrets || []).map(function (x) {
          return '<label>' + esc(x.label) + '<input type="password" autocomplete="new-password" data-secret="' + esc(x.name) + '"></label>'
            + '<label>Confirm ' + esc(x.label) + '<input type="password" autocomplete="new-password" data-confirm="' + esc(x.name) + '"></label>';
        }).join('');
        var c = card('<h4>Step ' + nx.n + ': ' + esc(nx.title) + '</h4><small>Runs the task <b>' + esc(nx.task_title) + '</b>'
          + (nx.process ? ', then follows the process in Process Monitor' : '') + '.</small>' + (rows ? '<table>' + rows + '</table>' : '') + miss + sec
          + '<div class="psa-actions"><button type="button" class="psa-ok">Do it</button><button type="button" class="psa-no">Stop the run</button></div>');
        c.classList.add('psa-approve');
        var ok = c.querySelector('.psa-ok');
        function values() {
          var out = {}, valid = true;
          c.querySelectorAll('[data-secret]').forEach(function (inp) {
            var conf = c.querySelector('[data-confirm="' + inp.getAttribute('data-secret') + '"]');
            if (!inp.value || inp.value !== conf.value) valid = false;
            out[inp.getAttribute('data-secret')] = inp.value;
          });
          c.querySelectorAll('[data-fill]').forEach(function (inp) { if (!inp.value.trim()) valid = false; });
          return valid ? out : null;
        }
        function refresh() { ok.disabled = !values(); }
        c.querySelectorAll('input').forEach(function (x) { x.addEventListener('input', refresh); });
        refresh();
        return new Promise(function (resolve) {
          ok.addEventListener('click', function () {
            var secrets = values(), fill = {};
            if (!secrets) return;
            c.querySelectorAll('[data-fill]').forEach(function (x) { fill[x.getAttribute('data-fill')] = x.value; });
            ok.disabled = true;
            call({ confirm: true, fill: fill }).then(function (r) {
              disable(c);
              c.querySelectorAll('input[type="password"]').forEach(function (x) { x.value = ''; });
              pendingSecrets[rb.run_id] = secrets;
              resolve(handle(r));
            }, function (e) { ok.disabled = false; addMsg('bot', '⚠️ ' + e.message, false); });
          });
          c.querySelector('.psa-no').addEventListener('click', function () { disable(c); resolve(call({ cancel: true }).then(handle, lost)); });
        });
      }
      function runTask(nx) {
        var secrets = pendingSecrets[rb.run_id] || {}, cur = null;
        delete pendingSecrets[rb.run_id];
        ws = openWorkspace('Agent: ' + nx.action.title);
        ws.onStep = function (n, total, text) { setStatus('working', 'Step ' + nx.n + ' · ' + n + '/' + total + ': ' + text); };
        cur = feed.add(nx.n + '. ' + nx.action.title + ' (on screen)', 'run');
        return runAction(nx.action, secrets, ws).catch(function (e) {
          return { status: 'error', message: 'The agent hit an unexpected problem: ' + ((e && e.message) || e) };
        }).then(function (res) {
          secrets = null;
          ws.done();
          ws.status(res.status === 'success' ? 'Done: ' + nx.action.title : 'Stopped: ' + res.message, res.status !== 'success');
          cur.remove();
          return call({ result: { status: res.status, message: res.message, failed_step: res.failed_step } }).then(handle, lost);
        });
      }
      function askClick(nx) {
        setStatus('waiting', 'Step ' + nx.n + ': waiting for you');
        var approval = nx.type === 'approval';
        var c = card('<h4>Step ' + nx.n + ': ' + esc(nx.title) + '</h4>' + (nx.instructions ? '<p class="psa-diag-sum">' + esc(nx.instructions) + '</p>' : '')
          + (nx.owner ? '<small>' + (approval ? 'Approver: ' : 'Owner: ') + esc(nx.owner) + '</small>' : '')
          + '<div class="psa-actions"><button type="button" class="psa-ok">' + (approval ? 'Approve' : 'Done') + '</button>'
          + '<button type="button" class="psa-no">' + (approval ? 'Reject' : 'Stop the run') + '</button></div>');
        c.classList.add('psa-approve');
        return new Promise(function (resolve) {
          c.querySelector('.psa-ok').addEventListener('click', function () {
            disable(c);
            resolve(call(approval ? { approve: true } : { done: true }).then(handle, lost));
          });
          c.querySelector('.psa-no').addEventListener('click', function () {
            disable(c);
            resolve(call(approval ? { approve: false } : { cancel: true }).then(handle, lost));
          });
        });
      }
      function finish(r) {
        var good = r.status === 'success';
        feed.finish(good ? 'success' : 'error');
        setStatus(good ? 'idle' : 'error');
        var items = (r.results || []).map(function (x) {
          var m = CHECK_MARK[x.status === 'ok' ? 'ok' : x.status === 'warn' ? 'warning' : x.status === 'stopped' ? 'info' : 'problem'];
          return '<div class="psa-chk ' + m[0] + '"><i>' + m[1] + '</i><span><b>' + x.n + '. ' + esc(x.title) + '</b>: ' + esc(x.detail) + '</span></div>';
        }).join('');
        var c = card('<h4>' + esc(rb.title) + ': ' + ({ success: 'completed', failed: 'stopped at a problem', stopped: 'stopped' }[r.status] || r.status) + '</h4>'
          + items + (r.failed_instance ? '<div class="psa-actions"><button type="button" class="psa-ok" data-why>Why did process ' + esc(r.failed_instance)
            + ' fail?</button></div>' : ''));
        var why = c.querySelector('[data-why]');
        if (why) why.addEventListener('click', function () { disable(c); send('Why did process ' + r.failed_instance + ' fail?'); });
      }
      return call({ inputs: inputs }).then(handle, function (e) {
        feed.finish('error');
        setStatus('error');
        addMsg('bot', '⚠️ ' + e.message, false);
        if (startCard) startCard.querySelectorAll('input,button').forEach(function (x) { x.disabled = false; });
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
    function runLive(live, opts) {
      opts = opts || {};                               /* recovery: same workspace, feed, recording and password */
      var ws = opts.ws || openWorkspace('Agent: ' + live.goal), feed = opts.feed || activity(live.goal), userCtx = pageContext();
      var lrec = opts.recorder || startLiveRecorder(ws.frame, live.goal), payload = {}, secrets = opts.secrets || {};
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
          var rec = lrec.rec, up = opts.update;
          if (status !== 'success' || !canTeach || !rec.component || !rec.steps.some(isAction)) return;
          if (opts.ws && !up) return;
          var c = card(up
            ? '<h4>Update the task?</h4><small>The page has changed since <b>' + esc(up.title) + '</b> was recorded; I worked out the '
              + 'difference live. Update the task with this run so next time is quick again.</small>'
              + '<div class="psa-actions"><button type="button" class="psa-ok">Review the update</button><button type="button" class="psa-no">Not now</button></div>'
            : '<h4>Make this a task?</h4><small>I worked this out step by step. Saved as a task, I can do it again in one go, '
              + 'with new values each time.</small><div class="psa-actions"><button type="button" class="psa-ok">Save as a task</button>'
              + '<button type="button" class="psa-no">No thanks</button></div>');
          c.querySelector('.psa-no').addEventListener('click', function () { disable(c); });
          c.querySelector('.psa-ok').addEventListener('click', function () {
            disable(c);
            var recording = { title: up ? up.title : live.goal, component: rec.component, query: rec.query || {}, steps: rec.steps };
            if (up) recording.task_id = up.task_id;
            reviewRecording(recording);
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
      clearInterval(dogsTimer);
      dogsTimer = setInterval(refreshDogs, 60000);
      clearInterval(phTimer);
      phTimer = setInterval(rotatePlaceholder, 4000);
      wave();
      if (!ready) {                       /* first open, or the last attempt failed: (re)connect */
        logEl.innerHTML = '';
        ensureConfig().then(function () {
          ready = true;
          return apiGet('/api/me').then(function (m) {
            me = m;
            canTeach = !!m.can_teach;
            setDogs(m.overnight && m.overnight.watchdogs);
            if (!panel.querySelector('#psa-featmenu').hidden) toggleFeatures(true);   /* opened while connecting */
            if (canTeach) panel.querySelector('#psa-rec').style.display = '';
          }, function (e) {
            addMsg('bot', 'I cannot reach the agent service at ' + (cfg.agentUrl || '(no URL)') + ': ' + e.message
              + '. Check that it is running and that this PeopleSoft address is in PSAGENT_ALLOWED_ORIGINS.', false);
          }).then(function () {
            if (state.history.length) state.history.forEach(function (h) { addMsg(h.who, h.text, false); });
            else greet();
            if (INC_LINK) { var id = INC_LINK; INC_LINK = null; openIncident(id); }
          });
        }, function (e) {
          console.warn('[PSAgent] config failed:', e);
          addMsg('bot', 'Sorry, the agent is not available right now: ' + e.message + '.', false);
        });
      }
      /* Focus the message box, unless the person is already typing somewhere in the panel
         (a password on a card must never end up in the chat box) */
      setTimeout(function () { if (!panel.contains(document.activeElement)) input.focus(); }, 100);
    }
    function close() {
      panel.classList.remove('psa-open');
      document.body.classList.remove('psa-docked');
      fab.setAttribute('aria-expanded', 'false');
      clearInterval(ctxTimer);
      clearInterval(phTimer);
      clearInterval(dogsTimer);
      fab.focus();
    }
    /* Pia waves hello the first time the panel opens in this browser session */
    function wave() {
      try { if (sessionStorage.getItem('psagent.waved')) return; sessionStorage.setItem('psagent.waved', '1'); } catch (e) { return; }
      var av = panel.querySelector('#psa-hdr .psa-av');
      av.classList.add('psa-wave');
      av.addEventListener('animationend', function () { av.classList.remove('psa-wave'); }, { once: true });
    }
    function setWide(on) {
      panel.classList.toggle('psa-wide', on);
      document.body.classList.toggle('psa-widebody', on);
      try { localStorage.setItem('psagent.wide', on ? '1' : ''); } catch (e) {}
    }
    try { if (localStorage.getItem('psagent.wide')) setWide(true); } catch (e) {}

    function submit() {
      if (reading) { reading.then(submit); return; }   /* a file still being read goes with this message, not the next */
      var t = input.value;
      if ((!String(t).trim() && !attached) || (busy && !liveWaiter)) return;
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
    panel.querySelector('#psa-attach').addEventListener('click', function () { panel.querySelector('#psa-file').click(); });
    panel.querySelector('#psa-file').addEventListener('change', function (e) {
      var as = uploadAs, cu = ciUpload;
      uploadAs = ciUpload = null;
      if (cu) ciUploadFile(e.target.files[0], cu);
      else takeFile(e.target.files[0], as);
      e.target.value = '';
    });
    panel.querySelector('#psa-feat').addEventListener('click', function (e) { e.stopPropagation(); toggleFeatures(); });
    panel.querySelector('#psa-dogs').addEventListener('click', showManager);
    document.addEventListener('click', function (e) {
      var menu = panel.querySelector('#psa-featmenu');
      if (!menu.hidden && !menu.contains(e.target)) toggleFeatures(false);
    });
    panel.addEventListener('dragover', function (e) { e.preventDefault(); panel.classList.add('psa-drop'); });
    panel.addEventListener('dragleave', function (e) { if (e.target === panel) panel.classList.remove('psa-drop'); });
    panel.addEventListener('drop', function (e) {
      e.preventDefault();
      panel.classList.remove('psa-drop');
      if (e.dataTransfer && e.dataTransfer.files.length) takeFile(e.dataTransfer.files[0]);
    });
    panel.querySelector('#psa-recentpop').addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-q]');
      if (b) send(b.getAttribute('data-q'));
    });
    panel.querySelector('#psa-new').addEventListener('click', function () {
      state = { sessionId: null, history: [] };
      saveState();
      logEl.innerHTML = '';
      setStatus('idle');
      /* fresh welcome data: new alerts, failed processes, runbooks and tasks published since the panel opened */
      apiGet('/api/me').then(function (m) { me = m; }, function () {}).then(function () { if (!logEl.children.length) greet(); });
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
    if (INC_LINK) open();
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
        cfg.roles = Array.isArray(c.roles) ? c.roles : null;   /* null: an iScript from before roles */
        return cfg;
      });
    configPromise.catch(function () { configPromise = null; });
    return configPromise;
  }

  /* POST payload as JSON (GET when payload is undefined); errors carry the service's message.
     ngrok-skip-browser-warning: on ngrok's free plan, GETs from a browser otherwise get its
     warning page (no CORS headers, so "Failed to fetch"); other hosts ignore the header */
  function api(path, payload, retried) {
    return ensureConfig().then(function () {
      var opts = { method: payload === undefined ? 'GET' : 'POST',
        headers: { 'Authorization': 'Bearer ' + cfg.token, 'ngrok-skip-browser-warning': '1' } };
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
    run: function (action, secrets) { return runAction(action, secrets || {}, openWorkspace('Agent: ' + (action.title || 'test'))); },
    /* the headless runner on the service: run a regression run started there -> its report */
    runSuite: function (runId, secrets) { return suiteLoop(runId, secrets || {}); }
  };

  function start() {
    /* Branding loads this file on every page, including the PeopleSoft content frame
       and the agent's own workspace frame: only the top window gets the widget. */
    try { if (window.top !== window && window.top.document) return; } catch (e) { /* cross-origin parent: show it */ }
    if (!document.body) return setTimeout(start, 250);
    /* the widget is only for users with an agent role (PSAGENT_USER, _MONITOR, _ADMIN): the iScript says which.
       If the iScript can't be read, show it anyway so opening it explains the problem. */
    ensureConfig().then(function () {
      if (cfg.roles && !cfg.roles.length) { try { console.info('[PSAgent] hidden: no PSAGENT role'); } catch (e) {} return; }
      show();
    }, show);
    function show() { initWidget(); try { console.info('[PSAgent] widget ' + VERSION); } catch (e) {} }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

})();
