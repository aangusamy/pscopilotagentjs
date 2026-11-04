import base64
import json
import tempfile
import unittest
from pathlib import Path

import yaml
from starlette.testclient import TestClient

from psagent import auth, runbooks
from psagent.agent import XLSX, Agent, Session, check_attachment
from psagent.db import QueryError
from psagent.server import create_app
from psagent.tasks import load_tasks, parse_task

from fakes import FakeLLM, response, text, tool

HERE = Path(__file__).resolve().parent
CHECKLIST = HERE.parent / "runbooks" / "templates" / "test_user_setup_checklist.xlsx"
TASKS = dict(load_tasks(HERE / "fixtures" / "tasks"))
TASKS["sync_portal_security"] = parse_task({
    "id": "sync_portal_security", "title": "Sync portal security", "summary": "Runs PORTAL_CSS.",
    "component": {"menu": "PORTAL_ADMIN", "component": "PORTAL_SEC_SYNC"},
    "fields": {"run_control_id": {"label": "Run Control ID", "required": True, "default": "AGSLFT4OT", "upper": True}},
    "playbook": [{"do": "open", "mode": "add_or_open", "keys": [{"field": {"id": "PRCSRUNCNTL_RUN_CNTL_ID"},
                                                                  "value": "{{run_control_id}}"}]},
                 {"do": "click", "button": {"id": "PRCSRQSTDLG_WRK_LOADPRCSRQSTDLGPB", "text": "Run"}},
                 {"do": "click", "button": {"id": "#ICSave", "text": "OK"}, "popup": True},
                 {"do": "expect_process"}]})

USER_FREE = "select oprid from psoprdefn where oprid = :user_id"
HAS_ROLE = ("select 'missing role' problem from dual where not exists (select 1 from psroleuser "
            "where roleuser = :user_id and rolename = 'Employee')")


def step(type, title, **kw):
    base = {"title": title, "type": type, "owner": "Security Administrator", "stop_on_fail": True, "task_id": "",
            "values": [], "process_name": "", "rule": "", "sql": "", "expect": "", "instructions": ""}
    base.update(kw)
    return base


def good_draft(**over):
    d = {"title": "Set up a test user", "summary": "Creates a test user, syncs portal security and checks it.",
         "inputs": [{"name": "user_id", "label": "User ID", "default": "psa_test01", "hint": "", "upper": True}],
         "steps": [
             step("check", "User ID is not taken", rule="No user profile with this ID", sql=USER_FREE, expect="no_rows"),
             step("task", "Create the user profile", task_id="create_user_profile",
                  values=[{"name": "user_id", "value": "{{user_id}}"}, {"name": "roles", "value": "Employee"}]),
             step("process", "Portal security sync", task_id="sync_portal_security", process_name="portal_css",
                  values=[{"name": "run_control_id", "value": "AGSLFT4OT"}]),
             step("check", "User has the role", rule="The user has Employee", sql=HAS_ROLE, expect="no_rows"),
             step("approval", "Security manager signs off", owner="Security Manager", instructions="Approve the new user"),
         ],
         "questions": ["Which description should the user get?"]}
    d.update(over)
    return d


class RunbookDB:
    """Answers by query: the user is free, the role is missing (it is created by step 2)."""

    def __init__(self):
        self.calls = []

    def query(self, sql, params=None, max_rows=200, who="", why="", max_cell=2000):
        self.calls.append((sql, params, why))
        if "BADCOL" in sql:
            raise QueryError('ORA-00904: "BADCOL": invalid identifier')
        if "psroleuser" in sql:
            return ["PROBLEM"], [["missing role"]], False
        return ["OPRID"], [], False


