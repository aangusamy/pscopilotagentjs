"""HTTP API used by the psagent.js widget.

POST /api/chat     {session_id?, message, page?}          -> reply
POST /api/confirm  {session_id, run_id, approved}         -> reply (+ action when approved)
POST /api/result   {session_id, run_id, status, message?, failed_step?} -> reply
GET  /health

Every /api call needs  Authorization: Bearer <token from IScript_PSAgentConfig>.
"""

import logging
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import auth
from .agent import Agent, SessionStore
from .tasks import load_tasks

log = logging.getLogger("psagent")

_STATUSES = {"success", "error", "stopped"}


def create_app(agent, secret, allowed_origins, dev_oprid=None):
    store = SessionStore()

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

    async def health(_request):
        return JSONResponse({"ok": True, "tasks": sorted(agent.tasks)})

    return Starlette(
        routes=[
            Route("/api/chat", chat, methods=["POST"]),
            Route("/api/confirm", confirm, methods=["POST"]),
            Route("/api/result", result, methods=["POST"]),
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


def app_from_env():
    """Entry point for uvicorn: `uvicorn --factory psagent.server:app_from_env`."""
    from .llm import ClaudeLLM  # noqa: PLC0415

    logging.basicConfig(level=os.environ.get("PSAGENT_LOG_LEVEL", "INFO"))
    secret = os.environ.get("PSAGENT_SHARED_SECRET", "")
    dev_oprid = os.environ.get("PSAGENT_DEV_OPRID") or None
    if not secret and not dev_oprid:
        raise SystemExit("Set PSAGENT_SHARED_SECRET (same value as &AGENT_SECRET in the iScript).")
    if dev_oprid:
        log.warning("PSAGENT_DEV_OPRID=%s: requests without a valid token run as this user. Never use in production.", dev_oprid)
    tasks_dir = os.environ.get("PSAGENT_TASKS_DIR", str(Path(__file__).resolve().parent.parent / "tasks"))
    origins = [o.strip() for o in os.environ.get("PSAGENT_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    llm = ClaudeLLM(
        model=os.environ.get("PSAGENT_MODEL", "claude-opus-5-5"),
        effort=os.environ.get("PSAGENT_EFFORT", "low"),
    )
    return create_app(Agent(llm, load_tasks(tasks_dir)), secret, origins, dev_oprid)
