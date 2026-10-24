import json
import unittest

from starlette.testclient import TestClient

from psagent import auth
from psagent.agent import Agent, Session
from psagent.db import check_select
from psagent.security import AccessChecker, _component, _name, actions_of, apply_goal
from psagent.server import create_app

from fakes import FakeLLM, response, text, tool
from test_troubleshoot import TASKS, FakeDB

# Rows from the real HR92U052 system (2026-10-25): ABSMITH has recruiting roles but none with HCCPRS1040,
# the only permission list that grants Create Job Opening; VELU has no SJT_OPR_CLS rows.
ROWS = {
    "security admin check": (["N"], lambda p: [[1 if p["o"] in ("PS", "VELU") else 0]]),
    "find the page": (["CREF", "LABEL", "MENU", "COMPONENT", "MARKET", "FOLDER"], lambda p: [
        ["HC_HRS_JOB_OPENING_GBL", "Create New Job Opening", "HRS_HRPM", "HRS_JOB_OPENING", "GBL", "RS Hidden Components"],
        ["HC_HRS_JO_LAUNCH_GBL", "Create Job Opening", "HRS_HRPM", "HRS_JO_LAUNCH", "GBL", "RS Hidden Components"]]),
    "user profile": (["OPRID", "OPRDEFNDESC", "EMPLID", "ACCTLOCK", "PTACCTLOCKDATE", "FAILEDLOGINS", "LASTSIGNONDTTM",
                      "OPRCLASS", "ROWSECCLASS"],
                     lambda p: [[p["o"], "Abagail Smith", "KU0130", 0, None, 0, None, "HCPPUSA", "HCDPUSA"]]
                     if p["o"] == "ABSMITH" else []),
    "user's roles": (["ROLENAME", "DYNAMIC_SW", "ROLESTATUS", "PERMLISTS"],
                     lambda p: [["Employee", "N", "A", 39], ["Recruitment User", "N", "A", 5]]),
    "component definition": (["DESCR", "ACTIONS", "SEARCHRECNAME", "ADDSRCHRECNAME"],
                             lambda p: [["Create Job Opening", 1, "HRS_INST_I", "HRS_INST_I"]]),
    "component pages": (["ITEMNAME", "PNLNAME", "ITEMLABEL", "HIDDEN"], lambda p: [
        ["HRS_JO_LAUNCH", "HRS_JO_LAUNCH", "Create Job Opening", 0],
        ["HRS_JO_PRFL_PRVW", "HRS_JO_PRFL_PRVW", "Combined Preview Profiles", 1]]),
    "menu items of the component": (["MENUNAME", "BARNAME", "ITEMNAME", "SEARCHRECNAME"],
                                    lambda p: [["HRS_HRPM", "USE", "HRS_JO_LAUNCH", " "]]),
    "what the user's permission lists grant on this component": (
        ["ROLENAME", "CLASSID", "PNLITEMNAME", "DISPLAYONLY", "AUTHORIZEDACTIONS"], lambda p: []),
    "menu links (content references)": (
        ["PORTAL_OBJNAME", "PORTAL_LABEL", "PORTAL_ISPUBLIC", "PORTAL_PERMTYPE", "PORTAL_PERMNAME"],
        lambda p: [["HC_HRS_JO_LAUNCH_GBL", "Create Job Opening", 2, "P", "HCCPRS1040"]]),
    "user's permission lists": (["CLASSID"], lambda p: [["HCCPSS1000"], ["HCCPRS0000"]]),
    "permission lists that grant this component": (
        ["CLASSID", "CLASSDEFNDESC", "PAGES", "ACTS", "COMPONENTS"], lambda p: [["HCCPRS1040", "Job Opening Management", 1, 1, 27]]),
    "roles holding HCCPRS1040": (["ROLENAME", "PERMISSION_LISTS", "USERS", "ROLESTATUS", "NEW_COMPONENTS_FOR_USER"], lambda p: [
        ["Hiring Manager", 12, 46, "A", 62], ["Recruiter", 17, 29, "A", 81], ["Recruitment Administrator", 33, 51, "A", 178]]),
    "what HCCPRS1040 adds": (["N"], lambda p: [[25]]),
    "users of HCCPRS1040": (["N"], lambda p: [[84]]),
    "search record definition": (["RECTYPE", "SQLTEXT"], lambda p: [[1, "SELECT SETID FROM PS_INSTALLATION"]]),
    "search record fields": (["FIELDNAME"], lambda p: [["SETID"]]),
    "user's row security": (["ROWSECCLASS"], lambda p: [["HCDPALL"]]),
    "user's security classes": (["CLASSID", "ACCESS_ROWS"], lambda p: []),
    "person's security attributes": (
        ["SCRTY_TYPE_CD", "SCRTY_KEY1", "SCRTY_KEY2", "SCRTY_KEY3", "EMPL_RCD", "BUSINESS_UNIT", "SETID_DEPT", "DEPTID",
         "COMPANY", "HR_STATUS", "USER_HAS_ACCESS", "ROWSECCLASS_GRANTS"],
        lambda p: [["001", "SHARE", "ADMIN", " ", 0, "GBIBU", "SHARE", "ADMIN", "GBI", "A", 0, 1]]),
}


