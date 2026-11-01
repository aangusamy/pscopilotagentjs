import base64
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from fakes import FakeLLM, response, text, tool
from psagent import audit, auth, runbooks
from psagent.agent import Agent, Reply, Session
from psagent.server import create_app, feature_of
from psagent.tasks import load_tasks

TASKS = load_tasks(Path(__file__).parent / "fixtures" / "tasks")
SECRET = "s3cret"
NOW = datetime(2026, 10, 30, 15, 0, 0)


def usage(i=0, o=0, cw=0, cr=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw, cache_read_input_tokens=cr)


class FakeMailer:
    def __init__(self):
        self.sent = []
        self.ps_url = "https://ps.example.com/psp/ps/EMPLOYEE/HRMS/"
        self.default_to = ["team@example.com"]

    def send(self, to, subject, html, text):
        self.sent.append((list(to), subject, html, text))
        return True


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = audit.Audit(self.tmp.name, days=90)
        audit.install(self.a)

    def tearDown(self):
        audit.install(None)
        self.a._db.close()
        self.tmp.cleanup()

    def add(self, when, event, oprid="VELU", feature="chat", **kw):
        self.a.add(event, kw.pop("detail", ""), kw.pop("outcome", "ok"), kw.pop("ref", ""), kw.pop("data", None),
                   ts=when, oprid=oprid, feature=feature, **kw)


class RecordingTest(AuditCase):
    def test_nothing_is_recorded_without_an_audit(self):
        audit.install(None)
        audit.record("chat", "Sent a message")
        audit.claude("claude-opus-5-5", usage(1000, 100), 1.0)
        audit.install(self.a)
        self.assertEqual(self.a.events(kind="all")["events"], [])

    def test_who_and_which_feature_come_from_the_context(self):
        with audit.scope("VELU", "chat", "sess1"):
            audit.record("chat", "Sent a message")
            audit.bind(feature="tasks")
            audit.record("task.run", "Create User Profile: done", ref="r1")
        audit.record("weekly.email")      # outside any request: the service's own
        e = {x["event"]: x for x in self.a.events(kind="all")["events"]}
        self.assertEqual((e["chat"]["oprid"], e["chat"]["feature"], e["chat"]["session"]), ("VELU", "chat", "sess1"))
        self.assertEqual((e["task.run"]["feature"], e["task.run"]["ref"]), ("tasks", "r1"))
        self.assertEqual((e["weekly.email"]["oprid"], e["weekly.email"]["feature"]), ("PSAGENT", "system"))

    def test_claude_cost_from_the_tokens(self):
        # Opus 5.5: $4 input, $20 output, $5 cache write, $0.20 cache read per million tokens
        self.assertAlmostEqual(self.a.cost("claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 100_000,
                                                               "cache_creation_input_tokens": 200_000,
                                                               "cache_read_input_tokens": 1_000_000}), 4 + 2 + 1 + 0.2)
        self.assertEqual(self.a.cost("some-other-model", {"input_tokens": 1000}), 0.0)
        with audit.scope("VELU", "troubleshoot"):
            audit.claude("claude-opus-5-5", usage(2000, 500, 10000, 30000), 2.5)
            audit.claude("claude-opus-5-5", None, 0.3, "API error 529: overloaded")
        ok, err = sorted(self.a.events(kind="claude")["events"], key=lambda x: x["id"])
        self.assertEqual((ok["tin"], ok["tout"], ok["tcw"], ok["tcr"], ok["ms"], ok["feature"]), (2000, 500, 10000, 30000, 2500, "troubleshoot"))
        self.assertAlmostEqual(ok["cost"], (2000 * 4 + 500 * 20 + 10000 * 5 + 30000 * 0.2) / 1e6)
        self.assertEqual((err["outcome"], err["cost"], err["detail"]), ("error", 0, "API error 529: overloaded"))

    def test_price_overrides(self):
        p = audit.parse_prices("claude-opus-5-5=3/15;my-model=1/2/1.25/0.1;bad=x")
        self.assertEqual(p["claude-opus-5-5"], (3.0, 15.0, 3.75, 0.3))
        self.assertEqual(p["my-model"], (1.0, 2.0, 1.25, 0.1))
        self.assertNotIn("bad", p)

    def test_queries_keep_the_sql_and_who_ran_them(self):
        with audit.scope("VELU", "troubleshoot"):
            audit.query("VELU", "process 557089", "SELECT *\n  FROM PSPRCSRQST WHERE PRCSINSTANCE = :1", 1, 0.04)
            audit.query("PSAGENT_SVC", "log files", "SELECT 1 FROM DUAL", None, 0.01, "ORA-00942: table or view does not exist")
        q = sorted(self.a.events(kind="queries")["events"], key=lambda x: x["id"])
        self.assertEqual((q[0]["detail"], q[0]["n"], q[0]["data"]["sql"]), ("process 557089", 1, "SELECT * FROM PSPRCSRQST WHERE PRCSINSTANCE = :1"))
        self.assertEqual((q[1]["oprid"], q[1]["outcome"], q[1]["data"]["as"]), ("VELU", "error", "PSAGENT_SVC"))
        self.assertIn("ORA-00942", q[1]["data"]["error"])
        self.assertEqual(self.a.events()["events"], [], "queries are not actions")

    def test_a_broken_file_never_stops_the_agent(self):
        self.a._db.close()
        self.a._db = sqlite3.connect(":memory:", check_same_thread=False)   # no table: every insert fails
        with self.assertLogs("psagent", "WARNING") as logs:
            audit.record("chat")
            audit.record("chat")
        self.assertEqual(len(logs.output), 1, "warned once")


