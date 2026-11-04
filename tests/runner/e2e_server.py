"""Test server for the browser tests (run by run_browser_tests.mjs).

One origin serves everything, like PeopleSoft + a reverse-proxied agent:
  /psc/ps/EMPLOYEE/HRMS/c/NUI_FRAMEWORK.PT_LANDINGPAGE.GBL  host page with psagent.js
  /psc/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.USERMAINT.GBL   mock component
  /psp/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.USERMAINT.GBL   portal page: header + TargetContent frame
  /api/*                                                   real agent API, scripted LLM
  /__test/action, /__test/bodies, /__test/task_file        test hooks
  /__test/watch_tick                                       one watcher round (the monitors run "overnight")
  /__test/norole                                           host page for a user without a PSAGENT role
  /psp/ps/?cmd=login                                       a sign-in page, for the headless regression runner
  /psc/ps/EMPLOYEE/HRMS/s/WEBLIB_SOAPTOCI...IScript_SOAPToCI  a mock SOAPTOCI (USER_PROFILE): GetCIShape, Create, Updatedata
  /__test/ci_fill, /__test/ci_users                        fill a downloaded CI template; the users the mock CI saved
  /psc/ps/EMPLOYEE/HRMS/q/?ICAction=ICQryNameExcelURL=...  a mock Query Viewer (Run to Excel) for the AI_ queries
  /__test/query_hits                                       what the browser asked the mock Query Viewer for
Recorded tasks are saved to a temporary copy of agent-service/tasks; monitors and test suites to temporary folders.
Regression tests are on, and unattended runs use the service's own headless browser against this server.
The audit log is on (a temporary folder) with 30 days of sample history; the scripted Claude calls and the mock
database's queries are recorded like real ones, so the dashboard shows what the test itself did.
"""

import json
import os
import random
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent-service"), str(ROOT / "agent-service" / "tests")]

import uvicorn  # noqa: E402
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from fakes import FakeLLM, response, text, tool  # noqa: E402
from psagent.agent import Agent  # noqa: E402
from psagent.db import check_select  # noqa: E402
from psagent import audit, ci, queries, regress, regress_runner  # noqa: E402
from psagent.server import Regression, create_app  # noqa: E402
from psagent.tasks import load_tasks  # noqa: E402
from psagent.watcher import Watcher  # noqa: E402
from test_security import FINISH as ACCESS_FINISH, SecDB  # noqa: E402  (real security rows: ABSMITH, HCCPRS1040)
from test_approvals import FINISH as REVIEW_FINISH, ROWS as APPROVAL_ROWS, XLAT  # noqa: E402  (absence request 1589)
from test_runbooks import HAS_ROLE, USER_FREE, step  # noqa: E402
from test_ci import SHAPE_XML, CIDB  # noqa: E402  (USER_PROFILE's real shape and metadata)
from test_queries import HEADS, HIRES, PTDB, ps_answer  # noqa: E402  (PeopleTools query tables in SQLite)

TASKS_DIR = Path(tempfile.mkdtemp(prefix="psagent-tasks-"))
shutil.copytree(ROOT / "agent-service" / "tests" / "fixtures" / "tasks", TASKS_DIR, dirs_exist_ok=True)
TASKS = load_tasks(TASKS_DIR)
RUNBOOKS_DIR = Path(tempfile.mkdtemp(prefix="psagent-runbooks-"))
MONITORS_DIR = Path(tempfile.mkdtemp(prefix="psagent-monitors-"))
REGRESSION_DIR = Path(os.environ.get("PSAGENT_E2E_REGRESSION_DIR") or tempfile.mkdtemp(prefix="psagent-regression-"))
HERE = Path(__file__).resolve().parent

# Claude's suggestions for the recording made in the browser test. emplid is
# deliberately "required" here; the test changes it to optional on the review card.
DRAFT = {
    "task_id": "create_user_quick", "title": "Create User (quick)", "summary": "Adds a user from the recorded steps.",
    "examples": ["create user anita"],
    "fields": [{"key": k, "name": n, "label": label, "mode": m, "hint": ""} for k, n, label, m in [
        ("PSOPRDEFN_SRCH_OPRID", "user_id", "User ID", "required"),
        ("PSOPRDEFN_DVW_OPRDEFNDESC$0", "description", "Description", "required"),
        ("PSOPRDEFN_SYMBOLICID", "symbolic_id", "Symbolic ID", "default"),
        ("OPRALIASTYPE_X", "id_type", "ID Type", "fixed"),
        ("ATTRVAL_X$0", "emplid", "Employee ID", "required"),
        ("PSROLEUSER_VW_ROLENAME", "roles", "Roles", "optional"),
    ]],
}

# Suggestions for the process run recorded in the browser test (Sync Portal Security)
DRAFT_PROCESS = {
    "task_id": "sync_portal_security", "title": "Sync Portal Security", "summary": "Runs the portal security sync.",
    "examples": ["sync portal security"],
    "fields": [{"key": k, "name": n, "label": label, "mode": m, "hint": ""} for k, n, label, m in [
        ("PRCSRUNCNTL_RUN_CNTL_ID", "run_control_id", "Run Control ID", "default"),
        ("PRCSRQSTDLG_WRK_SERVERNAME", "server_name", "Server Name", "fixed"),
        ("PRCSRQSTDLG_WRK_SELECT_FLAG$0", "select", "Select", "fixed"),
    ]],
}

