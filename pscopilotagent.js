/* ============================================================
   HR COPILOT WIDGET — Final Production Version
   Paste at the BOTTOM of PTNUI_MENU_JS in App Designer
   ============================================================ */

(function () {

  var DIRECT_LINE_SECRET = ' ';
  var EMPLID             = 'KU0001';
  var FULLNAME           = 'Test User';
  var TOKEN_API          = 'https://directline.botframework.com/v3/directline/tokens/generate';
  var WEBCHAT_JS_URL     = 'https://cdn.botframework.com/botframework-webchat/latest/webchat.js';

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
      + '#hrcopilot-retrybtn{margin-top:8px;padding:8px 20px;background:#0078d4;color:white;border:none;border-radius:6px;font-size:12px;cursor:pointer;font-family:Segoe UI,sans-serif;}';

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
    panel.innerHTML =
      '<div id="hrcopilot-hdr">'
      + '<div style="width:34px;height:34px;background:rgba(255,255,255,.18);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0">&#129302;</div>'
      + '<div><h3>HR Copilot Assistant</h3><p>Powered by PeopleSoft AI</p></div>'
      + '<div id="hrcopilot-hdr-status"><div id="hrcopilot-hdrdot"></div>Online</div>'
      + '<button id="hrcopilot-closebtn">&#x2715;</button>'
      + '</div>'
      + '<div id="hrcopilot-body">'
      + '<div id="hrcopilot-loader"><div class="hrc-spin"></div><p>Connecting securely...</p></div>'
      + '<div id="hrcopilot-err"><p>&#9888; Could not connect.<br>Please try again.</p><button id="hrcopilot-retrybtn">Retry</button></div>'
      + '<div id="hrcopilot-wc"></div>'
      + '</div>';
    document.body.appendChild(panel);

    /* ── State ────────────────────────────────────────── */
    var isOpen  = false;
    var loaded  = false;
    var loading = false;

    /* ── Close button ─────────────────────────────────── */
    document.getElementById('hrcopilot-closebtn').addEventListener('click', function (e) {
      e.stopPropagation();
      isOpen = false;
      panel.classList.remove('hrc-open');
      document.getElementById('hrc-ic-chat').style.display  = 'block';
      document.getElementById('hrc-ic-close').style.display = 'none';
    });

    /* ── FAB click ────────────────────────────────────── */
    fab.addEventListener('click', function (e) {
      e.stopPropagation();
      isOpen = !isOpen;
      panel.classList.toggle('hrc-open', isOpen);
      document.getElementById('hrc-ic-chat').style.display  = isOpen ? 'none'  : 'block';
      document.getElementById('hrc-ic-close').style.display = isOpen ? 'block' : 'none';
      tt.classList.remove('hrc-show');
      var badge = document.getElementById('hrcopilot-badge');
      if (isOpen && badge) badge.remove();
      if (isOpen && !loaded && !loading) { loading = true; loadWebChatJS(); }
    });

    /* ── Outside click ────────────────────────────────── */
    document.addEventListener('click', function (e) {
      if (isOpen && !panel.contains(e.target) && e.target !== fab) {
        isOpen = false;
        panel.classList.remove('hrc-open');
        document.getElementById('hrc-ic-chat').style.display  = 'block';
        document.getElementById('hrc-ic-close').style.display = 'none';
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

    /* ── Step 1: Load BotFramework WebChat JS ─────────── */
    function loadWebChatJS() {
      if (window.WebChat) { fetchToken(); return; }
      var sc = document.createElement('script');
      sc.src     = WEBCHAT_JS_URL;
      sc.onload  = function () { fetchToken(); };
      sc.onerror = function () { showError(); };
      document.head.appendChild(sc);
    }

    /* ── Step 2: Get Direct Line token ───────────────── */
    function fetchToken() {
      fetch(TOKEN_API, {
        method: 'POST',
        headers: {
          'Authorization': 'Bearer ' + DIRECT_LINE_SECRET,
          'Content-Type':  'application/json'
        },
        body: JSON.stringify({ user: { id: EMPLID, name: FULLNAME } })
      })
      .then(function (r) {
        if (!r.ok) throw new Error('Token API returned HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data.token) throw new Error('No token in response');
        renderWebChat(data.token);
      })
      .catch(function (err) {
        console.error('HR Copilot token error:', err);
        showError();
      });
    }

    /* ── Step 3: Render BotFramework WebChat ──────────── */
    function renderWebChat(token) {
      document.getElementById('hrcopilot-loader').style.display = 'none';
      document.getElementById('hrcopilot-err').classList.remove('hrc-show');

      var el = document.getElementById('hrcopilot-wc');
      el.style.cssText = 'width:100%;height:100%;';

      window.WebChat.renderWebChat({
        directLine: window.WebChat.createDirectLine({ token: token }),
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
      }, el);

      loaded  = true;
      loading = false;
    }

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