import json
import unittest

from starlette.testclient import TestClient

from psagent import auth
from psagent.agent import Agent, Session
from psagent.approvals import AbsenceReviewer, confidence_level
from psagent.db import check_select
from psagent.server import create_app

from fakes import FakeLLM, response, text, tool
from test_troubleshoot import TASKS

XLAT = {"WF_STATUS": [["A", "Approved"], ["S", "Submitted"]],
        "EOAWSTEP_STATUS": [["A", "Approved"], ["P", "Pending"]],
        "EOAWTHREAD_STATUS": [["P", "Pending"]],
        "NEG_BAL_OPTN": [["A", "Allow Negative Balance"], ["U", "Take as Unpaid Time"]]}

# Rows from the real HR92U052 system (2026-10-27): thread 1589, Edmund Donahue's vacation on 2018-01-31,
# waiting for RCHANNING. PS is a PeopleSoft Administrator.
ROWS = {
    "approval admin check": (["N"], lambda p: [[1 if p["o"] == "PS" else 0]]),
    "security admin check": (["N"], lambda p: [[0]]),
    "recent failed requests": (["PRCSINSTANCE", "PRCSNAME", "RUNSTATUS"], lambda p: []),
    "is the user an approver of this request": (["N"], lambda p: [[1 if p["o"] == "RCHANNING" else 0]]),
    "pending absence approvals": (
        ["THREAD", "EMPLID", "BGN_DT", "END_DT", "EOAWREQUESTOR_ID", "ABSENCE", "NAME", "SINCE", "APPROVERS", "APPROVER",
         "MINE", "HOURS"],
        lambda p: [[1589, "KU0102", "2018-01-31 00:00:00", "2018-01-31 00:00:00", "EDONAHUE", "Vacation", "Edmund Donahue",
                    "2017-12-06 16:23:06", 1, "RCHANNING", 1 if p["o"] == "RCHANNING" else 0, 8.0]]),
    "the absence request": (
        ["EOAWTHREAD_ID", "EOAWDEFN_ID", "ORIGINATORID", "EOAWREQUESTOR_ID", "EOAWTHREAD_STATUS", "EOAWDTTM_MODIFIED",
         "COMMENTS", "TRANSACTION_NBR", "EMPLID", "EMPL_RCD", "BGN_DT", "END_DT", "PIN_TAKE_NUM"],
        lambda p: [[1589, "AbsenceMgmtBySupervisorId", "EDONAHUE", "EDONAHUE", "P", "2017-12-06 16:23:06", None, 2444,
                    "KU0102", 0, "2018-01-31 00:00:00", "2018-01-31 00:00:00", 2392]] if p["t"] == 1589 else []),
    "today": (["TODAY"], lambda p: [["2018-01-20"]]),
    "the requested absence": (
        ["BGN_DT", "END_DT", "DURATION_ABS", "DURATION_DYS", "ABSENCE_REASON", "WF_STATUS", "VOIDED_IND",
         "BEGIN_DAY_HALF_IND", "END_DAY_HALF_IND", "ACTION_DT_SS", "COMMENTS"],
        lambda p: [["2018-01-31 00:00:00", "2018-01-31 00:00:00", 8.0, 1.0, "VAC", "S", "N", "N", "N", "2017-12-06", None]]),
    "earlier version of the absence": (
        ["ORIG_BEGIN_DT", "ORIG_END_DT", "ORIG_PIN_TAKE_NUM", "ORIG_ABSENCE"],
        lambda p: [["2018-01-31 00:00:00", "2018-01-31 00:00:00", 2392, "Vacation"]]),
    "absence type rules": (["PIN_NM", "DESCR", "ABS_TYPE_OPTN", "NEG_BAL_OPTN", "NEG_LMT_VAL", "MGR_APPR_REQ_IND"],
                           lambda p: [["K0WAT VAC TAKE", "Vacation", "VAC", "A", 0.0, "N"]]),
    "approval chain": (["EOAWSTAGE_NBR", "EOAWPATH_ID", "EOAWSTEP_NBR", "OPRID", "EOAWSTEP_STATUS", "DTTM_CREATED",
                        "EOAWDTTM_COMPLETE"],
                       lambda p: [[1, "1", 1.0, "RCHANNING", "P", "2017-12-06 16:23:06", None]]),
    "approval comments": (["EOAW_OPRID", "EOAWDTTM_MODIFIED", "COMMENTS"], lambda p: []),
    "the employee's job": (
        ["EMPLID", "NAME_DISPLAY", "HR_STATUS", "EMPL_STATUS", "DEPTID", "DEPARTMENT", "JOBCODE", "JOB_TITLE",
         "SUPERVISOR_ID", "FULL_PART_TIME", "STD_HOURS", "HIRE_DT"],
        lambda p: [["KU0102", "Edmund Donahue", "A", "A", "13111", "Accounts Payable", "600145", "Manager-Accounting",
                    "KU0046", "F", 40.0, "1998-08-28 00:00:00"]]),
    "entitlements of the absence type": (["PRIORITY", "PIN_NUM", "PIN_NM", "DESCR"],
                                         lambda p: [[1, 2385, "K0WAE VAC", "Vacations Entitlement"]]),
    "balance of Vacations Entitlement": (["PIN_NM", "BALANCE", "SLICE_END_DT", "CAL_RUN_ID"],
                                         lambda p: [["K0WAE VAC_BAL", 571.666657, "2017-12-31 00:00:00", "K0CG K0CAK17M12"]]
                                         if p["nm"] == "K0WAE VAC_BAL" else []),
    "absences not yet deducted": (["H", "N"], lambda p: [[16.0, 2]]),
    "the employee's other absences": (
        ["BGN_DT", "END_DT", "ABSENCE", "DURATION_ABS", "WF_STATUS", "ABSENCE_REASON"],
        lambda p: [["2017-09-08 00:00:00", "2017-09-08 00:00:00", "Vacation", 8.0, "A", "PTO"],
                   ["2018-01-31 00:00:00", "2018-02-01 00:00:00", "Sick", 16.0, "A", " "]]),
    "team size": (["N"], lambda p: [[7]]),
    "team absences on the same days": (["EMPLID", "NAME_DISPLAY", "BGN_DT", "END_DT", "ABSENCE", "WF_STATUS"],
                                       lambda p: [["KU0046", "Rosanna Channing", "2018-01-29 00:00:00",
                                                   "2018-02-02 00:00:00", "Vacation", "A"]]),
}


