import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from starlette.testclient import TestClient

from fakes import FakeLLM, response, text, tool
from psagent import auth, monitors, runbooks
from psagent.agent import Agent
from psagent.db import check_select
from psagent.server import create_app
from psagent.tasks import load_tasks
from psagent.watcher import ReportFiles, Watcher, incident_email, summary_email

TASKS = load_tasks(Path(__file__).parent / "fixtures" / "tasks")
SECRET = "s3cret"


class WatchDB:
    """Answers the watcher's and the drafts' queries by their purpose."""

    def __init__(self):
        self.failed = [(557089, "Application Engine", "AEMINITEST", "PS", "test", "10", "2026-10-28 17:56:52")]
        self.stuck = [(557022, "Application Engine", "PSXPARCHATTR", "PS", " ", "5", "2026-07-23 20:56:28", " ")]
        self.check_rows = [["JSMITH"]]
        self.servers = [("PRCS8622", "3", "2026-10-30 22:05:08")]
        self.queries = []

    def query(self, sql, params=None, max_rows=200, who="", why="", **kw):
        check_select(sql)
        self.queries.append((why, sql, params))
        if "failures" in why or (why.startswith("monitor ") and "RUNSTATUS IN ('3', '10')" in sql):
            return ["PRCSINSTANCE", "PRCSTYPE", "PRCSNAME", "OPRID", "RUNCNTLID", "RUNSTATUS", "ENDED"], list(self.failed), False
        if "stuck" in why or (why.startswith("monitor ") and "RUNDTTM <" in sql):
            return ["PRCSINSTANCE", "PRCSTYPE", "PRCSNAME", "OPRID", "RUNCNTLID", "RUNSTATUS", "RUNDTTM", "SERVER"], list(self.stuck), False
        if why == "process server status":
            return ["SERVERNAME", "SERVERSTATUS", "LASTUPDDTTM"], list(self.servers), False
        if why == "daily summary":
            return ["RUNSTATUS", "N"], [("9", 28), ("10", 1)], False
        if why == "monitor draft preview (deadline)":
            return ["N"], [[3]], False
        if why.endswith("(latest runs)"):
            return ["PRCSINSTANCE", "PRCSTYPE", "PRCSNAME", "OPRID", "RUNCNTLID", "RUNSTATUS", "RUNDTTM", "ENDED"], [], False
        return ["OPRID"], list(self.check_rows), False


class FakeTroubleshooter:
    def __init__(self):
        self.started = []

    def log_files(self, instance):
        return [{"name": f"AE_{instance}.log", "path": f"/psreports/ps/1/AE_{instance}.log", "size": 100}]

    def start(self, who, instance, question=""):
        self.started.append((who, instance))
        return {"instance": instance}

    def step(self, inv, who, logs=None):
        self.logs = logs
        return {"steps": [], "done": True, "diagnosis": {
            "category": "code", "confidence": "high", "summary": "Step02 divides by zero.", "where": "AEMINITEST.MAIN.Step02",
            "root_cause": "1 / 0 at statement 3", "evidence": ["Decimal division by zero (180,109)"],
            "fix_steps": ["Remove line 5 of Step02 OnExecute."], "code_fix": "", "rerun": "Submit AEMINITEST again.",
            "instance": inv["instance"], "queries": []}}


class FakeMailer:
    host, ps_url = "smtp.test", "http://ps:8000/psp/ps/EMPLOYEE/HRMS/"

    def __init__(self, default_to=("ops@example.com",)):
        self.default_to, self.sent = list(default_to), []

    def send(self, to, subject, html, txt):
        self.sent.append({"to": list(to) or self.default_to, "subject": subject, "html": html, "text": txt})
        return True


class FakeReports:
    user = "PSAGENT_SVC"

    def fetch(self, files):
        return [{"name": f["name"], "text": "Decimal division by zero occurred.", "part": ""} for f in files]


def mon(**kw):
    m, card, problems = monitors.check(kw, WatchDB(), "VELU", email=True, default_to=["ops@example.com"])
    assert not problems, problems
    return m


