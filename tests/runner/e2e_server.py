"""Test server for the browser tests (run by run_browser_tests.mjs).

One origin serves everything, like PeopleSoft + a reverse-proxied agent:
  /psc/ps/EMPLOYEE/HRMS/c/NUI_FRAMEWORK.PT_LANDINGPAGE.GBL  host page with psagent.js
  /psc/ps/EMPLOYEE/HRMS/c/MAINTAIN_SECURITY.USERMAINT.GBL   mock component
  /api/*                                                   real agent API, scripted LLM
  /__test/action, /__test/bodies                           test hooks
"""

import json
import sys
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

TASKS = load_tasks(ROOT / "agent-service" / "tasks")
HERE = Path(__file__).resolve().parent

USER = {"user_id": "VELU", "description": "Velu Arumugam", "emplid": "KU0042",
        "roles": "EMPLOYEE, PeopleSoft User", "primary_permission_list": "HCCPEMPLOYEE"}

llm = FakeLLM(
    response(tool("start_task", {"task_id": "create_user_profile"}),
             tool("set_fields", {"fields": [{"name": k, "value": v} for k, v in USER.items()]}, id="b"),
             tool("request_confirmation", {}, id="c")),
    response(text("Here is the summary. Enter the password and click **Confirm**.")),
    response(text("Done. User **VELU** was created and linked to employee **KU0042**.")),
)
app = create_app(Agent(llm, TASKS), secret="", allowed_origins=[], dev_oprid="TESTER")
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


def component(request):
    name = request.path_params["name"]
    if name.startswith("NUI_FRAMEWORK."):
        return HTMLResponse(HOST)
    if name.startswith("MAINTAIN_SECURITY.USERMAINT."):
        return FileResponse(HERE / "mock_usermaint.html")
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
    Route("/psagent.js", lambda r: FileResponse(ROOT / "psagent.js", media_type="text/javascript")),
    Route("/__test/action", action),
    Route("/__test/bodies", lambda r: JSONResponse(bodies)),
])

if __name__ == "__main__":
    uvicorn.run(Recorder(app), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
