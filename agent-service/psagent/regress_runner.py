"""Unattended regression runs: a headless browser on the agent service signs in to PeopleSoft as a test user and
runs a suite with the same widget code the browser uses (PSAgent.runSuite), so both behave the same.

  PSAGENT_TEST_USER / _PASSWORD   the PeopleSoft user the runs sign in as: a PSAGENT role (so the widget gets a
                                  token) and access to the tasks' pages
  PSAGENT_TEST_SECRETS            values for password fields the cases need, e.g. password=Welcome#2026
  PSAGENT_PS_URL                  the PeopleSoft address (as for the watcher)

Runs start from the agent panel (Run unattended), at a suite's time of day (run_at), or after a patch: the
scheduler watches PeopleSoft's update log (PS_MAINTENANCE_LOG, PSRELEASE) and runs the suites marked
after_patch when it changes. The report is emailed to the suite's recipients (or PSAGENT_ALERT_TO).
Needs Playwright on the service: pip install playwright, then python -m playwright install chromium.
"""

import json
import logging
import re
import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path

from . import audit
from .db import QueryError
from .regress import VERDICT

log = logging.getLogger("psagent")

RUN_TIMEOUT = 2 * 3600 * 1000      # ms: a whole suite
PATCH_SQL = ("SELECT (SELECT COUNT(*) FROM PS_MAINTENANCE_LOG) || '/' || (SELECT TO_CHAR(MAX(DTTM_IMPORTED), "
             "'YYYY-MM-DD HH24:MI:SS') FROM PS_MAINTENANCE_LOG) || '/' || (SELECT COUNT(*) FROM PSRELEASE) FROM DUAL")


def available():
    try:
        import playwright.sync_api  # noqa: F401, PLC0415
        return True
    except ImportError:
        return False


class Headless:
    def __init__(self, ps_url, user, password, secrets=None, widget=None):
        m = re.match(r"^(https?://[^/]+)(/psp/[^/]+/)", ps_url or "")
        if not m:
            raise ValueError("PSAGENT_PS_URL must look like http://host:port/psp/<site>/...")
        self.ps_url, self.origin, self.site = ps_url, m.group(1), m.group(2)
        self.user, self._password, self.secrets = user, password, dict(secrets or {})
        self.widget = Path(widget) if widget else Path(__file__).resolve().parents[2] / "psagent.js"

    def open(self, page, run_id=""):
        """Sign in and open PeopleSoft with the widget ready (psagent.js from Branding, or the service's copy)."""
        page.on("pageerror", lambda e: log.warning("unattended run %s: page error: %s", run_id, e))
        page.on("console", lambda m: m.type in ("error", "warning") and log.warning("unattended run %s: console %s: %s",
                                                                              run_id, m.type, m.text))
        page.goto(f"{self.origin}{self.site}?cmd=login&languageCd=ENG", wait_until="domcontentloaded")
        page.fill("#userid", self.user)
        page.fill("#pwd", self._password)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
            page.click("input[name=Submit], #login input[type=submit]")
        if page.locator("#userid").count():
            raise PermissionError(f"PeopleSoft sign-in as {self.user} failed (PSAGENT_TEST_USER / _PASSWORD)")
        home = self.ps_url + "h/?tab=DEFAULT" if self.ps_url.endswith("/") else self.ps_url   # the portal root has no page
        page.goto(home, wait_until="domcontentloaded", timeout=60000)
        ready = "() => !!(window.PSAgent && window.PSAgent.runSuite)"   # not the function itself: Playwright would call it
        try:
            page.wait_for_function(ready, timeout=20000)
            return "branding"
        except Exception:   # noqa: BLE001 (Playwright's TimeoutError): no psagent.js, or an old one without runSuite
            # an old copy from Branding: take its panel and workspace away so only one widget is on the page
            page.evaluate("() => document.querySelectorAll('[id^=psa-]').forEach(e => e.remove())")
            page.add_script_tag(content=self.widget.read_text(encoding="utf-8"))
            page.wait_for_function(ready, timeout=20000)
            return "service copy"

    def run(self, run_id, secret_names=()):
        """Sign in, open PeopleSoft, run the suite in the page. -> the widget's report, or raises."""
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        secrets = {n: self.secrets.get(n) or self.secrets.get("password", "") for n in secret_names}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_context(viewport={"width": 1500, "height": 1000},
                                           extra_http_headers={"ngrok-skip-browser-warning": "1"}).new_page()
                widget = self.open(page, run_id)
                log.info("unattended run %s: signed in as %s, widget from %s", run_id, self.user, widget)
                page.set_default_timeout(RUN_TIMEOUT)
                return page.evaluate("([id, s]) => window.PSAgent.runSuite(id, s)", [run_id, secrets])
            finally:
                browser.close()