class ApprovalDB:
    def __init__(self):
        self.queries = []

    def query(self, sql, params=None, max_rows=200, who="", why="", max_cell=2000):
        check_select(sql)                       # every query the tools build must pass the read-only check
        self.queries.append((" ".join(sql.split()), params, why))
        if why.startswith("meaning of "):
            return ["FIELDVALUE", "XLATLONGNAME"], XLAT[params["f"]], False
        cols, rows = ROWS[why]
        return cols, rows(params or {}), False


FINISH = {
    "recommendation": "send_back", "confidence_score": 88,
    "confidence_reason": "Balance, overlaps and team absences were all read from the data.",
    "summary": "Edmund Donahue already has approved sick leave on 31 Jan 2018, and their manager is off that week.",
    "request": "Vacation, 31 Jan 2018, 8 hours, Edmund Donahue (KU0102)",
    "checks": [{"check": "Balance", "status": "ok", "detail": "555.67 hours available after 16 not yet deducted."},
               {"check": "Overlaps", "status": "problem", "detail": "Approved sick leave on 31 Jan - 1 Feb 2018."},
               {"check": "Team coverage", "status": "weird", "detail": "1 of 7 off; the manager KU0046."}],
    "comment": "This overlaps your approved sick leave on 31 January; please withdraw or change the dates.",
    "not_checked": ["Public holidays"]}


def review_llm(*more, finish=None):
    return FakeLLM(
        response(tool("list_absence_approvals", {"everyone": False})),
        response(text("One absence request is waiting for you.")),
        response(tool("get_absence_request", {"thread": "1589"})),
        response(tool("finish", finish or FINISH, id="f")),
        *more)


