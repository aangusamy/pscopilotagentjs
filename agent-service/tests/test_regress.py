import base64
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from starlette.testclient import TestClient

from fakes import FakeLLM, response, text, tool
from psagent import auth, regress, regress_runner, runbooks
from psagent.agent import Agent
from psagent.server import Regression, create_app
from psagent.tasks import load_tasks

TASKS = load_tasks(Path(__file__).parent / "fixtures" / "tasks")
SECRET = "s3cret"
NEW = {"title": "New user", "task_id": "create_user_profile",
       "values": [{"name": "user_id", "value": "RT{{n}}"}, {"name": "description", "value": "Regression {{n}}"},
                  {"name": "roles", "value": "EMPLOYEE"}], "expect": "saved", "message": "", "notes": ""}
DUP = {"title": "Duplicate user is refused", "task_id": "create_user_profile",
       "values": [{"name": "user_id", "value": "PS"}, {"name": "description", "value": "Dup"}],
       "expect": "error", "message": "already exists", "notes": ""}


def suite(cases=(NEW, DUP), **kw):
    s, card, problems = regress.check_suite({"title": "User admin regression", "cases": list(cases), **kw}, TASKS, "VELU")
    assert not problems, problems
    return s


class FakeOperator:
    """Scripted live mode: the actions it answers with, in order."""

    def __init__(self, *actions):
        self.actions, self.calls = list(actions), []

    def step(self, run, snapshot, result=None, answer=None, approved=None, declined=None):
        self.calls.append({"result": result, "approved": approved})
        return self.actions.pop(0)