class Unattended:
    """Starts unattended runs: on request, at a suite's time, after a patch. One at a time."""

    def __init__(self, runner, store, headless, db=None, mailer=None):
        self.runner, self.store, self.headless, self.db, self.mailer = runner, store, headless, db, mailer
        self.busy = None              # run_id of the run in progress
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.state_path = store.dir / "state.json"

    def _state(self):
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, st):
        try:
            self.store.dir.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(st), encoding="utf-8")
        except OSError as e:
            log.warning("regression state not saved: %s", e)

    def start(self, suite, why="requested"):
        """Start a run in the background. -> run_id, or None when one is already running."""
        with self._lock:
            if self.busy:
                return None
            run = self.runner.start(suite, self.headless.user, mode="server")
            self.busy = run.run_id
        names = [s["name"] for s in self.runner.secrets(suite)]
        threading.Thread(target=self._go, args=(run, names, why), name="psagent-regress", daemon=True).start()
        return run.run_id

    def _go(self, run, names, why):
        log.info("unattended regression run %s of %s (%s) as %s", run.run_id, run.suite["id"], why, self.headless.user)
        try:
            self.headless.run(run.run_id, names)
        except Exception as e:          # the report still goes out, with what ran
            log.warning("unattended regression run %s: %s", run.run_id, e)
            if not run.done:
                self.runner.stop(run)
                if run.report is not None:
                    run.report["note"] = f"The run stopped: {e}"
                    self.store.save_report(run.report)
        finally:
            if not run.done:
                self.runner.stop(run)
            with self._lock:
                self.busy = None

    # ----- the schedule ---------------------------------------------------------------------
    def loop(self, interval=60):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, args=(interval,), name="psagent-regress-schedule", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self, interval):
        while not self._stop.is_set():
            try:
                with audit.scope("PSAGENT_REGRESS", "regression"):
                    self.tick()
            except Exception:
                log.exception("regression schedule failed")
            self._stop.wait(interval)

    def tick(self, now=None):
        """Start what is due: suites at their time of day, and after_patch suites when the update log changed."""
        now = datetime.fromtimestamp(now or time.time())
        st = self._state()
        due = []
        for s in self.store.suites.values():
            at = s.get("run_at")
            if at:
                h, m = map(int, at.split(":"))
                key = f"ran_{s['id']}"
                if now >= now.replace(hour=h, minute=m, second=0, microsecond=0) and st.get(key) != now.strftime("%Y-%m-%d"):
                    st[key] = now.strftime("%Y-%m-%d")
                    due.append((s, f"daily at {at}"))
        if self.db is not None and any(s.get("after_patch") for s in self.store.suites.values()):
            try:
                _, rows, _ = self.db.query(PATCH_SQL, {}, max_rows=1, who="PSAGENT_REGRESS", why="patch level")
                sig = str(rows[0][0]) if rows else ""
            except QueryError as e:
                log.warning("patch level: %s", e)
                sig = st.get("patch", "")
            if st.get("patch") and sig and sig != st["patch"]:
                due += [(s, "a patch was applied") for s in self.store.suites.values() if s.get("after_patch")]
            if sig:
                st["patch"] = sig
        self._save_state(st)
        started = []
        for s, why in due:
            if self.busy:
                log.info("regression run of %s (%s) skipped: a run is in progress", s["id"], why)
                continue
            started.append(self.start(s, why))
        return started


# ----- the report by email ---------------------------------------------------------------------

def report_email(report, ps_url=""):
    """-> (subject, html, text)"""
    subject = f"[PeopleSoft Agent] Regression {report['title']}: {report['headline']}"
    color = {"passed": "#065f46", "changed": "#92400e", "failed": "#b91c1c", "stopped": "#475569"}
    h = ['<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:760px">',
         f'<h2 style="margin:0 0 4px">Regression: {escape(report["title"])}</h2>',
         f'<p style="margin:0 0 10px">{escape(report["headline"])}. Run {escape(report["run_id"])} by {escape(report["by"])}'
         f'{" (unattended)" if report.get("mode") == "server" else ""}, {escape(report["started"])} to {escape(report.get("ended", ""))}.</p>']
    t = [f"Regression: {report['title']}", report["headline"], ""]
    if report.get("newly_failing"):
        h.append(f'<p style="color:#b91c1c"><b>Failing since the last run:</b> {escape(", ".join(report["newly_failing"]))}</p>')
        t.append("Failing since the last run: " + ", ".join(report["newly_failing"]))
    if report.get("fixed"):
        h.append(f'<p style="color:#065f46"><b>Fixed since the last run:</b> {escape(", ".join(report["fixed"]))}</p>')
        t.append("Fixed since the last run: " + ", ".join(report["fixed"]))
    if report.get("note"):
        h.append(f'<p style="color:#92400e">{escape(report["note"])}</p>')
        t.append(report["note"])
    h.append('<table style="border-collapse:collapse;font-size:13px"><tr>' + "".join(
        f'<th style="text-align:left;padding:3px 8px;border-bottom:1px solid #d1d5db">{x}</th>'
        for x in ("#", "Test case", "Task", "Result", "Details")) + "</tr>")
    for r in report["results"]:
        details = "<br>".join(escape(x) for x in [r.get("detail", "")] + r.get("changes", []) if x)
        h.append(f'<tr><td style="padding:3px 8px">{r["n"]}</td><td style="padding:3px 8px">{escape(r["title"])}</td>'
                 f'<td style="padding:3px 8px">{escape(r["task_title"])}</td><td style="padding:3px 8px;color:{color.get(r["verdict"])}">'
                 f'<b>{VERDICT.get(r["verdict"])}</b></td><td style="padding:3px 8px">{details}</td></tr>')
        t.append(f"{r['n']}. {r['title']} ({r['task_title']}): {VERDICT.get(r['verdict'])}. {r.get('detail', '')}")
        t += [f"   {x}" for x in r.get("changes", [])]
    h.append("</table>")
    if ps_url:
        h.append(f'<p><a href="{escape(ps_url.rstrip("/") + "/h/?tab=DEFAULT")}">Open PeopleSoft</a> and choose ＋ Features › '
                 "Regression tests for the full report.</p>")
    h.append("</div>")
    return subject, "".join(h), "\n".join(t)