USER = {"user_id": "VELU", "description": "Velu Arumugam", "emplid": "KU0042",
        "roles": "EMPLOYEE, PeopleSoft User", "primary_permission_list": "HCCPEMPLOYEE"}

def dog(title, kind, processes, **kw):
    return {"title": title, "summary": "", "kind": kind, "processes": processes, "users": [], "stuck_minutes": 0,
            "deadline": "", "investigate": True, "rule": "", "sql": "", "expect": "", "every_minutes": 0, "at": [],
            "notify": [], "paused": False, "notes": "", **kw}


llm = FakeLLM(
    response(tool("start_task", {"task_id": "create_user_profile"}),
             tool("set_fields", {"fields": [{"name": k, "value": v} for k, v in USER.items()]}, id="b"),
             tool("request_confirmation", {}, id="c")),
    response(text("Here is the summary. Enter the password and click **Confirm**.")),
    response(text("Done. User **VELU** was created and linked to employee **KU0042**.")),
    # after the recording test saved create_user_quick:
    response(tool("start_task", {"task_id": "create_user_quick"}),
             tool("set_fields", {"fields": [{"name": "user_id", "value": "anita"},
                                            {"name": "description", "value": "Anita Rao"},
                                            {"name": "roles", "value": "EMPLOYEE, Manager"}]}, id="b2"),
             tool("request_confirmation", {}, id="c2")),
    response(text("Please confirm.")),
    response(text("Done. User **ANITA** was created with the recorded task.")),
    # after the process test saved sync_portal_security:
    response(tool("start_task", {"task_id": "sync_portal_security"}), tool("request_confirmation", {}, id="c3")),
    response(text("Please confirm.")),
    response(text("Done. The portal security sync was submitted.")),
    # live mode: no task fits, Claude works it out on the page
    response(tool("work_live", {"goal": "Create user LIVE1", "details": "user LIVE1, symbolic ID SYSADM1"}, id="w1")),
    response(text("On it: I'll work it out on screen.")),
    *[response(tool(n, a, id=f"l{i}")) for i, (n, a) in enumerate([
        ("act", {"note": "Opening User Profiles", "actions": [
            {"kind": "open_page", "target": "MAINTAIN_SECURITY.USERMAINT.GBL", "value": ""}]}),
        ("act", {"note": "Switching to Add", "actions": [
            {"kind": "click", "target": "PTS_CFG_CL_WRK_PTS_PAGE_TOGGLE", "value": ""}]}),
        ("act", {"note": "Adding LIVE1", "actions": [
            {"kind": "set", "target": "PSOPRDEFN_SRCH_OPRID", "value": "LIVE1"},
            {"kind": "click", "target": "PTS_CFG_CL_WRK_PTS_ADD_BTN", "value": ""}]}),
        ("act", {"note": "Symbolic ID", "actions": [{"kind": "set", "target": "PSOPRDEFN_SYMBOLICID", "value": "SYSADM1"}]}),
        ("type_secret", {"target": "PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT", "label": "New Password"}),
        ("type_secret", {"target": "PSUSRPRFL_WRK_PTPSWDCLEARTXTCONF", "label": "Confirm Password"}),
        ("ask_user", {"question": "What description should LIVE1 have?"}),
        ("act", {"note": "ID tab", "actions": [{"kind": "tab", "target": "ID", "value": ""}]}),
        ("act", {"note": "Description", "actions": [
            {"kind": "set", "target": "PSOPRDEFN_DVW_OPRDEFNDESC$0", "value": "Live One"}]}),
        ("act", {"note": "Saving", "actions": [{"kind": "click", "target": "#ICSave", "value": ""}]}),   # refused
        ("request_commit", {"target": "#ICSave", "summary": [{"label": "User ID", "value": "LIVE1"},
                                                             {"label": "Description", "value": "Live One"}]}),
        ("finish", {"success": True, "summary": "Created user LIVE1 (Live One) and PeopleSoft saved it."}),
    ])],
    response(text("Done: user **LIVE1** was created, worked out live.")),
    # recovery: the recorded task meets a changed page and is finished live
    response(tool("start_task", {"task_id": "create_user_profile"}, id="r1"),
             tool("set_fields", {"fields": [{"name": "user_id", "value": "RECOV"}, {"name": "description", "value": "Recov User"},
                                            {"name": "roles", "value": "EMPLOYEE"}]}, id="r2"),
             tool("request_confirmation", {}, id="r3")),
    response(text("Please confirm.")),
    *[response(tool(n, a, id=f"rv{i}")) for i, (n, a) in enumerate([
        ("act", {"note": "Description moved: Long Description", "actions": [
            {"kind": "set", "target": "PSOPRDEFN_LONGDESCR", "value": "Recov User"}]}),
        ("act", {"note": "Roles tab", "actions": [{"kind": "tab", "target": "Roles", "value": ""}]}),
        ("act", {"note": "Role EMPLOYEE", "actions": [{"kind": "set", "target": "PSROLEUSER_VW_ROLENAME$0", "value": "EMPLOYEE"}]}),
        ("request_commit", {"target": "#ICSave", "summary": [{"label": "User ID", "value": "RECOV"},
                                                             {"label": "Description", "value": "Recov User (field moved)"}]}),
        ("finish", {"success": True, "summary": "Created RECOV; Description is now Long Description on the ID tab."}),
    ])],
    response(text("Done: **RECOV** created. The Description field had moved, so I finished it live.")),
    # documents: an access request form with two people; the second has no description and a role in words
    response(tool("prepare_batch", {"task_id": "create_user_profile", "items": [
        {"label": "DOCA1 - Anita Desai", "fields": [{"name": "user_id", "value": "DOCA1"}, {"name": "description", "value": "Anita Desai"},
                                                    {"name": "roles", "value": "EMPLOYEE"}], "unsure": []},
        {"label": "DOCB2 - Mark Chen", "fields": [{"name": "user_id", "value": "DOCB2"}, {"name": "roles", "value": "Manager"}],
         "unsure": [{"name": "roles", "note": "form says 'needs manager access'"}]},
    ]}, id="d1"), tool("request_confirmation", {}, id="d2")),
    response(text("I found **2 people** on the form. Please check the table; one description is missing.")),
    response(text("Both users were created from the form: **DOCA1** and **DOCB2**.")),
    # troubleshooting: the user's last failed process (557060, AEMINITEST divides by zero)
    response(tool("troubleshoot_process", {"instance": "", "question": "why did my last process fail?"}, id="t1")),
    response(text("I'm looking into process **557060** now.")),
    response(tool("get_process", {"instance": "557060"}, id="t2")),
    response(tool("get_peoplecode", {"program": "AEMINITEST", "section": "MAIN", "step": "Step02"}, id="t3")),
    response(tool("finish", {
        "category": "code", "confidence": "high",
        "summary": "AEMINITEST stops because line 5 of Step02 always divides by zero.",
        "where": "AEMINITEST.MAIN.Step02 (PeopleCode) - statement 3, line 5",
        "root_cause": "`&test = 1 / 0;` divides by zero on every run, before the validation logic.",
        "evidence": ["Message log: Decimal division by zero occurred. (180,109) ... Step02.OnExecute PCPC:108 Statement:3",
                     "PeopleCode line 5 [stmt 3]: &test = 1 / 0;", "Earlier runs 557051-557053 of the same program succeeded"],
        "fix_steps": ["Open AEMINITEST in App Designer, Step02 PeopleCode.", "Delete line 5 (&test = 1 / 0;) and save.",
                      "Rerun the process."],
        "code_fix": "Local number &nTest = 0;\nLocal string &sName = \"PSIQ\";\n\n&nTest = &nTest + 1;",
        "rerun": "Run AEMINITEST again from Request AE (or ask me to run it)."}, id="t4")),
    # security access check: ABSMITH cannot open Create Job Opening; Apply adds the role live
    response(tool("check_user_access", {"user": "ABSMITH", "page": "Create Job Opening",
                                        "question": "Why can't ABSMITH access Create Job Opening?"}, id="s1")),
    response(text("I'm checking **ABSMITH**'s security for Create Job Opening now.")),
    response(tool("find_page", {"text": "Create Job Opening"}, id="s2")),
    response(tool("check_access", {"user": "ABSMITH", "component": "HRS_HRPM.HRS_JO_LAUNCH.GBL"}, id="s3")),
    response(tool("finish", ACCESS_FINISH, id="s4")),
    response(tool("act", {"note": "Opening User Profiles", "actions": [
        {"kind": "open_page", "target": "MAINTAIN_SECURITY.USERMAINT.GBL", "value": ""}]}, id="s5")),
    response(tool("finish", {"success": True, "summary": "Added role Hiring Manager to ABSMITH and PeopleSoft saved it."}, id="s6")),
    response(text("Done: **ABSMITH** now has the role **Hiring Manager**. Ask them to sign out and in again.")),
    # absence approvals: the pending list, then Review with AI on Edmund Donahue's vacation
    response(tool("list_absence_approvals", {"everyone": False}, id="a1")),
    response(text("**1** absence request is waiting.")),
    response(tool("get_absence_request", {"thread": "1589"}, id="a2")),
    response(tool("finish", REVIEW_FINISH, id="a3")),
    # runbook drafted from the attached 5-step checklist (agent-service/runbooks/templates/test_user_setup_checklist.xlsx)
    response(tool("draft_runbook", {
        "title": "Set up a test user", "summary": "Creates a test user with the Employee role, checks it and gets sign-off.",
        "inputs": [{"name": "user_id", "label": "User ID", "default": "PSA_TEST01", "hint": "", "upper": True}],
        "steps": [
            step("check", "Make sure the new user ID is not taken", rule="No user profile exists with this user ID",
                 sql=USER_FREE, expect="no_rows"),
            step("task", "Create the user profile and add the Employee role", task_id="create_user_profile",
                 values=[{"name": "user_id", "value": "{{user_id}}"}, {"name": "roles", "value": "EMPLOYEE"}]),  # the mock page's role name
            step("manual", "Run the portal security sync", instructions="No task for it yet: record Sync Portal Security"),
            step("check", "Check the user has the role", rule="The user has the role Employee", sql=HAS_ROLE,
                 expect="no_rows"),
            step("approval", "Security manager signs off the new user", owner="Security Manager",
                 instructions="Approve the new user"),
        ],
        "questions": ["Step 3: there is no Sync Portal Security task yet, so it is a manual step until you record it."]},
        id="r1")),
    response(text("I drafted **Set up a test user** from your checklist: 5 steps. Please check the note, then publish.")),
    # ...and once published, run it from its tile on the welcome screen
    response(tool("start_runbook", {"name": "Set up a test user"}, id="r2")),
    response(text("**Set up a test user** has 5 steps. Check the inputs and click Start.")),
    # a monitor set up in the chat, then what it found "overnight"
    response(tool("draft_monitor", {
        "title": "Stuck requests", "summary": "Requests waiting too long for a process server", "kind": "stuck",
        "processes": [], "users": [], "stuck_minutes": 30, "investigate": False, "rule": "", "sql": "", "expect": "",
        "every_minutes": 0, "at": [], "notify": ["ops@example.com"]}, id="m1")),
    response(text("This monitor would alert on **2** requests stuck right now. Save it to start.")),
    response(tool("show_incidents", {"hours": 16}, id="m2")),
    response(text("One alert overnight: **2 requests stuck**, analysed below.")),
    # the watch plan template uploaded from + Features > Watch jobs (agent-service/runbooks/templates/watch_plan_template.xlsx)
    response(tool("draft_watch_plan", {"title": "Nightly jobs", "summary": "Batch jobs the support team watches overnight.",
                                       "watchdogs": [
        dog("AE test program failures", "failures", ["AEMINITEST"], notify=["ops@example.com"]),
        dog("Cross-reference report failures", "failures", ["XRFWIN"], notify=["ops@example.com"]),
        dog("Cross-reference report stuck", "stuck", ["XRFWIN"], stuck_minutes=30, notify=["ops@example.com"]),
        dog("Portal security sync by 07:00", "deadline", ["PORTAL_CSS"], deadline="07:00", notify=["security@example.com"])],
        "questions": []}, id="w1")),
    response(text("**Nightly jobs** has 4 watchdogs. XRFWIN has **2** requests stuck right now. Confirm to schedule.")),
    # regression tests: the test plan uploaded from + Features > Regression tests
    response(tool("draft_test_suite", {"title": "User admin regression", "summary": "", "run_at": "", "after_patch": False, "notify": [],
                                       "cases": [
        {"title": "New user", "task_id": "create_user_profile", "expect": "saved", "message": "", "notes": "",
         "values": [{"name": "user_id", "value": "RT{{n}}"}, {"name": "description", "value": "Regression {{n}}"},
                    {"name": "roles", "value": "EMPLOYEE"}]},
        {"title": "Duplicate user is refused", "task_id": "create_user_profile", "expect": "error", "message": "already exists",
         "notes": "", "values": [{"name": "user_id", "value": "PS"}, {"name": "description", "value": "Duplicate"},
                                 {"name": "roles", "value": "EMPLOYEE"}]}],
        "questions": []}, id="t1")),
    response(text("**User admin regression**: 2 cases on Create user profile, one negative (a duplicate user ID).")),
    # ...the second run meets the upgraded page (Description moved): case 1 is finished live, Save auto-approved
    *[response(tool(n, a, id=f"tl{i}")) for i, (n, a) in enumerate([
        ("act", {"note": "Description moved: Long Description", "actions": [
            {"kind": "set", "target": "PSOPRDEFN_LONGDESCR", "value": "Regression 0002"}]}),
        ("act", {"note": "Roles tab", "actions": [{"kind": "tab", "target": "Roles", "value": ""}]}),
        ("act", {"note": "Role EMPLOYEE", "actions": [{"kind": "set", "target": "PSROLEUSER_VW_ROLENAME$0", "value": "EMPLOYEE"}]}),
        ("request_commit", {"target": "#ICSave", "summary": [{"label": "User ID", "value": "RT0002"}]}),
        ("finish", {"success": True, "summary": "Created RT0002; Description is now Long Description."}),
    ])],
    # ask the data: Claude picks AI_NEW_HIRES, the browser runs it, a breakdown by department
    response(tool("find_queries", {"question": "How many new hires this year by department?"}, id="aq1")),
    response(tool("run_query", {"name": "AI_NEW_HIRES", "prompts": [{"name": "BIND1", "value": "2026-01-01"},
                                                                    {"name": "End Date", "value": "10/31/2026"}]}, id="aq2")),
    response(text("Running **AI_NEW_HIRES** (New Hires) from 2026-01-01 to 2026-10-31.")),
    response(tool("summarize_result", {"group_by": ["Department"], "op": "count", "measure": ""}, id="aq3")),
    response(text("**7** new hires this year: **4** in 10200, 2 in 10300 and 1 in 10400 (from AI_NEW_HIRES).")),
    drafts=[DRAFT, DRAFT_PROCESS],
)
class TraceDB(SecDB):
    """Also posts a 232 MB trace file (like the real PORTAL_CSS run): the widget must read only its end.
    And answers the absence reviewer's queries (request 1589)."""

    def query(self, sql, params=None, **kw):
        why = kw.get("why", "")
        if why.startswith("ci "):                          # Load with a CI: the catalog and the checks
            return CI_DB.query(sql, params, kw.get("max_rows", 200), kw.get("who", ""), why)
        if why.startswith("query "):                       # Ask the data: the AI_ catalog and prompt checks
            return Q_DB.query(sql, params, kw.get("max_rows", 200), kw.get("who", ""), why)
        if why.startswith("runbook draft check"):          # the draft's check queries, test-run
            check_select(sql)
            return (["OPRID"], [], False) if "psoprdefn" in sql.lower() else (["PROBLEM"], [["missing role"]], False)
        if why.startswith("runbook check"):                # running it: the user is free, then has the role
            check_select(sql)
            return ["OPRID"], [], False
        if why == "monitor draft process":
            return ["PRCSTYPE"], [["Application Engine"]], False
        if why == "monitor draft preview (deadline)":
            return ["N"], [[3]], False
        if why.startswith("monitor"):                      # monitor drafts and the watcher: two requests stuck
            check_select(sql)
            return (["PRCSINSTANCE", "PRCSTYPE", "PRCSNAME", "OPRID", "RUNCNTLID", "RUNSTATUS", "RUNDTTM", "SERVER"],
                    [[557022, "Application Engine", "PSXPARCHATTR", "PS", " ", "5", "2026-07-23 20:56:28", " "],
                     [557040, "Application Engine", "PSXPARCHATTR", "PS", " ", "5", "2026-10-23 01:57:29", " "]], False)
        if why == "process server status":
            return ["SERVERNAME", "SERVERSTATUS", "LASTUPDDTTM"], [["PRCS8622", "3", "2026-10-30 22:05:08"]], False
        if why.startswith("meaning of "):
            return ["FIELDVALUE", "XLATLONGNAME"], XLAT[params["f"]], False
        if why in APPROVAL_ROWS and why not in ("security admin check", "recent failed requests"):
            check_select(sql)
            cols, rows = APPROVAL_ROWS[why]
            return cols, rows(params or {}), False
        cols, rows, cut = super().query(sql, params, **kw)
        if "FROM PS_CDM_LIST" in sql.upper():
            rows = rows + [["http://ps:8000/psreports/ps", 85515, "AE_AEMINITEST_557060.trc", TRACE_SIZE]]
        return cols, rows, cut


