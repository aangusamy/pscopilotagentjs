"""The watcher: runs the watchdogs of the saved watch plans (monitors.py) on its own,
with nobody signed in.

Every minute it runs the watchdogs that are due, each with one read-only query and
no Claude:

  failures   new failed requests -> one incident each (the same process, run control
             and user failing again within 12 hours is counted on the open incident),
             investigated by the troubleshooter with the message log and, when a
             service user is configured, the posted log files
  stuck      newly stuck requests -> one incident, with the process servers' status
  deadline   at the time of day, no Success in the 24 hours before -> one incident saying
             what happened instead (never ran, failed (investigated), still waiting, running late)
  check      the check turns wrong -> one incident; cleared when it passes again

Monitor users manage the watchdogs from the agent panel: pause or resume one or a whole
plan, run one now, delete one or a plan, or stop all watching. Every change is kept
in the history with who made it.

Incidents are kept in monitors/incidents/<id>.json, shown in the agent panel to
monitor users and emailed (PSAGENT_SMTP_*) to the monitor's recipients with the
analysis and the action needed. A summary of the last 24 hours is emailed once a day
to PSAGENT_ALERT_TO (PSAGENT_WATCH_SUMMARY, default 07:00). Investigations
are capped per day (PSAGENT_WATCH_MAX_INVESTIGATIONS). Nothing is rerun or changed:
the alert links to PeopleSoft, where a person decides.
"""

import http.cookiejar
import json
import logging
import re
import smtplib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from html import escape, unescape
from pathlib import Path

from . import audit, monitors
from .db import QueryError
from .troubleshoot import MAX_ROUNDS, RUNSTATUS

log = logging.getLogger("psagent")

WHO = "PSAGENT_WATCH"          # how the watcher's queries appear in the query log
OVERLAP = 15                   # minutes of overlap between failure windows (seen instances are skipped)
MAX_WINDOW = 24 * 60           # minutes looked back at most (after the service was down, older failures are not alerted)
SAME_CAUSE_HOURS = 12
KEEP_DAYS = 7                  # incidents kept in memory and shown
MAX_SEEN = 500
SERVER_STATUS = {"1": "Error", "2": "Down", "3": "Running", "4": "Suspended", "5": "Running with no report node",
                 "6": "Suspended (disk low)", "7": "Suspended (offline)", "8": "Running (report repository full)",
                 "9": "Overload"}