class SpreadsheetTest(unittest.TestCase):
    def test_checklist_template_reads_as_rows(self):
        out = runbooks.xlsx_text(CHECKLIST.read_bytes())
        self.assertIn("## Sheet: Checklist", out)
        self.assertIn("Make sure the new user ID is not taken | Check", out)
        self.assertIn("PORTAL_ADMIN.PORTAL_SEC_SYNC | PORTAL_CSS", out)
        self.assertIn("## Sheet: Step types", out)

    def test_csv_and_bad_files(self):
        out = runbooks.csv_text("Step,What to do\n1,Check the user\n".encode("cp1252"))
        self.assertIn("2: 1 | Check the user", out)
        for bad in (b"not a zip", b""):
            with self.assertRaises(runbooks.DocError):
                runbooks.xlsx_text(bad)
        with self.assertRaises(runbooks.DocError):
            runbooks.csv_text(b"")

    def test_spreadsheet_reaches_claude_as_a_text_document(self):
        att, err = check_attachment({"name": "checklist.xlsx", "media_type": XLSX,
                                     "data": base64.b64encode(CHECKLIST.read_bytes()).decode()})
        self.assertIsNone(err)
        llm = FakeLLM(response(text("ok")))
        s = Session(id="s", oprid="PS")
        Agent(llm, TASKS).chat(s, "build a runbook from this", None, att)
        block = llm.calls[0][0]["content"][0]
        self.assertEqual(block["type"], "document")
        self.assertEqual(block["source"]["type"], "text")
        self.assertIn("Run the portal security sync", block["source"]["data"])
        self.assertEqual(s.document, "checklist.xlsx")
        bad, err = check_attachment({"name": "x.xlsx", "media_type": XLSX, "data": base64.b64encode(b"zip?").decode()})
        self.assertIsNone(bad)
        self.assertIn("not a valid .xlsx", err)


class CheckTest(unittest.TestCase):
    def test_good_draft(self):
        db = RunbookDB()
        rb, card, problems = runbooks.check(good_draft(), TASKS, db, "PS", "checklist.xlsx")
        self.assertEqual(problems, [])
        self.assertEqual(rb["id"], "set_up_a_test_user")
        self.assertEqual(rb["inputs"]["user_id"], {"label": "User ID", "required": True, "upper": True, "default": "PSA_TEST01"})
        self.assertEqual([list(s)[2] for s in rb["steps"]], ["owner"] * 5)
        task, proc = rb["steps"][1]["task"], rb["steps"][2]["process"]
        self.assertEqual(task, {"task": "create_user_profile", "values": {"user_id": "{{user_id}}", "roles": ["Employee"]}})
        self.assertEqual(proc, {"task": "sync_portal_security", "process": "PORTAL_CSS", "values": {"run_control_id": "AGSLFT4OT"}})
        self.assertEqual(rb["steps"][4]["approval"], "Approve the new user")
        # check queries are test-run with the input defaults: step 1 passes, step 4 fails today (step 2 creates it)
        self.assertEqual(card["steps"][0]["test"]["passes"], True)
        self.assertEqual(card["steps"][0]["test"]["with"], {"user_id": "PSA_TEST01"})
        self.assertEqual(card["steps"][3]["test"]["passes"], False)
        self.assertEqual(db.calls[0][1], {"user_id": "PSA_TEST01"})
        self.assertIn("Description", card["steps"][1]["asks"])       # required, not given: asked when it runs
        self.assertIn("Password", card["steps"][1]["asks"])          # secret: typed on the card
        self.assertEqual(card["questions"], ["Which description should the user get?"])
        self.assertEqual(yaml.safe_load(runbooks.to_yaml(rb)), rb)

    def test_problems_are_reported(self):
        d = good_draft(steps=[
            step("task", "Unknown", task_id="make_coffee"),
            step("task", "Password", task_id="create_user_profile", values=[{"name": "password", "value": "x"}]),
            step("task", "Placeholder", task_id="create_user_profile", values=[{"name": "user_id", "value": "{{emplid}}"}]),
            step("process", "Not a process", task_id="create_user_profile"),
            step("check", "Writes", rule="x", sql="delete from psoprdefn"),
            step("check", "Unknown bind", rule="x", sql="select 1 from dual where :run_id = 'X'"),
            step("check", "Bad column", rule="x", sql="select BADCOL from psoprdefn where oprid = :user_id"),
            step("approval", "No owner", owner=""),
            step("coffee", "Bad type"),
        ])
        _, _, problems = runbooks.check(d, TASKS, RunbookDB())
        text_ = "\n".join(problems)
        for want in ("unknown task 'make_coffee'", "'password' is not a field", "{{emplid}} is not one of the runbook inputs",
                     "does not submit a process", "only SELECT or WITH", ":run_id is not one of the runbook inputs",
                     "BADCOL", "an approval needs an owner", "type must be one of"):
            self.assertIn(want, text_)

    def test_without_database_checks_are_not_tested(self):
        _, card, problems = runbooks.check(good_draft(), TASKS, None)
        self.assertEqual(problems, [])
        self.assertEqual(card["steps"][0]["test"], {"skipped": "no database access"})