TRACE_SIZE = 232 * 1048576


def trace(request):
    """Honors Range: bytes=-N like the PeopleSoft web server; never sends the whole 'file'."""
    rng = request.headers.get("range", "")
    if not rng.startswith("bytes=-"):
        return Response("refusing to send 232 MB", status_code=500)
    tail = ("-- 16.41.02 ... SQL trace lines ...\n" * 4000)[-(int(rng[7:]) - 60):] + "-- ABENDED at Step Step02 (trace end)\n"
    return Response(tail, status_code=206, media_type="text/plain",
                    headers={"Content-Range": f"bytes {TRACE_SIZE - len(tail)}-{TRACE_SIZE - 1}/{TRACE_SIZE}"})


DB = TraceDB()
AUDIT_DIR = Path(tempfile.mkdtemp(prefix="psagent-audit-"))
AUDIT = audit.Audit(AUDIT_DIR, days=90)
CI_DB = CIDB()
CI_STORE = ci.Store(tempfile.mkdtemp(prefix="psagent-ci-"))
CI_SAVED = {}          # what the mock CI saved: user -> {values, roles, emails}
Q_DB = PTDB()
QUERY_HITS = []        # what the browser asked the mock Query Viewer for


def seed_audit(a, days=30, now=None):
    """A month of plausible use: a few people chatting and running tasks, the watcher's alerts, a weekly test run."""
    rnd, now = random.Random(7), now or datetime.now()
    people = {"VELU": ("tasks", 6), "ABSMITH": ("troubleshoot", 3), "HCRUSA_KU0007": ("approvals", 2), "PS": ("security", 1)}
    for d in range(days, 0, -1):
        day = (now - timedelta(days=d)).replace(hour=8, minute=0, second=0)
        if day.weekday() >= 5 and rnd.random() < 0.7:
            continue
        for who, (feature, busy) in people.items():
            for k in range(rnd.randint(0, busy)):
                t = day + timedelta(minutes=rnd.randint(0, 540))
                sess = f"{who.lower()}-{d}-{k}"
                a.add("chat", "Sent a message", ts=t, oprid=who, feature="chat", session=sess, ms=rnd.randint(2000, 9000))
                for c in range(rnd.randint(1, 3)):
                    tin, tout, tcr = rnd.randint(1500, 6000), rnd.randint(150, 900), rnd.randint(8000, 40000)
                    a.add("claude", "claude-opus-5-5", ts=t, oprid=who, feature=feature if c else "chat", session=sess,
                          model="claude-opus-5-5", tin=tin, tout=tout, tcr=tcr, ms=rnd.randint(1500, 6000),
                          cost=a.cost("claude-opus-5-5", {"input_tokens": tin, "output_tokens": tout, "cache_read_input_tokens": tcr}))
                if feature == "tasks":
                    failed = rnd.random() < 0.12
                    a.add("task.confirmed", "Create User Profile", ts=t, oprid=who, feature="tasks", ref=f"run-{d}{k}",
                          data={"task": "create_user_profile", "values": [{"label": "User ID", "value": f"NEW{d:02d}{k}"},
                                                                         {"label": "Roles", "value": "EMPLOYEE"}]})
                    a.add("task.run", "Create User Profile: " + ("Save. PeopleSoft said: User ID already exists" if failed else "done"),
                          "failed" if failed else "ok", f"run-{d}{k}", ts=t, oprid=who, feature="tasks")
                elif feature == "troubleshoot":
                    inst = 557000 + d * 3 + k
                    a.add("query", f"process {inst}", ts=t, oprid=who, feature="troubleshoot", n=1, ms=40,
                          data={"sql": f"SELECT PRCSINSTANCE, PRCSNAME, RUNSTATUS FROM PSPRCSRQST WHERE PRCSINSTANCE = {inst}"})
                    a.add("troubleshoot", f"Process {inst}: Divide by zero at Step02 statement 3", ts=t, oprid=who,
                          feature="troubleshoot", ref=str(inst))
                elif feature == "approvals":
                    a.add("absence.review", f"Request {1500 + d}: approve", ts=t, oprid=who, feature="approvals", ref=str(1500 + d))
                else:
                    a.add("access.check", "ABSMITH / Create Job Opening: missing role Recruiter", ts=t, oprid=who, feature="security")
        if d % 4 == 0:
            a.add("incident", "AEMINITEST failed: Divide by zero at Step02 statement 3", "alert", f"inc-{d}",
                  ts=day.replace(hour=3), oprid="PSAGENT_WATCH", feature="watcher")
            a.add("claude", "claude-opus-5-5", ts=day.replace(hour=3), oprid="PSAGENT_WATCH", feature="watcher",
                  model="claude-opus-5-5", tin=24000, tout=2200, tcr=60000, ms=43000, cost=0.152)
        if d % 7 == 2:
            a.add("regression.run", "Smoke test: 2 of 2 passed (unattended)", ts=day.replace(hour=6), oprid="TESTER", feature="regression")
    a.add("claude", "API error 529: overloaded", "error", ts=now - timedelta(days=2, hours=3), oprid="ABSMITH",
          feature="troubleshoot", model="claude-opus-5-5", ms=300)