class SuiteTest(unittest.TestCase):
    def test_check(self):
        s, card, problems = regress.check_suite({"title": "User admin regression", "cases": [NEW, DUP], "run_at": "02:00",
                                                 "after_patch": True, "notify": ["QA@Example.com"]}, TASKS, "VELU")
        self.assertEqual(problems, [])
        self.assertEqual((s["id"], s["run_at"], s["after_patch"], s["notify"]), ("user_admin_regression", "02:00", True, ["qa@example.com"]))
        self.assertEqual(s["cases"][0]["values"], {"user_id": "RT{{n}}", "description": "Regression {{n}}", "roles": ["EMPLOYEE"]})
        self.assertEqual([c["expect"] for c in card["cases"]], ["Saved", 'Error: "already exists"'])

    def test_problems(self):
        bad = [{"title": "x", "task_id": "nope", "values": []},
               {"title": "y", "task_id": "create_user_profile", "values": [{"name": "password", "value": "pw"}, {"name": "zzz", "value": "1"}]},
               {"title": "z", "task_id": "create_user_profile", "values": [{"name": "user_id", "value": "Z1"}], "expect": "error"},
               dict(NEW), dict(NEW)]
        _, _, problems = regress.check_suite({"title": "Bad", "cases": bad, "run_at": "7am"}, TASKS, "VELU")
        for part in ("case 1 (x): there is no task 'nope'", "passwords are typed when the run starts", "'zzz' is not a field",
                     "case 2 (y): Description is required", "needs the expected message", "two cases are called 'New user'",
                     "Run unattended at must be HH:MM"):
            self.assertTrue(any(part in p for p in problems), (part, problems))

    def test_changes_on_reupload(self):
        old = suite()
        changed = dict(DUP, message="exists already")
        _, card, _ = regress.check_suite({"title": "User admin regression", "cases": [NEW, changed,
                                          dict(NEW, title="Another user")]}, TASKS, "VELU", existing=old)
        self.assertEqual(card["changes"], {"added": ["Another user"], "removed": [], "changed": ["Duplicate user is refused"], "unchanged": 1})

    def test_smoke_and_template(self):
        s, skipped = regress.smoke_suite(TASKS)
        self.assertEqual([(c["title"], c["values"]) for c in s["cases"]],
                         [("Create User Profile (smoke)", {"user_id": "RT{{n}}", "description": "Regression test {{n}}"})])
        sheet = runbooks.xlsx_text(regress.template_xlsx(TASKS))
        for part in ("1: Test suite | My regression tests", "## Sheet: create_user_profile", "1: Task | create_user_profile",
                     "User ID * (user_id)", "Description * (description)", "Create User Profile (smoke) | RT{{n}}"):
            self.assertIn(part, sheet)
        self.assertNotIn("(password)", sheet)                       # passwords are never in the sheet
        back = runbooks.xlsx_text(regress.to_xlsx(suite(run_at="02:00"), TASKS))
        self.assertIn("3: Run unattended at | 02:00", back)
        self.assertIn("Duplicate user is refused | PS", back)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = regress.Store(self.tmp.name)
        self.reports = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, operator=None):
        return regress.Runner(self.store, lambda: TASKS, operator, lambda rep, s: self.reports.append(rep))

    def test_passes_and_negative_case(self):
        r = self.runner()
        run = r.start(suite(), "VELU")
        self.assertEqual(run.n, "0001")
        self.assertEqual(r.secrets(run.suite), [{"name": "password", "label": "Password"}])
        c = r.next(run)["case"]
        self.assertEqual((c["n"], c["of"], c["expect"]), (1, 2, "Saved"))
        self.assertIn('"RT0001"', str(c["action"]["steps"]).replace("'", '"'))       # {{n}} filled in
        self.assertEqual(r.result(run, 1, {"status": "success", "message": "Saved"})["verdict"]["verdict"], "passed")
        c = r.next(run)["case"]
        v = r.result(run, 2, {"status": "error", "message": "The value you tried to add already exists.", "failed_step": "Open"})
        self.assertEqual(v["verdict"]["verdict"], "passed")
        rep = r.next(run)["report"]
        self.assertEqual((rep["headline"], rep["counts"]["passed"]), ("2 of 2 passed", 2))
        self.assertEqual(self.reports[0]["run_id"], run.run_id)
        self.assertEqual(self.store.report(run.run_id)["headline"], "2 of 2 passed")
        # the next run: case 1 fails now -> "failing since the last run"
        run2 = r.start(suite(), "VELU")
        self.assertEqual(run2.n, "0002")
        r.next(run2)
        self.assertEqual(r.result(run2, 1, {"status": "error", "message": "Role EMPLOYEE is invalid", "failed_step": "Set Role"})
                         ["verdict"]["detail"], "Stopped at Set Role: Role EMPLOYEE is invalid")
        r.next(run2)
        r.result(run2, 2, {"status": "success", "message": "Saved"})                  # an error was expected
        rep = r.next(run2)["report"]
        self.assertEqual(rep["headline"], "0 of 2 passed, 2 failed")
        self.assertEqual(rep["newly_failing"], ["New user", "Duplicate user is refused"])
        self.assertEqual([x["previous"] for x in rep["results"]], ["passed", "passed"])
        self.assertIn("but it went through", rep["results"][1]["detail"])

    def test_page_changed_is_finished_live(self):
        op = FakeOperator({"type": "do", "note": "Description moved: Long Description", "steps": [{"do": "set"}]},
                          {"type": "confirm", "confirm_id": "c1", "button": "Save"},
                          {"type": "done", "summary": "Created RT0001"})
        r = self.runner(op)
        run = r.start(suite([NEW]), "VELU")
        c = r.next(run)["case"]
        i = next(n for n, st in enumerate(c["action"]["steps"]) if "description" in str(st).lower())
        rec = r.result(run, 1, {"status": "error", "mismatch": True, "failed_index": i, "message": "Could not find Description"})
        self.assertIn("recover", rec)
        self.assertEqual(r.live_step(run, {})["action"]["type"], "do")
        self.assertEqual(r.live_step(run, {}, result={"ok": True})["action"]["type"], "confirm")
        v = r.live_step(run, {}, approved="c1")["verdict"]
        self.assertEqual(op.calls[-1]["approved"], "c1")                          # Save approved by the runner
        self.assertEqual(v["verdict"], "changed")
        self.assertEqual(v["changes"][0][:6], "Step " + str(i + 1)[0])
        self.assertIn("Live: Description moved: Long Description", v["changes"])
        self.assertIn("Finished live: Created RT0001", v["changes"])
        self.assertEqual(r.next(run)["report"]["headline"], "0 of 1 passed, 1 passed with changes")

    def test_live_question_fails_and_stop(self):
        r = self.runner(FakeOperator({"type": "ask", "question": "Which role?"}))
        run = r.start(suite(), "VELU")
        r.next(run)
        r.result(run, 1, {"status": "error", "mismatch": True, "failed_index": 2, "message": "x"})
        v = r.live_step(run, {})["verdict"]
        self.assertEqual((v["verdict"], "Which role?" in v["detail"]), ("failed", True))
        rep = r.stop(run)
        self.assertEqual(rep["headline"], "0 of 2 passed, 1 failed, 1 stopped")
        self.assertIsNone(r.get(run.run_id, "SOMEONE_ELSE"))


