import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml
from starlette.testclient import TestClient

from psagent import auth, recorder
from psagent.agent import Agent, Session
from psagent.llm import LLMError
from psagent.server import create_app
from psagent.tasks import load_tasks, parse_task

from fakes import FakeLLM, response, text, tool

TASKS_DIR = Path(__file__).resolve().parent / "fixtures" / "tasks"


def s(id, value, label="", kind="text", column=None):
    field = {"id": id, "label": label}
    if column:
        field["column"] = column
    return {"do": "set", "field": field, "kind": kind, "value": value}


# What the widget sends after a teacher creates user KUMAR by hand on USERMAINT.
RECORDING = {
    "title": "Create user profile",
    "component": {"menu": "MAINTAIN_SECURITY", "component": "USERMAINT", "market": "GBL"},
    "query": {"Action": "A"},
    "steps": [
        s("PSOPRDEFN_SRCH_OPRID", "KUMAR", "User ID"),
        {"do": "click", "button": {"id": "#ICSearch", "text": "Add"}},
        s("PSOPRDEFN_OPRDEFNDESC", "Kumar Raja", "Description"),
        s("PSOPRDEFN_SYMBOLICID", "SYSADM1", "Symbolic ID", kind="select"),
        s("PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT", "", "New Password", kind="password"),
        s("PSUSRPRFL_WRK_OPERPSWDCONF", "", "Confirm Password", kind="password"),
        {"do": "tab", "label": "ID"},
        s("OPRALIASTYPE_X", "Employee", "ID Type", kind="select"),
        s("ATTRVAL_X$0", "KU0099", column="Attribute Value"),
        {"do": "tab", "label": "Roles"},
        s("PSROLEUSER_VW_ROLENAME$0", "EMPLOYEE", column="Role Name"),
        s("PSROLEUSER_VW_ROLENAME$1", "Manager", column="Role Name"),
        {"do": "click", "button": {"id": "#ICSave", "text": "Save"}},
    ],
}

REVIEWED_MODES = {
    "PSOPRDEFN_SRCH_OPRID": ("user_id", "required"),
    "PSOPRDEFN_OPRDEFNDESC": ("description", "required"),
    "PSOPRDEFN_SYMBOLICID": ("symbolic_id", "default"),
    "OPRALIASTYPE_X": ("id_type", "fixed"),
    "ATTRVAL_X$0": ("emplid", "optional"),
    "PSROLEUSER_VW_ROLENAME": ("roles", "optional"),
}


def claude_draft():
    return {
        "task_id": "create_user_quick", "title": "Create User (quick)", "summary": "Adds a user.",
        "examples": ["create user anita"],
        "fields": [{"key": k, "name": n, "label": n.replace("_", " ").title(), "mode": m, "hint": ""}
                   for k, (n, m) in REVIEWED_MODES.items()],
    }


class CandidatesTest(unittest.TestCase):
    def test_values_grids_and_secrets(self):
        cands = {c["key"]: c for c in recorder.candidates(RECORDING)}
        self.assertEqual(cands["PSROLEUSER_VW_ROLENAME"]["kind"], "list")
        self.assertEqual(cands["PSROLEUSER_VW_ROLENAME"]["value"], ["EMPLOYEE", "Manager"])
        self.assertEqual(cands["ATTRVAL_X$0"]["kind"], "text")    # one grid row = plain value
        self.assertEqual(cands["ATTRVAL_X$0"]["label"], "Attribute Value")
        secrets = [k for k in cands if k.startswith("secret:")]
        self.assertEqual(secrets, ["secret:password"])            # New Password + Confirm Password
        self.assertEqual(len(cands), 7)

    def test_validation(self):
        for bad in [None, {}, {**RECORDING, "steps": []},
                    {**RECORDING, "steps": [{"do": "click", "button": {"id": "#ICSave"}}]},
                    {**RECORDING, "steps": [{"do": "rm -rf"}]},
                    {**RECORDING, "component": {"menu": "x y", "component": "C"}}]:
            with self.assertRaises(recorder.RecordingError):
                recorder.draft(bad)