seed_audit(AUDIT)
_query = DB.query


def _audited_query(sql, params=None, **kw):
    t0 = time.monotonic()
    out = _query(sql, params, **kw)
    audit.query(kw.get("who", ""), kw.get("why", ""), sql, len(out[1]) if out else 0, time.monotonic() - t0)
    return out


DB.query = _audited_query
_create = llm.create


def _audited_create(*a, **kw):
    """The scripted answers cost like a typical real call: about 3,000 tokens in, 300 out, 20,000 from the cache."""
    out = _create(*a, **kw)
    audit.claude("claude-opus-5-5", SimpleNamespace(input_tokens=3000, output_tokens=300, cache_creation_input_tokens=0,
                                                    cache_read_input_tokens=20000), 1.2)
    return out


llm.create = _audited_create
REG_STORE = regress.Store(REGRESSION_DIR)
WATCHER = Watcher(DB, None, str(MONITORS_DIR), summary_at=None)   # no investigations: a stuck monitor needs none
WATCHER.start = lambda *a, **k: None                              # the test runs its rounds via /__test/watch_tick
AGENT = Agent(llm, TASKS, DB)   # TESTER is PSAGENT_ADMIN in development (teachers "*" below)
REGRESSION = Regression(REG_STORE, regress.Runner(REG_STORE, lambda: AGENT.tasks, AGENT.operator))   # unattended: set in main
app = create_app(AGENT, secret="", allowed_origins=[], dev_oprid="TESTER", teachers={"*"}, tasks_dir=str(TASKS_DIR),
                 runbooks_dir=str(RUNBOOKS_DIR), watcher=WATCHER, monitor_users={"*"}, regression=REGRESSION,
                 audit_log=AUDIT, auditors={"*"}, audit_jobs=audit.Jobs(AUDIT, budget=25, weekly=""), ci_store=CI_STORE,
                 query_catalog=queries.Catalog(DB), widget_file=ROOT / "psagent.js")
