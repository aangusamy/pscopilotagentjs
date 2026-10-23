"""HTTP API used by the psagent.js widget.

POST /api/chat     {session_id?, message, page?}          -> reply
POST /api/confirm  {session_id, run_id, approved}         -> reply (+ action when approved)
POST /api/result   {session_id, run_id, status, message?, failed_step?} -> reply
GET  /api/me                                              -> {oprid, can_teach}
POST /api/recordings/draft {recording}                    -> {draft}       (teachers)
POST /api/recordings/save  {recording, draft, overwrite?} -> {task_id, ...} (teachers)
GET  /health

Every /api call needs  Authorization: Bearer <token from IScript_PSAgentConfig>.
"""

import logging
import os
import threading
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import auth, recorder
from .agent import Agent, SessionStore
from .tasks import TaskError, load_tasks, parse_task

log = logging.getLogger("psagent")

_STATUSES = {"success", "error", "stopped"}


def create_app(agent, secret, allowed_origins, dev_oprid=None, teachers=(), tasks_dir=None):
    """teachers: OPRIDs allowed to record tasks ("*" = everyone). tasks_dir: where recorded tasks are saved."""
    store = SessionStore()
    save_lock = threading.Lock()

    def can_teach(oprid):
        return "*" in teachers or oprid in teachers

    def oprid_of(request):
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer ") and secret:
            try:
                return auth.verify(header[7:].strip(), secret)
            except auth.AuthError as e:
                log.info("rejected token: %s", e)
                return None
        return dev_oprid  # only set when PSAGENT_DEV_OPRID is configured

    async def body(request):
        try:
            data = await request.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def bad(msg, code=400):
        return JSONResponse({"error": msg}, status_code=code)

    async def chat(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not str(data.get("message", "")).strip():
            return bad("message is required")
        s = store.get_or_create(data.get("session_id"), oprid)
        s.can_teach = can_teach(oprid)
        page = data.get("page") if isinstance(data.get("page"), dict) else None

        def work():
            with s.lock:
                return agent.chat(s, str(data["message"])[:4000], page)

        reply = await run_in_threadpool(work)
        return JSONResponse({"session_id": s.id, **reply.to_dict()})

    async def confirm(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("run_id"):
            return bad("run_id is required")
        s = store.get(data.get("session_id"), oprid)
        if s is None:
            return bad("unknown session", 404)

        def work():
            with s.lock:
                return agent.decide(s, str(data["run_id"]), data.get("approved") is True)

        reply = await run_in_threadpool(work)
        return JSONResponse({"session_id": s.id, **reply.to_dict()})

    async def result(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or data.get("status") not in _STATUSES or not data.get("run_id"):
            return bad("run_id and status (success|error|stopped) are required")
        s = store.get(data.get("session_id"), oprid)
        if s is None:
            return bad("unknown session", 404)

        def work():
            with s.lock:
                return agent.report(
                    s, str(data["run_id"]), data["status"],
                    str(data.get("message") or ""), str(data.get("failed_step") or "") or None,
                )

        reply = await run_in_threadpool(work)
        return JSONResponse({"session_id": s.id, **reply.to_dict()})

    async def me(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        return JSONResponse({"oprid": oprid, "can_teach": can_teach(oprid)})

    async def draft(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_teach(oprid):
            return bad("you are not allowed to record tasks", 403)
        data = await body(request)
        if data is None:
            return bad("recording is required")
        try:
            d = await run_in_threadpool(recorder.draft, data.get("recording"), agent.llm)
        except recorder.RecordingError as e:
            return bad(str(e))
        d["exists"] = d["task_id"] in agent.tasks
        return JSONResponse({"draft": d})

    async def save(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_teach(oprid):
            return bad("you are not allowed to record tasks", 403)
        if not tasks_dir:
            return bad("saving tasks is not configured", 503)
        data = await body(request)
        if data is None or not isinstance(data.get("draft"), dict):
            return bad("recording and draft are required")
        try:
            task_dict = recorder.build(data.get("recording"), data["draft"], oprid)
            task = parse_task(task_dict, source=task_dict["id"])
        except (recorder.RecordingError, TaskError) as e:
            return bad(str(e))
        path = Path(tasks_dir) / f"{task.id}.yaml"
        with save_lock:
            if task.id in agent.tasks and data.get("overwrite") is not True:
                return bad(f"a task named {task.id} already exists", 409)
            tmp = path.with_suffix(".yaml.tmp")
            tmp.write_text(recorder.to_yaml(task_dict))
            tmp.replace(path)
            agent.add_task(task)
        log.info("task %s recorded by %s -> %s", task.id, oprid, path)
        return JSONResponse({"task_id": task.id, "title": task.title, "examples": task.examples,
                             "fields": [f.label for f in task.fields.values()], "steps": len(task.playbook)})

    async def health(_request):
        return JSONResponse({"ok": True, "tasks": sorted(agent.tasks)})

    return Starlette(
        routes=[
            Route("/api/chat", chat, methods=["POST"]),
            Route("/api/confirm", confirm, methods=["POST"]),
            Route("/api/result", result, methods=["POST"]),
            Route("/api/me", me),
            Route("/api/recordings/draft", draft, methods=["POST"]),
            Route("/api/recordings/save", save, methods=["POST"]),
            Route("/health", health),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=allowed_origins,
                allow_methods=["POST", "GET"],
                allow_headers=["Authorization", "Content-Type"],
            )
        ],
    )


def load_dotenv(path):
    """Read KEY=VALUE lines into os.environ. Variables already set win."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value:
            os.environ.setdefault(key, value)


def app_from_env():
    """Entry point for uvicorn: `uvicorn --factory psagent.server:app_from_env`."""
    from .llm import ClaudeLLM  # noqa: PLC0415

    load_dotenv(os.environ.get("PSAGENT_ENV_FILE", Path(__file__).resolve().parent.parent / ".env"))
    logging.basicConfig(level=os.environ.get("PSAGENT_LOG_LEVEL", "INFO"))
    secret = os.environ.get("PSAGENT_SHARED_SECRET", "")
    dev_oprid = os.environ.get("PSAGENT_DEV_OPRID") or None
    if not secret and not dev_oprid:
        raise SystemExit("Set PSAGENT_SHARED_SECRET (same value as &AGENT_SECRET in the iScript).")
    if dev_oprid:
        log.warning("PSAGENT_DEV_OPRID=%s: requests without a valid token run as this user. Never use in production.", dev_oprid)
    tasks_dir = os.environ.get("PSAGENT_TASKS_DIR", str(Path(__file__).resolve().parent.parent / "tasks"))
    origins = [o.strip() for o in os.environ.get("PSAGENT_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    teachers = {t.strip() for t in os.environ.get("PSAGENT_TEACHERS", "").split(",") if t.strip()}
    llm = ClaudeLLM(
        model=os.environ.get("PSAGENT_MODEL", "claude-opus-5-5"),
        effort=os.environ.get("PSAGENT_EFFORT", "low"),
    )
    return create_app(Agent(llm, load_tasks(tasks_dir)), secret, origins, dev_oprid, teachers, tasks_dir)