class RolesTokenTest(unittest.TestCase):
    def test_roles_round_trip_and_old_tokens(self):
        self.assertEqual(auth.identify(auth.make_token(SECRET, "VELU", roles=["ADMIN", "USER"]), SECRET),
                         ("VELU", frozenset({"ADMIN", "USER"})))
        self.assertEqual(auth.identify(auth.make_token(SECRET, "VELU"), SECRET), ("VELU", None))        # first iScript
        self.assertEqual(auth.identify(auth.make_token(SECRET, "A.B", roles=["MONITOR"]), SECRET), ("A.B", frozenset({"MONITOR"})))
        self.assertEqual(auth.identify(auth.make_token(SECRET, "VELU", roles=[]), SECRET), ("VELU", frozenset()))

    def test_roles_cannot_be_changed(self):
        good = auth.make_token(SECRET, "VELU", roles=["USER"])
        with self.assertRaises(auth.AuthError):
            auth.identify(good.replace(".USER.", ".ADMIN+USER."), SECRET)
        old = auth.make_token(SECRET, "VELU")              # an old token with roles pushed in does not verify either
        oprid, day, sig = old.split(".")
        with self.assertRaises(auth.AuthError):
            auth.identify(f"{oprid}.{day}.ADMIN.{sig}", SECRET)

    def test_matches_the_iscript_hash(self):
        # HashSHA256(secret|OPRID|day|roles) as the iScript computes it (UTF-16LE, "{9}" prefix)
        import base64
        import hashlib
        day = datetime.now().strftime("%Y%m%d")
        sig = base64.b64encode(hashlib.sha256(f"{SECRET}|PS|{day}|ADMIN+MONITOR".encode("utf-16-le")).digest()).decode()
        self.assertEqual(auth.identify(f"PS.{day}.ADMIN+MONITOR.{{9}}{sig}", SECRET), ("PS", frozenset({"ADMIN", "MONITOR"})))