bodies = []


class Recorder:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.inner(scope, receive, send)
        chunks = []
        while True:
            msg = await receive()
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body"):
                break
        bodies.append(b"".join(chunks).decode())
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        return await self.inner(scope, replay, send)


HOST = """<!doctype html><html><head><meta charset="utf-8"><title>Homepage</title></head>
<body><h1>Mock Fluid homepage</h1>
<script>window.PSAGENT_CONFIG = {agentUrl: '', token: 'dev', oprid: 'TESTER', fullname: 'Test User'};</script>
<script src="/psagent.js"></script></body></html>"""


PORTAL = """<!doctype html><html><head><meta charset="utf-8"><title>User Profiles</title>
<style>html,body{margin:0;height:100%}#PT_HEADER{height:40px;background:#333;color:#fff;padding:8px}
#ptifrmtgtframe{width:100%;height:calc(100% - 60px);border:0}</style></head>
<body><div id="PT_HEADER"><a id="PT_NAVBAR" href="#" onclick="return false">NavBar</a></div>
<iframe id="ptifrmtgtframe" name="TargetContent" src="/psc/ps/EMPLOYEE/HRMS/c/{name}?{query}"></iframe>
<script>window.PSAGENT_CONFIG = {agentUrl: '', token: 'dev', oprid: 'TESTER', fullname: 'Test User'};</script>
<script src="/psagent.js"></script></body></html>"""


LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Oracle PeopleSoft Sign-in</title></head><body>
<form id="login" method="post" action="?&cmd=login&languageCd=ENG"><input type="hidden" name="lcsrf_token" value="t0k">
<input type="text" id="userid" name="userid"><input type="password" id="pwd" name="pwd"><input name="Submit" type="submit" value="Sign In">
</form></body></html>"""


async def login(request):
    """Like PeopleSoft's sign-in: TESTER / Te5ter! gets PS_TOKEN and the homepage, anything else the sign-in page again."""
    if request.method == "POST":
        form = urllib.parse.parse_qs((await request.body()).decode())
        if form.get("userid") == ["TESTER"] and form.get("pwd") == ["Te5ter!"] and form.get("lcsrf_token") == ["t0k"]:
            r = RedirectResponse("/psp/ps/EMPLOYEE/HRMS/c/NUI_FRAMEWORK.PT_LANDINGPAGE.GBL", status_code=302)
            r.set_cookie("PS_TOKEN", "ok")
            return r
    return HTMLResponse(LOGIN)


def _soap(inner):
    return ('<?xml version="1.0"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
            f"<SOAP-ENV:Body>{inner}</SOAP-ENV:Body></SOAP-ENV:Envelope>")


def _fault(key, text, mset="91", mnum="37", explain=""):
    return _soap("<SOAP-ENV:Fault><faultcode>200</faultcode><faultstring>Component Interface API</faultstring><runcode>0</runcode>"
                 f"<detail><keyinformation><UserID>{key}</UserID></keyinformation><messages><type>Error</type><messagesetnumber>{mset}"
                 f"</messagesetnumber><messagenumber>{mnum}</messagenumber><messagetext>{text}</messagetext><explaintext>{explain}"
                 "</explaintext></messages></detail></SOAP-ENV:Fault>")