class DraftTest(unittest.TestCase):
    def test_rules_without_llm(self):
        d = recorder.draft(RECORDING)
        modes = {f["key"]: f["mode"] for f in d["fields"]}
        self.assertEqual(modes["PSOPRDEFN_SRCH_OPRID"], "required")
        self.assertEqual(modes["PSOPRDEFN_SYMBOLICID"], "default")
        self.assertEqual(modes["PSROLEUSER_VW_ROLENAME"], "optional")
        self.assertEqual(modes["secret:password"], "secret")
        self.assertEqual(d["task_id"], "create_user_profile")
        self.assertEqual(d["suggested_by"], "rules")

    def test_claude_suggestions_applied(self):
        llm = FakeLLM(drafts=[claude_draft()])
        d = recorder.draft(RECORDING, llm)
        self.assertEqual(d["suggested_by"], "claude")
        self.assertEqual(d["task_id"], "create_user_quick")
        f = {x["key"]: x for x in d["fields"]}
        self.assertEqual((f["OPRALIASTYPE_X"]["name"], f["OPRALIASTYPE_X"]["mode"]), ("id_type", "fixed"))
        self.assertEqual(f["secret:password"]["mode"], "secret")   # never offered to Claude
        prompt, schema = llm.structured_calls[0]
        self.assertNotIn("secret:password", json.dumps(schema))
        self.assertIn("KU0099", prompt)

    def test_llm_failure_falls_back_to_rules(self):
        d = recorder.draft(RECORDING, FakeLLM())   # structured() raises LLMError
        self.assertEqual(d["suggested_by"], "rules")