class MonitorDraftTest(unittest.TestCase):
    def test_failures_preview_and_description(self):
        m, card, problems = monitors.check({"title": "Payroll failures", "kind": "failures", "processes": ["PSPCALC", "pay*"],
                                            "users": ["ps"], "notify": ["Payroll@Example.com"], "investigate": True},
                                           WatchDB(), "VELU", email=True)
        self.assertEqual(problems, [])
        self.assertEqual((m["id"], m["processes"], m["users"], m["notify"], m["every_minutes"]),
                         ("payroll_failures", ["PSPCALC", "PAY%"], ["PS"], ["payroll@example.com"], 5))
        self.assertEqual(card["preview"]["label"], "Would have alerted 1 time in the last 30 days")
        self.assertEqual(card["preview"]["sample"][0][:2], [557089, "AEMINITEST"])
        self.assertIn("PSPCALC, PAY%", card["what"])

    def test_problems(self):
        _, _, problems = monitors.check({"title": "x y", "kind": "check", "sql": "SELECT OPRID FROM PSOPRDEFN WHERE OPRID = :u",
                                         "notify": ["not-an-email"]}, WatchDB(), "VELU")
        self.assertTrue(any("no inputs" in p for p in problems), problems)
        self.assertTrue(any("not an email" in p for p in problems), problems)
        _, _, problems = monitors.check({"title": "Bad", "kind": "check", "sql": "DELETE FROM PSOPRDEFN"}, WatchDB(), "VELU")
        self.assertTrue(any("not allowed" in p for p in problems), problems)
        _, _, problems = monitors.check({"title": "Bad", "kind": "failures", "at": ["06:00"]}, WatchDB(), "VELU")
        self.assertTrue(any("set times of day only for checks" in p for p in problems), problems)
        _, _, problems = monitors.check({"title": "Bad", "kind": "sometimes"}, WatchDB(), "VELU")
        self.assertTrue(any("kind must be" in p for p in problems), problems)

    def test_zero_means_the_default(self):
        m, card, _ = monitors.check({"title": "Stuck", "kind": "stuck", "stuck_minutes": 0, "every_minutes": 0}, WatchDB(), "V")
        self.assertEqual((m["stuck_minutes"], m["every_minutes"], card["when"]), (30, 5, "Every 5 minutes"))
        m, _, _ = monitors.check({"title": "Check", "kind": "check", "sql": "SELECT 1 FROM DUAL", "every_minutes": 0}, WatchDB(), "V")
        self.assertEqual(m["every_minutes"], 60)

    def test_email_warnings(self):
        _, card, _ = monitors.check({"title": "Stuck", "kind": "stuck", "stuck_minutes": 30}, WatchDB(), "VELU", email=False)
        self.assertIn("PSAGENT_SMTP_HOST", card["warnings"][0])
        _, card, _ = monitors.check({"title": "Stuck", "kind": "stuck"}, WatchDB(), "VELU", email=True, default_to=["ops@x.com"])
        self.assertIn("ops@x.com", card["warnings"][0])

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            monitors.save_plan(d, monitors.single_plan(mon(title="Locked users", kind="check",
                                                           sql="SELECT OPRID FROM PSOPRDEFN WHERE ACCTLOCK = 1", at=["06:00"])))
            Path(d, "broken.yaml").write_text("kind: nonsense\n")
            # a file with one monitor, as saved before plans, still loads (as a plan of one)
            Path(d, "old_one.yaml").write_text("id: old_one\ntitle: Old one\nkind: stuck\nstuck_minutes: 30\nevery_minutes: 5\n")
            loaded = monitors.flatten(monitors.load_plans(d))
            self.assertEqual(sorted(loaded), ["locked_users.locked_users", "old_one.old_one"])
            self.assertEqual(loaded["locked_users.locked_users"]["at"], ["06:00"])


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db, self.ts, self.mail = WatchDB(), FakeTroubleshooter(), FakeMailer()

    def tearDown(self):
        self.tmp.cleanup()

    def watcher(self, *mons, **kw):
        for m in mons:
            monitors.save_plan(self.tmp.name, monitors.single_plan(m))
        kw.setdefault("summary_at", None)
        return Watcher(self.db, self.ts, self.tmp.name, self.mail, FakeReports(), **kw)

    def test_failure_is_investigated_and_emailed_once(self):
        w = self.watcher(mon(title="AE failures", kind="failures", processes=["AEMINITEST"], notify=["team@example.com"]))
        new = w.tick(1000.0)
        self.assertEqual(len(new), 1)
        inc = new[0]
        self.assertEqual(inc["title"], "AEMINITEST failed (process 557089)")
        self.assertEqual(self.ts.started, [("PSAGENT_WATCH", "557089")])
        self.assertEqual(self.ts.logs[0]["name"], "AE_557089.log")                 # log file read as the service user
        self.assertEqual(inc["summary"], "Step02 divides by zero.")
        mail = self.mail.sent[0]
        self.assertEqual(mail["to"], ["team@example.com"])
        self.assertEqual(mail["subject"], "[PeopleSoft Agent] AEMINITEST failed (process 557089)")
        for part in ("ANALYSIS", "Code issue, confidence high", "ACTION NEEDED", "1. Remove line 5 of Step02 OnExecute.",
                     "2. Submit AEMINITEST again.", "psagent=" + inc["id"]):
            self.assertIn(part, mail["text"])
        self.assertIn("Action needed", mail["html"])
        # the next tick sees the same failure again in its overlap window: nothing new
        self.assertEqual(w.tick(1000.0 + 300), [])
        self.assertEqual(len(self.mail.sent), 1)
        # filters went into the query as binds
        why, sql, params = [q for q in self.db.queries if q[0] == "monitor ae_failures.ae_failures"][0]
        self.assertIn("R.PRCSNAME LIKE :p0", sql)
        self.assertEqual(params["p0"], "AEMINITEST")

    def test_same_cause_counts_on_the_open_incident(self):
        w = self.watcher(mon(title="AE failures", kind="failures"))
        w.tick(1000.0)
        self.db.failed = [(557090, "Application Engine", "AEMINITEST", "PS", "test", "10", "2026-10-28 18:30:00")]
        self.assertEqual(w.tick(1400.0), [])
        self.assertEqual(w.incidents[0]["occurrences"], 2)
        self.assertEqual(w.incidents[0]["instances"], ["557089", "557090"])
        self.assertEqual(len(self.ts.started), 1)

    def test_daily_investigation_cap(self):
        w = self.watcher(mon(title="All failures", kind="failures"), max_investigations=0)
        inc = w.tick(1000.0)[0]
        self.assertEqual(self.ts.started, [])
        self.assertIn("daily limit", inc["note"])
        self.assertIn("Open the request in Process Monitor", self.mail.sent[0]["text"])

    def test_stuck_requests_with_server_status(self):
        w = self.watcher(mon(title="Stuck", kind="stuck", stuck_minutes=30))
        inc = w.tick(1000.0)[0]
        self.assertEqual(inc["title"], "PSXPARCHATTR stuck (process 557022)")
        self.assertIn("Every process server is running", inc["summary"])
        self.assertEqual(self.mail.sent[0]["to"], ["ops@example.com"])        # no recipients named: the default ones
        self.assertEqual(w.tick(1400.0), [])                                  # still stuck: not alerted again
        self.db.servers = [("PRCS8622", "2", "2026-10-30 22:05:08")]
        self.db.stuck.append((557023, "SQR Report", "XRFWIN", "PS", "x", "5", "2026-10-30 20:00:00", "PRCS8622"))
        inc = w.tick(1800.0)[0]
        self.assertIn("PRCS8622 (Down)", inc["summary"])

    def test_check_alerts_once_until_it_passes(self):
        w = self.watcher(mon(title="Locked users", kind="check", rule="No account is locked",
                             sql="SELECT OPRID FROM PSOPRDEFN WHERE ACCTLOCK = 1", every_minutes=60))
        inc = w.tick(1000.0)[0]
        self.assertEqual(inc["check"]["rows"], [["JSMITH"]])
        self.assertEqual(w.tick(1000.0 + 3600), [])
        self.db.check_rows = []
        w.tick(1000.0 + 7200)
        self.assertTrue(w.incidents[0]["cleared"])
        self.db.check_rows = [["ABSMITH"]]
        self.assertEqual(len(w.tick(1000.0 + 3 * 3600)), 1)

    def test_schedule_paused_and_reload(self):
        w = self.watcher(mon(title="Morning check", kind="check", sql="SELECT OPRID FROM PSOPRDEFN", at=["06:00"]),
                         mon(title="Stuck", kind="stuck"))
        six = datetime(2026, 10, 30, 6, 0).timestamp()
        w.set_paused("stuck.stuck", True)
        self.assertEqual(w.tick(six - 60), [])
        self.assertEqual({i["monitor"] for i in w.tick(six + 60)}, {"morning_check.morning_check"})
        self.assertEqual(w.tick(six + 3600), [])                               # once a day
        again = Watcher(self.db, self.ts, self.tmp.name, self.mail)
        self.assertTrue(again.monitors["stuck.stuck"]["paused"])                     # saved
        self.assertEqual([i["monitor"] for i in again.incidents], ["morning_check.morning_check"])   # incidents kept

    def test_daily_summary(self):
        w = self.watcher(summary_at="07:00")
        seven = datetime(2026, 10, 30, 7, 0).timestamp()
        w.tick(seven - 60)
        self.assertEqual(self.mail.sent, [])
        w.tick(seven + 60)
        w.tick(seven + 120)
        self.assertEqual(len(self.mail.sent), 1)
        self.assertIn("29 processes ended, 1 failed", self.mail.sent[0]["text"])


