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
    drafts=[DRAFT, DRAFT_PROCESS],
)
app = create_app(Agent(llm, TASKS), secret="", allowed_origins=[], dev_oprid="TESTER",
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
    Route("/__test/action", action),
    Route("/__test/bodies", lambda r: JSONResponse(bodies)),
    Route("/__test/task_file", lambda r: Response((TASKS_DIR / (r.query_params["id"] + ".yaml")).read_text())),
])

if __name__ == "__main__":
    uvicorn.run(Recorder(app), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
