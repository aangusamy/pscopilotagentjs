"""Test server for the browser tests (run by run_browser_tests.mjs).

One origin serves everything, like PeopleSoft + a reverse-proxied agent:
  /psc/ps/EMPLOYEE/HRMS/c/NUI_FRAMEWORK.PT_LANDINGPAGE.GBL  host page with psagent.js
  /psc/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.USERMAINT.GBL   mock component
  /psp/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.USERMAINT.GBL   portal page: header + TargetContent frame
  /api/*                                                   real agent API, scripted LLM
  /__test/action, /__test/bodies, /__test/task_file        test hooks
Recorded tasks are saved to a temporary copy of agent-service/tasks.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "agent-service"), str(ROOT / "agent-service" / "tests")]

import uvicorn  # noqa: E402
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from fakes import FakeLLM, response, text, tool  # noqa: E402
from psagent.agent import Agent  # noqa: E402
from psagent.server import create_app  # noqa: E402
from psagent.tasks import load_tasks  # noqa: E402
from test_troubleshoot import FakeDB  # noqa: E402  (rows from the real instance 557060)

TASKS_DIR = Path(tempfile.mkdtemp(prefix="psagent-tasks-"))
shutil.copytree(ROOT / "agent-service" / "tests" / "fixtures" / "tasks", TASKS_DIR, dirs_exist_ok=True)
TASKS = load_tasks(TASKS_DIR)
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
    drafts=[DRAFT, DRAFT_PROCESS],
)
app = create_app(Agent(llm, TASKS, FakeDB()), secret="", allowed_origins=[], dev_oprid="TESTER",
                 teachers={"*"}, tasks_dir=str(TASKS_DIR))
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


def portal(request):
    query = "&".join(filter(None, [request.url.query, "PortalActualURL=x&PortalRegistryName=EMPLOYEE"]))
    return HTMLResponse(PORTAL.replace("{name}", request.path_params["name"]).replace("{query}", query))


def component(request):
    name = request.path_params["name"]
    if name.startswith("NUI_FRAMEWORK."):
        return HTMLResponse(HOST)
    if name.startswith("MAINTAIN_SECURITY.USERMAINT."):
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
    Route("/psagent.js", lambda r: FileResponse(ROOT / "psagent.js", media_type="text/javascript")),
    # the report repository, as PeopleSoft serves a posted log file
    Route("/psreports/ps/85515/AE_AEMINITEST_557060.stdout", lambda r: Response(
        "PSAESRV started service request at 16.40.55 2026-10-24\n\nDecimal division by zero occurred. (180,109) "
        "AEMINITEST.MAIN.GBL.default.1900-01-01.Step02.OnExecute  PCPC:108  Statement:3\n\nProcess 557060 ABENDED at Step "
        "AEMINITEST.MAIN.Step02 (PeopleCode) -- RC = 8 (108,524)\n", media_type="text/plain")),
    Route("/__test/action", action),
    Route("/__test/bodies", lambda r: JSONResponse(bodies)),
    Route("/__test/task_file", lambda r: Response((TASKS_DIR / (r.query_params["id"] + ".yaml")).read_text())),
])

if __name__ == "__main__":
    uvicorn.run(Recorder(app), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