class ReviewerToolsTest(unittest.TestCase):
    def test_request_brings_balance_overlaps_team_and_status(self):
        db = ApprovalDB()
        r = AbsenceReviewer(None, db)
        inv = r.begin("1589")
        out = json.loads(r.get_request(inv, "RCHANNING"))
        self.assertEqual(out["request"]["days_until_start"], 11)
        self.assertEqual(out["request"]["event"]["WF_STATUS"], "S (Submitted)")
        self.assertIsNone(out["request"]["changes_earlier_absence"])
        self.assertEqual(out["absence_type_rules"]["NEG_BAL_OPTN"], "A (Allow Negative Balance)")
        b = out["balance"][0]
        self.assertEqual((b["accumulator"], b["as_of"], b["not_yet_deducted"], b["available_before_this_request"]),
                         ("K0WAE VAC_BAL", "2017-12-31", 16.0, 555.67))
        self.assertEqual([o["ABSENCE"] for o in out["other_absences"]["overlapping_this_request"]], ["Sick"])
        self.assertEqual(out["other_absences"]["last_12_months_by_type"], {"Vacation": 8.0})
        self.assertEqual((out["team"]["active_members"], out["team"]["key"]), (7, "KU0046"))
        self.assertEqual(out["team"]["off_on_same_days"][0]["WF_STATUS"], "A (Approved)")
        self.assertEqual(inv.status, {"thread_status": "Pending", "waiting_for": ["RCHANNING"], "waiting_count": 1,
                                      "decided": [], "step": "1 of 1", "submitted": "2017-12-06",
                                      "requested_by": "EDONAHUE", "reviewer_is_approver": True})
        self.assertTrue(all(q[0].upper().startswith(("SELECT", "WITH")) for q in db.queries))

    def test_unknown_request(self):
        r = AbsenceReviewer(None, ApprovalDB())
        self.assertIn("no absence request", r.get_request(r.begin("42"), "PS"))

    def test_who_sees_what(self):
        r = AbsenceReviewer(None, ApprovalDB())
        self.assertTrue(r.can_review("RCHANNING", "1589"))                  # the approver
        self.assertTrue(r.can_review("PS", "1589", admin=True))             # PSAGENT_ADMIN: any request
        self.assertFalse(r.can_review("ABSMITH", "1589") or r.can_review("PS", "1589"))
        db = ApprovalDB()
        AbsenceReviewer(None, db).pending("RCHANNING", everyone=True)       # not an admin: own queue only
        self.assertIn("U2.OPRID = :o", db.queries[-1][0])
        db.queries.clear()
        items = AbsenceReviewer(None, db).pending("PS", everyone=True, admin=True)
        self.assertNotIn("U2.OPRID = :o", db.queries[-1][0])
        self.assertEqual(items[0], {"thread": "1589", "employee": "KU0102", "name": "Edmund Donahue", "absence": "Vacation",
                                    "from": "2018-01-31", "to": "2018-01-31", "hours": 8.0, "requested_by": "EDONAHUE",
                                    "since": "2017-12-06", "approvers": 1, "approver": "RCHANNING",
                                    "waiting_for_you": False})

    def test_confidence(self):
        self.assertEqual([confidence_level(x) for x in (95, 80, 79, 50, 49)], ["high", "high", "medium", "medium", "low"])
        r = AbsenceReviewer(None, ApprovalDB())
        inv = r.begin("1589")
        d = r._diagnosis(inv, {**FINISH, "recommendation": "approve", "confidence_score": 140})
        self.assertEqual((d["confidence_score"], d["confidence"]), (50, "medium"))   # approve despite a problem: capped
        self.assertIn("Capped at 50", d["confidence_reason"])
        d = r._diagnosis(inv, {**FINISH, "recommendation": "maybe", "confidence_score": "x", "checks": []})
        self.assertEqual((d["recommendation"], d["confidence_score"], d["confidence"]), ("needs_review", 0, "low"))