def draft_llm(*drafts, after="I drafted the runbook: please review it."):
    return FakeLLM(*[response(tool("draft_runbook", d, id=f"d{i}")) for i, d in enumerate(drafts)], response(text(after)))


class AgentFlowTest(unittest.TestCase):
    def test_problems_go_back_to_claude_then_the_card_is_shown(self):
        bad = good_draft(steps=[step("task", "Unknown", task_id="make_coffee")])
        llm = draft_llm(bad, good_draft())
        s = Session(id="s", oprid="PS", can_teach=True)
        r = Agent(llm, TASKS, RunbookDB()).chat(s, "build the runbook", None)
        self.assertIn("make_coffee", llm.calls[1][-1]["content"][0]["content"])      # first draft rejected
        self.assertEqual(r.runbook["title"], "Set up a test user")
        self.assertEqual(len(r.runbook["steps"]), 5)
        self.assertEqual(s.runbook["draft_id"], r.runbook["draft_id"])
        self.assertEqual(r.messages, ["I drafted the runbook: please review it."])

    def test_card_shown_with_problems_after_retries(self):
        bad = good_draft(steps=[step("task", "Unknown", task_id="make_coffee"), step("manual", "Tick")])
        r = Agent(draft_llm(bad, bad, bad), TASKS, RunbookDB()).chat(Session(id="s", oprid="PS", can_teach=True), "go", None)
        self.assertEqual(len(r.runbook["problems"]), 1)
        self.assertEqual([x["type"] for x in r.runbook["steps"]], ["manual"])

    def test_only_teachers(self):
        llm = draft_llm(good_draft(), after="You can't.")
        r = Agent(llm, TASKS, RunbookDB()).chat(Session(id="s", oprid="ABSMITH"), "go", None)
        self.assertIsNone(r.runbook)
        self.assertIn("PSAGENT_ADMIN", llm.calls[1][-1]["content"][0]["content"])


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.secret = "s3cret"
        llm = draft_llm(good_draft())
        self.client = TestClient(create_app(Agent(llm, TASKS, RunbookDB()), self.secret, ["http://ps"], runbooks_dir=self.dir.name))

    def tearDown(self):
        self.dir.cleanup()

    def h(self, oprid="PS"):
        return {"Authorization": "Bearer " + auth.make_token(self.secret, oprid, roles=["ADMIN"] if oprid == "PS" else ["USER"])}

    def test_publish(self):
        r = self.client.post("/api/chat", json={"message": "build the runbook"}, headers=self.h()).json()
        sid, draft = r["session_id"], r["runbook"]["draft_id"]
        self.assertEqual(self.client.post("/api/runbooks/save", json={"session_id": sid, "draft_id": "nope"},
                                          headers=self.h()).status_code, 409)
        out = self.client.post("/api/runbooks/save", json={"session_id": sid, "draft_id": draft}, headers=self.h())
        self.assertEqual(out.status_code, 200, out.text)
        self.assertEqual(out.json()["path"], "runbooks/set_up_a_test_user.yaml")
        saved = yaml.safe_load((Path(self.dir.name) / "set_up_a_test_user.yaml").read_text(encoding="utf-8"))
        self.assertEqual(saved["source"]["by"], "PS")
        self.assertEqual(len(saved["steps"]), 5)
        # published: the draft is gone, so publishing again needs a new draft
        self.assertEqual(self.client.post("/api/runbooks/save", json={"session_id": sid, "draft_id": draft},
                                          headers=self.h()).status_code, 409)

    def test_only_teachers_publish(self):
        self.assertEqual(self.client.post("/api/runbooks/save", json={"session_id": "x", "draft_id": "y"},
                                          headers=self.h("ABSMITH")).status_code, 403)


