/* PeopleSoft Agent loader: paste this in place of the whole psagent.js (in PS_COPILOT_HTML for classic
   pages, at the end of PTNUI_MENU_JS for Fluid). It fetches psagent.js from the agent service
   (GET /psagent.js), so a new psagent.js on the service reaches users without changing PeopleSoft.
   No slash-slash anywhere: PeopleSoft minifies this definition, and a minifier may read one as a comment. */
(function () {
  var SRC = 'https:\/\/oversight-maritime-saturday.ngrok-free.dev\/psagent.js';
  /* the widget is for the top window only, as in psagent.js: frames need not download it */
  try { if (window.top !== window && window.top.document) return; } catch (e) { /* cross-origin parent: load it */ }
  if (window.PSAgent || window.PSAgentLoading) return;
  window.PSAgentLoading = true;
  fetch(SRC, { headers: { 'ngrok-skip-browser-warning': '1' }, cache: 'no-cache' })
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
    .then(function (code) {
      var s = document.createElement('script');
      s.text = code;
      (document.head || document.documentElement).appendChild(s);
    })
    .catch(function (e) {
      window.PSAgentLoading = false;
      try { console.warn('[PSAgent] cannot load ' + SRC + ': ' + e.message); } catch (x) { /* no console */ }
    });
})();