class Watcher:
    def __init__(self, db, troubleshooter=None, directory=None, mailer=None, reports=None,
                 max_investigations=20, summary_at="07:00"):
        self.db, self.troubleshooter, self.mailer, self.reports = db, troubleshooter, mailer, reports
        self.dir = Path(directory) if directory else None
        self.max_investigations, self.summary_at = max_investigations, summary_at
        self.plans = monitors.load_plans(self.dir) if self.dir else {}
        self.monitors = monitors.flatten(self.plans)
        self.state = self._read(self.dir / "state.json", {}) if self.dir else {}
        self.state.setdefault("monitors", {})
        self.incidents = self._load_incidents()
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()     # a tick and "run now" never run watchdogs at the same time
        self._stop = threading.Event()
        self._thread = None

    # ----- setup and management -----------------------------------------------------------
    def add_plan(self, plan, who=""):
        """A plan confirmed in the chat (new, or replacing the one with the same name): live from the next tick."""
        with self._lock:
            if self.dir:
                monitors.save_plan(self.dir, plan)
            self.plans = {**self.plans, plan["id"]: plan}
            self.monitors = monitors.flatten(self.plans)
        self._history(who, f"saved the watch plan {plan['title']} ({len(plan['watchdogs'])} watchdogs)")

    def add(self, mon, who=""):
        """One watchdog set up in the chat: saved as a plan of its own."""
        self.add_plan(monitors.single_plan(mon), who)

    def _change(self, plan_id, change, who, what):
        """Apply change(plan) -> plan or None (delete it), save, and keep it in the history."""
        with self._lock:
            plan = self.plans.get(plan_id)
            if plan is None:
                return None
            plan = change({**plan, "watchdogs": [dict(d) for d in plan["watchdogs"]]})
            plans = dict(self.plans)
            if plan is None or not plan["watchdogs"]:
                plans.pop(plan_id)
                if self.dir:
                    monitors.delete_plan(self.dir, plan_id)
            else:
                plans[plan_id] = plan
                if self.dir:
                    monitors.save_plan(self.dir, plan)
            self.plans, self.monitors = plans, monitors.flatten(plans)
        self._history(who, what)
        return plan or {"id": plan_id, "deleted": True}

    def set_paused(self, mon_id, paused, who=""):
        mon = self.monitors.get(mon_id)
        if mon is None:
            return None

        def change(plan):
            for d in plan["watchdogs"]:
                if d["id"] == mon["wd"]:
                    d["paused"] = bool(paused)
            return plan
        self._change(mon["plan"], change, who, f"{'paused' if paused else 'resumed'} {mon['title']}")
        return self.monitors.get(mon_id)

    def pause_plan(self, plan_id, paused, who=""):
        def change(plan):
            for d in plan["watchdogs"]:
                d["paused"] = bool(paused)
            return plan
        plan = self.plans.get(plan_id)
        return plan and self._change(plan_id, change, who, f"{'paused' if paused else 'resumed'} the plan {plan['title']}")

    def delete(self, mon_id, who=""):
        mon = self.monitors.get(mon_id)
        if mon is None:
            return None
        return self._change(mon["plan"], lambda plan: {**plan, "watchdogs": [d for d in plan["watchdogs"] if d["id"] != mon["wd"]]},
                            who, f"deleted {mon['title']}")

    def delete_plan(self, plan_id, who=""):
        plan = self.plans.get(plan_id)
        return plan and self._change(plan_id, lambda _p: None, who, f"deleted the plan {plan['title']}")

    def set_stopped(self, stopped, who=""):
        """Stop (or start again) all watching, without touching the plans."""
        with self._lock:
            self.state["stopped"] = bool(stopped)
            self._save_state()
        self._history(who, "stopped all watching" if stopped else "started watching again")

    def run_now(self, mon_id, who=""):
        """Run one watchdog now, whatever its schedule (a test): -> its new incidents."""
        mon = self.monitors.get(mon_id)
        if mon is None:
            return None
        self._history(who, f"ran {mon['title']} now")
        now = time.time()
        with self._run_lock:
            try:
                new = self._run(mon, now, forced=True)
            except QueryError as e:
                return {"error": str(e)}
            self.state["monitors"].setdefault(mon["id"], {})["last"] = now
            self._save_state()
        return new

    def _history(self, who, what):
        log.info("watcher: %s %s", who or "?", what)
        with self._lock:
            self.state["history"] = ([{"at": _fmt(time.time()), "who": who, "what": what}] + self.state.get("history", []))[:200]
            self._save_state()

    def describe(self):
        """The plans and their watchdogs, as the chat and the watchdog manager show them."""
        plans = []
        for pid, plan in self.plans.items():
            dogs = []
            for d in plan["watchdogs"]:
                m = self.monitors[f"{pid}.{d['id']}"]
                st = self.state["monitors"].get(m["id"], {})
                last = max((i for i in self.incidents if i["monitor"] == m["id"]), key=lambda i: i["found"], default=None)
                dogs.append({"id": m["id"], "title": m["title"], "kind": m["kind"], "what": monitors.describe_what(m),
                             "when": monitors.describe_when(m), "paused": bool(m.get("paused")), "notify": m.get("notify", []),
                             "last_run": _fmt(st.get("last")), "failing": bool(st.get("failing")),
                             "last_incident": last and {"id": last["id"], "title": last["title"], "found": last["found"]}})
            src = plan.get("source") or {}
            plans.append({"id": pid, "title": plan["title"], "summary": plan.get("summary", ""), "by": src.get("by", ""),
                          "on": src.get("on", ""), "watchdogs": dogs})
        return plans

    def counts(self):
        dogs = list(self.monitors.values())
        return {"watchdogs": len(dogs), "active": len([d for d in dogs if not d.get("paused")]), "plans": len(self.plans),
                "alerts": len(self.recent(24)), "stopped": bool(self.state.get("stopped"))}

    def recent(self, hours=24):
        cut = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        return [i for i in self.incidents if i["found"] >= cut]

    def get(self, incident_id):
        return next((i for i in self.incidents if i["id"] == incident_id), None)

    # ----- the loop -------------------------------------------------------------------
    def start(self, interval=60):
        if self._thread or not self.db:
            return
        self._thread = threading.Thread(target=self._loop, args=(interval,), name="psagent-watcher", daemon=True)
        self._thread.start()
        log.info("Watcher started: %d watchdogs in %d plans (%s)%s, email: %s, log files: %s", len(self.monitors),
                 len(self.plans), ", ".join(sorted(self.plans)) or "none set up yet",
                 "; STOPPED (start it in the agent panel)" if self.state.get("stopped") else "",
                 f"via {self.mailer.host}" if self.mailer else "not set up (PSAGENT_SMTP_HOST): alerts in the panel only",
                 "as " + self.reports.user if self.reports else "message log only (no PSAGENT_SVC_USER)")

    def stop(self):
        self._stop.set()

    def _loop(self, interval):
        while not self._stop.is_set():
            try:
                with audit.scope("PSAGENT_WATCH", "watcher"):
                    self.tick()
            except Exception:           # the watcher never dies; the next tick tries again
                log.exception("watcher tick failed")
            self._stop.wait(interval)

    def tick(self, now=None):
        """Run every monitor that is due, then the daily summary. -> the new incidents."""
        now = now or time.time()
        new = []
        if self.state.get("stopped"):
            return new
        with self._run_lock:
            for mon in list(self.monitors.values()):
                if mon.get("paused") or not self._due(mon, now):
                    continue
                try:
                    new += self._run(mon, now)
                except QueryError as e:
                    log.warning("watchdog %s: %s", mon["id"], e)
                self.state["monitors"].setdefault(mon["id"], {})["last"] = now
                self._save_state()
        self._summary(now)
        return new

    def _due(self, mon, now):
        last = self.state["monitors"].get(mon["id"], {}).get("last")
        if mon.get("at"):
            today = datetime.fromtimestamp(now).replace(second=0, microsecond=0)
            for t in mon["at"]:
                h, m = map(int, t.split(":"))
                slot = today.replace(hour=h, minute=m).timestamp()
                if slot <= now and (last is None or last < slot):
                    return True
            return False
        return last is None or now - last >= mon.get("every_minutes", 5) * 60 - 5

    def _run(self, mon, now, forced=False):
        st = self.state["monitors"].setdefault(mon["id"], {})
        if mon["kind"] == "failures":
            return self._failures(mon, st, now)
        if mon["kind"] == "stuck":
            return self._stuck(mon, st)
        if mon["kind"] == "deadline":
            return self._deadline(mon, st)
        return self._check(mon, st, forced)

    # ----- kinds -----------------------------------------------------------------------
    def _failures(self, mon, st, now):
        last = st.get("last")
        minutes = min(MAX_WINDOW, int((now - last) / 60) + OVERLAP if last else mon.get("every_minutes", 5) + OVERLAP)
        sql, binds = monitors.failures_sql(mon)
        _, rows, _ = self.db.query(sql, {**binds, "m": minutes}, max_rows=200, who=WHO, why=f"monitor {mon['id']}")
        seen, new = set(st.get("seen", [])), []
        for inst, ptype, pname, oprid, runcntl, status, ended in rows:
            inst = str(inst)
            if inst in seen:
                continue
            seen.add(inst)
            st["seen"] = (st.get("seen", []) + [inst])[-MAX_SEEN:]
            req = {"instance": inst, "type": ptype, "process": str(pname or "").strip(), "oprid": oprid,
                   "runcntl": str(runcntl or "").strip(), "status": RUNSTATUS.get(str(status), str(status)),
                   "ended": str(ended)[:19]}
            same = self._same_cause(mon, req)
            if same:
                same["occurrences"] += 1
                same["instances"] = (same["instances"] + [inst])[-20:]
                self._save_incident(same)
                continue
            inc = self._incident(mon, f"{req['process']} failed (process {inst})", request=req, instances=[inst])
            if mon.get("investigate", True):
                self._investigate(inc)
            self._publish(inc, mon)
            new.append(inc)
        return new

    def _same_cause(self, mon, req):
        cut = (datetime.now() - timedelta(hours=SAME_CAUSE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        for i in self.incidents:
            r = i.get("request") or {}
            if (i["monitor"] == mon["id"] and i["found"] >= cut and r.get("process") == req["process"]
                    and r.get("runcntl") == req["runcntl"] and r.get("oprid") == req["oprid"]):
                return i
        return None

    def _stuck(self, mon, st):
        sql, binds = monitors.stuck_sql(mon)
        _, rows, more = self.db.query(sql, {**binds, "n": mon["stuck_minutes"]}, max_rows=200, who=WHO,
                                      why=f"monitor {mon['id']}")
        seen = set(st.get("seen", []))
        fresh = [r for r in rows if str(r[0]) not in seen]
        st["seen"] = [str(r[0]) for r in rows][-MAX_SEEN:]      # still stuck; a request that leaves and comes back alerts again
        if not fresh:
            return []
        _, servers, _ = self.db.query("SELECT SERVERNAME, SERVERSTATUS, LASTUPDDTTM FROM PSSERVERSTAT ORDER BY SERVERNAME",
                                      {}, max_rows=50, who=WHO, why="process server status")
        items = [{"instance": str(r[0]), "process": str(r[2] or "").strip(), "oprid": r[3],
                  "status": RUNSTATUS.get(str(r[5]), str(r[5])), "run_time": str(r[6])[:16],
                  "server": str(r[7] or "").strip() or "(any)"} for r in fresh]
        names = sorted({i["process"] for i in items})
        title = (f"{len(items)} requests stuck" if len(items) > 1 else f"{items[0]['process']} stuck (process {items[0]['instance']})")
        inc = self._incident(mon, title, stuck=items[:50], instances=[i["instance"] for i in items][:20],
                             servers=[{"name": s[0], "status": SERVER_STATUS.get(str(s[1]), str(s[1])),
                                       "heartbeat": str(s[2])[:19]} for s in servers])
        inc["summary"] = (f"{len(items)}{'+' if more else ''} request(s) of {', '.join(names[:5])} still waiting more than "
                          f"{mon['stuck_minutes']} minutes after their run time. " + _servers_note(inc["servers"], items))
        self._save_incident(inc)
        self._publish(inc, mon)
        return [inc]

    def _deadline(self, mon, st):
        """At the deadline: did the process end in Success in the 24 hours before? If not, say what happened instead."""
        sql, binds = monitors.succeeded_sql(mon)
        _, ok, _ = self.db.query(sql, {**binds, "m": 24 * 60}, max_rows=1, who=WHO, why=f"monitor {mon['id']}")
        if ok:
            st["failing"] = False
            return []
        st["failing"] = True
        sql, binds = monitors.latest_sql(mon)
        _, rows, _ = self.db.query(sql, binds, max_rows=5, who=WHO, why=f"monitor {mon['id']} (latest runs)")
        proc = mon["processes"][0]
        recent = [{"instance": str(r[0]), "process": str(r[2] or "").strip(), "oprid": r[3], "runcntl": str(r[4] or "").strip(),
                   "status": RUNSTATUS.get(str(r[5]), str(r[5])), "code": str(r[5]), "run_time": str(r[6])[:16],
                   "ended": str(r[7])[:16]} for r in rows]
        inc = self._incident(mon, f"{proc} did not succeed by {mon['deadline']}", recent=recent)
        last = recent[0] if recent else None
        if last is None:
            inc["deadline_state"] = "never"
            inc["summary"] = f"{proc} has no request at all: it was never submitted, or its requests were deleted."
        elif last["code"] in monitors.SUCCESS:
            inc["deadline_state"] = "not_run"
            inc["summary"] = (f"{proc} did not run in the 24 hours before {mon['deadline']}. Its last success was "
                              f"process {last['instance']} on {last['ended']}.")
        elif last["code"] in monitors.FAILED:
            inc["deadline_state"] = "failed"
            inc["summary"] = f"The last run of {proc}, process {last['instance']}, ended in {last['status']} on {last['ended']}."
            inc["request"] = {"instance": last["instance"], "type": str(rows[0][1]), "process": proc, "oprid": last["oprid"],
                              "runcntl": last["runcntl"], "status": last["status"], "ended": last["ended"]}
            if mon.get("investigate", True):
                note = inc["summary"]
                self._investigate(inc)
                inc["summary"] = note + (" " + inc["diagnosis"]["summary"] if inc.get("diagnosis") else "")
        elif last["code"] in monitors.STUCK:
            inc["deadline_state"] = "waiting"
            _, servers, _ = self.db.query("SELECT SERVERNAME, SERVERSTATUS, LASTUPDDTTM FROM PSSERVERSTAT ORDER BY SERVERNAME",
                                          {}, max_rows=50, who=WHO, why="process server status")
            inc["servers"] = [{"name": s[0], "status": SERVER_STATUS.get(str(s[1]), str(s[1])), "heartbeat": str(s[2])[:19]}
                              for s in servers]
            inc["summary"] = (f"Process {last['instance']} of {proc} is still {last['status']} (run time {last['run_time']}): "
                              "it never started. " + _servers_note(inc["servers"], [{"server": "(any)"}]))
        else:
            inc["deadline_state"] = "running"
            inc["summary"] = f"Process {last['instance']} of {proc} is still {last['status']} (run time {last['run_time']}): it is running late."
        self._save_incident(inc)
        self._publish(inc, mon)
        return [inc]

    def _check(self, mon, st, forced=False):
        cols, rows, more = self.db.query(mon["sql"], {}, max_rows=20, who=WHO, why=f"monitor {mon['id']}")
        wrong = bool(rows) if mon["expect"] == "no_rows" else not rows
        if not wrong:
            if st.get("failing"):
                st["failing"] = False
                for i in self.incidents:
                    if i["monitor"] == mon["id"] and not i.get("cleared"):
                        i["cleared"] = _fmt(time.time())
                        self._save_incident(i)
            return []
        if st.get("failing") and not forced:
            return []                   # already reported; one alert until it passes again
        st["failing"] = True
        inc = self._incident(mon, mon["title"], check={"columns": cols[:8], "rows": [r[:8] for r in rows[:10]],
                                                       "count": len(rows), "more": more})
        inc["summary"] = (f"{mon.get('rule') or mon['title']}: " + (f"{len(rows)}{'+' if more else ''} rows are wrong."
                                                                    if rows else "nothing was found."))
        self._save_incident(inc)
        self._publish(inc, mon)
        return [inc]

    # ----- incidents -------------------------------------------------------------------
    def _incident(self, mon, title, **extra):
        now = datetime.now()
        inc = {"id": f"inc-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}", "monitor": mon["id"], "monitor_title": mon["title"],
               "kind": mon["kind"], "title": title, "found": f"{now:%Y-%m-%d %H:%M:%S}", "occurrences": 1, **extra}
        with self._lock:
            self.incidents = [inc] + self.incidents
        self._save_incident(inc)
        return inc

    def _investigate(self, inc):
        if not self.troubleshooter:
            return
        day = datetime.now().strftime("%Y-%m-%d")
        used = self.state.setdefault("investigations", {})
        if used.get("day") != day:
            used.update(day=day, n=0)
        if used["n"] >= self.max_investigations:
            inc["note"] = f"Not investigated: the daily limit of {self.max_investigations} investigations was reached."
            return
        used["n"] += 1
        instance = inc["request"]["instance"]
        logs = []
        if self.reports:
            try:
                logs = self.reports.fetch(self.troubleshooter.log_files(instance))
            except Exception as e:      # the message log is still there
                log.warning("log files of %s: %s", instance, e)
        inc["log_files"] = [lf["name"] for lf in logs]
        inv = self.troubleshooter.start(WHO, instance, "Found by the overnight watcher: find out why it failed.")
        out = {}
        for n in range(MAX_ROUNDS + 1):
            out = self.troubleshooter.step(inv, WHO, logs if n == 0 else None)
            if out.get("done"):
                break
        d = out.get("diagnosis")
        if d:
            inc["diagnosis"] = d
            inc["summary"] = d.get("summary") or ""
        else:
            inc["note"] = out.get("error") or "The investigation did not reach a diagnosis."
        log.info("watcher: %s investigated: %s", instance, inc.get("summary") or inc.get("note"))

    def _publish(self, inc, mon):
        if self.mailer:
            inc["emailed"] = self.mailer.send(mon.get("notify") or [], *incident_email(inc, self.mailer.ps_url))
        self._save_incident(inc)
        audit.record("incident", f"{inc['title']}: {inc.get('summary') or inc.get('note') or inc.get('found') or ''}"[:500], "alert",
                     inc["id"], {"monitor": mon["id"], "emailed": inc.get("emailed")}, oprid="PSAGENT_WATCH", feature="watcher")

    # ----- daily summary ---------------------------------------------------------------
    def _summary(self, now):
        if not self.summary_at or not self.mailer or not self.mailer.default_to:
            return
        t = datetime.fromtimestamp(now)
        h, m = map(int, self.summary_at.split(":"))
        if t < t.replace(hour=h, minute=m, second=0) or self.state.get("summary_day") == t.strftime("%Y-%m-%d"):
            return
        self.state["summary_day"] = t.strftime("%Y-%m-%d")
        self._save_state()
        try:
            _, rows, _ = self.db.query("SELECT RUNSTATUS, COUNT(*) FROM PSPRCSRQST WHERE NVL(ENDDTTM, LASTUPDDTTM) > SYSDATE - 1 "
                                       "GROUP BY RUNSTATUS", {}, who=WHO, why="daily summary")
        except QueryError as e:
            log.warning("daily summary: %s", e)
            rows = []
        counts = {RUNSTATUS.get(str(s), str(s)): n for s, n in rows}
        self.mailer.send(self.mailer.default_to, *summary_email(self.recent(24), counts, self.mailer.ps_url))

    # ----- files -----------------------------------------------------------------------
    @staticmethod
    def _read(path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _save_state(self):
        if not self.dir:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / "state.json.tmp"
            tmp.write_text(json.dumps(self.state), encoding="utf-8")
            tmp.replace(self.dir / "state.json")
        except OSError as e:
            log.warning("watcher state not saved: %s", e)

    def _save_incident(self, inc):
        if not self.dir:
            return
        try:
            d = self.dir / "incidents"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{inc['id']}.json").write_text(json.dumps(inc, indent=1, default=str), encoding="utf-8")
        except OSError as e:
            log.warning("incident %s not saved: %s", inc["id"], e)

    def _load_incidents(self):
        if not self.dir or not (self.dir / "incidents").is_dir():
            return []
        cut = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        out = [i for i in (self._read(p, None) for p in (self.dir / "incidents").glob("inc-*.json"))
               if isinstance(i, dict) and i.get("found", "") >= cut]
        return sorted(out, key=lambda i: i["found"], reverse=True)


def _fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else None


def _servers_note(servers, items):
    if not servers:
        return "No process server has ever reported to Process Scheduler."
    down = [s for s in servers if s["status"] != "Running"]
    if down:
        return "Process server(s) not running: " + ", ".join(f"{s['name']} ({s['status']})" for s in down) + "."
    if all(i["server"] == "(any)" for i in items):
        return ("Every process server is running, so the waiting requests are probably not allowed on any of them "
                "(server definition: process categories and types, or the process definition's server).")
    return "Every process server is running: check its Max Concurrent and the process categories."


# ----- email --------------------------------------------------------------------------

class Mailer:
    """Sends alerts by SMTP (PSAGENT_SMTP_*). Each monitor names its recipients; PSAGENT_ALERT_TO is the default
    and gets the daily summary."""

    def __init__(self, host, port=25, user="", password="", sender="", starttls=None, default_to=(), ps_url=""):
        self.host, self.port, self.user, self._password = host, int(port or 25), user, password
        self.sender = sender or user or "psagent@localhost"
        self.starttls = (self.port == 587) if starttls is None else starttls
        self.default_to, self.ps_url = list(default_to), ps_url

    def send(self, to, subject, html, text):
        to = [a for a in dict.fromkeys(list(to) or self.default_to) if a]
        if not to:
            return False
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, self.sender, ", ".join(to)
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        try:
            smtp = (smtplib.SMTP_SSL(self.host, self.port, timeout=30) if self.port == 465
                    else smtplib.SMTP(self.host, self.port, timeout=30))
            with smtp:
                if self.starttls and self.port != 465:
                    smtp.starttls()
                if self.user:
                    smtp.login(self.user, self._password)
                smtp.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError) as e:
            log.warning("alert email to %s not sent: %s", ", ".join(to), e)
            return False


def _links(ps_url, inc=None):
    if not ps_url:
        return []
    base = ps_url.rstrip("/") + "/"
    out = []
    if inc:
        out.append(("Open in the agent", base + "h/?tab=DEFAULT&psagent=" + urllib.parse.quote(inc["id"])))
    out.append(("Process Monitor", base + "c/PROCESSMONITOR.PROCESSMONITOR.GBL"))
    return out


def _e(s):
    return escape(str(s if s is not None else ""))


def actions(inc):
    """What a person has to do, in order."""
    d = inc.get("diagnosis") or {}
    steps = list(d.get("fix_steps") or [])
    if d.get("rerun"):
        steps.append(d["rerun"])
    state = inc.get("deadline_state")
    if not steps and state in ("never", "not_run"):
        steps = ["Submit the process now from its run control page, or ask the agent to run it.",
                 "Check its recurrence or the job that should have submitted it (Process Scheduler > Recurrence Definition)."]
    if not steps and state == "waiting":
        steps = ["Check the process servers in Process Monitor (Server List) and start or fix the one that should run it.",
                 "It starts on its own once a server picks it up; tell the people waiting for its output that it is late."]
    if not steps and state == "running":
        steps = ["Watch it in Process Monitor; if it has stopped making progress, look at its log and the database sessions.",
                 "Tell the people waiting for its output that it is late."]
    if not steps and inc.get("kind") == "stuck":
        steps = ["Check the process servers in Process Monitor (Server List) and start or fix the one that should run "
                 "these requests.", "Then the waiting requests start on their own; cancel the ones no longer needed."]
    if not steps and inc.get("kind") == "check":
        steps = ["Correct the rows listed above in PeopleSoft.",
                 "The monitor clears itself on its next run once the check passes."]
    if not steps:
        steps = ["Open the request in Process Monitor and read the message log and log files."]
    return steps


_BOX = "font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:720px"


def incident_email(inc, ps_url=""):
    """-> (subject, html, text) for one alert: what happened, the analysis, and the action needed."""
    d, r = inc.get("diagnosis"), inc.get("request")
    subject = f"[PeopleSoft Agent] {inc['title']}"
    facts = []
    if r:
        facts = [("Process", f"{r['process']} ({r['type']})"), ("Instance", r["instance"]), ("Status", r["status"]),
                 ("User", r["oprid"]), ("Run control", r["runcntl"] or "-"), ("Ended", r["ended"])]
    facts += [("Monitor", inc["monitor_title"]), ("Found", inc["found"][:16])]
    if inc.get("occurrences", 1) > 1:
        facts.append(("Failed again", f"{inc['occurrences']} times: {', '.join(inc['instances'])}"))
    todo = actions(inc)
    h = [f'<div style="{_BOX}">', f'<h2 style="margin:0 0 4px;color:#b91c1c">&#9888; {_e(inc["title"])}</h2>',
         '<table style="border-collapse:collapse;margin:8px 0 12px">'
         + "".join(f'<tr><td style="padding:2px 12px 2px 0;color:#6b7280">{_e(k)}</td><td>{_e(v)}</td></tr>' for k, v in facts)
         + "</table>"]
    t = [inc["title"], ""] + [f"{k}: {v}" for k, v in facts] + [""]
    if d:
        cat = (d.get("category") or "unknown").title()
        h.append(f'<h3 style="margin:12px 0 4px">Analysis</h3><p style="margin:0 0 6px"><b>{_e(cat)} issue</b>, '
                 f'confidence {_e(d.get("confidence"))}. {_e(d.get("summary"))}</p>')
        if d.get("where"):
            h.append(f'<p style="margin:0 0 6px">Where: <code>{_e(d["where"])}</code></p>')
        h.append(f'<p style="margin:0 0 6px"><b>Root cause:</b> {_e(d.get("root_cause"))}</p>')
        if d.get("evidence"):
            h.append('<p style="margin:0"><b>Evidence</b></p><ul style="margin:2px 0 8px">'
                     + "".join(f"<li>{_e(x)}</li>" for x in d["evidence"][:6]) + "</ul>")
        t += ["ANALYSIS", f"{cat} issue, confidence {d.get('confidence')}. {d.get('summary')}",
              f"Where: {d.get('where') or '-'}", f"Root cause: {d.get('root_cause')}", ""]
        t += [f"- {x}" for x in (d.get("evidence") or [])[:6]] + ([""] if d.get("evidence") else [])
    elif inc.get("summary"):
        h.append(f'<h3 style="margin:12px 0 4px">Analysis</h3><p>{_e(inc["summary"])}</p>')
        t += ["ANALYSIS", inc["summary"], ""]
    if inc.get("servers"):
        h.append('<p style="margin:0"><b>Process servers</b></p><ul style="margin:2px 0 8px">' + "".join(
            f"<li>{_e(s['name'])}: {_e(s['status'])} (last heartbeat {_e(s['heartbeat'])})</li>" for s in inc["servers"]) + "</ul>")
        t += [f"Server {s['name']}: {s['status']} (last heartbeat {s['heartbeat']})" for s in inc["servers"]] + [""]
    if inc.get("recent"):
        h.append('<p style="margin:0"><b>Latest runs</b></p>' + _table(
            ["Instance", "Status", "User", "Run control", "Run time", "Ended"],
            [[x["instance"], x["status"], x["oprid"], x["runcntl"], x["run_time"], x["ended"]] for x in inc["recent"]]))
        t += ["Latest runs:"] + [f"{x['instance']} {x['status']} {x['oprid']} {x['runcntl']} {x['run_time']} {x['ended']}"
                                 for x in inc["recent"]] + [""]
    if inc.get("stuck"):
        rows = inc["stuck"][:15]
        h.append(_table(["Instance", "Process", "User", "Status", "Run time", "Server"],
                        [[x["instance"], x["process"], x["oprid"], x["status"], x["run_time"], x["server"]] for x in rows]))
        t += [f"{x['instance']} {x['process']} {x['oprid']} {x['status']} {x['run_time']} {x['server']}" for x in rows] + [""]
    if inc.get("check"):
        c = inc["check"]
        h.append(_table(c["columns"], c["rows"][:10]))
        t += [" | ".join(c["columns"])] + [" | ".join(str(x) for x in row) for row in c["rows"][:10]] + [""]
    h.append('<h3 style="margin:12px 0 4px;color:#b45309">Action needed</h3><ol style="margin:2px 0 8px">'
             + "".join(f"<li>{_e(x)}</li>" for x in todo) + "</ol>")
    t += ["ACTION NEEDED"] + [f"{n}. {x}" for n, x in enumerate(todo, 1)] + [""]
    if d and d.get("code_fix"):
        h.append('<p style="margin:0"><b>Suggested code</b></p><pre style="background:#f3f4f6;padding:8px;white-space:pre-wrap">'
                 f'{_e(d["code_fix"])}</pre>')
        t += ["Suggested code:", d["code_fix"], ""]
    if inc.get("note"):
        h.append(f'<p style="color:#6b7280">{_e(inc["note"])}</p>')
        t += [inc["note"], ""]
    links = _links(ps_url, inc)
    if links:
        h.append("<p>" + " &nbsp; ".join(f'<a href="{_e(u)}" style="background:#4f46e5;color:#fff;padding:6px 12px;'
                                         f'border-radius:4px;text-decoration:none">{_e(n)}</a>' for n, u in links) + "</p>")
        t += [f"{n}: {u}" for n, u in links]
    h.append('<p style="color:#9ca3af;font-size:12px">Sent by the PeopleSoft Agent watcher. It only reads: nothing was '
             "rerun or changed.</p></div>")
    return subject, "".join(h), "\n".join(t)


def _table(cols, rows):
    th = "".join(f'<th style="text-align:left;padding:3px 8px;border-bottom:1px solid #d1d5db">{_e(c)}</th>' for c in cols)
    tr = "".join("<tr>" + "".join(f'<td style="padding:3px 8px;border-bottom:1px solid #f3f4f6">{_e(x)}</td>' for x in row)
                 + "</tr>" for row in rows)
    return f'<table style="border-collapse:collapse;font-size:13px;margin:4px 0 10px"><tr>{th}</tr>{tr}</table>'


def summary_email(incidents, counts, ps_url=""):
    ran = sum(counts.values())
    failed = counts.get("Error", 0) + counts.get("No Success", 0)
    head = f"Last 24 hours: {ran} processes ended, {failed} failed, {len(incidents)} alert(s)."
    subject = f"[PeopleSoft Agent] Daily summary: {failed} failed, {len(incidents)} alert(s)"
    h = [f'<div style="{_BOX}">', f'<h2 style="margin:0 0 8px">Daily summary</h2><p>{_e(head)}'
         + ("" if incidents else " All quiet.") + "</p>"]
    t = [head, ""]
    if counts:
        h.append(_table(["Status", "Processes"], sorted(counts.items(), key=lambda x: -x[1])))
        t += [f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])] + [""]
    if incidents:
        h.append(_table(["Found", "Alert", "Analysis"],
                        [[i["found"][5:16], i["title"], (i.get("summary") or i.get("note") or "")[:300]] for i in incidents[:15]]))
        t += [f"{i['found'][5:16]}  {i['title']}: {(i.get('summary') or i.get('note') or '')[:300]}" for i in incidents[:15]]
    links = _links(ps_url)
    if links:
        h.append("<p>" + " &nbsp; ".join(f'<a href="{_e(u)}">{_e(n)}</a>' for n, u in links) + "</p>")
        t += [""] + [f"{n}: {u}" for n, u in links]
    h.append("</div>")
    return subject, "".join(h), "\n".join(t)