class ReportsTest(AuditCase):
    def setUp(self):
        super().setUp()
        d = NOW - timedelta(days=1)
        for who, sess in (("VELU", "s1"), ("VELU", "s1"), ("VELU", "s2"), ("ABSMITH", "s3")):
            self.add(d, "chat", who, session=sess, detail="Sent a message")
        self.add(d, "claude", "VELU", "tasks", model="claude-opus-5-5", tin=1000, tout=200, cost=0.50)
        self.add(d, "claude", "ABSMITH", "troubleshoot", model="claude-opus-5-5", tin=5000, tout=900, cost=1.25)
        self.add(d, "claude", "PSAGENT_WATCH", "watcher", model="claude-opus-5-5", cost=0.75)
        self.add(d, "task.run", "VELU", "tasks", detail="Create User Profile: done", ref="r1")
        self.add(d, "task.run", "VELU", "tasks", detail="Create User Profile: Save. PeopleSoft said: already exists",
                 outcome="failed", ref="r2")
        self.add(d, "query", "ABSMITH", "troubleshoot", detail="process 557089", n=1, data={"sql": "SELECT 1 FROM PSPRCSRQST"})
        self.add(d, "incident", "PSAGENT_WATCH", "watcher", detail="AEMINITEST failed", outcome="alert")
        self.add(NOW - timedelta(days=9), "claude", "VELU", "chat", cost=2.00)          # the week before
        self.add(NOW - timedelta(days=200), "chat", "OLDUSER", detail="Sent a message")  # past the retention

    def test_overview(self):
        o = self.a.overview(7, NOW, budget=10)
        t = o["totals"]
        self.assertEqual((t["users"], t["conversations"], t["messages"], t["runs"], t["runs_ok"], t["runs_failed"]), (2, 3, 4, 2, 1, 1))
        self.assertEqual((t["claude_calls"], t["queries"], t["failures"], t["incidents"]), (3, 1, 1, 1))
        self.assertAlmostEqual(t["cost"], 2.50)
        self.assertAlmostEqual(o["cost_before"], 2.00)
        self.assertEqual([d["day"] for d in o["daily"]][-2:], ["2026-10-29", "2026-10-30"])
        self.assertEqual(len(o["daily"]), 7)
        self.assertEqual(o["daily"][-2]["cost"], 2.5)
        self.assertEqual([u["oprid"] for u in o["top_users"]], ["ABSMITH", "VELU"], "the watcher is not a user")
        self.assertEqual(o["features"][0]["label"], "Troubleshooter")
        self.assertEqual((o["month"]["cost"], o["month"]["budget"], o["month"]["share"]), (4.5, 10, 0.45), "October so far")

    def test_usage_by_user_and_by_feature(self):
        u = self.a.usage(7, "user", NOW)
        rows = {r["k"]: r for r in u["rows"]}
        self.assertEqual((rows["VELU"]["messages"], rows["VELU"]["conversations"], rows["VELU"]["runs"], rows["VELU"]["failures"]), (3, 2, 2, 1))
        self.assertEqual(rows["VELU"]["per_conversation"], 0.25)
        self.assertTrue(rows["PSAGENT_WATCH"]["system"])
        self.assertAlmostEqual(u["total"], 2.5)
        f = {r["k"]: r for r in self.a.usage(7, "feature", NOW)["rows"]}
        self.assertEqual((f["troubleshoot"]["label"], f["troubleshoot"]["cost"], f["troubleshoot"]["queries"]), ("Troubleshooter", 1.25, 1))

    def test_audit_log_filters_and_pages(self):
        all_actions = self.a.events()["events"]
        self.assertTrue(all(e["event"] not in ("claude", "query") for e in all_actions))
        self.assertEqual({e["oprid"] for e in self.a.events(user="absmith")["events"]}, {"ABSMITH"})
        self.assertEqual([e["ref"] for e in self.a.events(outcome="failed")["events"]], ["r2"])
        self.assertEqual([e["ref"] for e in self.a.events(text="already exists")["events"]], ["r2"])
        self.assertEqual({e["feature"] for e in self.a.events(feature="watcher", kind="all")["events"]}, {"watcher"})
        self.assertEqual(len(self.a.events(since="2026-10-29", until="2026-10-29", kind="claude")["events"]), 3)
        page1 = self.a.events(kind="all", limit=5)
        self.assertTrue(page1["more"])
        page2 = self.a.events(kind="all", limit=5, before=page1["events"][-1]["id"])
        self.assertFalse({e["id"] for e in page1["events"]} & {e["id"] for e in page2["events"]})
        self.assertIn("OLDUSER", self.a.users())

    def test_failures(self):
        f = self.a.failures(7, NOW)
        self.assertEqual([x["ref"] for x in f["failures"]], ["r2"])
        self.assertEqual(f["by_feature"], [("Tasks", 1)])

    def test_purge_after_the_retention_period(self):
        self.assertEqual(self.a.purge(NOW), 1)
        self.assertNotIn("OLDUSER", self.a.users())

    def test_excel_exports(self):
        ev = runbooks.xlsx_text(audit.events_xlsx(self.a.events(kind="all")["events"]))
        self.assertIn("Time | User | Feature | Event | Details", ev)
        self.assertIn("SELECT 1 FROM PSPRCSRQST", ev)
        us = runbooks.xlsx_text(audit.usage_xlsx(self.a.usage(7, "user", NOW)))
        self.assertIn("ABSMITH", us)
        self.assertIn("Total", us)