class SecDB(FakeDB):
    """The process troubleshooter's canned rows plus the security tables."""

    def query(self, sql, params=None, max_rows=200, who="", why="", max_cell=2000):
        if why in ROWS:
            check_select(sql)
            self.queries.append((" ".join(sql.split()), params, why))
            cols, rows = ROWS[why]
            return cols, rows(params or {}), False
        return super().query(sql, params, max_rows, who, why, max_cell)


FINISH = {
    "verdict": "missing_access", "confidence": "high",
    "summary": "None of ABSMITH's roles contains HCCPRS1040, the only permission list that grants Create Job Opening.",
    "page": "Create Job Opening (HRS_HRPM.HRS_JO_LAUNCH.GBL)",
    "checks": [{"check": "User profile", "status": "ok", "detail": "Exists, not locked."},
               {"check": "Permission lists", "status": "missing", "detail": "No permission list grants HRS_JO_LAUNCH."},
               {"check": "Row security", "status": "bogus", "detail": "HRS_INST_I has none."}],
    "missing": "Permission list HCCPRS1040 (Add on page HRS_JO_LAUNCH).",
    "fix": {"kind": "add_role", "user": "ABSMITH", "role": "Hiring Manager", "permission_list": "HCCPRS1040 (Job Opening Management)",
            "menu": "HRS_HRPM", "component": "HRS_JO_LAUNCH (HRS_HRPM.HRS_JO_LAUNCH.GBL)", "pages": ["HRS_JO_LAUNCH"],
            "actions": ["Add"], "description": "Add role Hiring Manager to ABSMITH.",
            "side_effects": "Only ABSMITH; about 62 new components."},
    "alternatives": ["Recruiter: 81 new components."], "after_fix": "ABSMITH signs out and in again."}


def access_llm(*more):
    return FakeLLM(
        response(tool("check_user_access", {"user": "absmith", "page": "Create Job Opening",
                                            "question": "Why can't ABSMITH access Create Job Opening?"})),
        response(text("Checking ABSMITH's access now.")),
        response(tool("find_page", {"text": "Create Job Opening"})),
        response(tool("check_access", {"user": "ABSMITH", "component": "HRS_HRPM.HRS_JO_LAUNCH.GBL"})),
        response(tool("finish", FINISH, id="f")),
        *more)


class AccessToolsTest(unittest.TestCase):
    def test_check_access_finds_the_gap_and_the_least_privilege_role(self):
        db = SecDB()
        r = json.loads(AccessChecker(None, db).check_access("ABSMITH", "HRS_HRPM.HRS_JO_LAUNCH.GBL", "PS"))
        self.assertFalse(r["can_open_component"])
        self.assertEqual(r["component"]["component_actions"], ["Add"])
        self.assertEqual([p["granted_actions"] for p in r["pages"]], [[], []])
        link = r["menu_links"][0]
        self.assertEqual((link["public"], link["user_has_permission"]), (False, False))   # 2 is not public
        self.assertFalse(r["search_record_row_security"]["applies_row_security"])
        pl = r["would_grant"]["permission_lists"][0]
        self.assertEqual((pl["CLASSID"], pl["ACTIONS"], pl["grants_all_visible_pages"]), ("HCCPRS1040", ["Add"], True))
        self.assertEqual(pl["roles"][0]["ROLENAME"], "Hiring Manager")
        self.assertEqual((pl["new_components_for_user"], pl["users_of_permission_list"]), (25, 84))
        self.assertTrue(all(q[0].upper().startswith(("SELECT", "WITH")) for q in db.queries))

    def test_menu_item_search_record_override(self):
        db = SecDB()
        ROWS_OVERRIDE = dict(ROWS)
        ROWS_OVERRIDE["menu items of the component"] = (ROWS["menu items of the component"][0],
                                                         lambda p: [["ADMINISTER_WORKFORCE_(GBL)", "USE", "JOB_DATA", "EMPLMT_SRCH_COR"]])
        ROWS_OVERRIDE["search record definition"] = (["RECTYPE", "SQLTEXT"],
                                                     lambda p: [[1, "SELECT ... FROM %Table(SJT_PERSON) SEC" if p["r"] == "EMPLMT_SRCH_COR" else "x"]])
        old = dict(ROWS)
        ROWS.update(ROWS_OVERRIDE)
        try:
            r = json.loads(AccessChecker(None, db).check_access("ABSMITH", "ADMINISTER_WORKFORCE_(GBL).JOB_DATA.GBL", "PS"))
        finally:
            ROWS.clear()
            ROWS.update(old)
        self.assertEqual(r["component"]["search_record"], "EMPLMT_SRCH_COR")         # menu names may contain ( )
        self.assertTrue(r["search_record_row_security"]["applies_row_security"])

    def test_row_security_never_refreshed(self):
        r = json.loads(AccessChecker(None, SecDB()).row_security("VELU", "KU0001", "PS"))
        self.assertFalse(r["user_reaches_person"])
        self.assertTrue(r["user_security_classes_missing"] and r["row_security_permission_list_would_grant"])
        self.assertIn("SCRTY_OPRCLS", r["likely_cause"])

    def test_admins(self):
        c = AccessChecker(None, SecDB(), admins=["SECADM"])
        self.assertTrue(c.is_admin("secadm"))                 # listed
        self.assertTrue(c.is_admin("PS"))                     # holds an admin role
        self.assertFalse(c.is_admin("ABSMITH"))
        self.assertFalse(AccessChecker(None, SecDB(), admin_roles=()).is_admin("PS"))

    def test_names_and_goals(self):
        self.assertEqual(_component("HRS_JO_LAUNCH (HRS_HRPM.HRS_JO_LAUNCH.GBL)"), "HRS_JO_LAUNCH")
        self.assertEqual(_name("ADMINISTER_WORKFORCE_(GBL)"), "ADMINISTER_WORKFORCE_(GBL)")
        self.assertEqual(actions_of(139), ["Add", "Update/Display", "Correction", "Data Entry"])
        goal, how = apply_goal({"kind": "add_role", "user": "ABSMITH", "role": "Hiring Manager", "permission_list": ""})
        self.assertEqual(goal, "Add role Hiring Manager to user ABSMITH")
        self.assertIn("MAINTAIN_SECURITY.USERMAINT.GBL", how)
        self.assertIsNone(apply_goal({"kind": "row_security", "user": "VELU", "role": "", "permission_list": ""}))