class DeadlineTest(unittest.TestCase):
    """'Must succeed by': at the time of day, no Success in the 24 hours before -> what happened instead."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db, self.mail = DeadlineDB(), FakeMailer()
        self.dog = mon(title="Portal sync by 07:00", kind="deadline", processes=["PORTAL_CSS"], deadline="07:00")
        monitors.save_plan(self.tmp.name, monitors.single_plan(self.dog))
        self.w = Watcher(self.db, FakeTroubleshooter(), self.tmp.name, self.mail, summary_at=None)
        self.seven = datetime(2026, 10, 30, 7, 0).timestamp()

    def tearDown(self):
        self.tmp.cleanup()

    def test_draft(self):
        self.assertEqual((self.dog["at"], self.dog["deadline"]), (["07:00"], "07:00"))
        _, card, problems = monitors.check({"title": "Sync", "kind": "deadline", "processes": ["PORTAL_CSS"], "deadline": "07:00"},
                                           self.db, "V")
        self.assertEqual(problems, [])
        self.assertEqual(card["preview"]["label"], "Succeeded on 3 of the last 30 days: would have alerted on about 27 days")
        self.assertEqual(card["when"], "Every day at 07:00")
        _, _, problems = monitors.check({"title": "Sync", "kind": "deadline", "processes": ["PORTAL%"]}, self.db, "V")
        self.assertTrue(any("exactly one process" in p for p in problems), problems)
        self.assertTrue(any("needs the time" in p for p in problems), problems)
        _, _, problems = monitors.check({"title": "Nope", "kind": "failures", "processes": ["NOSUCHPRC"]}, self.db, "V")
        self.assertEqual(problems, ["process NOSUCHPRC is not defined in Process Scheduler"])

    def test_succeeded_no_alert(self):
        self.db.succeeded = [[557093, "2026-10-29 21:43"]]
        self.assertEqual(self.w.tick(self.seven - 60), [])            # not yet 07:00
        self.assertEqual(self.w.tick(self.seven + 60), [])
        self.assertEqual(self.mail.sent, [])

    def test_failed_last_run_is_investigated(self):
        self.db.latest = [[557100, "Application Engine", "PORTAL_CSS", "PS", "sync", "3", "2026-10-30 05:00", "2026-10-30 05:01"]]
        inc = self.w.tick(self.seven + 60)[0]
        self.assertEqual(inc["title"], "PORTAL_CSS did not succeed by 07:00")
        self.assertEqual(inc["deadline_state"], "failed")
        self.assertIn("process 557100, ended in Error", inc["summary"])
        self.assertIn("Step02 divides by zero.", inc["summary"])
        self.assertIn("Latest runs", self.mail.sent[0]["html"])
        self.assertEqual(self.w.tick(self.seven + 120), [])           # once a day

    def test_never_ran_and_waiting(self):
        inc = self.w.tick(self.seven + 60)[0]
        self.assertEqual(inc["deadline_state"], "never")
        self.assertIn("Submit the process now", self.mail.sent[0]["text"])
        self.db.latest = [[557101, "Application Engine", "PORTAL_CSS", "PS", "sync", "5", "2026-10-30 05:00", "None"]]
        inc = self.w.run_now("portal_sync_by_07_00.portal_sync_by_07_00")[0]
        self.assertEqual(inc["deadline_state"], "waiting")
        self.assertIn("never started", inc["summary"])


class DeadlineDB(WatchDB):
    def __init__(self):
        super().__init__()
        self.succeeded, self.latest = [], []

    def query(self, sql, params=None, max_rows=200, who="", why="", **kw):
        check_select(sql)
        if why == "monitor draft process":
            return ["PRCSTYPE"], ([["Application Engine"]] if params["p"] != "NOSUCHPRC" else []), False
        if why == "monitor draft preview (deadline)":
            return ["N"], [[3]], False
        if why == "monitor draft preview (latest runs)" or why.endswith("(latest runs)"):
            return ["PRCSINSTANCE", "PRCSTYPE", "PRCSNAME", "OPRID", "RUNCNTLID", "RUNSTATUS", "RUNDTTM", "ENDED"], list(self.latest), False
        if why.startswith("monitor ") and "RUNSTATUS IN ('9', '17')" in sql:
            return ["PRCSINSTANCE", "ENDED"], list(self.succeeded), False
        return super().query(sql, params, max_rows, who, why, **kw)


class PlanTest(unittest.TestCase):
    """The watch plan: the template round trip, the overview with what changes, and managing its watchdogs."""

    ROWS = [{"title": "AE test program failures", "kind": "failures", "processes": ["AEMINITEST"], "notify": ["ops@example.com"]},
            {"title": "Cross-reference report stuck", "kind": "stuck", "processes": ["XRFWIN"], "stuck_minutes": 30},
            {"title": "Portal security sync by 07:00", "kind": "deadline", "processes": ["PORTAL_CSS"], "deadline": "07:00"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DeadlineDB()

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, rows=None, existing=None):
        return monitors.check_plan({"title": "Nightly jobs", "summary": "Overnight", "watchdogs": rows or self.ROWS},
                                   self.db, "VELU", email=True, default_to=["ops@example.com"], existing=existing,
                                   document="watch_plan.xlsx")

    def test_template_reads_back(self):
        text = runbooks.xlsx_text(monitors.template_xlsx())
        for part in ("1: Watch plan | Nightly jobs", "AEMINITEST | Failure | Every 5 minutes", "XRFWIN | Stuck | Every 5 minutes |  | 30",
                     "PORTAL_CSS | Must succeed by | Daily at | 07:00", "## Sheet: How to fill"):
            self.assertIn(part, text)

    def test_overview_and_round_trip(self):
        plan, card, problems = self.plan()
        self.assertEqual(problems, [])
        self.assertEqual([d["id"] for d in plan["watchdogs"]],
                         ["ae_test_program_failures", "cross_reference_report_stuck", "portal_security_sync_by_07_00"])
        self.assertEqual([c["n"] for c in card["watchdogs"]], [1, 2, 3])
        self.assertTrue(all(c.get("preview") for c in card["watchdogs"]))
        self.assertEqual(card["warnings"], ["Alerts go to the default recipients (ops@example.com)"])   # once, not per row
        # downloaded as Excel, it is the template again, with the plan's rows
        text = runbooks.xlsx_text(monitors.to_xlsx(plan))
        self.assertIn("Cross-reference report stuck | XRFWIN | Stuck | Every 5 minutes |  | 30", text)
        # re-uploaded with a change: the overview says what changes
        rows = [dict(r) for r in self.ROWS[:2]] + [{"title": "Locked users", "kind": "check", "rule": "None locked",
                                                     "sql": "SELECT OPRID FROM PSOPRDEFN WHERE ACCTLOCK = 1", "at": ["06:30"]}]
        rows[1]["stuck_minutes"] = 45
        _, card2, _ = self.plan(rows, existing=plan)
        self.assertEqual(card2["changes"], {"added": ["Locked users"], "removed": ["Portal security sync by 07:00"],
                                            "changed": [{"title": "Cross-reference report stuck", "fields": ["stuck_minutes"]}],
                                            "unchanged": 1})

    def test_plan_problems(self):
        _, _, problems = self.plan([self.ROWS[0], self.ROWS[0]])
        self.assertTrue(any("two watchdogs are called" in p for p in problems), problems)
        _, _, problems = self.plan([{"title": "x", "kind": "failures"}])
        self.assertTrue(any(p.startswith("row 1 (x)") for p in problems), problems)

    def test_manage(self):
        plan, _, _ = self.plan()
        w = Watcher(self.db, FakeTroubleshooter(), self.tmp.name, FakeMailer(), summary_at=None)
        w.add_plan(plan, "VELU")
        self.assertEqual(w.counts(), {"watchdogs": 3, "active": 3, "plans": 1, "alerts": 0, "stopped": False})
        w.set_paused("nightly_jobs.cross_reference_report_stuck", True, "VELU")
        self.assertEqual(w.counts()["active"], 2)
        w.delete("nightly_jobs.portal_security_sync_by_07_00", "VELU")
        saved = monitors.load_plans(self.tmp.name)["nightly_jobs"]
        self.assertEqual([(d["id"], d.get("paused", False)) for d in saved["watchdogs"]],
                         [("ae_test_program_failures", False), ("cross_reference_report_stuck", True)])
        w.set_stopped(True, "VELU")
        self.assertEqual(w.tick(), [])                                  # stopped: nothing runs
        self.assertTrue(Watcher(self.db, None, self.tmp.name).counts()["stopped"])   # and stays stopped after a restart
        w.set_stopped(False, "VELU")
        self.assertEqual(len(w.run_now("nightly_jobs.ae_test_program_failures", "VELU")), 1)
        w.delete_plan("nightly_jobs", "VELU")
        self.assertEqual((w.counts()["watchdogs"], list(Path(self.tmp.name).glob("*.yaml"))), (0, []))
        self.assertEqual([h["what"] for h in w.state["history"]][:3],
                         ["deleted the plan Nightly jobs", "ran AE test program failures now", "started watching again"])
        self.assertEqual(w.state["history"][0]["who"], "VELU")


class EmailTest(unittest.TestCase):
    def test_incident_email_escapes_and_links(self):
        inc = {"id": "inc-1", "monitor_title": "M", "kind": "check", "title": "<b>bad</b>", "found": "2026-10-30 06:00:00",
               "summary": "x & y", "check": {"columns": ["OPRID"], "rows": [["<script>"]]}}
        subject, html, txt = incident_email(inc, "http://ps/psp/ps/EMPLOYEE/HRMS/")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;b&gt;bad&lt;/b&gt;", html)
        self.assertIn("http://ps/psp/ps/EMPLOYEE/HRMS/h/?tab=DEFAULT&amp;psagent=inc-1", html)
        self.assertIn("Correct the rows listed above", txt)
        subject, html, txt = summary_email([], {"Success": 3})
        self.assertIn("All quiet", html)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = WatchDB()
        self.watcher = Watcher(self.db, FakeTroubleshooter(), self.tmp.name, FakeMailer())

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, llm):
        return TestClient(create_app(Agent(llm, TASKS, self.db), SECRET, ["http://ps"], watcher=self.watcher))

    @staticmethod
    def h(oprid, *roles):
        return {"Authorization": "Bearer " + auth.make_token(SECRET, oprid, roles=list(roles))}

    def test_no_role_no_access(self):
        c = self.client(FakeLLM())
        self.assertEqual(c.get("/api/me", headers=self.h("NOBODY")).status_code, 401)
        me = c.get("/api/me", headers=self.h("JOE", "USER")).json()
        self.assertEqual((me["roles"], me["can_teach"], me["can_monitor"], me["overnight"]), (["USER"], False, False, None))
        me = c.get("/api/me", headers=self.h("BOSS", "ADMIN")).json()
        self.assertEqual((me["can_teach"], me["can_monitor"]), (True, True))
        self.assertEqual(c.get("/api/incidents", headers=self.h("JOE", "USER")).status_code, 403)

    def test_set_up_a_monitor_in_the_chat_and_see_its_alert(self):
        draft = {"title": "AE failures", "summary": "Failures of the AE test program", "kind": "failures",
                 "processes": ["AEMINITEST"], "users": [], "stuck_minutes": 0, "investigate": True, "rule": "", "sql": "",
                 "expect": "", "every_minutes": 0, "at": [], "notify": ["team@example.com"]}
        llm = FakeLLM(response(tool("draft_monitor", draft)), response(text("Here is the monitor.")),
                      response(tool("show_incidents", {"hours": 16}, id="t2")), response(text("One failure overnight.")))
        c = self.client(llm)
        # a user without the monitor role is refused by the tool
        r = c.post("/api/chat", json={"message": "watch AEMINITEST"}, headers=self.h("JOE", "USER")).json()
        self.assertIsNone(r["monitor"])
        self.assertIn("PSAGENT_MONITOR", llm.calls[1][-1]["content"][0]["content"])
        llm.responses[:0] = [response(tool("draft_monitor", draft)), response(text("Here is the monitor."))]
        h = self.h("VELU", "MONITOR", "USER")
        r = c.post("/api/chat", json={"message": "watch AEMINITEST and email team@example.com"}, headers=h).json()
        card = r["monitor"]
        self.assertEqual(card["preview"]["label"], "Would have alerted 1 time in the last 30 days")
        self.assertEqual(c.post("/api/monitors/save", json={"session_id": r["session_id"], "draft_id": card["draft_id"]},
                                headers=self.h("JOE", "USER")).status_code, 403)
        saved = c.post("/api/monitors/save", json={"session_id": r["session_id"], "draft_id": card["draft_id"]}, headers=h)
        self.assertEqual(saved.json()["path"], "monitors/ae_failures.yaml")
        self.assertTrue(Path(self.tmp.name, "ae_failures.yaml").is_file())
        self.assertIn("ae_failures.ae_failures", self.watcher.monitors)
        # overnight the watcher raises an alert; the panel and the chat show it
        self.watcher.tick()
        me = c.get("/api/me", headers=h).json()
        self.assertEqual(me["overnight"]["count"], 1)
        inc_id = me["overnight"]["items"][0]["id"]
        one = c.get(f"/api/incidents?id={inc_id}", headers=h).json()["incidents"][0]
        self.assertEqual(one["diagnosis"]["summary"], "Step02 divides by zero.")
        self.assertEqual(one["actions"], ["Remove line 5 of Step02 OnExecute.", "Submit AEMINITEST again."])
        r = c.post("/api/chat", json={"session_id": r["session_id"], "message": "what happened overnight?"}, headers=h).json()
        self.assertEqual([i["id"] for i in r["incidents"]["items"]], [inc_id])
        self.assertEqual(r["incidents"]["hours"], 16)


class PlanApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DeadlineDB()
        self.watcher = Watcher(self.db, FakeTroubleshooter(), self.tmp.name, FakeMailer(), summary_at=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload_overview_confirm_manage_download(self):
        rows = [dict(r, summary="", users=[], stuck_minutes=r.get("stuck_minutes", 0), deadline=r.get("deadline", ""),
                     investigate=True, rule="", sql="", expect="", every_minutes=0, at=[], notify=r.get("notify", []),
                     paused=False, notes="") for r in PlanTest.ROWS]
        llm = FakeLLM(response(tool("draft_watch_plan", {"title": "Nightly jobs", "summary": "", "watchdogs": rows, "questions": []})),
                      response(text("3 watchdogs; AEMINITEST failed 1 time in the last 30 days.")))
        c = TestClient(create_app(Agent(llm, TASKS, self.db), SECRET, ["http://ps"], watcher=self.watcher))
        h = ApiTest.h("VELU", "MONITOR")
        user = ApiTest.h("JOE", "USER")
        t = c.get("/api/templates/watch_plan", headers=h).json()
        self.assertEqual(t["name"], "watch_plan_template.xlsx")
        self.assertEqual(c.get("/api/templates/watch_plan", headers=user).status_code, 404)
        r = c.post("/api/chat", json={"message": "Build a watch plan from this sheet"}, headers=h).json()
        card = r["watch_plan"]
        self.assertEqual((card["title"], len(card["watchdogs"]), card["exists"]), ("Nightly jobs", 3, False))
        self.assertEqual(c.post("/api/watchplans/save", json={"session_id": r["session_id"], "draft_id": card["draft_id"]},
                                headers=user).status_code, 403)
        saved = c.post("/api/watchplans/save", json={"session_id": r["session_id"], "draft_id": card["draft_id"]}, headers=h).json()
        self.assertEqual((saved["path"], saved["counts"]["watchdogs"]), ("monitors/nightly_jobs.yaml", 3))
        self.assertEqual(c.get("/api/me", headers=h).json()["overnight"]["watchdogs"]["active"], 3)
        m = c.get("/api/watchdogs", headers=h).json()
        self.assertEqual([d["title"] for d in m["plans"][0]["watchdogs"]],
                         ["AE test program failures", "Cross-reference report stuck", "Portal security sync by 07:00"])
        self.assertEqual(c.get("/api/watchdogs", headers=user).status_code, 403)
        a = lambda **kw: c.post("/api/watchdogs/action", json=kw, headers=h)   # noqa: E731
        self.assertEqual(a(action="pause", id="nightly_jobs.cross_reference_report_stuck").json()["counts"]["active"], 2)
        ran = a(action="run", id="nightly_jobs.ae_test_program_failures").json()
        self.assertEqual(ran["ran"][0]["title"], "AEMINITEST failed (process 557089)")
        self.assertEqual(a(action="delete", id="nightly_jobs.nope").status_code, 404)
        self.assertEqual(a(action="stop").json()["counts"]["stopped"], True)
        x = c.get("/api/watchplans/export?plan=nightly_jobs", headers=h).json()
        import base64
        sheet = runbooks.xlsx_text(base64.b64decode(x["data"]))
        self.assertIn("Cross-reference report stuck | XRFWIN | Stuck | Every 5 minutes |  | 30 |  |  |  |  | Paused", sheet)
        self.assertEqual(a(action="delete_plan", plan="nightly_jobs").json()["counts"]["watchdogs"], 0)
        self.assertEqual([h["who"] for h in m["history"]], ["VELU"])


class ReportFilesTest(unittest.TestCase):
    """The service user signs in like a browser (hidden fields such as lcsrf_token, then userid/pwd) and downloads
    the posted file; when the session has ended (the sign-on page comes back) it signs in again."""

    def test_sign_in_and_download(self):
        state = {"logins": 0, "expire": False}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if "cmd=login" in self.path:
                    return self._send(200, '<form><input type="hidden" name="lcsrf_token" value="abc=">'
                                           '<input type="text" name="userid"></form>', "text/html")
                signed = "PS_TOKEN=ok" in (self.headers.get("Cookie") or "")
                if not signed or state["expire"]:
                    state["expire"] = False
                    return self._send(200, "<html>sign on</html>", "text/html")
                self._send(200, "Decimal division by zero occurred.", "text/plain")

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                if "lcsrf_token=abc%3D" in body and "userid=PSAGENT_SVC" in body and "pwd=pw" in body:
                    state["logins"] += 1
                    self.send_response(302)
                    self.send_header("Set-Cookie", "PS_TOKEN=ok; Path=/")
                    self.send_header("Location", "/psp/ps/EMPLOYEE/HRMS/h/?tab=DEFAULT")
                    self.end_headers()
                    return
                self._send(200, "<html>bad password</html>", "text/html")

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                self.wfile.write(body.encode())

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}/psp/ps/EMPLOYEE/HRMS/"
            r = ReportFiles(base, "PSAGENT_SVC", "pw")
            files = [{"name": "AE_X_1.log", "path": "/psreports/ps/1/AE_X_1.log", "size": 40}]
            self.assertEqual(r.fetch(files)[0]["text"], "Decimal division by zero occurred.")
            state["expire"] = True
            self.assertEqual(r.fetch(files)[0]["text"], "Decimal division by zero occurred.")
            self.assertEqual(state["logins"], 2)
            self.assertEqual(ReportFiles(base, "PSAGENT_SVC", "wrong").fetch(files), [])   # logged, not raised
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