class JobsTest(AuditCase):
    def test_purge_once_a_day(self):
        jobs = audit.Jobs(self.a, weekly="")
        self.assertEqual(jobs.tick(NOW), ["purged"])
        self.assertEqual(jobs.tick(NOW + timedelta(hours=1)), [])
        self.assertEqual(jobs.tick(NOW + timedelta(days=1)), ["purged"])

    def test_budget_alerts_at_80_and_100_percent_once_a_month(self):
        mailer = FakeMailer()
        jobs = audit.Jobs(self.a, mailer, budget=100, to=["finance@example.com"], weekly="")
        self.add(NOW, "claude", cost=50)
        self.assertEqual(jobs.tick(NOW), ["purged"])
        self.add(NOW, "claude", cost=35)
        self.assertEqual(jobs.tick(NOW), ["budget 0.8"])
        self.assertEqual(jobs.tick(NOW), [])
        self.add(NOW, "claude", cost=20)
        self.assertEqual(jobs.tick(NOW), ["budget 1.0"])
        self.assertEqual(jobs.tick(NOW), [])
        self.assertEqual([m[0] for m in mailer.sent], [["finance@example.com"]] * 2)
        self.assertIn("passed 80% of the October 2026 budget: $85.00 of $100.00", mailer.sent[0][1])
        self.assertIn("reached the October 2026 budget: $105.00 of $100.00", mailer.sent[1][1])
        self.assertEqual([e["event"] for e in self.a.events(feature="audit")["events"]], ["budget.alert", "budget.alert"])

    def test_straight_to_100_percent_sends_one_alert(self):
        mailer = FakeMailer()
        jobs = audit.Jobs(self.a, mailer, budget=10, weekly="")
        self.add(NOW, "claude", cost=12)
        jobs.tick(NOW)
        jobs.tick(NOW)
        self.assertEqual(len(mailer.sent), 1)
        self.assertIn("reached", mailer.sent[0][1])
        self.assertEqual(mailer.sent[0][0], [], "no PSAGENT_AUDIT_TO: the mailer's default recipients")

    def test_weekly_email_once_a_week(self):
        mailer = FakeMailer()
        jobs = audit.Jobs(self.a, mailer, weekly="Mon 08:00")
        self.add(NOW - timedelta(days=1), "chat", "VELU", session="s1")
        self.add(NOW - timedelta(days=1), "claude", "VELU", cost=1.5)
        self.add(NOW - timedelta(days=1), "task.run", "VELU", "tasks", outcome="failed", detail="Create User Profile: Save")
        monday = datetime(2026, 11, 2, 7, 59)
        self.assertNotIn("weekly", jobs.tick(monday))
        self.assertIn("weekly", jobs.tick(monday.replace(hour=8, minute=1)))
        self.assertNotIn("weekly", jobs.tick(monday.replace(hour=9)))
        self.assertIn("weekly", jobs.tick(monday + timedelta(days=7, hours=1)))
        subject, html, body = mailer.sent[0][1:]
        self.assertIn("Week of 2026-10-27: 1 users, 1 task runs, $1.50", subject)
        self.assertIn("VELU", html)
        self.assertIn("Create User Profile: Save", body)