def published():
    rb, _, problems = runbooks.check(good_draft(), TASKS, None, "PS", "checklist.xlsx")
    assert not problems, problems
    return rb


class RunDB(RunbookDB):
    """At run time: the user is free, the role is there after step 2, PORTAL_CSS goes Processing -> Success."""

    def __init__(self, statuses=("7", "9"), role=(["missing role"],)):
        super().__init__()
        self.statuses, self.role = list(statuses), [list(r) for r in role]

    def query(self, sql, params=None, max_rows=200, who="", why="", max_cell=2000):
        self.calls.append((sql, params, why))
        if why == "runbook process baseline":
            return ["MAX"], [[899]], False
        if why == "runbook process status":
            return ["PRCSINSTANCE", "PRCSNAME", "RUNSTATUS"], [[900, "PORTAL_CSS", self.statuses.pop(0)]], False
        if why.startswith("runbook check") and "psroleuser" in sql:
            return ["PROBLEM"], self.role, False
        return ["OPRID"], [], False


class EngineTest(unittest.TestCase):
    def go(self, run, db, **req):
        return runbooks.step(run, req, TASKS, db, "PS")

    def test_full_run(self):
        db, run = RunDB(role=()), runbooks.new_run(published())
        self.assertEqual(run.waiting, "inputs")
        out = self.go(run, db, inputs={"user_id": "psa_test02"})
        self.assertEqual(run.inputs, {"user_id": "PSA_TEST02"})                      # upper case input
        self.assertEqual([(e["n"], e["status"]) for e in out["events"]], [(1, "ok")])   # check 1 passed
        nx = out["next"]
        self.assertEqual(nx["type"], "task")
        self.assertIn({"label": "User ID", "value": "PSA_TEST02"}, nx["rows"])
        self.assertEqual([m["name"] for m in nx["missing"]], ["description"])
        self.assertEqual(nx["secrets"], [{"name": "password", "label": "Password"}])
        self.assertIn("still needed: Description", self.go(run, db, confirm=True)["error"])
        nx = self.go(run, db, confirm=True, fill={"description": "Test User Two"})["next"]
        self.assertEqual(nx["type"], "run")
        self.assertIn("PSA_TEST02", json.dumps(nx["action"]["steps"]))
        self.assertIn("{{secret:password}}", json.dumps(nx["action"]["steps"]))      # filled in the browser only
        nx = self.go(run, db, result={"status": "success", "message": "Saved"})["next"]
        self.assertEqual((nx["type"], nx["process"]), ("task", True))               # step 3: the process
        self.go(run, db, confirm=True)
        out = self.go(run, db, result={"status": "success", "message": "Submitted to the Process Scheduler"})
        self.assertEqual(out["next"], {"type": "wait", "n": 3, "title": "Portal security sync", "text": "PORTAL_CSS 900: Processing"})
        status_sql = [c for c in db.calls if c[2] == "runbook process status"][0]
        self.assertEqual(status_sql[1], {"o": "PS", "b": 899, "p": "PORTAL_CSS"})    # found by the user, after the baseline
        out = self.go(run, db, poll=True)
        self.assertEqual([(e["n"], e["status"], e["detail"]) for e in out["events"]],
                         [(3, "ok", "PORTAL_CSS 900: Success"), (4, "ok", "The user has Employee")])
        self.assertEqual(out["next"]["type"], "approval")
        out = self.go(run, db, approve=True)
        self.assertTrue(out["done"])
        self.assertEqual(out["status"], "success")
        self.assertEqual([r["status"] for r in out["results"]], ["ok"] * 5)
        self.assertEqual(out["results"][4]["detail"], "Approved by PS")
        self.assertIn("ended", self.go(run, db, poll=True)["error"])

    def test_auto_run_needs_no_confirm_card_unless_the_task_needs_something(self):
        self.assertNotIn("auto_run", runbooks.check(good_draft(auto_run=True), TASKS, None, "PS")[0])   # never from a draft
        self.assertFalse(runbooks.start_card(runbooks.new_run(published()))["auto_run"])
        db, run = RunDB(role=()), runbooks.new_run({**published(), "auto_run": True})
        self.assertTrue(runbooks.start_card(run)["auto_run"])
        out = self.go(run, db, inputs={"user_id": "psa_test02"})
        self.assertEqual(out["next"]["type"], "task")                                # step 2 needs a password: it asks
        self.go(run, db, confirm=True, fill={"description": "Test User Two"})
        out = self.go(run, db, result={"status": "success", "message": "Saved"})
        self.assertEqual([(e["n"], e["status"]) for e in out["events"]], [(2, "ok")])
        self.assertEqual((out["next"]["type"], out["next"]["n"]), ("run", 3))         # step 3 runs straight away
        self.assertEqual(run.pending["baseline"], 899)
        self.go(run, db, result={"status": "success", "message": "Submitted to the Process Scheduler"})
        self.assertEqual(self.go(run, db, poll=True)["next"]["type"], "approval")    # approvals still wait
        self.assertEqual(self.go(run, db, approve=True)["status"], "success")

    def test_failures_stop_the_run(self):
        # a failing check stops it before anything is done
        db = RunDB()
        db.query = lambda sql, params=None, **kw: (["OPRID"], [["PSA_TEST01"]], False)
        run = runbooks.new_run(published())
        out = self.go(run, db, inputs={})
        self.assertEqual((out["status"], out["results"][0]["status"]), ("failed", "failed"))
        self.assertIn("found OPRID=PSA_TEST01", out["results"][0]["detail"])
        # the task fails on screen
        run = runbooks.new_run(published())
        self.go(run, RunDB(), inputs={})
        self.go(run, RunDB(), confirm=True, fill={"description": "x"})
        out = self.go(run, RunDB(), result={"status": "error", "message": "User ID already exists", "failed_step": "Add"})
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["results"][-1]["detail"], "Stopped at Add: User ID already exists")
        # the process ends in error: the instance is offered for troubleshooting
        db, run = RunDB(statuses=("3",)), runbooks.new_run(published())
        self.go(run, db, inputs={})
        self.go(run, db, confirm=True, fill={"description": "x"})
        self.go(run, db, result={"status": "success"})
        self.go(run, db, confirm=True)
        out = self.go(run, db, result={"status": "success", "message": "Submitted: process instance 900"})
        self.assertEqual((out["status"], out["failed_instance"]), ("failed", "900"))
        self.assertEqual([c[1] for c in db.calls if c[2] == "runbook process status"], [{"i": 900}])  # by its instance

    def test_warn_stop_reject_and_wrong_requests(self):
        rb = published()
        rb["steps"][0]["on_fail"] = "warn"
        db = RunDB()
        db.query = lambda sql, params=None, **kw: (["OPRID"], [["PSA_TEST01"]], False)
        run = runbooks.new_run(rb)
        out = self.go(run, db, inputs={})
        self.assertEqual((out["events"][0]["status"], out["next"]["type"]), ("warn", "task"))   # goes on
        self.assertIn("confirm the task", self.go(run, db, approve=True)["error"])
        out = self.go(run, db, cancel=True)
        self.assertEqual((out["status"], out["results"][-1]["detail"]), ("stopped", "Stopped by PS"))
        # rejected approval
        rb = {"id": "sign_off", "title": "Sign off", "inputs": {}, "steps": [{"id": "s1", "title": "Boss", "owner": "Boss", "approval": "OK?"}]}
        run = runbooks.new_run(rb)
        self.assertEqual(self.go(run, None, inputs={})["next"], {"type": "approval", "n": 1, "title": "Boss", "owner": "Boss",
                                                                  "instructions": "OK?"})
        self.assertEqual(self.go(run, None, approve=False)["results"][0]["detail"], "Rejected by PS")
        # a required input without default
        rb = {"id": "needs_input", "title": "x", "inputs": {"run_id": {"label": "Run ID", "required": True}}, "steps": [{"manual": "x"}]}
        self.assertIn("needed to start: Run ID", self.go(runbooks.new_run(rb), None, inputs={})["error"])

    def test_find_and_load(self):
        rbs = {"set_up_a_test_user": published(), "month_end": {"id": "month_end", "title": "Month-end payroll close", "steps": []}}
        for name in ("Set up a test user", "set_up_a_test_user", "the set up a test user runbook", "test user"):
            self.assertEqual(runbooks.find(rbs, name)["id"], "set_up_a_test_user", name)
        self.assertEqual(runbooks.find(rbs, "month-end payroll")["id"], "month_end")
        self.assertIsNone(runbooks.find(rbs, "year end"))
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ok.yaml").write_text(runbooks.to_yaml(published()), encoding="utf-8")
            (Path(d) / "bad.yaml").write_text("steps: [", encoding="utf-8")
            (Path(d) / "other.yaml").write_text("id: x\n", encoding="utf-8")
            self.assertEqual(list(runbooks.load_runbooks(d)), ["set_up_a_test_user"])

    def test_start_runbook_tool(self):
        llm = FakeLLM(response(tool("start_runbook", {"name": "year end"})), response(text("There is no such runbook.")),
                      response(tool("start_runbook", {"name": "test user"})), response(text("Check the inputs and click Start.")))
        agent, s = Agent(llm, TASKS, RunDB()), Session(id="s", oprid="PS")
        agent.add_runbook(published())
        r = agent.chat(s, "run year end", None)
        self.assertIsNone(r.runbook_run)
        result = json.loads(llm.calls[1][-1]["content"][0]["content"])
        self.assertEqual(result["runbooks"], ["Set up a test user"])
        r = agent.chat(s, "run test user", None)
        self.assertEqual(r.runbook_run["title"], "Set up a test user")
        self.assertEqual(r.runbook_run["inputs"][0], {"name": "user_id", "label": "User ID", "default": "PSA_TEST01", "hint": ""})
        self.assertEqual([x["type"] for x in r.runbook_run["steps"]], ["check", "task", "process", "check", "approval"])


