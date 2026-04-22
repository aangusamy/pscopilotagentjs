/* ============================================================
   HR COPILOT WIDGET — Final Production Version
   Paste at the BOTTOM of PTNUI_MENU_JS in App Designer
   ============================================================ */

(function () {

  /* iScript URL — update WEBLIB name to match your App Designer record */
  var CONFIG_ISCRIPT_URL = '/psc/ps/EMPLOYEE/HRMS/s/WEBLIB_COPILOT.COPILOT_ISCRIPT1.FieldFormula.IScript_HRCopilotConfig';
  var WEBCHAT_JS_URL     = 'https://cdn.botframework.com/botframework-webchat/latest/webchat.js';

  /* Populated at runtime from iScript — never hardcoded */
  var EMPLID        = '';
  var FULLNAME      = '';
  var _speechToken  = '';
  var _speechRegion = 'eastus';

  /* ── Wait for body to exist ───────────────────────────── */
  function domReady(fn) {
    if (document.body) {
      fn();
    } else if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      setTimeout(fn, 300);
    }
  }

  /* ── Main init ────────────────────────────────────────── */
  function initHRCopilot() {
    if (document.getElementById('hrcopilot-fab')) return;
    if (!document.body) { setTimeout(initHRCopilot, 300); return; }

    /* ── Styles ─────────────────────────────────────────── */
    var css = '#hrcopilot-fab{position:fixed!important;bottom:24px!important;right:24px!important;width:56px!important;height:56px!important;border-radius:50%!important;background:linear-gradient(135deg,#003865,#0078d4)!important;border:none!important;cursor:pointer!important;box-shadow:0 4px 16px rgba(0,56,101,.45)!important;z-index:2147483647!important;display:flex!important;align-items:center!important;justify-content:center!important;padding:0!important;transition:transform .2s ease!important;}'
      + '#hrcopilot-fab:hover{transform:scale(1.08)!important;}'
      + '#hrcopilot-badge{position:absolute;top:-2px;right:-2px;width:18px;height:18px;background:#e74c3c;border-radius:50%;font-size:10px;font-weight:700;color:white;display:flex;align-items:center;justify-content:center;border:2px solid white;pointer-events:none;}'
      + '#hrcopilot-tooltip{position:fixed!important;bottom:90px!important;right:24px!important;background:#003865;color:white;padding:8px 14px;border-radius:20px;font-size:12px;font-weight:500;white-space:nowrap;box-shadow:0 3px 12px rgba(0,0,0,.2);z-index:2147483646!important;opacity:0;transform:translateY(6px);transition:opacity .3s,transform .3s;pointer-events:none;font-family:Segoe UI,sans-serif;}'
      + '#hrcopilot-tooltip.hrc-show{opacity:1;transform:translateY(0);}'
      + '#hrcopilot-tooltip:after{content:"";position:absolute;bottom:-5px;right:20px;border-left:5px solid transparent;border-right:5px solid transparent;border-top:5px solid #003865;}'
      + '#hrcopilot-panel{position:fixed!important;bottom:92px!important;right:24px!important;width:380px!important;height:580px!important;border-radius:16px!important;overflow:hidden!important;box-shadow:0 8px 40px rgba(0,0,0,.22)!important;border:1px solid #d0dce8!important;display:flex!important;flex-direction:column!important;background:#fff!important;z-index:2147483645!important;transform:scale(.85) translateY(20px);transform-origin:bottom right;opacity:0;pointer-events:none;transition:transform .25s cubic-bezier(.34,1.56,.64,1),opacity .2s ease;}'
      + '#hrcopilot-panel.hrc-open{transform:scale(1) translateY(0)!important;opacity:1!important;pointer-events:all!important;}'
      + '#hrcopilot-hdr{background:linear-gradient(135deg,#003865,#0078d4);padding:14px 16px;display:flex;align-items:center;gap:10px;flex-shrink:0;}'
      + '#hrcopilot-hdr h3{font-size:13px;font-weight:600;margin:0;color:white;font-family:Segoe UI,sans-serif;}'
      + '#hrcopilot-hdr p{font-size:11px;opacity:.7;margin:2px 0 0;color:white;font-family:Segoe UI,sans-serif;}'
      + '#hrcopilot-hdr-status{margin-left:auto;display:flex;align-items:center;gap:5px;font-size:11px;color:white;opacity:.85;flex-shrink:0;font-family:Segoe UI,sans-serif;}'
      + '#hrcopilot-hdrdot{width:7px;height:7px;background:#4ade80;border-radius:50%;animation:hrcBlink 2s infinite;}'
      + '#hrcopilot-closebtn{background:rgba(255,255,255,.2);border:none;color:white;width:26px;height:26px;border-radius:50%;cursor:pointer;font-size:14px;line-height:26px;text-align:center;flex-shrink:0;margin-left:6px;}'
      + '@keyframes hrcBlink{0%,100%{opacity:1;}50%{opacity:.35;}}'
      + '#hrcopilot-body{flex:1;overflow:hidden;position:relative;}'
      + '#hrcopilot-loader{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;background:#fff;z-index:2;}'
      + '#hrcopilot-loader p{font-size:13px;color:#666;margin:0;font-family:Segoe UI,sans-serif;}'
      + '.hrc-spin{width:32px;height:32px;border:3px solid #e0e8f0;border-top:3px solid #0078d4;border-radius:50%;animation:hrcSpin .8s linear infinite;}'
      + '@keyframes hrcSpin{to{transform:rotate(360deg);}}'
      + '#hrcopilot-wc{width:100%;height:100%;}'
      + '#hrcopilot-err{position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:20px;text-align:center;background:#fff;}'
      + '#hrcopilot-err.hrc-show{display:flex;}'
      + '#hrcopilot-err p{font-size:12px;color:#666;font-family:Segoe UI,sans-serif;margin:0;}'
      + '#hrcopilot-retrybtn{margin-top:8px;padding:8px 20px;background:#0078d4;color:white;border:none;border-radius:6px;font-size:12px;cursor:pointer;font-family:Segoe UI,sans-serif;}'
      + '@keyframes hrcErrorPulse{0%,100%{box-shadow:0 4px 16px rgba(0,56,101,.45);}50%{box-shadow:0 0 0 8px rgba(231,76,60,.35),0 4px 16px rgba(0,56,101,.45);}}'
      + '#hrcopilot-fab.hrc-error-alert{animation:hrcErrorPulse 1.2s ease-in-out 4!important;}'
      + '#hrc-mic-bar{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#f8f9fa;border-top:1px solid #e0e0e0;flex-shrink:0;}'
      + '#hrc-mic-btn{width:36px;height:36px;border-radius:50%;background:#0078d4;border:2px solid transparent;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:background .2s;}'
      + '#hrc-mic-btn:hover{background:#005a9e;}'
      + '#hrc-mic-btn:focus{outline:3px solid #003865;outline-offset:3px;}'
      + '#hrc-mic-btn.hrc-recording{background:#c0392b;animation:hrcMicPulse 1s ease-in-out infinite;}'
      + '@keyframes hrcMicPulse{0%,100%{box-shadow:0 0 0 0 rgba(192,57,43,.5);}70%{box-shadow:0 0 0 8px rgba(192,57,43,0);}}'
      + '#hrc-mic-status{font-size:11px;color:#555;font-family:Segoe UI,sans-serif;flex:1;}'
      + '#hrc-mic-status.hrc-listening{color:#c0392b;font-weight:600;}'
      /* ── WCAG focus & skip-link ─────────────────────── */
      + '#hrcopilot-panel *:focus{outline:3px solid #003865!important;outline-offset:2px!important;}'
      + '#hrcopilot-skiplink{position:absolute;top:-999px;left:0;background:#003865;color:white;padding:6px 12px;font-size:12px;font-family:Segoe UI,sans-serif;z-index:9999;border-radius:0 0 6px 0;text-decoration:none;}'
      + '#hrcopilot-skiplink:focus{top:0;}';

    var s = document.createElement('style');
    s.id = 'hrcopilot-styles';
    s.textContent = css;
    document.head.appendChild(s);

    /* ── Tooltip ──────────────────────────────────────── */
    var tt = document.createElement('div');
    tt.id = 'hrcopilot-tooltip';
    tt.textContent = 'Ask HR anything';
    document.body.appendChild(tt);

    /* ── FAB ──────────────────────────────────────────── */
    var fab = document.createElement('button');
    fab.id = 'hrcopilot-fab';
    fab.title = 'HR Copilot';
    fab.setAttribute('aria-label', 'Open HR Copilot Assistant');
    fab.setAttribute('aria-expanded', 'false');
    fab.setAttribute('aria-controls', 'hrcopilot-panel');
    fab.innerHTML =
      '<span id="hrcopilot-badge">1</span>'
      + '<svg id="hrc-ic-chat" width="26" height="26" viewBox="0 0 24 24" fill="none">'
      + '<path d="M21 15C21 15.53 20.79 16.04 20.41 16.41 20.04 16.79 19.53 17 19 17H7L3 21V5C3 4.47 3.21 3.96 3.59 3.59 3.96 3.21 4.47 3 5 3H19C19.53 3 20.04 3.21 20.41 3.59 20.79 3.96 21 4.47 21 5V15Z" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
      + '<circle cx="9" cy="10" r="1" fill="white"/><circle cx="12" cy="10" r="1" fill="white"/><circle cx="15" cy="10" r="1" fill="white"/>'
      + '</svg>'
      + '<svg id="hrc-ic-close" width="26" height="26" viewBox="0 0 24 24" fill="none" style="display:none">'
      + '<path d="M18 6L6 18M6 6L18 18" stroke="white" stroke-width="2.5" stroke-linecap="round"/>'
      + '</svg>';
    document.body.appendChild(fab);

    /* ── Panel ────────────────────────────────────────── */
    var panel = document.createElement('div');
    panel.id = 'hrcopilot-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-label', 'HR Copilot Assistant');
    panel.innerHTML =
      /* Skip link for keyboard users */
      '<a id="hrcopilot-skiplink" href="#hrcopilot-wc">Skip to chat</a>'
      + '<div id="hrcopilot-hdr">'
      + '<div style="width:34px;height:34px;background:rgba(255,255,255,.18);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0" aria-hidden="true">&#129302;</div>'
      + '<div><h3 id="hrcopilot-title">HR Copilot Assistant</h3><p>Powered by PeopleSoft AI</p></div>'
      + '<div id="hrcopilot-hdr-status" aria-live="polite"><div id="hrcopilot-hdrdot" aria-hidden="true"></div><span>Online</span></div>'
      + '<button id="hrcopilot-closebtn" aria-label="Close HR Copilot">&#x2715;</button>'
      + '</div>'
      + '<div id="hrcopilot-body">'
      + '<div id="hrcopilot-loader" aria-live="polite" aria-label="Connecting"><div class="hrc-spin" aria-hidden="true"></div><p>Connecting securely...</p></div>'
      + '<div id="hrcopilot-err" role="alert"><p>&#9888; Could not connect.<br>Please try again.</p><button id="hrcopilot-retrybtn">Retry</button></div>'
      + '<div id="hrcopilot-wc" role="log" aria-label="Conversation" aria-live="off"></div>'
      + '</div>'
      + '<div id="hrc-mic-bar">'
      + '<button id="hrc-mic-btn" aria-label="Start voice input" aria-pressed="false">'
      + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
      + '<rect x="9" y="2" width="6" height="11" rx="3" fill="white"/>'
      + '<path d="M5 11a7 7 0 0 0 14 0" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
      + '<line x1="12" y1="18" x2="12" y2="22" stroke="white" stroke-width="2" stroke-linecap="round"/>'
      + '<line x1="8" y1="22" x2="16" y2="22" stroke="white" stroke-width="2" stroke-linecap="round"/>'
      + '</svg></button>'
      + '<span id="hrc-mic-status" aria-live="polite">Tap mic to speak</span>'
      + '</div>'
      /* Hidden ARIA live region for bot replies — screen readers announce this */
      + '<div id="hrc-sr-announce" aria-live="polite" aria-atomic="true" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;"></div>';
    document.body.appendChild(panel);

    /* ── State ────────────────────────────────────────── */
    var isOpen             = false;
    var loaded             = false;
    var loading            = false;
    var wcStore            = null;
    var storeConnected     = false;
    var lastPSError        = null;   /* most recent PS error captured from page */

    /* ── Shared open/close helpers (keep ARIA in sync) ── */
    function openPanel() {
      isOpen = true;
      panel.classList.add('hrc-open');
      fab.setAttribute('aria-expanded', 'true');
      fab.setAttribute('aria-label', 'Close HR Copilot Assistant');
      document.getElementById('hrc-ic-chat').style.display  = 'none';
      document.getElementById('hrc-ic-close').style.display = 'block';
      tt.classList.remove('hrc-show');
      var badge = document.getElementById('hrcopilot-badge');
      if (badge) badge.remove();
      /* Move focus into the panel for keyboard/screen-reader users */
      setTimeout(function () {
        var sendBox = panel.querySelector('input[aria-label],textarea,button:not(#hrcopilot-closebtn)');
        if (sendBox) sendBox.focus();
        else document.getElementById('hrcopilot-closebtn').focus();
      }, 300);
      if (!loaded && !loading) { loading = true; loadWebChatJS(); }
    }

    function closePanel() {
      isOpen = false;
      panel.classList.remove('hrc-open');
      fab.setAttribute('aria-expanded', 'false');
      fab.setAttribute('aria-label', 'Open HR Copilot Assistant');
      document.getElementById('hrc-ic-chat').style.display  = 'block';
      document.getElementById('hrc-ic-close').style.display = 'none';
      fab.focus(); /* return focus to FAB */
    }

    /* ── Close button ─────────────────────────────────── */
    document.getElementById('hrcopilot-closebtn').addEventListener('click', function (e) {
      e.stopPropagation();
      closePanel();
    });

    /* ── FAB click ────────────────────────────────────── */
    fab.addEventListener('click', function (e) {
      e.stopPropagation();
      isOpen ? closePanel() : openPanel();
    });

    /* ── Outside click ────────────────────────────────── */
    document.addEventListener('click', function (e) {
      if (isOpen && !panel.contains(e.target) && e.target !== fab) closePanel();
    });

    /* ── Escape key closes panel (WCAG 2.1 §2.1) ─────── */
    document.addEventListener('keydown', function (e) {
      if (isOpen && (e.key === 'Escape' || e.keyCode === 27)) closePanel();
    });

    /* ── Focus trap inside panel (WCAG 2.1 §2.1.2) ───── */
    panel.addEventListener('keydown', function (e) {
      if (!isOpen || e.key !== 'Tab') return;
      var focusable = panel.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])');
      var first = focusable[0];
      var last  = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    });

    /* ── Retry button ─────────────────────────────────── */
    document.getElementById('hrcopilot-retrybtn').addEventListener('click', function () {
      document.getElementById('hrcopilot-err').classList.remove('hrc-show');
      document.getElementById('hrcopilot-loader').style.display = 'flex';
      loading = true;
      loadWebChatJS();
    });

    /* ── Tooltip on load ──────────────────────────────── */
    setTimeout(function () {
      tt.classList.add('hrc-show');
      setTimeout(function () { tt.classList.remove('hrc-show'); }, 3500);
    }, 2500);

    /* ── Step 1: Load BotFramework WebChat JS + Speech SDK ── */
    function loadWebChatJS() {
      if (window.WebChat) { fetchToken(); return; }
      var sc = document.createElement('script');
      sc.src    = WEBCHAT_JS_URL;
      sc.onload = function () {
        /* Load Microsoft Cognitive Services Speech SDK — required for Azure ponyfill */
        var sdk = document.createElement('script');
        sdk.src    = 'https://aka.ms/csspeech/jsbrowserpackageraw';
        sdk.onload = function () { fetchToken(); };
        sdk.onerror= function () { fetchToken(); }; /* speech unavailable but chat still works */
        document.head.appendChild(sdk);
      };
      sc.onerror = function () { showError(); };
      document.head.appendChild(sc);
    }

    /* ── Step 2: Fetch identity from PS iScript, then get DL token ── */
    function fetchToken() {
      fetch(CONFIG_ISCRIPT_URL, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' }
      })
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.text();
        })
        .then(function (text) {
          console.log('[HRC] iScript raw response:', text.substring(0, 300));
          return JSON.parse(text);
        })
        .then(applyConfig)
        .catch(function (err) {
          console.warn('HR Copilot iScript unavailable:', err);
          showError();
        });
    }

    function applyConfig(cfg) {
      EMPLID        = cfg.emplid       || '';
      FULLNAME      = cfg.fullname     || cfg.emplid || 'User';
      _speechRegion = cfg.azureRegion  || 'eastus';
      /* iScript returns secrets — JS uses them to fetch short-lived tokens */
      fetch('https://directline.botframework.com/v3/directline/tokens/generate', {
        method:  'POST',
        headers: { 'Authorization': 'Bearer ' + cfg.dlSecret, 'Content-Type': 'application/json' },
        body:    JSON.stringify({ user: { id: EMPLID, name: FULLNAME } })
      })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.token) throw new Error('no DL token');
        /* fetch Azure speech token */
        return fetch('https://' + _speechRegion + '.api.cognitive.microsoft.com/sts/v1.0/issueToken', {
          method:  'POST',
          headers: { 'Ocp-Apim-Subscription-Key': cfg.azureKey, 'Content-Length': '0' }
        })
        .then(function (r) { return r.text(); })
        .then(function (t) { _speechToken = t || ''; renderWebChat(d.token); });
      })
      .catch(function (err) { console.error('HR Copilot token error:', err); showError(); });
    }

    function fetchDevToken() {
      EMPLID   = 'KU0001';
      FULLNAME = 'Test User';
      fetch('https://directline.botframework.com/v3/directline/tokens/generate', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + 'REPLACE_IF_NEEDED', 'Content-Type': 'application/json' },
        body: JSON.stringify({ user: { id: EMPLID, name: FULLNAME } })
      })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (!d.token) throw new Error('no token'); renderWebChat(d.token); })
      .catch(function () { showError(); });
    }

    /* ── Step 3: Render BotFramework WebChat ──────────── */
    function renderWebChat(token) {
      document.getElementById('hrcopilot-loader').style.display = 'none';
      document.getElementById('hrcopilot-err').classList.remove('hrc-show');

      var el = document.getElementById('hrcopilot-wc');
      el.style.cssText = 'width:100%;height:100%;';

      /* Store middleware: mark connected + enrich outgoing error-query messages */
      wcStore = window.WebChat.createStore({}, function (storeAPI) {
        return function (next) {
          return function (action) {
            /* ── Announce bot replies to screen readers ── */
            if (action.type === 'DIRECT_LINE/INCOMING_ACTIVITY') {
              var act = action.payload && action.payload.activity;
              if (act && act.type === 'message' && act.from && act.from.role === 'bot' && act.text) {
                var announcer = document.getElementById('hrc-sr-announce');
                if (announcer) {
                  announcer.textContent = '';
                  setTimeout(function () { announcer.textContent = 'HR Copilot: ' + act.text.replace(/[*_~`#]/g, ''); }, 50);
                }
              }
            }

            if (action.type === 'DIRECT_LINE/CONNECT_FULFILLED') {
              storeConnected = true;
              /* Inject greeting card with capability chips */
              setTimeout(function () {
                var pg        = getPageContext();
                var pageLabel = pg.pageTitle && pg.component
                  ? pg.pageTitle + ' (' + pg.component + ')'
                  : (pg.pageTitle || pg.component);
                var pageHint  = pageLabel
                  ? '\n\nYou are currently on: **' + pageLabel + '**'
                  : '';
                storeAPI.dispatch({
                  type: 'DIRECT_LINE/INCOMING_ACTIVITY',
                  payload: {
                    activity: {
                      type:       'message',
                      id:         'hrc-greeting-' + Date.now(),
                      timestamp:  new Date().toISOString(),
                      channelId:  'directline',
                      from:       { id: 'bot', name: 'HR Copilot', role: 'bot' },
                      textFormat: 'markdown',
                      text: 'Hi **' + FULLNAME + '**! I am your PeopleSoft HR Copilot.' + pageHint + '\n\nHere is what I can do for you:\n\n'
                          + '- 🧭 **Navigate** — ask *"take me to paycheck"* or *"show leave balance"*\n'
                          + '- ⚠️ **Explain errors** — ask *"what is this error?"*\n'
                          + '- 📄 **Understand pages** — ask *"what is this page for?"*\n'
                          + '- 🪜 **Step-by-step guidance** — just describe your task',
                      suggestedActions: {
                        actions: [
                          { type: 'imBack', title: '🧭 Take me to Paycheck',    value: 'Take me to Paycheck' },
                          { type: 'imBack', title: '💰 Show my Leave Balance',  value: 'Show my Leave Balance' },
                          { type: 'imBack', title: '📄 What is this page for?', value: 'What is this page for?' },
                          { type: 'imBack', title: '⚠️ Help with this error',   value: 'Can you help with this error?' }
                        ]
                      }
                    }
                  }
                });
              }, 600);
            }

            if (action.type === 'WEB_CHAT/SEND_MESSAGE') {
              var userText  = (action.payload && action.payload.text) || '';
              var enriched  = userText;

              /* ── Append PS error + page context when user asks about an error ── */
              if (/\b(this error|that error|error|issue|problem|know this|what happened|what does this mean|help me with this|why (am i|did i)|explain this)\b/i.test(userText)) {
                var errCtx = captureCurrentPSError();
                if (errCtx) {
                  enriched += '\n\n[PeopleSoft error on screen: "' + errCtx + '"]';
                  var epg = getPageContext();
                  if (epg.component || epg.pageTitle) {
                    var epgLabel = epg.pageTitle && epg.component
                      ? '"' + epg.pageTitle + '" (' + epg.component + ')'
                      : (epg.pageTitle ? '"' + epg.pageTitle + '"' : epg.component);
                    enriched += '\n[Page where error occurred: ' + epgLabel
                      + (epg.menu ? ', Menu=' + epg.menu : '') + ']';
                  }
                }
              }

              /* ── Append page/component context when user asks about the page ── */
              if (/\b(what is this (page|component|screen|form)|what does this (page|component|screen) do|what is this for|explain this (page|component)|tell me about this (page|component)|what (can|should) i do (here|on this)|how do i use this|purpose of this (page|component))\b/i.test(userText)) {
                var pg = getPageContext();
                if (pg.component || pg.pageTitle) {
                  var pgLabel = pg.pageTitle && pg.component
                    ? '"' + pg.pageTitle + '" (' + pg.component + ')'
                    : (pg.pageTitle ? '"' + pg.pageTitle + '"' : pg.component);
                  var pgLine = '\n\n[Current PeopleSoft page: ' + pgLabel
                    + (pg.menu   ? ', Menu=' + pg.menu   : '')
                    + (pg.portal ? ', Portal=' + pg.portal : '')
                    + ']';
                  enriched += pgLine;
                }
              }

              if (enriched !== userText) {
                action = { type: 'WEB_CHAT/SEND_MESSAGE', payload: { text: enriched } };
              }
            }

            return next(action);
          };
        };
      });

      var wcOptions = {
        directLine: window.WebChat.createDirectLine({ token: token }),
        store:    wcStore,
        userID:   EMPLID,
        username: FULLNAME,
        locale:   'en-US',
        styleOptions: {
          accent:                     '#0078d4',
          backgroundColor:            '#ffffff',
          bubbleBackground:           '#f0f4f8',
          bubbleTextColor:            '#333',
          bubbleBorderColor:          '#d0dce8',
          bubbleBorderRadius:         12,
          bubbleBorderWidth:          1,
          bubbleFromUserBackground:   '#003865',
          bubbleFromUserTextColor:    '#ffffff',
          bubbleFromUserBorderRadius: 12,
          bubbleFromUserBorderWidth:  0,
          botAvatarInitials:          'AI',
          userAvatarInitials:         EMPLID.substring(0, 2).toUpperCase(),
          botAvatarBackgroundColor:   '#0078d4',
          userAvatarBackgroundColor:  '#003865',
          hideUploadButton:           true,
          sendBoxBackground:          '#f8f9fa',
          sendBoxButtonColor:         '#0078d4',
          sendBoxBorderTop:           'solid 1px #e0e0e0',
          sendBoxHeight:              44,
          timestampColor:             '#aaa',
          timestampFormat:            'relative',
          bubbleMaxWidth:             300,
          rootHeight:                 '100%',
          rootWidth:                  '100%',
        }
      };

      window.WebChat.renderWebChat(wcOptions, el);
      loaded  = true;
      loading = false;
      initMicButton();
    }

    /* ── Voice input — Azure Speech SDK ─────────────────
         Uses window.SpeechSDK loaded from aka.ms/csspeech.
         Falls back to browser Web Speech API if SDK not ready.
    ─────────────────────────────────────────────────── */
    function initMicButton() {
      var btn  = document.getElementById('hrc-mic-btn');
      var stat = document.getElementById('hrc-mic-status');
      var isSecure = location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1';

      if (!isSecure) {
        btn.disabled = true; btn.style.opacity = '0.4';
        btn.setAttribute('aria-label', 'Voice input requires HTTPS');
        stat.textContent = 'Voice needs HTTPS';
        return;
      }

      var isRecording  = false;
      var recognizer   = null;

      function resetMic() {
        isRecording = false;
        btn.classList.remove('hrc-recording');
        btn.setAttribute('aria-pressed', 'false');
        btn.setAttribute('aria-label', 'Start voice input');
        stat.className   = '';
        stat.textContent = 'Tap mic to speak';
        recognizer = null;
      }

      function putTranscriptInSendBox(transcript) {
        stat.textContent = 'Press Enter to send';
        setTimeout(function () { if (!isRecording) stat.textContent = 'Tap mic to speak'; }, 4000);
        if (wcStore) wcStore.dispatch({ type: 'WEB_CHAT/SET_SEND_BOX', payload: { text: transcript } });
        setTimeout(function () {
          var sendBox = document.querySelector(
            '#hrcopilot-wc input[type="text"],'
            + '#hrcopilot-wc textarea,'
            + '#hrcopilot-wc [role="textbox"]'
          );
          if (sendBox) {
            var proto  = sendBox.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
            var setter = Object.getOwnPropertyDescriptor(proto, 'value');
            if (setter && setter.set) setter.set.call(sendBox, transcript);
            else sendBox.value = transcript;
            sendBox.dispatchEvent(new Event('input', { bubbles: true }));
            sendBox.focus();
          }
        }, 50);
      }

      function startAzureRecognition() {
        var SDK = window.SpeechSDK;
        var speechCfg  = SDK.SpeechConfig.fromAuthorizationToken(_speechToken, _speechRegion);
        speechCfg.speechRecognitionLanguage = 'en-US';
        var audioCfg = SDK.AudioConfig.fromDefaultMicrophoneInput();
        recognizer   = new SDK.SpeechRecognizer(speechCfg, audioCfg);

        isRecording = true;
        btn.classList.add('hrc-recording');
        btn.setAttribute('aria-pressed', 'true');
        btn.setAttribute('aria-label', 'Stop voice input');
        stat.className   = 'hrc-listening';
        stat.textContent = 'Listening... speak now';

        recognizer.recognizeOnceAsync(
          function (result) {
            recognizer.close();
            resetMic();
            if (result.text && result.text.trim()) {
              putTranscriptInSendBox(result.text.trim());
            } else {
              stat.textContent = 'No speech heard — tap mic to try again';
            }
          },
          function (err) {
            recognizer.close();
            resetMic();
            stat.textContent = 'Speech error — tap mic to retry';
            console.warn('Azure STT error:', err);
          }
        );
      }

      function startBrowserRecognition() {
        var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR) { stat.textContent = 'Voice not supported — use Chrome or Edge'; return; }
        var rec = new SR();
        rec.lang = 'en-US'; rec.continuous = false; rec.interimResults = false;
        rec.onstart  = function () {
          isRecording = true;
          btn.classList.add('hrc-recording');
          btn.setAttribute('aria-pressed', 'true');
          btn.setAttribute('aria-label', 'Stop voice input');
          stat.className = 'hrc-listening'; stat.textContent = 'Listening... speak now';
        };
        rec.onresult = function (e) { putTranscriptInSendBox(e.results[0][0].transcript.trim()); };
        rec.onend    = function () { resetMic(); };
        rec.onerror  = function (e) {
          resetMic();
          var msgs = { 'not-allowed': 'Mic access denied', 'no-speech': 'No speech heard, try again', 'network': 'Speech service busy — try again' };
          stat.textContent = msgs[e.error] || ('Error: ' + e.error);
        };
        try { rec.start(); recognizer = { close: function() { try { rec.stop(); } catch(x){} } }; }
        catch (err) { stat.textContent = 'Could not start mic'; resetMic(); }
      }

      btn.addEventListener('click', function () {
        if (isRecording) {
          if (recognizer) { recognizer.close(); }
          resetMic();
          return;
        }
        if (window.SpeechSDK && _speechToken) {
          startAzureRecognition();
        } else {
          startBrowserRecognition();
        }
      });
    }

    /* ── Read current PS page / component context ────── */
    function getPageContext() {
      var ctx = { component: '', menu: '', portal: '', node: '', pageTitle: '', href: '' };
      var cf  = getContentFrame();
      try {
        ctx.href = cf ? cf.location.href : window.location.href;
        /* PS URL pattern — handles both /psc/ (classic) and /psp/ (fluid) */
        var m = ctx.href.match(/\/ps[cp][^/]*\/[^/]+\/([^/]+)\/([^/]+)\/[ch]\/([^?#/]+)/i);
        if (m) {
          ctx.portal    = m[1];
          ctx.node      = m[2];
          var parts     = m[3].split('.');
          ctx.menu      = parts[0] || '';
          ctx.component = parts[1] || '';
        }
      } catch (e) {}
      try {
        if (cf) {
          /* PS stores the human-readable page label in window variables (confirmed from console) */
          ctx.pageTitle = (cf.szCrefLabel || cf.szPinCrefLabel || '').trim();

          /* Fall back to DOM selectors if PS variables are empty */
          if (!ctx.pageTitle && cf.document) {
            var titleSelectors = [
              '#win0PTNAV_PAGEBAR_LBL', '#win0divPTNAV_PAGEBAR_LBL',
              '.ps-nav-page-title', '#PSNAV_GROUPLET_TITLE',
              '.PSPAGE_TITLE', 'h1'
            ];
            for (var i = 0; i < titleSelectors.length; i++) {
              var el = cf.document.querySelector(titleSelectors[i]);
              if (el) {
                var t = (el.innerText || el.textContent || '').trim();
                if (t) { ctx.pageTitle = t; break; }
              }
            }
          }
          if (!ctx.pageTitle) ctx.pageTitle = (cf.document && cf.document.title) || '';
        }
      } catch (e) {}
      return ctx;
    }

    /* ── Locate the PS content iframe window ─────────── */
    function getContentFrame() {
      var iframeEl = document.getElementById('ptifrmtgtframe')
                  || document.getElementById('ptifrmcontent')
                  || document.querySelector('iframe[name="TargetContent"]')
                  || document.querySelector('iframe[id*="tgtframe"]')
                  || document.querySelector('iframe[id*="content"]');
      if (iframeEl) { try { return iframeEl.contentWindow; } catch (e) {} }
      var names = ['TargetContent', 'win0', 'main', 'content'];
      for (var i = 0; i < names.length; i++) {
        try { if (window.frames[names[i]]) return window.frames[names[i]]; } catch (e) {}
      }
      return null;
    }

    /* ── Scrape PS error from page DOM + content frame ── */
    function captureCurrentPSError() {
      var selectors = [
        /* PS nav-frame alert dialog (confirmed from console: div#alertmsg) */
        '#alertmsg',
        /* PS Classic message dialog */
        '#PTMSGBOXTEXT', '.PTMSGBOXTEXT',
        '#win0divPT_MSGBOX', '[id*="MSGBOX"]', '[class*="PTMSGBOX"]',
        /* PS Classic inline page errors */
        '#PSPAGE_MSGTEXT', '#ERRMSGTEXT', '#PSWARNINGMSGTEXT',
        '#win0divPSPAGE_MSGTEXT', '#win0divERRMSGTEXT',
        '.PSWARNINGMSGTEXT', '.PSERROREDITEMTEXT',
        /* PS Fluid errors */
        '.ps-exception-msg', '.ps_box-error', '.ps-alert-error',
        '[id$="_MSGTEXT"]', '[class*="PSERROR"]', '[class*="PSWARNING"]'
      ];
      /* Search nav frame document AND PS content iframe document */
      var docs = [document];
      var cf = getContentFrame();
      if (cf) { try { docs.push(cf.document); } catch (e) {} }

      for (var d = 0; d < docs.length; d++) {
        for (var i = 0; i < selectors.length; i++) {
          try {
            var el = docs[d].querySelector(selectors[i]);
            if (el) {
              var txt = (el.innerText || el.textContent || '').trim();
              if (txt) return txt;
            }
          } catch (e) {}
        }
      }
      return lastPSError || null;
    }

    /* ── Passively capture PS WinMessage errors (no auto-send) ── */
    function hookWinMessage() {
      /* Hook both the nav frame and the PS content frame */
      var targets = [window];
      var cf = getContentFrame();
      if (cf) targets.push(cf);

      targets.forEach(function (targetWin) {
        ['WinMessage', 'PSAlert', 'PT_Alert'].forEach(function (fnName) {
          if (typeof targetWin[fnName] === 'function' && !targetWin[fnName]._hrcHooked) {
            var orig = targetWin[fnName];
            targetWin[fnName] = function () {
              var result = orig.apply(this, arguments);
              var first  = arguments[0];
              var msg    = (typeof first === 'string') ? first
                         : (first && typeof first.text === 'string') ? first.text
                         : String(first);
              lastPSError = msg;
              fab.classList.add('hrc-error-alert');
              return result;
            };
            targetWin[fnName]._hrcHooked = true;
          }
        });
      });
    }

    /* Poll — content frame + its PS functions load after nav frame */
    hookWinMessage();
    var hookTimer = setInterval(function () { hookWinMessage(); }, 2000);
    setTimeout(function () { clearInterval(hookTimer); }, 120000);

    /* ── Error state ──────────────────────────────────── */
    function showError() {
      document.getElementById('hrcopilot-loader').style.display = 'none';
      document.getElementById('hrcopilot-err').classList.add('hrc-show');
      loading = false;
    }
  }

  /* ── Fire when DOM is ready ───────────────────────────── */
  domReady(function () {
    /* Extra safety — NUI loads async, retry if body not ready */
    if (document.body) {
      initHRCopilot();
    } else {
      var attempts = 0;
      var interval = setInterval(function () {
        attempts++;
        if (document.body) {
          clearInterval(interval);
          initHRCopilot();
        }
        if (attempts > 20) clearInterval(interval);
      }, 250);
    }
  });

})();