class ApiTest(AuditCase):
    def client(self, llm, **kw):
        self.agent = Agent(llm, TASKS)
        return TestClient(create_app(self.agent, SECRET, ["http://ps"], audit_log=self.a, **kw))

    @staticmethod
    def h(oprid, *roles):
        return {"Authorization": "Bearer " + auth.make_token(SECRET, oprid, roles=list(roles))}

    def test_paths_to_features(self):
        self.assertEqual([feature_of(p) for p in ("/api/chat", "/api/confirm", "/api/troubleshoot/step", "/api/watchdogs/action",
                                                  "/api/templates/watch_plan", "/api/regress/next", "/api/audit/export", "/api/x")],
                         ["chat", "tasks", "troubleshoot", "watcher", "watcher", "regression", "audit", "chat"])

    def test_only_auditors_open_the_dashboard(self):
        c = self.client(FakeLLM())
        self.assertFalse(c.get("/api/me", headers=self.h("BOSS", "ADMIN", "MONITOR", "USER")).json()["can_audit"])
        self.assertEqual(c.get("/api/audit", headers=self.h("BOSS", "ADMIN", "MONITOR", "USER")).status_code, 403)
        h = self.h("AUD", "AUDITOR")
        self.assertTrue(c.get("/api/me", headers=h).json()["can_audit"])
        for tab in ("overview", "usage", "events", "failures", "ops"):
            self.assertEqual(c.get(f"/api/audit?tab={tab}&days=30", headers=h).status_code, 200, tab)
        self.assertEqual(c.get("/api/audit?tab=nope", headers=h).status_code, 400)
        # in production the PSAGENT_AUDITORS list counts for nothing: only the role does
        old = {"Authorization": "Bearer " + auth.make_token(SECRET, "AUD2")}
        c2 = self.client(FakeLLM(), auditors={"AUD2"})
        self.assertEqual(c2.get("/api/audit", headers=old).status_code, 403)
        # in development (no token, PSAGENT_DEV_OPRID) it still adds the right
        c4 = TestClient(create_app(Agent(FakeLLM(), TASKS), "", ["http://ps"], dev_oprid="AUD2", audit_log=self.a, auditors={"AUD2"}))
        self.assertEqual(c4.get("/api/audit").status_code, 200)
        # the audit log switched off
        c3 = TestClient(create_app(Agent(FakeLLM(), TASKS), SECRET, ["http://ps"]))
        self.assertFalse(c3.get("/api/me", headers=h).json()["can_audit"])
        self.assertEqual(c3.get("/api/audit", headers=h).status_code, 503)
        audit.install(self.a)

    def test_a_task_run_end_to_end_in_the_audit_log(self):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "VELU2"}, {"name": "description", "value": "Velu A"},
                                                    {"name": "roles", "value": "EMPLOYEE"}]}, id="b"),
                     tool("request_confirmation", {}, id="c")),
            response(text("Please confirm.")),
            response(text("Created VELU2.")))
        c = self.client(llm)
        h = self.h("VELU", "USER")
        c.get("/api/me", headers=h)
        c.get("/api/me", headers=h)                   # one sign-in per half hour
        r = c.post("/api/chat", json={"message": "create user VELU2 with my secret note"}, headers=h).json()
        run = c.post("/api/confirm", json={"session_id": r["session_id"], "run_id": r["confirm"]["run_id"], "approved": True},
                     headers=h).json()["action"]["run_id"]
        c.post("/api/result", json={"session_id": r["session_id"], "run_id": run, "status": "success"}, headers=h)
        ev = {e["event"]: e for e in self.a.events(kind="all")["events"]}
        self.assertEqual([e["event"] for e in reversed(self.a.events()["events"])], ["signin", "chat", "task.confirmed", "task.run"])
        self.assertEqual((ev["chat"]["detail"], ev["chat"]["session"]), ("Sent a message", r["session_id"]))
        self.assertEqual(ev["task.confirmed"]["data"]["values"][0], {"label": "User ID", "value": "VELU2"})
        self.assertEqual((ev["task.run"]["outcome"], ev["task.run"]["detail"], ev["task.run"]["feature"]),
                         ("ok", "Create User Profile: done", "tasks"))
        raw = self.a.path.read_bytes() + (self.a.path.with_name("psagent.db-wal").read_bytes()
                                          if self.a.path.with_name("psagent.db-wal").exists() else b"")
        self.assertNotIn(b"secret note", raw, "what users type is not kept")

    def test_auditor_asks_in_the_chat(self):
        self.add(datetime.now(), "claude", "ABSMITH", "troubleshoot", cost=1.25)
        self.add(datetime.now(), "claude", "VELU", "tasks", cost=0.40)
        args = {"kind": "usage_by_user", "days": 7, "user": "", "feature": "", "text": ""}
        llm = FakeLLM(response(tool("audit_report", args)), response(text("ABSMITH spent the most: $1.25.")),
                      response(tool("audit_report", args, id="t2")), response(text("I can't show that.")))
        c = self.client(llm)
        r = c.post("/api/chat", json={"message": "who spent the most this week?"}, headers=self.h("AUD", "AUDITOR")).json()
        self.assertEqual(r["messages"], ["ABSMITH spent the most: $1.25."])
        result = llm.calls[1][-1]["content"][0]
        self.assertNotIn("is_error", result)
        self.assertIn('"label": "ABSMITH"', result["content"])
        c.post("/api/chat", json={"message": "who spent the most?"}, headers=self.h("JOE", "USER"))
        self.assertTrue(llm.calls[3][-1]["content"][0]["is_error"])
        self.assertIn("PSAGENT_AUDITOR", llm.calls[3][-1]["content"][0]["content"])

    def test_export(self):
        self.add(datetime.now(), "task.run", "VELU", "tasks", detail="Create User Profile: done")
        c = self.client(FakeLLM())
        r = c.get("/api/audit/export?tab=events", headers=self.h("AUD", "AUDITOR")).json()
        self.assertEqual(r["name"], "psagent-audit-log.xlsx")
        self.assertIn("Create User Profile: done", runbooks.xlsx_text(base64.b64decode(r["data"])))
        r = c.get("/api/audit/export?tab=usage&by=feature", headers=self.h("AUD", "AUDITOR")).json()
        self.assertTrue(r["name"].startswith("psagent-usage-by-feature-"))
        self.assertEqual(self.a.events(feature="audit")["events"][0]["event"], "audit.export")


class AgentEventsTest(AuditCase):
    def test_tools_move_the_turn_to_their_feature(self):
        agent, s = Agent(FakeLLM(), TASKS), Session(id="s", oprid="VELU")
        with audit.scope("VELU", "chat"):
            agent._run_tool(s, "start_task", {"task_id": "create_user_profile"}, Reply())
            audit.claude("claude-opus-5-5", usage(100, 10), 0.1)
        self.assertEqual(self.a.events(kind="claude")["events"][0]["feature"], "tasks")


if __name__ == "__main__":
    unittest.main()