async def soaptoci(request):
    """Like WEBLIB_SOAPTOCI for USER_PROFILE: GetCIShape, Create and Updatedata, one transaction per request."""
    import xml.etree.ElementTree as ET
    raw = (await request.body()).decode()
    if not raw or request.query_params.get("postDataBin") != "y":    # like PIA: without postDataBin=y the body is dropped
        return Response("No content provided.")
    body = next(e for e in ET.fromstring(raw).iter() if e.tag.endswith("Body"))
    tran = list(body)[0]
    action, _, name = tran.tag.split("__")
    if name != "USER_PROFILE":
        return Response(_fault("", f"Cannot find Component Interface {name}", "91", "2"), media_type="text/xml")
    if action == "GetCIShape":
        return Response(SHAPE_XML, media_type="text/xml")
    vals = {c.tag: (c.text or "") for c in tran if len(c) == 0}
    uid = vals.get("UserID", "")
    roles = [(c.get("CINodeAction") or "", c.findtext("RoleName") or "") for c in tran if c.tag == "Roles"]
    emails = [c.findtext("EmailAddress") or "" for c in tran if c.tag == "EmailAddresses"]
    for i, (_, role) in enumerate(roles):
        if role not in ("EMPLOYEE", "PeopleSoft User", "Manager"):
            return Response(_fault(uid, "Invalid value -- press the prompt button or hyperlink for a list of valid values (15,11) "
                                        f"{{USER_PROFILE.Roles({i + 1}).RoleName}}", "15", "11",
                                   "The value entered in the field does not match one of the allowable values."), media_type="text/xml")
    if action == "Create":
        if uid in CI_DB.users:
            return Response(_fault(uid, "Data being added conflicts with existing data. (18,1)", "18", "1"), media_type="text/xml")
        CI_DB.users.add(uid)
        CI_SAVED[uid] = {"values": vals, "roles": [r for _, r in roles], "emails": emails}
    elif action == "Updatedata":
        if uid not in CI_DB.users:
            return Response(_fault(uid, "Failed to get the CI", "91", "59"), media_type="text/xml")
        u = CI_SAVED.setdefault(uid, {"values": {}, "roles": [], "emails": []})
        u["values"].update(vals)
        u["roles"] += [r for a, r in roles if a == "INSERT"]
    else:
        return Response(_fault(uid, f"Unknown Method {action}", "91", "53"), media_type="text/xml")
    return Response(_soap(f"<{action}__CompIntfc__USER_PROFILE><notification>1</notification><detail><keyinformation><UserID>{uid}"
                          f"</UserID></keyinformation></detail></{action}__CompIntfc__USER_PROFILE>"), media_type="text/xml")