class UnattendedTest(unittest.TestCase):
    class FakeHeadless:
        user = "PSAGENT_TEST"

        def __init__(self):
            self.ran = []

        def run(self, run_id, names):
            self.ran.append((run_id, names))

    class PatchDB:
        sig = "76453/2025-04-28 22:40:08/3"

        def query(self, sql, params=None, **kw):
            return ["SIG"], [[self.sig]], False

    def test_schedule_and_patch(self):
        with tempfile.TemporaryDirectory() as d:
            store = regress.Store(d)
            store.save(suite(run_at="02:00"))
            store.save(dict(suite(), id="after_patch_suite", title="After patch", after_patch=True))
            runner = regress.Runner(store, lambda: TASKS)
            db, h = self.PatchDB(), self.FakeHeadless()
            u = regress_runner.Unattended(runner, store, h, db)
            two = datetime(2026, 10, 31, 2, 0).timestamp()
            u.busy = None
            self.assertEqual(u.tick(two - 60), [])                       # not yet, and the patch level is only noted
            started = u.tick(two + 60)
            self.assertEqual(len(started), 1)
            for _ in range(50):                                          # the run thread ends (the fake does nothing)
                if not u.busy:
                    break
                __import__("time").sleep(0.05)
            self.assertEqual(h.ran[0][1], ["password"])
            self.assertEqual(u.tick(two + 120), [])                      # once a day
            db.sig = "76460/2026-10-31 01:00:00/3"                       # a patch was applied
            started = u.tick(two + 180)
            self.assertEqual(len(started), 1)
            self.assertEqual(runner.runs[started[0]].suite["id"], "after_patch_suite")
            self.assertEqual(runner.runs[started[0]].mode, "server")
            for _ in range(50):
                if not u.busy:
                    break
                __import__("time").sleep(0.05)

    def test_report_email(self):
        rep = {"run_id": "rt-1", "title": "User admin", "headline": "1 of 2 passed, 1 failed", "by": "PSAGENT_TEST", "mode": "server",
               "started": "2026-10-31 02:00:00", "ended": "2026-10-31 02:03:00", "counts": {}, "newly_failing": ["New user"],
               "fixed": [], "results": [{"n": 1, "title": "New user", "task_title": "Create user profile", "verdict": "failed",
                                         "detail": "Stopped at Save: <oops>", "changes": []}]}
        subject, html, txt = regress_runner.report_email(rep, "http://ps/psp/ps/EMPLOYEE/HRMS/")
        self.assertEqual(subject, "[PeopleSoft Agent] Regression User admin: 1 of 2 passed, 1 failed")
        self.assertIn("Failing since the last run: New user", txt)
        self.assertIn("&lt;oops&gt;", html)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = regress.Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def h(oprid, *roles):
        return {"Authorization": "Bearer " + auth.make_token(SECRET, oprid, roles=list(roles))}

    def client(self, llm, on=True):
        agent = Agent(llm, TASKS)
        reg = Regression(self.store, regress.Runner(self.store, lambda: agent.tasks, agent.operator)) if on else None
        return TestClient(create_app(agent, SECRET, ["http://ps"], regression=reg))

    def test_off_without_test_env(self):
        c = self.client(FakeLLM(), on=False)
        self.assertEqual(c.get("/api/regress", headers=self.h("BOSS", "ADMIN")).status_code, 503)
        self.assertIsNone(c.get("/api/me", headers=self.h("BOSS", "ADMIN")).json()["regress"])

    def test_upload_save_run_report(self):
        llm = FakeLLM(response(tool("draft_test_suite", {"title": "User admin regression", "summary": "", "run_at": "", "after_patch": False,
                                                         "notify": [], "cases": [NEW, DUP], "questions": []})),
                      response(text("2 cases, one negative.")))
        c = self.client(llm)
        admin, user = self.h("BOSS", "ADMIN"), self.h("JOE", "USER")
        self.assertEqual(c.get("/api/me", headers=admin).json()["regress"], {"suites": 0, "unattended": False})
        self.assertEqual(c.get("/api/regress", headers=user).status_code, 403)
        t = c.get("/api/templates/regression", headers=admin).json()
        self.assertIn("## Sheet: create_user_profile", runbooks.xlsx_text(base64.b64decode(t["data"])))
        r = c.post("/api/chat", json={"message": "Build a regression test suite from this test plan"}, headers=admin).json()
        card = r["test_suite"]
        self.assertEqual((card["title"], len(card["cases"])), ("User admin regression", 2))
        saved = c.post("/api/regress/save", json={"session_id": r["session_id"], "draft_id": card["draft_id"]}, headers=admin).json()
        self.assertEqual(saved["path"], "regression/user_admin_regression.yaml")
        st = c.post("/api/regress/start", json={"suite": "user_admin_regression"}, headers=admin).json()
        self.assertEqual((len(st["cases"]), st["secrets"]), (2, [{"name": "password", "label": "Password"}]))
        step = lambda what, **kw: c.post(f"/api/regress/{what}", json={"run_id": st["run_id"], **kw}, headers=admin)   # noqa: E731
        self.assertEqual(c.post("/api/regress/next", json={"run_id": st["run_id"]}, headers=user).status_code, 404)   # not the owner
        self.assertEqual(step("next").json()["case"]["n"], 1)
        self.assertEqual(step("result", n=1, result={"status": "success", "message": "Saved"}).json()["verdict"]["verdict"], "passed")
        self.assertEqual(step("next").json()["case"]["n"], 2)
        step("result", n=2, result={"status": "error", "message": "already exists"})
        rep = step("next").json()["report"]
        self.assertEqual(rep["headline"], "2 of 2 passed")
        home = c.get("/api/regress", headers=admin).json()
        self.assertEqual(home["suites"][0]["last"]["headline"], "2 of 2 passed")
        self.assertEqual(home["unattended"]["available"], False)
        x = c.get(f"/api/regress/report?xlsx=1&run={rep['run_id']}", headers=admin).json()
        self.assertIn("Duplicate user is refused", runbooks.xlsx_text(base64.b64decode(x["data"])))
        self.assertEqual(c.post("/api/regress/unattended", json={"suite": "user_admin_regression"}, headers=admin).status_code, 503)
        self.assertEqual(c.post("/api/regress/delete", json={"suite": "user_admin_regression"}, headers=admin).json(),
                         {"deleted": "user_admin_regression"})


if __name__ == "__main__":
    unittest.main()