class ReviewFlowTest(unittest.TestCase):
    def test_list_then_review_to_recommendation(self):
        llm = review_llm()
        agent, s = Agent(llm, TASKS, ApprovalDB()), Session(id="s", oprid="RCHANNING")
        r = agent.chat(s, "Show my pending approvals")
        self.assertEqual([i["thread"] for i in r.approvals["items"]], ["1589"])
        self.assertTrue(r.approvals["items"][0]["waiting_for_you"])
        self.assertEqual((r.approvals["everyone"], r.approvals["admin"]), (False, False))
        t = agent.start_review(s, "1589")["troubleshoot"]
        self.assertEqual((t["kind"], t["title"]), ("approval", "Reviewing absence request 1589"))
        steps = []
        for _ in range(3):
            out = agent.troubleshoot_step(s, t["run_id"])
            steps += out["steps"]
            if out["done"]:
                break
        self.assertEqual(steps, ["Read absence request 1589: balance, approvals, other absences, team", "Diagnosis ready"])
        d = out["diagnosis"]
        self.assertEqual((d["kind"], d["recommendation"], d["confidence_score"], d["confidence"]),
                         ("approval", "send_back", 88, "high"))
        self.assertEqual(d["checks"][2]["status"], "info")                          # unknown status cleaned
        self.assertEqual(d["status"]["waiting_for"], ["RCHANNING"])                 # approval status from the database
        self.assertIn("[absence review result] request 1589", s.messages[-1]["content"])
        self.assertIsNone(s.investigation)

    def test_only_approvers_or_admins(self):
        agent = Agent(FakeLLM(), TASKS, ApprovalDB())
        self.assertIn("not waiting for your approval", agent.start_review(Session(id="s", oprid="ABSMITH"), "1589")["error"])
        self.assertIn("thread ID", agent.start_review(Session(id="s", oprid="PS"), "abc")["error"])
        self.assertIn("database access", Agent(FakeLLM(), TASKS).start_review(Session(id="s", oprid="PS"), "1589")["error"])

    def test_everyone_only_for_admins(self):
        llm = FakeLLM(response(tool("list_absence_approvals", {"everyone": True})), response(text("None.")))
        db = ApprovalDB()
        r = Agent(llm, TASKS, db).chat(Session(id="s", oprid="RCHANNING"), "show everyone's pending absences")
        self.assertFalse(r.approvals["everyone"])
        self.assertIn("U2.OPRID = :o", [q for q in db.queries if q[2] == "pending absence approvals"][0][0])

    def test_api(self):
        c = TestClient(create_app(Agent(review_llm(), TASKS, ApprovalDB()), "k", []))
        h = {"Authorization": f"Bearer {auth.make_token('k', 'RCHANNING')}"}
        self.assertTrue(c.get("/api/me", headers=h).json()["approvals"])
        r = c.post("/api/chat", json={"message": "Show my pending approvals"}, headers=h).json()
        sid = r["session_id"]
        self.assertEqual(r["approvals"]["items"][0]["name"], "Edmund Donahue")
        t = c.post("/api/approvals/review", json={"session_id": sid, "thread": "1589"}, headers=h).json()["troubleshoot"]
        for _ in range(3):
            out = c.post("/api/troubleshoot/step", json={"session_id": sid, "run_id": t["run_id"]}, headers=h).json()
            if out["done"]:
                break
        self.assertEqual(out["diagnosis"]["recommendation"], "send_back")
        other = {"Authorization": f"Bearer {auth.make_token('k', 'ABSMITH')}"}
        review = "/api/approvals/review"
        self.assertEqual(c.post(review, json={"session_id": sid, "thread": ""}, headers=h).status_code, 400)
        self.assertEqual(c.post(review, json={"session_id": "nope", "thread": "1589"}, headers=h).status_code, 404)
        self.assertEqual(c.post(review, json={"session_id": sid, "thread": "1589"}, headers=other).status_code,
                         404)                                                         # someone else's session


if __name__ == "__main__":
    unittest.main()