async def ci_fill(request):
    """Fill a downloaded CI template like a person would: rows typed under the headers, per sheet ({sheet: [{column: value}]})."""
    import base64
    from psagent import runbooks, xlsx
    data = json.loads((await request.body()).decode())
    book = []
    for name, rows in runbooks.xlsx_sheets(base64.b64decode(data["file"])):
        cells = [c for _, c in rows]
        head = next((i for i, c in enumerate(cells) if c and c[0] in ("Action", "Row action")), None)
        if head is not None:
            for r in data["rows"].get(name, []):
                cells.append([next((v for k, v in r.items() if h == k or h.endswith(f"({k})")), "") for h in cells[head]])
        book.append({"name": name, "rows": cells, "header": head})
    return Response(xlsx.write(book), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def query_viewer(request):
    """Like Query Viewer's Run links for AI_NEW_HIRES: the Excel file filtered by hire date (dates read only as
    YYYY-MM-DD: anything else gives no rows, like PeopleSoft), its prompt page when a prompt is missing."""
    action, _, name = request.query_params.get("ICAction", "").partition("=")
    binds = {k: v for k, v in request.query_params.items() if k.upper().startswith("BIND")}
    QUERY_HITS.append({"action": action, "query": name, **binds})
    if name != "PUBLIC.AI_NEW_HIRES":
        return HTMLResponse(f"<html><body>Query [Open Query] [CQryStmt::Prepare] QDM could not load query definition [ {name} ]</body></html>")
    if not binds.get("BIND1") or not binds.get("BIND2"):
        return HTMLResponse("<html><body>AI_NEW_HIRES - New Hires * Start Date * End Date <input name='InputKeys_bind1'></body></html>")

    def serial(d):
        try:
            return (datetime.strptime(d, "%Y-%m-%d") - datetime(1899, 12, 30)).days
        except ValueError:
            return None
    lo, hi = serial(binds["BIND1"]), serial(binds["BIND2"])
    rows = [r for r in HIRES if lo is not None and hi is not None and lo <= float(r[2]) <= hi]
    if action == "ICQryNameURL":
        return HTMLResponse("<table>" + "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td></tr>" for r in rows) + "</table>")
    return Response(ps_answer("New Hires", HEADS, rows), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def portal(request):
    query = "&".join(filter(None, [request.url.query, "PortalActualURL=x&PortalRegistryName=EMPLOYEE"]))
    return HTMLResponse(PORTAL.replace("{name}", request.path_params["name"]).replace("{query}", query))


def component(request):
    name = request.path_params["name"]
    if name.startswith("NUI_FRAMEWORK."):
        return HTMLResponse(HOST)
    if name.startswith(("MAINTAIN_SECURITY.USERMAINT.", "ADMINISTER_WORKFORCE_(GBL).EMERGENCY_CONTACT.")):
        return FileResponse(HERE / "mock_usermaint.html")
    if name.startswith("PORTAL_ADMIN.PORTAL_SEC_SYNC."):
        return FileResponse(HERE / "mock_runctl.html")
    return Response("not found", status_code=404)


def action(request):
    values = {}
    task = TASKS["create_user_profile"]
    for k, v in json.loads(request.query_params.get("values", "{}")).items():
        values[k], err = task.fields[k].normalize(v)
        assert not err, err
    return JSONResponse({"run_id": "test", "title": task.title, "component": task.component,
                         "steps": task.render(values)})


app.router.routes.extend([
    Route("/psc/ps/EMPLOYEE/HRMS/c/{name}", component),
    Route("/psp/ps/EMPLOYEE/HRMS/c/{name}", portal),
    # the report repository, as PeopleSoft serves a posted log file
    Route("/psreports/ps/85515/AE_AEMINITEST_557060.stdout", lambda r: Response(
        "PSAESRV started service request at 16.40.55 2026-10-24\n\nDecimal division by zero occurred. (180,109) "
        "AEMINITEST.MAIN.GBL.default.1900-01-01.Step02.OnExecute  PCPC:108  Statement:3\n\nProcess 557060 ABENDED at Step "
        "AEMINITEST.MAIN.Step02 (PeopleCode) -- RC = 8 (108,524)\n", media_type="text/plain")),
    Route("/psreports/ps/85515/AE_AEMINITEST_557060.trc", trace),
    Route("/__test/action", action),
    Route("/__test/bodies", lambda r: JSONResponse(bodies)),
    Route("/__test/task_file", lambda r: Response((TASKS_DIR / (r.query_params["id"] + ".yaml")).read_text())),
    Route("/__test/watch_tick", lambda r: JSONResponse([i["id"] for i in WATCHER.tick()])),
    Route("/__test/norole", lambda r: HTMLResponse(HOST.replace("fullname: 'Test User'", "fullname: 'No Role', roles: []"))),
    Route("/psc/ps/EMPLOYEE/HRMS/s/WEBLIB_SOAPTOCI.SOAPTOCI.FieldFormula.IScript_SOAPToCI", soaptoci, methods=["POST"]),
    Route("/__test/ci_fill", ci_fill, methods=["POST"]),
    Route("/__test/ci_users", lambda r: JSONResponse(CI_SAVED)),
    Route("/psc/ps/EMPLOYEE/HRMS/q/", query_viewer),
    Route("/__test/query_hits", lambda r: JSONResponse(QUERY_HITS)),
    Route("/psp/ps/", login, methods=["GET", "POST"]),
])

if __name__ == "__main__":
    port = int(sys.argv[1])
    REGRESSION.unattended = regress_runner.Unattended(REGRESSION.runner, REG_STORE, regress_runner.Headless(
        f"http://127.0.0.1:{port}/psp/ps/EMPLOYEE/HRMS/c/NUI_FRAMEWORK.PT_LANDINGPAGE.GBL", "TESTER", "Te5ter!",
        {"password": "Unatt3nd!"}, ROOT / "psagent.js"))
    uvicorn.run(Recorder(app), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