def reviewed(**over):
    d = recorder.draft(RECORDING, FakeLLM(drafts=[claude_draft()]))
    d.update(over)
    return d


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.task_dict = recorder.build(RECORDING, reviewed(), "TEACHER")
        self.task = parse_task(yaml.safe_load(recorder.to_yaml(self.task_dict)))

    def test_fields(self):
        f = self.task.fields
        self.assertEqual(list(f), ["user_id", "description", "symbolic_id", "password", "emplid", "roles"])
        self.assertTrue(f["user_id"].required and f["user_id"].upper)
        self.assertEqual(f["symbolic_id"].default, "SYSADM1")
        self.assertTrue(f["password"].sensitive)
        self.assertFalse(f["emplid"].required)
        self.assertEqual(f["roles"].type, "list")
        self.assertFalse(f["roles"].upper)                          # "Manager" is mixed case
        self.assertEqual(self.task.missing({}), ["user_id", "description"])

    def test_playbook_without_optionals(self):
        steps = self.task.render({"user_id": "ANITA", "description": "Anita Rao"})
        kinds = [st["do"] for st in steps]
        self.assertEqual(kinds, ["navigate", "set", "click", "set", "set", "set", "set", "click", "expect_saved"])
        self.assertEqual(steps[0]["query"], {"Action": "A"})
        self.assertEqual(steps[1]["value"], "ANITA")
        self.assertEqual(steps[5]["value"], "{{secret:password}}")
        self.assertEqual(steps[6]["value"], "{{secret:password}}")

    def test_playbook_with_optionals(self):
        steps = self.task.render({"user_id": "A", "description": "A", "emplid": "KU1", "roles": ["EMPLOYEE"]})
        idtab = steps[[st.get("label") for st in steps].index("ID"):]
        self.assertEqual(idtab[1]["value"], "Employee")             # fixed value kept from the recording
        self.assertEqual(idtab[2]["value"], "KU1")
        grid = next(st for st in steps if st["do"] == "grid")
        self.assertEqual(grid, {"do": "grid", "field": {"id": "PSROLEUSER_VW_ROLENAME", "column": "Role Name"},
                                "values": ["EMPLOYEE"]})

    def test_real_layout_fixed_setup_follows_optional_value(self):
        # Real PT 8.5x USERMAINT: Description is entered on the ID tab after the EMPLID,
        # so the ID tab always runs, but ID Type = Employee only matters with an EMPLID.
        rec = copy.deepcopy(RECORDING)
        steps = rec["steps"]
        del steps[2]                                                # no Description on General
        steps.insert(8, s("PSOPRDEFN_DVW_OPRDEFNDESC$0", "Kumar Raja", "Description"))
        d = reviewed()
        for f in d["fields"]:
            if f["key"] == "PSOPRDEFN_OPRDEFNDESC":
                f["key"] = "PSOPRDEFN_DVW_OPRDEFNDESC$0"
        task = parse_task(recorder.build(rec, d, "T"))
        without = task.render({"user_id": "A", "description": "Anita"})
        self.assertIn({"do": "tab", "label": "ID"}, without)
        ids = [st["field"]["id"] for st in without if st["do"] == "set"]
        self.assertIn("PSOPRDEFN_DVW_OPRDEFNDESC$0", ids)
        self.assertNotIn("OPRALIASTYPE_X", ids)
        self.assertNotIn("ATTRVAL_X$0", ids)
        with_emp = [st["field"]["id"] for st in task.render({"user_id": "A", "description": "A", "emplid": "KU1"})
                    if st["do"] == "set"]
        self.assertEqual(with_emp[-3:], ["OPRALIASTYPE_X", "ATTRVAL_X$0", "PSOPRDEFN_DVW_OPRDEFNDESC$0"])

    def test_repeated_same_value_is_dropped(self):
        rec = copy.deepcopy(RECORDING)
        rec["steps"].insert(2, s("PSOPRDEFN_SRCH_OPRID", "KUMAR", "User ID"))   # re-set after the Add click
        steps = parse_task(recorder.build(rec, reviewed(), "T")).render({"user_id": "A", "description": "A"})
        ids = [st["field"]["id"] for st in steps if st["do"] == "set"]
        self.assertEqual(ids.count("PSOPRDEFN_SRCH_OPRID"), 1)

    def test_yaml_header(self):
        self.assertTrue(recorder.to_yaml(self.task_dict).startswith("# Recorded with PSAgent teach mode by TEACHER"))

    def test_all_fixed_is_a_valid_task(self):
        d = reviewed()
        for f in d["fields"]:
            if f["mode"] != "secret":
                f["mode"] = "fixed"
        task = parse_task(recorder.build(RECORDING, d, "T"))       # e.g. "run my weekly check"
        self.assertEqual(list(task.fields), ["password"])
        steps = task.render({})
        self.assertEqual(steps[1]["value"], "KUMAR")
        self.assertEqual(next(st for st in steps if st["do"] == "grid")["values"], ["EMPLOYEE", "Manager"])

    def test_build_errors(self):
        cases = [reviewed(task_id="X"), reviewed(task_id="../../etc/passwd"), reviewed(title="")]
        bad_mode = reviewed()
        bad_mode["fields"][0]["mode"] = "secret"                    # can't turn a text value into a secret
        cases.append(bad_mode)
        for d in cases:
            with self.assertRaises(recorder.RecordingError):
                recorder.build(RECORDING, d, "T")


SECRET = "k"


def hdr(oprid):
    return {"Authorization": f"Bearer {auth.make_token(SECRET, oprid)}"}


class RecordingApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.llm = FakeLLM(drafts=[claude_draft()])
        self.agent = Agent(self.llm, load_tasks(TASKS_DIR))
        self.c = TestClient(create_app(self.agent, SECRET, [], teachers={"TEACHER"}, tasks_dir=self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_teachers(self):
        self.assertEqual(self.c.get("/api/me", headers=hdr("CLERK")).json(), {"oprid": "CLERK", "can_teach": False})
        self.assertTrue(self.c.get("/api/me", headers=hdr("TEACHER")).json()["can_teach"])
        r = self.c.post("/api/recordings/draft", json={"recording": RECORDING}, headers=hdr("CLERK"))
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/recordings/save", json={"recording": RECORDING, "draft": {}}, headers=hdr("CLERK"))
        self.assertEqual(r.status_code, 403)

    def test_draft_save_then_use(self):
        d = self.c.post("/api/recordings/draft", json={"recording": RECORDING}, headers=hdr("TEACHER")).json()["draft"]
        self.assertFalse(d["exists"])
        r = self.c.post("/api/recordings/save", json={"recording": RECORDING, "draft": d}, headers=hdr("TEACHER"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["task_id"], "create_user_quick")
        saved = Path(self.tmp.name) / "create_user_quick.yaml"
        self.assertIn("create_user_quick", load_tasks(self.tmp.name))   # what a restart would load
        self.assertIn("create_user_quick", self.agent.tasks)            # live without a restart
        again = self.c.post("/api/recordings/save", json={"recording": RECORDING, "draft": d}, headers=hdr("TEACHER"))
        self.assertEqual(again.status_code, 409)
        d2 = copy.deepcopy(d)
        d2["title"] = "Create User (v2)"
        r = self.c.post("/api/recordings/save", json={"recording": RECORDING, "draft": d2, "overwrite": True},
                        headers=hdr("TEACHER"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Create User (v2)", saved.read_text())

    def test_bad_draft_rejected(self):
        d = recorder.draft(RECORDING)
        d["task_id"] = "../../etc/passwd"
        r = self.c.post("/api/recordings/save", json={"recording": RECORDING, "draft": d}, headers=hdr("TEACHER"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])


class AnnouncementTest(unittest.TestCase):
    def task(self):
        return parse_task(recorder.build(RECORDING, reviewed(), "T"))

    def test_new_task_announced_once_as_system_message(self):
        llm = FakeLLM(response(text("Hi")), response(text("I can now create users quickly.")), response(text("ok")))
        agent, s = Agent(llm, load_tasks(TASKS_DIR)), Session(id="s", oprid="PS")
        agent.chat(s, "hi")
        agent.add_task(self.task())
        agent.chat(s, "what can you do?")
        roles = [m["role"] for m in s.messages]
        self.assertEqual(roles, ["user", "assistant", "user", "system", "assistant"])
        self.assertIn("create_user_quick", s.messages[3]["content"])
        self.assertNotIn("create_user_quick", s.system)                 # prompt prefix untouched
        agent.chat(s, "thanks")
        self.assertEqual([m["role"] for m in s.messages].count("system"), 1)

    def test_announcement_taken_back_when_llm_fails(self):
        llm = FakeLLM(response(text("Hi")), LLMError("down"), response(text("ok")))
        agent, s = Agent(llm, load_tasks(TASKS_DIR)), Session(id="s", oprid="PS")
        agent.chat(s, "hi")
        agent.add_task(self.task())
        agent.chat(s, "hello?")
        self.assertEqual(s.messages[-1]["role"], "user")                # no dangling system message
        agent.chat(s, "again")
        self.assertEqual([m["role"] for m in s.messages], ["user", "assistant", "user", "user", "system", "assistant"])

    def test_offer_recording(self):
        def run(can_teach):
            llm = FakeLLM(response(tool("offer_recording", {"title": "Create Requisition"})), response(text("ok")))
            s = Session(id="s", oprid="PS", can_teach=can_teach)
            r = Agent(llm, load_tasks(TASKS_DIR)).chat(s, "let me teach you requisitions")
            return r, llm.calls[-1][-1]["content"][0]
        r, result = run(True)
        self.assertEqual(r.record, {"title": "Create Requisition"})
        r, result = run(False)
        self.assertIsNone(r.record)
        self.assertTrue(result["is_error"])


if __name__ == "__main__":
    unittest.main()