class RunApiTest(unittest.TestCase):
    def test_run_through_the_api_and_log(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "set_up_a_test_user.yaml").write_text(runbooks.to_yaml(published()), encoding="utf-8")
            llm = FakeLLM(response(tool("start_runbook", {"name": "Set up a test user"})), response(text("Click Start.")))
            client = TestClient(create_app(Agent(llm, TASKS, RunDB(role=())), "s3cret", ["http://ps"], runbooks_dir=d))
            h = {"Authorization": "Bearer " + auth.make_token("s3cret", "ABSMITH", roles=["USER"])}      # not an admin: may run
            me = client.get("/api/me", headers=h).json()
            self.assertEqual(me["runbooks"], [{"id": "set_up_a_test_user", "title": "Set up a test user",
                                               "summary": "Creates a test user, syncs portal security and checks it.", "steps": 5}])
            r = client.post("/api/chat", json={"message": "run Set up a test user"}, headers=h).json()
            sid, rid = r["session_id"], r["runbook_run"]["run_id"]

            def step(**req):
                return client.post("/api/runbooks/step", json={"session_id": sid, "run_id": rid, **req}, headers=h)
            self.assertEqual(step(inputs={"user_id": "PSA_TEST09"}).json()["next"]["type"], "task")
            self.assertEqual(step(approve=True).status_code, 409)                     # not waiting for that
            step(confirm=True, fill={"description": "Nine"})
            step(result={"status": "success"})
            step(confirm=True)
            step(result={"status": "success", "message": "Submitted: process instance 900"})
            step(poll=True)
            out = step(approve=True).json()
            self.assertEqual(out["status"], "success")
            logs = list((Path(d) / "runs").glob("set_up_a_test_user-rbk-*.json"))
            self.assertEqual(len(logs), 1)
            saved = json.loads(logs[0].read_text(encoding="utf-8"))
            self.assertEqual((saved["by"], saved["status"], saved["inputs"]), ("ABSMITH", "success", {"user_id": "PSA_TEST09"}))
            self.assertEqual(len(saved["steps"]), 5)


if __name__ == "__main__":
    unittest.main()
