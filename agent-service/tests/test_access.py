"""Who may use what (access.py): the four agent roles, checked by the service on every request."""
import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from fakes import FakeLLM, response, text, tool
from psagent import access, audit, auth, ci, queries
from psagent.agent import Agent
from psagent.server import create_app
from psagent.tasks import load_tasks
from test_queries import PTDB

SECRET = "s3cret"
TASKS = load_tasks(Path(__file__).parent / "fixtures" / "tasks")


def h(oprid, *roles):
    return {"Authorization": "Bearer " + auth.make_token(SECRET, oprid, roles=list(roles))}


class CapsTest(unittest.TestCase):
    def test_roles_build_on_each_other_auditor_apart(self):
        self.assertEqual(access.caps({"USER"}), {"work"})
        self.assertEqual(access.caps({"MONITOR"}), {"work", "monitor"})
        self.assertEqual(access.caps({"ADMIN"}), {"work", "monitor", "admin"})
        self.assertEqual(access.caps({"AUDITOR"}), {"audit"}, "auditors don't administer, administrators don't audit")
        self.assertEqual(access.caps({"ADMIN", "AUDITOR"}), {"work", "monitor", "admin", "audit"})
        self.assertEqual(access.caps({"SOMETHING"}), set())
        self.assertIn("PSAGENT_MONITOR", access.needs(access.MONITOR))


class RoleMatrixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = audit.Audit(Path(self.tmp.name) / "audit", days=90)
        self.db = PTDB()
        self.llm = FakeLLM()
        self.agent = Agent(self.llm, TASKS, self.db)
        self.c = TestClient(create_app(self.agent, SECRET, ["http://ps"], audit_log=self.audit, ci_store=ci.Store(self.tmp.name),
                                       query_catalog=queries.Catalog(self.db), tasks_dir=self.tmp.name))

    def tearDown(self):
        audit.install(None)
        self.audit._db.close()
        self.tmp.cleanup()

    def test_me_says_what_each_role_may_use(self):
        want = {  # role: (work, monitor, admin, audit)
            "USER": (True, False, False, False), "MONITOR": (True, True, False, False),
            "ADMIN": (True, True, True, False), "AUDITOR": (False, False, False, True)}
        for role, (work, monitor, admin, aud) in want.items():
            me = self.c.get("/api/me", headers=h("U_" + role, role)).json()
            self.assertEqual((me["can_work"], me["can_monitor"], me["can_teach"], me["can_audit"]), (work, monitor, admin, aud), role)
            self.assertEqual((me["ask_data"], me["ci"], me["troubleshoot"], me["own_access"]), (work,) * 4, role)
            self.assertEqual(me["security"], admin, role)
            self.assertEqual(bool(me["tasks"]), work, role)

    def test_who_is_refused_where(self):
        for path, allowed in (("/api/watchdogs", {"MONITOR", "ADMIN"}), ("/api/audit", {"AUDITOR"}),
                              ("/api/queries", {"USER", "MONITOR", "ADMIN"}), ("/api/ci?q=user", {"USER", "MONITOR", "ADMIN"})):
            for role in ("USER", "MONITOR", "ADMIN", "AUDITOR"):
                code = self.c.get(path, headers=h("U_" + role, role)).status_code
                self.assertEqual(code == 403, role not in allowed, f"{path} as {role}: {code}")
        for role, code in (("USER", 403), ("MONITOR", 403)):
            r = self.c.post("/api/recordings/draft", json={"recording": {}}, headers=h("U_" + role, role))
            self.assertEqual(r.status_code, code, role)
        self.assertNotEqual(self.c.post("/api/recordings/draft", json={"recording": {}}, headers=h("A", "ADMIN")).status_code, 403)

    def test_no_role_no_agent_and_old_tokens_are_users(self):
        self.assertEqual(self.c.get("/api/me", headers=h("NOBODY")).status_code, 401, "a token without any agent role")
        old = {"Authorization": "Bearer " + auth.make_token(SECRET, "OLD")}       # an iScript from before roles
        me = self.c.get("/api/me", headers=old).json()
        self.assertEqual((me["can_work"], me["can_monitor"], me["can_teach"], me["can_audit"]), (True, False, False, False))

    def test_auditor_chats_but_cannot_work(self):
        self.llm.responses += [response(tool("start_task", {"task_id": "create_user_profile"})), response(text("I can't do that."))]
        r = self.c.post("/api/chat", json={"message": "create user JSMITH"}, headers=h("AUD", "AUDITOR"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("PSAGENT_USER", json.dumps(self.llm.calls[-1][-1]["content"]))
        sid = r.json()["session_id"]
        for path in ("/api/confirm", "/api/result", "/api/live/step", "/api/query/result"):
            self.assertEqual(self.c.post(path, json={"session_id": sid, "run_id": "x", "status": "success"},
                                         headers=h("AUD", "AUDITOR")).status_code, 403, path)

    def test_a_role_taken_away_stops_at_once(self):
        """Rights are worked out from the token on every request, not kept from the first message."""
        self.llm.responses += [response(text("Hello.")),
                               response(tool("offer_recording", {"title": "Create Requisition"})), response(text("No."))]
        r = self.c.post("/api/chat", json={"message": "hi"}, headers=h("VELU", "ADMIN")).json()
        self.c.post("/api/chat", json={"session_id": r["session_id"], "message": "record a task"}, headers=h("VELU", "USER"))
        self.assertIn("may not record tasks", json.dumps(self.llm.calls[-1][-1]["content"]))

    def test_refused_attempts_are_in_the_audit_log(self):
        self.c.get("/api/watchdogs", headers=h("CLERK", "USER"))                       # an endpoint: 403
        self.llm.responses += [response(tool("check_user_access", {"user": "PS", "page": "Job Data", "question": ""})),
                               response(text("Only admins."))]
        self.c.post("/api/chat", json={"message": "why can't PS open Job Data"}, headers=h("CLERK", "USER"))   # a tool
        self.c.get("/api/me", headers=h("NOBODY"))                                     # a token without an agent role
        self.c.get("/api/me", headers={"Authorization": "Bearer " + auth.make_token("wrong", "MALLORY", roles=["ADMIN"])})
        self.c.get("/api/me", headers={"Authorization": "Bearer " + auth.make_token(SECRET, "OLD", day="20200101", roles=["USER"])})
        refused = self.audit.events(outcome="denied", kind="all")["events"]
        got = sorted((e["oprid"], e["detail"].split(":")[0]) for e in refused)
        self.assertEqual(got, [("(unknown)", "Rejected token"), ("CLERK", "only monitor users manage the watchdogs"),
                               ("CLERK", "this user may check only their own access (user 'me'); checking other users' access is for "
                                         "PSAGENT_ADMIN users"),
                               ("NOBODY", "Token without any PSAGENT role")])
        self.assertTrue(all(e["event"] == "access.denied" for e in refused))
        self.assertEqual(self.audit.overview(1)["totals"]["denied"], 4)
        self.assertNotIn("expired", json.dumps(refused), "an expired token is normal: not a refused attempt")

    def test_dev_user_and_secret_together_refused(self):
        import os
        from unittest import mock
        from psagent.server import app_from_env
        env = {"PSAGENT_ENV_FILE": os.path.join(self.tmp.name, "none.env"), "PSAGENT_SHARED_SECRET": "x", "PSAGENT_DEV_OPRID": "DEV"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaisesRegex(SystemExit, "both set"):
                app_from_env()

    def test_lists_count_only_in_development(self):
        prod = TestClient(create_app(Agent(FakeLLM(), TASKS), SECRET, [], teachers={"*"}, monitor_users={"*"}, auditors={"*"}))
        me = prod.get("/api/me", headers=h("VELU", "USER")).json()
        self.assertEqual((me["can_teach"], me["can_monitor"], me["can_audit"]), (False, False, False))
        dev = TestClient(create_app(Agent(FakeLLM(), TASKS), "", [], dev_oprid="DEV", teachers={"*"}))
        self.assertTrue(dev.get("/api/me").json()["can_teach"])


if __name__ == "__main__":
    unittest.main()