class AccessFlowTest(unittest.TestCase):
    def test_question_to_diagnosis_to_apply(self):
        llm = access_llm()
        agent, s = Agent(llm, TASKS, SecDB()), Session(id="s", oprid="PS")
        r = agent.chat(s, "Why can't ABSMITH access Create Job Opening?")
        t = r.troubleshoot
        self.assertEqual((t["kind"], t["user"], t["title"]), ("security", "ABSMITH", "Checking ABSMITH's access to Create Job Opening"))
        steps = []
        for _ in range(4):
            out = agent.troubleshoot_step(s, t["run_id"])
            steps += out["steps"]
            if out["done"]:
                break
        self.assertEqual(steps, ['Looked up the page "Create Job Opening" in the menu',
                                 "Checked ABSMITH's roles, permission lists, pages and menu link for HRS_HRPM.HRS_JO_LAUNCH.GBL",
                                 "Diagnosis ready"])
        d = out["diagnosis"]
        self.assertEqual((d["kind"], d["verdict"], d["can_apply"]), ("security", "missing_access", True))
        self.assertEqual((d["fix"]["component"], d["fix"]["permission_list"]), ("HRS_JO_LAUNCH", "HCCPRS1040"))
        self.assertEqual(d["checks"][2]["status"], "info")                         # unknown status cleaned
        self.assertIn("[access check result] ABSMITH / Create Job Opening", s.messages[-1]["content"])
        self.assertIsNone(s.investigation)
        live = agent.apply_security_fix(s, d["run_id"])["live"]
        self.assertEqual(live["goal"], "Add role Hiring Manager to user ABSMITH")
        self.assertIn("Do not grant anything else", s.live.details)
        self.assertEqual(s.running["task_id"], "security fix")
        self.assertIn("error", agent.apply_security_fix(s, "sec-unknown"))

    def test_only_security_admins(self):
        llm = FakeLLM(response(tool("check_user_access", {"user": "PS", "page": "Job Data", "question": ""})),
                      response(text("Only security administrators can check that.")))
        r = Agent(llm, TASKS, SecDB()).chat(Session(id="s", oprid="ABSMITH"), "why can't PS open Job Data")
        self.assertIsNone(r.troubleshoot)
        self.assertIn("only security administrators", json.dumps(llm.calls[-1][-1]["content"]))

    def test_api(self):
        app = create_app(Agent(access_llm(), TASKS, SecDB()), "k", [])
        c = TestClient(app)
        h = {"Authorization": f"Bearer {auth.make_token('k', 'PS')}"}
        self.assertTrue(c.get("/api/me", headers=h).json()["security"])
        other = {"Authorization": f"Bearer {auth.make_token('k', 'ABSMITH')}"}
        self.assertFalse(c.get("/api/me", headers=other).json()["security"])
        r = c.post("/api/chat", json={"message": "Why can't ABSMITH access Create Job Opening?"}, headers=h).json()
        sid, run = r["session_id"], r["troubleshoot"]["run_id"]
        for _ in range(4):
            out = c.post("/api/troubleshoot/step", json={"session_id": sid, "run_id": run}, headers=h).json()
            if out["done"]:
                break
        self.assertEqual(out["diagnosis"]["fix"]["role"], "Hiring Manager")
        a = c.post("/api/security/apply", json={"session_id": sid, "run_id": run}, headers=h)
        self.assertEqual(a.json()["live"]["goal"], "Add role Hiring Manager to user ABSMITH")
        self.assertEqual(c.post("/api/security/apply", json={"session_id": sid, "run_id": "nope"}, headers=h).status_code, 409)


if __name__ == "__main__":
    unittest.main()