# ----- log files as the service user --------------------------------------------------

class ReportFiles:
    """Downloads posted log files the way View Log/Trace does, signed in as a PeopleSoft service user that has
    only Process Monitor and Report Manager access (PSAGENT_SVC_USER / PSAGENT_SVC_PASSWORD).
    ps_url: the PeopleSoft address users sign in on, e.g. http://host:8000/psp/ps/EMPLOYEE/HRMS/."""

    BIG = 2_000_000
    PART = 150_000
    SEND = 60_000

    def __init__(self, ps_url, user, password):
        m = re.match(r"^(https?://[^/]+)(/psp/[^/]+/)", ps_url or "")
        if not m:
            raise ValueError("PSAGENT_PS_URL must look like http://host:port/psp/<site>/...")
        self.origin, self.site, self.user, self._password = m.group(1), m.group(2), user, password
        self._jar = http.cookiejar.CookieJar()
        self._open = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar)).open
        self._signed_in = False
        self._lock = threading.Lock()

    def _request(self, url, data=None, headers=None):
        h = {"User-Agent": "psagent-watcher", "ngrok-skip-browser-warning": "1", **(headers or {})}
        return self._open(urllib.request.Request(url, data=data, headers=h), timeout=30)

    def sign_in(self):
        url = f"{self.origin}{self.site}?cmd=login&languageCd=ENG"
        with self._request(url) as r:
            page = r.read().decode("utf-8", "replace")
        form = {}
        for tag in re.findall(r"<input[^>]*type=\"hidden\"[^>]*>", page, re.I):
            name, value = re.search(r'name="([^"]*)"', tag), re.search(r'value="([^"]*)"', tag)
            if name:
                form[name.group(1)] = unescape(value.group(1)) if value else ""
        form.update(userid=self.user, pwd=self._password)
        with self._request(url, urllib.parse.urlencode(form).encode(),
                           {"Content-Type": "application/x-www-form-urlencoded"}) as r:
            r.read()
        if not any(c.name == "PS_TOKEN" for c in self._jar):
            raise PermissionError(f"PeopleSoft sign-in as {self.user} failed (check PSAGENT_SVC_USER / _PASSWORD)")
        self._signed_in = True

    def fetch(self, files):
        """files: troubleshoot.log_files() -> [{name, text, part}] for the troubleshooter."""
        out = []
        with self._lock:
            for lf in files[:6]:
                try:
                    out.append(self._one(lf))
                except (urllib.error.URLError, OSError, PermissionError) as e:
                    log.warning("log file %s: %s", lf.get("name"), e)
        return out

    def _one(self, lf, retry=True):
        if not self._signed_in:
            self.sign_in()
        size = int(lf.get("size") or 0)
        big = size > self.BIG
        with self._request(self.origin + lf["path"], headers={"Range": f"bytes=-{self.PART}"} if big else None) as r:
            text = r.read(self.PART if big else self.BIG).decode("utf-8", "replace")
            html = "html" in (r.headers.get("Content-Type") or "").lower() and not lf["name"].lower().endswith((".htm", ".html"))
            partial = r.status == 206
        if html:                         # the sign-on page: the session ended
            if not retry:
                raise PermissionError(f"{self.user} cannot open {lf['name']} (no Report Manager access?)")
            self._signed_in = False
            return self._one(lf, retry=False)
        part = (f"last {len(text) // 1024} KB of {size // 1024} KB" if partial else
                f"first {len(text) // 1024} KB of {size // 1024} KB") if big else ""
        if len(text) > self.SEND:
            text = text[:self.SEND // 4] + "\n…(middle of the file left out)…\n" + text[-self.SEND * 3 // 4:]
        return {"name": lf["name"], "text": text, "part": part}
