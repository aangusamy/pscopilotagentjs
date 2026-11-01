"""HTTP API used by the psagent.js widget.

POST /api/chat     {session_id?, message, page?, attachment?{name, media_type, data}} -> reply
POST /api/confirm  {session_id, run_id, approved, rows?}  -> reply (+ action / batch_action when approved)
POST /api/batch/result {session_id, batch_id, results}    -> reply
POST /api/troubleshoot/step {session_id, run_id, logs?}   -> {steps, done, diagnosis}  (processes, access checks
                                                             and absence reviews)
POST /api/approvals/review {session_id, thread}           -> {troubleshoot}  (review a pending absence request)
POST /api/security/apply {session_id, run_id}             -> {live}  (security admins: make the proposed fix live)
POST /api/result   {session_id, run_id, status, message?, failed_step?} -> reply
POST /api/live/step {session_id, run_id, snapshot, result?, answer?, approved?, declined?} -> {action}
POST /api/live/recover {session_id, run_id, failed_index, error}  -> {live}  (recorded run hit a changed page)
GET  /api/me                                              -> {oprid, roles, can_teach, can_monitor, tasks, troubleshoot,
                                                             security, approvals, runbooks, overnight}
POST /api/recordings/draft {recording}                    -> {draft}       (teachers)
POST /api/recordings/save  {recording, draft, overwrite?} -> {task_id, ...} (teachers)
POST /api/runbooks/save {session_id, draft_id, overwrite?} -> {runbook_id, path} (teachers: publish a drafted runbook)
POST /api/runbooks/step {session_id, run_id, inputs? | fill?+confirm | result | poll | approve | done | cancel}
                                                          -> {events, next, done, status, results}  (run a runbook)
POST /api/monitors/save {session_id, draft_id}            -> {monitor_id, path}  (monitor role: save a drafted watchdog)
POST /api/watchplans/save {session_id, draft_id}          -> {plan_id, path}  (monitor role: confirm a drafted watch plan)
GET  /api/watchdogs                                       -> {plans, counts, history}  (monitor role: the watchdog manager)
POST /api/watchdogs/action {action, id?|plan?}            -> {plans, counts}  (pause, resume, run, delete, pause_plan,
                                                             resume_plan, delete_plan, stop, start)
GET  /api/watchplans/export?plan=<id>                     -> {name, data}  (the plan as the Excel template, base64)
GET  /api/templates/watch_plan | runbook                  -> {name, data}  (the blank templates, base64)
GET  /api/incidents?hours=24 | ?id=<incident>             -> {incidents}  (monitor role: the watcher's alerts)
Excel to CI (any agent user; PeopleSoft's CI permissions decide what each may load; rows are saved by the browser
through WEBLIB_SOAPTOCI in the user's own session):
GET  /api/ci?q=                                           -> {cis, more}   (the CIs the user may load)
GET  /api/ci/shape?ci= | POST {ci, xml}                   -> {shape} | {request}  (the CI's shape: cached, else the GetCIShape
                                                             request for the browser to send; POST its answer)
POST /api/ci/template {ci, props?}                        -> {name, data}  (the template, base64)
POST /api/ci/check {ci?, name, data}                      -> {load}  (the uploaded template, every row checked)
POST /api/ci/start | pause | stop {load_id}               -> {load}
POST /api/ci/next {load_id}                               -> {row: {n, label, action, xml}} | {done, load}
POST /api/ci/result {load_id, n, response}                -> {result, load}  (SOAPTOCI's answer for one row)
GET  /api/ci/loads | /api/ci/load?load= | /api/ci/export?load=&what=results|fix
GET  /api/audit?tab=overview|usage|events|failures|ops&days=&by=&user=&feature=&kind=&outcome=&q=&since=&until=&before=
                                                          -> the admin dashboard's data (auditor role)
GET  /api/audit/export?tab=events|usage&...               -> {name, data}  (the same, as Excel)
Regression tests (PSAGENT_TEST_ENV=on; teachers, except that a run's own steps need only its owner):
GET  /api/regress                                         -> {suites, runs, smoke, unattended}
POST /api/regress/save {session_id, draft_id}             -> {suite_id}   (save a drafted test suite)
POST /api/regress/start {suite | smoke: true}             -> {run_id, cases, secrets}   (run it in this browser)
POST /api/regress/next {run_id}                           -> {case: {action}} | {done, report}
POST /api/regress/result {run_id, n, result}              -> {verdict} | {recover}   (the page changed: finish it live)
POST /api/regress/live {run_id, snapshot, result?, approved?} -> {action} | {verdict}
POST /api/regress/stop {run_id}                           -> {report}
POST /api/regress/unattended {suite | smoke: true}        -> {run_id}   (the headless runner on the service)
POST /api/regress/delete {suite}
GET  /api/regress/export?suite= | /api/regress/report?run=&xlsx=1  -> {name, data} | {report}
GET  /health

Every /api call needs  Authorization: Bearer <token from IScript_PSAgentConfig>. The token carries the user's
agent roles (PeopleSoft roles PSAGENT_USER, PSAGENT_MONITOR, PSAGENT_ADMIN, PSAGENT_AUDITOR); a token with no role is
refused. Tokens from the first iScript have no roles: they count as PSAGENT_USER (access.py). PSAGENT_TEACHERS /
PSAGENT_MONITORS / PSAGENT_AUDITORS count only in development (PSAGENT_DEV_OPRID).
Every call is recorded in the audit log (audit.py) under the user and the feature its path belongs to.
"""

import base64
import binascii
import logging
import os
import re
import threading
import time
from pathlib import Path

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import access, audit, auth, ci, monitors, queries, recorder, regress, runbooks
from .agent import Agent, SessionStore, check_attachment
from .tasks import TaskError, load_tasks, parse_task

log = logging.getLogger("psagent")

_STATUSES = {"success", "error", "stopped"}
# the audit log's feature for each API path (the longest matching prefix)
_PATH_FEATURE = [("/api/chat", "chat"), ("/api/me", "chat"), ("/api/confirm", "tasks"), ("/api/result", "tasks"),
                 ("/api/batch", "tasks"), ("/api/troubleshoot", "troubleshoot"), ("/api/security", "security"),
                 ("/api/approvals", "approvals"), ("/api/live", "live"), ("/api/recordings", "recorder"),
                 ("/api/runbooks", "runbooks"), ("/api/templates/runbook", "runbooks"), ("/api/monitors", "watcher"),
                 ("/api/watch", "watcher"), ("/api/incidents", "watcher"), ("/api/templates/watch_plan", "watcher"),
                 ("/api/regress", "regression"), ("/api/templates/regression", "regression"), ("/api/audit", "audit"),
                 ("/api/ci", "ci"), ("/api/quer", "data")]


def feature_of(path):
    return max(((p, f) for p, f in _PATH_FEATURE if path.startswith(p)), default=("", "chat"), key=lambda x: len(x[0]))[1]


def create_app(agent, secret, allowed_origins, dev_oprid=None, teachers=(), tasks_dir=None, runbooks_dir=None,
               watcher=None, monitor_users=(), dev_roles=None, regression=None, audit_log=None, auditors=(), audit_jobs=None,
               ci_store=None, query_catalog=None):
    """Who may do what comes from the agent roles in the signed token (access.py). teachers / monitor_users / auditors:
    OPRIDs given ADMIN / MONITOR / AUDITOR rights besides their roles, in development only (dev_oprid set; "*" =
    everyone): in production only the roles count. tasks_dir / runbooks_dir: where
    recorded tasks and published runbooks are saved. watcher: runs the monitors (started with the app). dev_roles:
    roles of dev_oprid (None = none given). audit_log: an audit.Audit (None = nothing recorded); audit_jobs: its
    housekeeping (purge, budget alerts, weekly email), started with the app. ci_store: Excel to CI loads (None = off).
    query_catalog: Ask the data, a queries.Catalog of the users' AI_ queries (None = off)."""
    store = SessionStore()
    audit.install(audit_log)
    signins = {}                  # oprid -> when the last sign-in was recorded (one per half hour)
    agent.watcher = watcher
    agent.regress = regression
    agent.queries = query_catalog
    save_lock = threading.Lock()
    if runbooks_dir:
        agent.runbooks_dir = runbooks_dir
        for rb in runbooks.load_runbooks(runbooks_dir).values():
            agent.add_runbook(rb)
        log.info("Runbooks (PSAGENT_RUNBOOKS_DIR=%s): %s", runbooks_dir, sorted(agent.runbooks) or "none published yet")

    def roles(request):
        return getattr(request.state, "roles", None)

    def caps_of(oprid, request):
        """What the user may do (access.py), worked out on every request from the roles in the signed token. A token
        from an iScript without roles counts as PSAGENT_USER. In development the lists add rights."""
        r = roles(request)
        c = set(access.caps(r if r is not None else {"USER"}))
        if dev_oprid:
            if "*" in teachers or oprid in teachers:
                c |= access.caps({"ADMIN"})
            if "*" in monitor_users or oprid in monitor_users:
                c |= access.caps({"MONITOR"})
            if "*" in auditors or oprid in auditors:
                c |= access.caps({"AUDITOR"})
        return c

    def can_work(oprid, request):
        return access.WORK in caps_of(oprid, request)

    def can_teach(oprid, request):
        return access.ADMIN in caps_of(oprid, request)

    def can_monitor(oprid, request):
        return access.MONITOR in caps_of(oprid, request)

    def can_audit(oprid, request):
        return access.AUDIT in caps_of(oprid, request)

    def session_of(request, oprid, data, create=False):
        """The user's conversation (None: unknown or someone else's), with what they may do set again from this
        request's token, so a role taken away in PeopleSoft stops working at once."""
        sid = (data or {}).get("session_id")
        s = store.get_or_create(sid, oprid) if create else store.get(sid, oprid)
        if s is not None:
            c = caps_of(oprid, request)
            s.can_work, s.can_monitor, s.can_teach = access.WORK in c, access.MONITOR in c, access.ADMIN in c
            s.can_audit = audit_log is not None and access.AUDIT in c
        return s

    def oprid_of(request):
        """The signed-in user, or None. A token without any agent role is refused: the widget is not for them."""
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer ") and secret:
            try:
                oprid, r = auth.identify(header[7:].strip(), secret)
            except auth.AuthError as e:
                log.info("rejected token: %s", e)
                if str(e) != "token expired":         # an expired token is normal: the widget fetches a new one
                    audit.record("access.denied", f"Rejected token: {e}", "denied", oprid="(unknown)",
                                 feature=feature_of(request.url.path))
                return None
            if r is not None and not r:
                log.info("rejected token of %s: no PSAGENT role", oprid)
                audit.record("access.denied", "Token without any PSAGENT role", "denied", oprid=oprid,
                             feature=feature_of(request.url.path))
                return None
            request.state.roles = r
            audit.enter(oprid, feature_of(request.url.path))
            return oprid
        request.state.roles = dev_roles
        if dev_oprid:  # only set when PSAGENT_DEV_OPRID is configured
            audit.enter(dev_oprid, feature_of(request.url.path))
        return dev_oprid

    async def body(request):
        try:
            data = await request.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def bad(msg, code=400):
        if code == 403:                               # a user without the role or right: kept as a refused attempt
            audit.record("access.denied", msg, "denied")
        return JSONResponse({"error": msg}, status_code=code)

    async def chat(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        attachment = None
        if data is not None and data.get("attachment") is not None:
            attachment, err = check_attachment(data["attachment"])
            if err:
                return bad(err)
        if data is None or not (str(data.get("message", "")).strip() or attachment):
            return bad("message is required")
        s = session_of(request, oprid, data, create=True)
        page = data.get("page") if isinstance(data.get("page"), dict) else None
        text = str(data.get("message") or "").strip()[:4000] or "Here is a document."

        def work():
            with s.lock:
                return agent.chat(s, text, page, attachment)

        if attachment and ci_store is not None and agent.db is not None and s.can_work:
            m = re.match(r"## Sheet: [^\n]*\n\d+: Component Interface (\w+)", attachment.get("text") or "")   # a Load with a CI template
            if m:
                name = m.group(1).upper()
                out = await run_in_threadpool(ci_check, oprid, name, attachment["name"], base64.b64decode(attachment["data"]))
                audit.record("chat", f"Attached {attachment['name']} (a {name} template)")
                if "error" in out:
                    return JSONResponse({"session_id": s.id, **agent_reply([f"⚠️ {out['error']}"], {"ci": name})})
                return JSONResponse({"session_id": s.id, **agent_reply([], {"ci": name, "load": out["load"]})})

        t0 = time.monotonic()
        reply = await run_in_threadpool(work)
        # what was asked is not kept, only that a message was sent (and a document's name)
        audit.record("chat", f"Attached {attachment['name']}" if attachment else "Sent a message", ms=int((time.monotonic() - t0) * 1000))
        return JSONResponse({"session_id": s.id, **reply.to_dict()})

    async def confirm(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("run_id"):
            return bad("run_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)

        rows = data.get("rows") if isinstance(data.get("rows"), list) else None

        def work():
            with s.lock:
                return agent.decide(s, str(data["run_id"]), data.get("approved") is True, rows)

        reply = await run_in_threadpool(work)
        return JSONResponse({"session_id": s.id, **reply.to_dict()})

    async def result(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or data.get("status") not in _STATUSES or not data.get("run_id"):
            return bad("run_id and status (success|error|stopped) are required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)

        def work():
            with s.lock:
                return agent.report(
                    s, str(data["run_id"]), data["status"],
                    str(data.get("message") or ""), str(data.get("failed_step") or "") or None,
                )

        reply = await run_in_threadpool(work)
        return JSONResponse({"session_id": s.id, **reply.to_dict()})

    async def troubleshoot_step(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("run_id"):
            return bad("run_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)
        logs = [{"name": str(x.get("name"))[:120], "text": str(x.get("text") or "")[:70000], "part": str(x.get("part") or "")[:60]}
                for x in (data.get("logs") or []) if isinstance(x, dict)][:6]

        def work():
            with s.lock:
                return agent.troubleshoot_step(s, str(data["run_id"]), logs)

        return JSONResponse(await run_in_threadpool(work))

    async def security_apply(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("run_id"):
            return bad("run_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)

        def work():
            with s.lock:
                return agent.apply_security_fix(s, str(data["run_id"]))

        out = await run_in_threadpool(work)
        if out.get("error"):
            return bad(out["error"], 403 if "administrators" in out["error"] else 409)
        return JSONResponse(out)

    async def approvals_review(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not str(data.get("thread") or "").strip():
            return bad("thread is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)

        def work():
            with s.lock:
                return agent.start_review(s, str(data["thread"]).strip()[:20], str(data.get("question") or "")[:1000])

        out = await run_in_threadpool(work)
        if out.get("error"):
            return bad(out["error"], 403 if "not waiting" in out["error"] else 409)
        return JSONResponse(out)

    async def batch_result(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("batch_id") or not isinstance(data.get("results"), list):
            return bad("batch_id and results are required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)
        results = [x for x in data["results"] if isinstance(x, dict) and x.get("status") in _STATUSES]

        def work():
            with s.lock:
                return agent.report_batch(s, str(data["batch_id"]), results)

        reply = await run_in_threadpool(work)
        return JSONResponse({"session_id": s.id, **reply.to_dict()})

    async def live_step(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("run_id"):
            return bad("run_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)

        def work():
            with s.lock:
                return agent.live_step(
                    s, str(data["run_id"]), data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {},
                    data.get("result") if isinstance(data.get("result"), dict) else None,
                    str(data["answer"])[:2000] if data.get("answer") is not None else None,
                    str(data.get("approved") or "") or None, data.get("declined") is True,
                )

        action = await run_in_threadpool(work)
        return JSONResponse({"action": action})

    async def live_recover(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("run_id"):
            return bad("run_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)

        def work():
            with s.lock:
                return agent.recover(s, str(data["run_id"]), data.get("failed_index"), str(data.get("error") or ""))

        live = await run_in_threadpool(work)
        if live is None:
            return bad("no recorded run to recover", 409)
        return JSONResponse({"live": live})

    async def me(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        c = caps_of(oprid, request)
        work = access.WORK in c
        tasks = [{"id": t.id, "title": t.title, "example": (t.examples or [t.title])[0],
                  "examples": t.examples[:3], "component": t.component.get("component", "")}
                 for t in agent.tasks.values()] if work else []
        security = agent.access is not None and access.ADMIN in c
        failed = []
        if agent.troubleshooter is not None and work:
            try:
                failed = await run_in_threadpool(agent.troubleshooter.recent_failures, oprid)
            except Exception as e:      # the welcome screen still works without the alert
                log.warning("recent failures for %s: %s", oprid, e)
        monitor = access.MONITOR in c
        overnight = None
        if monitor and watcher is not None:
            recent = watcher.recent(24)
            overnight = {"count": len(recent), "monitors": len(watcher.monitors), "watchdogs": watcher.counts(),
                         "items": [{"id": i["id"], "title": i["title"], "found": i["found"]} for i in recent[:3]]}
        r = roles(request)
        if time.time() - signins.get(oprid, 0) > 1800:
            signins[oprid] = time.time()
            audit.record("signin", "Opened the agent", data={"roles": sorted(r) if r is not None else None})
        return JSONResponse({"oprid": oprid, "roles": sorted(r) if r is not None else None, "caps": sorted(c),
                             "can_audit": audit_log is not None and access.AUDIT in c,
                             "ci": ci_store is not None and agent.db is not None and work,
                             "ask_data": query_catalog is not None and work,
                             "can_teach": access.ADMIN in c, "can_monitor": monitor, "can_work": work,
                             "watcher": watcher is not None, "overnight": overnight, "tasks": tasks,
                             "regress": ({"suites": len(regression.store.suites), "unattended": regression.unattended is not None}
                                         if regression is not None and access.ADMIN in c else None),
                             "troubleshoot": agent.troubleshooter is not None and work, "security": security,
                             "own_access": agent.access is not None and work, "failed": failed,
                             "approvals": agent.approvals is not None and work,
                             "runbooks": [{"id": i, "title": rb.get("title") or i, "summary": rb.get("summary") or "",
                                           "steps": len(rb["steps"])} for i, rb in agent.runbooks.items()] if work else []})

    async def draft(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_teach(oprid, request):
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
        if not can_teach(oprid, request):
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
        audit.record("task.recorded", f"{task.title} ({len(task.playbook)} steps)" + (" replaced the old version" if data.get("overwrite") else ""),
                     ref=task.id)
        return JSONResponse({"task_id": task.id, "title": task.title, "examples": task.examples,
                             "fields": [f.label for f in task.fields.values()], "steps": len(task.playbook)})

    async def runbook_save(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_teach(oprid, request):
            return bad("you are not allowed to publish runbooks", 403)
        if not runbooks_dir:
            return bad("saving runbooks is not configured", 503)
        data = await body(request)
        if data is None or not data.get("draft_id"):
            return bad("draft_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)
        with s.lock:
            draft = s.runbook if s.runbook and s.runbook["draft_id"] == data["draft_id"] else None
        if draft is None:
            return bad("this draft is no longer current; ask for the runbook again", 409)
        rb = draft["runbook"]
        path = Path(runbooks_dir) / f"{rb['id']}.yaml"
        with save_lock:
            if path.exists() and data.get("overwrite") is not True:
                return bad(f"a runbook named {rb['id']} already exists", 409)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".yaml.tmp")
            tmp.write_text(runbooks.to_yaml(rb), encoding="utf-8")
            tmp.replace(path)
            agent.add_runbook(rb)
        with s.lock:
            s.runbook = None
            s.messages.append({"role": "user", "content": f"[event] The user published the runbook {rb['id']} "
                                                          f"({len(rb['steps'])} steps) as {path.name}."})
        log.info("runbook %s published by %s -> %s", rb["id"], oprid, path)
        audit.record("runbook.published", f"{rb['title']} ({len(rb['steps'])} steps)", ref=rb["id"])
        return JSONResponse({"runbook_id": rb["id"], "title": rb["title"], "steps": len(rb["steps"]),
                             "path": f"runbooks/{path.name}"})

    async def runbook_step(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("run_id"):
            return bad("run_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)
        req = {k: data[k] for k in ("inputs", "fill", "confirm", "result", "poll", "approve", "done", "cancel") if k in data}

        def work():
            with s.lock:
                return agent.runbook_step(s, str(data["run_id"]), req)

        out = await run_in_threadpool(work)
        if out.get("error"):
            return bad(out["error"], 409)
        return JSONResponse(out)

    async def monitor_save(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_monitor(oprid, request):
            return bad("you are not allowed to set up monitors", 403)
        if watcher is None or watcher.dir is None:
            return bad("the watcher is not running on the agent service", 503)
        data = await body(request)
        if data is None or not data.get("draft_id"):
            return bad("draft_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)
        with s.lock:
            draft = s.monitor if s.monitor and s.monitor["draft_id"] == data["draft_id"] else None
        if draft is None:
            return bad("this draft is no longer current; ask for the monitor again", 409)
        mon = draft["monitor"]
        with save_lock:
            watcher.add(mon, oprid)
        path = watcher.dir / f"{mon['id']}.yaml"
        with s.lock:
            s.monitor = None
            s.messages.append({"role": "user", "content": f"[event] The user saved the monitor {mon['id']}: "
                                                          f"{monitors.describe_what(mon)}, {monitors.describe_when(mon)}."})
        log.info("monitor %s saved by %s -> %s", mon["id"], oprid, path)
        audit.record("monitor.saved", f"{mon['title']}: {monitors.describe_what(mon)}, {monitors.describe_when(mon)}", ref=mon["id"])
        return JSONResponse({"monitor_id": mon["id"], "title": mon["title"], "when": monitors.describe_when(mon),
                             "path": f"monitors/{path.name}"})

    async def watchplan_save(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_monitor(oprid, request):
            return bad("you are not allowed to set up watchdogs", 403)
        if watcher is None or watcher.dir is None:
            return bad("the watcher is not running on the agent service", 503)
        data = await body(request)
        if data is None or not data.get("draft_id"):
            return bad("draft_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)
        with s.lock:
            draft = s.watch_plan if s.watch_plan and s.watch_plan["draft_id"] == data["draft_id"] else None
        if draft is None:
            return bad("this draft is no longer current; upload the plan again", 409)
        plan = draft["plan"]
        with save_lock:
            watcher.add_plan(plan, oprid)
        audit.record("watchplan.saved", f"{plan['title']} ({len(plan['watchdogs'])} watchdogs)", ref=plan["id"])
        with s.lock:
            s.watch_plan = None
            s.messages.append({"role": "user", "content": f"[event] The user confirmed the watch plan {plan['id']} "
                                                          f"({len(plan['watchdogs'])} watchdogs); the watcher runs it now."})
        return JSONResponse({"plan_id": plan["id"], "title": plan["title"], "watchdogs": len(plan["watchdogs"]),
                             "path": f"monitors/{plan['id']}.yaml", "counts": watcher.counts()})

    def manager_view():
        return {"plans": watcher.describe(), "counts": watcher.counts(), "history": watcher.state.get("history", [])[:10]}

    async def watchdogs(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_monitor(oprid, request):
            return bad("only monitor users manage the watchdogs", 403)
        if watcher is None:
            return bad("the watcher is not running on the agent service", 503)
        return JSONResponse(manager_view())

    async def watchdog_action(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_monitor(oprid, request):
            return bad("only monitor users manage the watchdogs", 403)
        if watcher is None:
            return bad("the watcher is not running on the agent service", 503)
        data = await body(request) or {}
        action, wid, pid = data.get("action"), str(data.get("id") or ""), str(data.get("plan") or "")
        ran = None
        with save_lock:
            if action in ("pause", "resume"):
                done = watcher.set_paused(wid, action == "pause", oprid)
            elif action == "delete":
                done = watcher.delete(wid, oprid)
            elif action in ("pause_plan", "resume_plan"):
                done = watcher.pause_plan(pid, action == "pause_plan", oprid)
            elif action == "delete_plan":
                done = watcher.delete_plan(pid, oprid)
            elif action in ("stop", "start"):
                watcher.set_stopped(action == "stop", oprid)
                done = True
            elif action == "run":
                done = True
            else:
                return bad("unknown action")
        if action == "run":
            ran = await run_in_threadpool(watcher.run_now, wid, oprid)
            done = ran is not None
            if isinstance(ran, dict):
                return bad(f"the watchdog's query failed: {ran['error']}", 409)
        if done is None:
            return bad("no such watchdog or plan", 404)
        audit.record(f"watchdog.{action}", {"stop": "Stopped all watching", "start": "Started watching again"}.get(action)
                     or f"{action.replace('_plan', ' plan').capitalize()} {pid or wid}", ref=pid or wid)
        from .agent import incident_view  # noqa: PLC0415
        out = manager_view()
        if ran is not None:
            out["ran"] = [incident_view(i) for i in ran]
        return JSONResponse(out)

    def xlsx_reply(name, raw):
        return JSONResponse({"name": name, "data": base64.b64encode(raw).decode()})

    async def watchplan_export(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_monitor(oprid, request):
            return bad("only monitor users manage the watchdogs", 403)
        plan = watcher.plans.get(request.query_params.get("plan", "")) if watcher is not None else None
        if plan is None:
            return bad("no such watch plan", 404)
        return xlsx_reply(f"{plan['id']}.xlsx", monitors.to_xlsx(plan))

    async def template(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        name = request.path_params["name"]
        if name == "watch_plan" and can_monitor(oprid, request):
            return xlsx_reply("watch_plan_template.xlsx", monitors.template_xlsx())
        if name == "regression" and can_teach(oprid, request) and regression is not None:
            return xlsx_reply("regression_test_plan.xlsx", regress.template_xlsx(agent.tasks))
        if name == "runbook" and can_teach(oprid, request):
            path = Path(__file__).resolve().parent.parent / "runbooks" / "templates" / "runbook_checklist_template.xlsx"
            if path.is_file():
                return xlsx_reply(path.name, path.read_bytes())
        return bad("no such template", 404)

    # ----- regression tests --------------------------------------------------------------------
    def regress_guard(request, oprid, teacher=True):
        if regression is None:
            return bad("regression tests are off: set PSAGENT_TEST_ENV=on on a test environment's agent service", 503)
        if teacher and not can_teach(oprid, request):
            return bad("only admins (PSAGENT_ADMIN) run regression tests", 403)
        return None

    def suite_of(data):
        if data.get("smoke") is True:
            return regress.smoke_suite(agent.tasks)[0]
        return regression.store.suites.get(str(data.get("suite") or ""))

    async def regress_home(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = regress_guard(request, oprid)
        if err:
            return err
        smoke, skipped = regress.smoke_suite(agent.tasks)
        reports = regression.store.reports(limit=50)
        last = {}
        for r in reports:
            last.setdefault(r["suite"], r)
        brief = lambda r: r and {k: r.get(k) for k in ("run_id", "headline", "started", "counts", "mode", "newly_failing")}  # noqa: E731
        u = regression.unattended
        return JSONResponse({
            "suites": [{"id": i, "title": x["title"], "summary": x.get("summary", ""), "cases": len(x["cases"]),
                        "run_at": x.get("run_at", ""), "after_patch": bool(x.get("after_patch")), "last": brief(last.get(i))}
                       for i, x in regression.store.suites.items()],
            "smoke": {"cases": len(smoke["cases"]), "skipped": skipped, "last": brief(last.get("smoke_test"))},
            "runs": [dict(brief(r), title=r["title"], suite=r["suite"]) for r in reports[:8]],
            "unattended": {"available": u is not None, "user": u.headless.user if u else "", "busy": u.busy if u else None}})

    async def regress_save(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = regress_guard(request, oprid)
        if err:
            return err
        data = await body(request) or {}
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)
        with s.lock:
            draft = s.test_suite if s.test_suite and s.test_suite["draft_id"] == data.get("draft_id") else None
        if draft is None:
            return bad("this draft is no longer current; upload the test plan again", 409)
        suite = draft["suite"]
        with save_lock:
            regression.store.save(suite)
        with s.lock:
            s.test_suite = None
            s.messages.append({"role": "user", "content": f"[event] The user saved the test suite {suite['id']} "
                                                          f"({len(suite['cases'])} cases)."})
        log.info("test suite %s saved by %s", suite["id"], oprid)
        audit.record("suite.saved", f"{suite['title']} ({len(suite['cases'])} cases)", ref=suite["id"])
        return JSONResponse({"suite_id": suite["id"], "title": suite["title"], "cases": len(suite["cases"]),
                             "path": f"regression/{suite['id']}.yaml"})

    async def regress_start(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = regress_guard(request, oprid)
        if err:
            return err
        data = await body(request) or {}
        suite = suite_of(data)
        if suite is None or not suite["cases"]:
            return bad("no such test suite, or it has no cases", 404)
        run = regression.runner.start(suite, oprid)
        return JSONResponse({"run_id": run.run_id, "title": suite["title"], "n": run.n, "secrets": regression.runner.secrets(suite),
                             "cases": [{"n": i + 1, "title": c["title"],
                                        "task": agent.tasks[c["task"]].title if c["task"] in agent.tasks else c["task"],
                                        "expect": regress.EXPECT_LABEL.get(c["expect"], "")} for i, c in enumerate(suite["cases"])]})

    async def regress_step(request):
        """next / result / live / stop for a run: only its owner (the headless runner's test user need not be an admin)."""
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = regress_guard(request, oprid, teacher=False)
        if err:
            return err
        data = await body(request) or {}
        run = regression.runner.get(str(data.get("run_id") or ""), oprid)
        if run is None:
            return bad("this test run is not active", 404)
        what = request.path_params["what"]

        def work():
            if what == "next":
                return regression.runner.next(run)
            if what == "result":
                res = data.get("result") if isinstance(data.get("result"), dict) else {}
                return regression.runner.result(run, data.get("n"), res)
            if what == "live":
                return regression.runner.live_step(run, data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {},
                                                   data.get("result") if isinstance(data.get("result"), dict) else None,
                                                   str(data.get("approved") or "") or None)
            if what == "stop":
                return {"report": regression.runner.stop(run)}
            return {"error": "unknown step"}

        out = await run_in_threadpool(work)
        if out.get("error"):
            return bad(out["error"], 409)
        return JSONResponse(out)

    async def regress_unattended(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = regress_guard(request, oprid)
        if err:
            return err
        if regression.unattended is None:
            return bad("unattended runs need the headless runner on the agent service (Playwright, PSAGENT_TEST_USER)", 503)
        data = await body(request) or {}
        suite = suite_of(data)
        if suite is None or not suite["cases"]:
            return bad("no such test suite, or it has no cases", 404)
        run_id = regression.unattended.start(suite, f"started by {oprid}")
        if run_id is None:
            return bad(f"an unattended run is already in progress ({regression.unattended.busy})", 409)
        audit.record("regression.unattended", f"Started an unattended run of {suite['title']} as {regression.unattended.headless.user}",
                     ref=run_id)
        return JSONResponse({"run_id": run_id, "user": regression.unattended.headless.user})

    async def regress_delete(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = regress_guard(request, oprid)
        if err:
            return err
        data = await body(request) or {}
        sid = str(data.get("suite") or "")
        if sid not in regression.store.suites:
            return bad("no such test suite", 404)
        regression.store.delete(sid)
        log.info("test suite %s deleted by %s", sid, oprid)
        audit.record("suite.deleted", f"Deleted the test suite {sid}", ref=sid)
        return JSONResponse({"deleted": sid})

    async def regress_export(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = regress_guard(request, oprid)
        if err:
            return err
        sid = request.query_params.get("suite", "")
        suite = regress.smoke_suite(agent.tasks)[0] if sid == "smoke_test" else regression.store.suites.get(sid)
        if suite is None:
            return bad("no such test suite", 404)
        return xlsx_reply(f"{suite['id']}.xlsx", regress.to_xlsx(suite, agent.tasks))

    async def regress_report(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = regress_guard(request, oprid)
        if err:
            return err
        rep = regression.store.report(request.query_params.get("run", ""))
        if rep is None:
            return bad("no such run", 404)
        if request.query_params.get("xlsx"):
            return xlsx_reply(f"regression-{rep['suite']}-{rep['run_id']}.xlsx", regress.report_xlsx(rep))
        return JSONResponse({"report": rep})

    async def incidents(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_monitor(oprid, request):
            return bad("only monitor users see the monitors' alerts", 403)
        if watcher is None:
            return JSONResponse({"incidents": []})
        from .agent import incident_view  # noqa: PLC0415
        one = request.query_params.get("id")
        if one:
            inc = watcher.get(one)
            return JSONResponse({"incidents": [incident_view(inc)]}) if inc else bad("no such alert", 404)
        try:
            hours = max(1, min(24 * 7, int(request.query_params.get("hours") or 24)))
        except ValueError:
            hours = 24
        return JSONResponse({"incidents": [incident_view(i) for i in watcher.recent(hours)]})

    # ----- Excel to CI ------------------------------------------------------------------------------
    def agent_reply(messages, ci_load):
        from .agent import Reply  # noqa: PLC0415
        return Reply(messages=messages, ci_load=ci_load).to_dict()

    def ci_guard(oprid, request):
        if not can_work(oprid, request):
            return bad(access.needs(access.WORK), 403)
        if ci_store is None:
            return bad("loading with a CI is off on this agent service (PSAGENT_CI=off)", 503)
        if agent.db is None:
            return bad("loading with a CI needs read-only database access (PSAGENT_DB_*): the checks use it", 503)
        return None

    def ci_can(oprid, name):
        """{create, update} for this user and CI, or None (not allowed)."""
        cis, _ = ci.catalog(agent.db, oprid, name)
        return next(({"create": c["create"], "update": c["update"]} for c in cis if c["name"] == name), None)

    def ci_check(oprid, name, filename, raw):
        if len(raw) > 10 * 1024 * 1024:
            return {"error": "the file is too large (10 MB at most)"}
        shape = ci_store.shapes.get(name)
        if shape is None:
            return {"error": f"open Load with a CI and pick {name} first (its shape is read from PeopleSoft once), then upload again"}
        can = ci_can(oprid, name)
        if can is None:
            return {"error": f"you may not load through {name}: your permission lists don't allow it"}
        try:
            records, problems = ci.read_upload(raw, shape)
        except ci.CIError as e:
            return {"error": str(e)}
        if not records:
            return {"error": "the file has no rows to load"}
        ci.check(records, shape, agent.db, oprid, can)
        ld = ci.new_load(ci_store, shape, records, problems, oprid, filename[:120], can)
        return {"load": ci.view(ld, shape)}

    def my_load(oprid, data):
        ld = ci_store.loads.get(str(data.get("load_id") or data.get("load") or ""))
        return ld if ld is not None and ld["owner"] == oprid else None

    async def query_list(request):
        """＋ Features › Ask the data: the AI_ queries this user may run."""
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        if not can_work(oprid, request):
            return bad(access.needs(access.WORK), 403)
        if query_catalog is None:
            return bad("Ask the data is off on this agent service (it needs PSAGENT_DB_*)", 404)
        try:
            mine = await run_in_threadpool(query_catalog.for_user, oprid, request.query_params.get("refresh") == "1")
        except Exception as e:      # QueryError: the catalog needs the query security tables
            return bad(f"could not read your queries: {e}", 502)
        return JSONResponse({"queries": [queries.view(q) for q in mine], "prefix": query_catalog.prefix})

    async def query_result(request):
        """The browser ran an AI_ query: PeopleSoft's Excel file (data, base64) or the text it showed instead (error)."""
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        data = await body(request)
        if data is None or not data.get("run_id"):
            return bad("run_id is required")
        s = session_of(request, oprid, data)
        if s is None:
            return bad("unknown session", 404)
        if not s.can_work:
            return bad(access.needs(access.WORK), 403)
        raw = None
        if data.get("data"):
            try:
                raw = base64.b64decode(str(data["data"]), validate=True)
            except (binascii.Error, ValueError):
                return bad("data is not base64")
            if len(raw) > 50 * 1024 * 1024:
                return bad("the result is too large (50 MB at most)")

        def work():
            with s.lock:
                return agent.query_result(s, str(data["run_id"]), raw, str(data.get("error") or "")[:2000])

        reply = await run_in_threadpool(work)
        return JSONResponse({"session_id": s.id, **reply.to_dict()})

    async def ci_list(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = ci_guard(oprid, request)
        if err:
            return err
        try:
            cis, more = await run_in_threadpool(ci.catalog, agent.db, oprid, request.query_params.get("q", "")[:60])
        except Exception as e:      # QueryError: the catalog needs PS_USERCLASS_VW
            return bad(f"could not read the Component Interfaces: {e}", 502)
        return JSONResponse({"cis": cis, "more": more, "soaptoci": ci.SOAPTOCI})

    async def ci_shape(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = ci_guard(oprid, request)
        if err:
            return err
        data = (await body(request) or {}) if request.method == "POST" else dict(request.query_params)
        name = re.sub(r"[^A-Z0-9_]", "", str(data.get("ci") or "").upper())[:30]
        if not name:
            return bad("ci is required")
        can = await run_in_threadpool(ci_can, oprid, name)
        if can is None:
            return bad(f"you may not load through {name}: your permission lists don't allow it", 403)
        if request.method == "POST":
            try:
                shape = ci.parse_shape(str(data.get("xml") or ""), name)
            except ci.CIError as e:
                return bad(str(e), 502)
            shape = await run_in_threadpool(ci.enrich, shape, agent.db, oprid)
            ci_store.put_shape(shape)
        elif name in ci_store.shapes and not data.get("refresh"):
            shape = ci_store.shapes[name]
        else:
            return JSONResponse({"request": ci.shape_request(name), "soaptoci": ci.SOAPTOCI})
        return JSONResponse({"shape": ci.summary(shape, can)})

    async def ci_template(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = ci_guard(oprid, request)
        if err:
            return err
        data = await body(request) or {}
        name = str(data.get("ci") or "").upper()
        shape = ci_store.shapes.get(name)
        if shape is None:
            return bad("pick the CI first", 409)
        can = await run_in_threadpool(ci_can, oprid, name)
        if can is None:
            return bad(f"you may not load through {name}", 403)
        props = data.get("props") if isinstance(data.get("props"), list) else None
        audit.record("ci.template", f"Downloaded the {name} template")
        return xlsx_reply(f"{name.lower()}_template.xlsx", ci.template_xlsx(shape, props, None, can))

    async def ci_upload(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = ci_guard(oprid, request)
        if err:
            return err
        data = await body(request) or {}
        try:
            raw = base64.b64decode(str(data.get("data") or ""), validate=True)
        except ValueError:
            return bad("data is not valid base64")
        name = str(data.get("ci") or "").upper()
        if not name:
            try:
                first = runbooks.xlsx_sheets(raw)[0][1][0][1]
                m = re.match(r"Component Interface (\w+)", str(first[0]))
                name = m.group(1).upper() if m else ""
            except (runbooks.DocError, IndexError):
                name = ""
        if not name:
            return bad("this is not a template from Load with a CI")
        out = await run_in_threadpool(ci_check, oprid, name, str(data.get("name") or "upload.xlsx"), raw)
        return bad(out["error"], 409) if "error" in out else JSONResponse(out)

    async def ci_step(request):
        """start / pause / stop / next / result for one of the user's own loads."""
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = ci_guard(oprid, request)
        if err:
            return err
        data = await body(request) or {}
        ld = my_load(oprid, data)
        if ld is None:
            return bad("no such load", 404)
        shape = ci_store.shapes.get(ld["ci"])
        if shape is None:
            return bad(f"the shape of {ld['ci']} is gone: open Load with a CI and pick it again", 409)
        what = request.path_params["what"]
        with save_lock:
            if what == "start":
                if ld["status"] == "done":
                    return bad("this load is finished: download the rows to fix and upload them as a new load", 409)
                if not ci.next_row(ld, shape):
                    return bad("no rows are ready to load", 409)
                first = ld["started"] is None
                ld["status"], ld["started"] = "running", ld["started"] or time.strftime("%Y-%m-%d %H:%M:%S")
                ci_store.save(ld)
                v = ci.view(ld, shape, 0)
                audit.record("ci.start", f"{'Started' if first else 'Resumed'} loading {v['total'] - v['processed']} rows into "
                             f"{ld['ci']} from {ld['file']}", ref=ld["load_id"])
            elif what in ("pause", "stop"):
                if ld["status"] in ("running", "paused", "checked"):
                    if what == "stop":
                        ci.finish(ci_store, ld, shape, "stopped")
                    else:
                        ld["status"] = "paused"
                        ci_store.save(ld)
            elif what == "next":
                if ld["status"] in ("done", "stopped"):
                    return JSONResponse({"done": True, "load": ci.view(ld, shape)})
                if ld["status"] != "running":
                    return JSONResponse({"paused": True, "load": ci.view(ld, shape, 0)})
                row = ci.next_row(ld, shape)
                if row is None:
                    if ld["status"] == "running":
                        ci.finish(ci_store, ld, shape, "done")
                    return JSONResponse({"done": True, "load": ci.view(ld, shape)})
                return JSONResponse({"row": row})
            elif what == "result":
                try:
                    n = int(data.get("n"))
                except (TypeError, ValueError):
                    return bad("n is required")
                res = ci.record_result(ci_store, ld, shape, n, str(data.get("response") or "")[:200000])
                if res is None:
                    return bad("no such row in this load", 404)
                return JSONResponse({"result": res, "load": ci.view(ld, shape, 0)})
            else:
                return bad("unknown step", 404)
        return JSONResponse({"load": ci.view(ld, shape)})

    async def ci_loads(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = ci_guard(oprid, request)
        if err:
            return err
        one = request.query_params.get("load")
        if one:
            ld = my_load(oprid, {"load": one})
            if ld is None or ld["ci"] not in ci_store.shapes:
                return bad("no such load", 404)
            return JSONResponse({"load": ci.view(ld, ci_store.shapes[ld["ci"]])})
        return JSONResponse({"loads": [ci.view(ld, ci_store.shapes[ld["ci"]], 0) for ld in ci_store.mine(oprid) if ld["ci"] in ci_store.shapes]})

    async def ci_export(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = ci_guard(oprid, request)
        if err:
            return err
        ld = my_load(oprid, {"load": request.query_params.get("load", "")})
        if ld is None or ld["ci"] not in ci_store.shapes:
            return bad("no such load", 404)
        shape = ci_store.shapes[ld["ci"]]
        base = ld["file"].rsplit(".", 1)[0]
        if request.query_params.get("what") == "fix":
            raw, n = ci.fix_xlsx(ld, shape)
            if not n:
                return bad("every row was saved: nothing to fix", 404)
            return xlsx_reply(f"{base}-to-fix.xlsx", raw)
        return xlsx_reply(f"{base}-results.xlsx", ci.results_xlsx(ld, shape))

    # ----- the admin dashboard (auditors) --------------------------------------------------------
    def audit_guard(request, oprid):
        if audit_log is None:
            return bad("the audit log is off on this agent service (PSAGENT_AUDIT=off)", 503)
        if not can_audit(oprid, request):
            return bad("only auditors (PeopleSoft role PSAGENT_AUDITOR) open the admin dashboard", 403)
        return None

    def _int(q, name, default, low, high):
        try:
            return max(low, min(high, int(q.get(name) or default)))
        except ValueError:
            return default

    def audit_data(q):
        tab, days = q.get("tab") or "overview", _int(q, "days", 7, 1, audit_log.days)
        if tab == "overview":
            return audit_log.overview(days, budget=audit_jobs.budget if audit_jobs else None)
        if tab == "usage":
            return audit_log.usage(days, q.get("by") or "user")
        if tab == "failures":
            return audit_log.failures(days)
        if tab == "events":
            out = audit_log.events(user=q.get("user", ""), feature=q.get("feature", ""), kind=q.get("kind") or "actions",
                                   outcome=q.get("outcome", ""), text=(q.get("q") or "")[:100], since=q.get("since", ""),
                                   until=q.get("until", ""), before=q.get("before") or None,
                                   limit=_int(q, "limit", 100, 1, 1000))
            out["users"] = audit_log.users()
            out["features"] = audit.FEATURES
            return out
        if tab == "ops":
            from .agent import incident_view  # noqa: PLC0415
            w = None
            if watcher is not None:
                w = {"counts": watcher.counts(), "plans": len(watcher.plans),
                     "incidents": [{k: v for k, v in incident_view(i).items() if k in ("id", "title", "found", "summary", "note", "when")}
                                   for i in watcher.recent(24 * days)][:30]}
            r = None
            if regression is not None:
                r = {"suites": len(regression.store.suites),
                     "runs": [{k: x.get(k) for k in ("run_id", "title", "headline", "started", "mode", "by", "counts", "newly_failing")}
                              for x in regression.store.reports(limit=15)]}
            return {"days": days, "watcher": w, "regression": r}
        return None

    async def audit_view(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = audit_guard(request, oprid)
        if err:
            return err
        out = await run_in_threadpool(audit_data, request.query_params)
        return JSONResponse(out) if out is not None else bad("no such tab")

    async def audit_export(request):
        oprid = oprid_of(request)
        if not oprid:
            return bad("unauthorized", 401)
        err = audit_guard(request, oprid)
        if err:
            return err
        q = dict(request.query_params)
        tab = q.get("tab") or "events"
        if tab == "events":
            q["limit"] = "1000"
            out = await run_in_threadpool(audit_data, q)
            raw, name = audit.events_xlsx(out["events"]), "psagent-audit-log.xlsx"
        elif tab == "usage":
            out = await run_in_threadpool(audit_data, q)
            raw, name = audit.usage_xlsx(out), f"psagent-usage-by-{out['by']}-{out['from']}-to-{out['to']}.xlsx"
        else:
            return bad("export the events or usage tab")
        audit.record("audit.export", f"Downloaded {name}")
        return xlsx_reply(name, raw)

    async def health(_request):
        return JSONResponse({"ok": True, "tasks": sorted(agent.tasks)})

    @asynccontextmanager
    async def lifespan(_app):
        if watcher is not None:
            watcher.start()
        if regression is not None and regression.unattended is not None:
            regression.unattended.loop()
        if audit_jobs is not None:
            audit_jobs.start()
        yield
        if audit_jobs is not None:
            audit_jobs.stop()
        if watcher is not None:
            watcher.stop()
        if regression is not None and regression.unattended is not None:
            regression.unattended.stop()

    return Starlette(
        routes=[
            Route("/api/chat", chat, methods=["POST"]),
            Route("/api/confirm", confirm, methods=["POST"]),
            Route("/api/result", result, methods=["POST"]),
            Route("/api/batch/result", batch_result, methods=["POST"]),
            Route("/api/troubleshoot/step", troubleshoot_step, methods=["POST"]),
            Route("/api/security/apply", security_apply, methods=["POST"]),
            Route("/api/approvals/review", approvals_review, methods=["POST"]),
            Route("/api/live/step", live_step, methods=["POST"]),
            Route("/api/live/recover", live_recover, methods=["POST"]),
            Route("/api/me", me),
            Route("/api/recordings/draft", draft, methods=["POST"]),
            Route("/api/recordings/save", save, methods=["POST"]),
            Route("/api/runbooks/save", runbook_save, methods=["POST"]),
            Route("/api/runbooks/step", runbook_step, methods=["POST"]),
            Route("/api/monitors/save", monitor_save, methods=["POST"]),
            Route("/api/watchplans/save", watchplan_save, methods=["POST"]),
            Route("/api/watchplans/export", watchplan_export),
            Route("/api/watchdogs", watchdogs),
            Route("/api/watchdogs/action", watchdog_action, methods=["POST"]),
            Route("/api/templates/{name}", template),
            Route("/api/regress", regress_home),
            Route("/api/regress/save", regress_save, methods=["POST"]),
            Route("/api/regress/start", regress_start, methods=["POST"]),
            Route("/api/regress/unattended", regress_unattended, methods=["POST"]),
            Route("/api/regress/delete", regress_delete, methods=["POST"]),
            Route("/api/regress/export", regress_export),
            Route("/api/regress/report", regress_report),
            Route("/api/regress/{what}", regress_step, methods=["POST"]),
            Route("/api/incidents", incidents),
            Route("/api/queries", query_list),
            Route("/api/query/result", query_result, methods=["POST"]),
            Route("/api/ci", ci_list),
            Route("/api/ci/shape", ci_shape, methods=["GET", "POST"]),
            Route("/api/ci/template", ci_template, methods=["POST"]),
            Route("/api/ci/check", ci_upload, methods=["POST"]),
            Route("/api/ci/loads", ci_loads),
            Route("/api/ci/load", ci_loads),
            Route("/api/ci/export", ci_export),
            Route("/api/ci/{what}", ci_step, methods=["POST"]),
            Route("/api/audit", audit_view),
            Route("/api/audit/export", audit_export),
            Route("/health", health),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=allowed_origins,
                allow_methods=["POST", "GET"],
                allow_headers=["Authorization", "Content-Type", "ngrok-skip-browser-warning"],
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
    if dev_oprid and secret:
        raise SystemExit("PSAGENT_DEV_OPRID and PSAGENT_SHARED_SECRET are both set: requests without a token would run as "
                         f"{dev_oprid}. Remove PSAGENT_DEV_OPRID (it is for development only, without the secret).")
    if dev_oprid:
        log.warning("PSAGENT_DEV_OPRID=%s: requests run as this user, without a token. Development only.", dev_oprid)
    tasks_dir = os.environ.get("PSAGENT_TASKS_DIR", str(Path(__file__).resolve().parent.parent / "tasks"))
    runbooks_dir = os.environ.get("PSAGENT_RUNBOOKS_DIR", str(Path(__file__).resolve().parent.parent / "runbooks"))
    # Browsers send the origin without a trailing slash: "http://host:8000/" would never match
    origins = [o.strip().rstrip("/") for o in os.environ.get("PSAGENT_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    teachers = _list(os.environ.get("PSAGENT_TEACHERS"))
    monitor_users = _list(os.environ.get("PSAGENT_MONITORS"))
    dev_roles = os.environ.get("PSAGENT_DEV_ROLES")
    dev_roles = frozenset(r.upper() for r in _list(dev_roles)) & set(auth.ROLES) if dev_roles is not None else None
    log.info("Allowed browser origins (PSAGENT_ALLOWED_ORIGINS): %s", origins or "none: only same-origin calls (reverse proxy) will work")
    auditors = _list(os.environ.get("PSAGENT_AUDITORS"))
    log.info("Who may do what: the PeopleSoft roles PSAGENT_USER, PSAGENT_MONITOR, PSAGENT_ADMIN and PSAGENT_AUDITOR "
             "in each user's token (see access.py)")
    if dev_oprid:
        log.info("Development: also teachers (PSAGENT_TEACHERS) %s, monitor users (PSAGENT_MONITORS) %s, auditors "
                 "(PSAGENT_AUDITORS) %s", sorted(teachers) or "none", sorted(monitor_users) or "none", sorted(auditors) or "none")
    elif teachers or monitor_users or auditors:
        log.warning("PSAGENT_TEACHERS / _MONITORS / _AUDITORS are ignored outside development: give the users the "
                    "PSAGENT_ADMIN / _MONITOR / _AUDITOR roles in PeopleSoft")
    for old in ("PSAGENT_SECURITY_ADMINS", "PSAGENT_SECURITY_ROLES", "PSAGENT_APPROVAL_ADMINS", "PSAGENT_APPROVAL_ROLES"):
        if os.environ.get(old):
            log.warning("%s is no longer used: checking other users' access and everyone's absence requests are for "
                        "PSAGENT_ADMIN users", old)
    llm = ClaudeLLM(
        model=os.environ.get("PSAGENT_MODEL", "claude-opus-5-5"),
        effort=os.environ.get("PSAGENT_EFFORT", "low"),
    )
    db = None
    try:
        from .db import from_env  # noqa: PLC0415
        db = from_env(os.environ)
    except ImportError:
        log.warning("python-oracledb is not installed: troubleshooting is off (pip install oracledb)")
    log.info("Troubleshooting database (PSAGENT_DB_*): %s", db.label if db else "not configured: troubleshooting is off")
    agent = Agent(llm, load_tasks(tasks_dir), db, os.environ.get("PSAGENT_TROUBLESHOOT_EFFORT", "medium"))
    mailer = mailer_from_env()
    audit_log, jobs = audit_from_env(mailer)
    ci_store = None
    if os.environ.get("PSAGENT_CI", "on").lower() not in ("off", "0", "false", "no"):
        ci_store = ci.Store(os.environ.get("PSAGENT_CI_DIR", str(Path(__file__).resolve().parent.parent / "ci_loads")))
        log.info("Excel to CI (PSAGENT_CI_DIR=%s): %s", ci_store.dir, "on" if db else "off until PSAGENT_DB_* is set (the checks need it)")
    catalog = None
    if db and os.environ.get("PSAGENT_QUERIES", "on").lower() not in ("off", "0", "false", "no"):
        catalog = queries.Catalog(db, os.environ.get("PSAGENT_QUERY_PREFIX", queries.PREFIX).strip().upper() or queries.PREFIX)
        log.info("Ask the data: PS Queries named %s* that each user may run", catalog.prefix)
    return create_app(agent, secret, origins, dev_oprid, teachers, tasks_dir, runbooks_dir,
                      watcher_from_env(agent, mailer), monitor_users, dev_roles, regression_from_env(agent, mailer),
                      audit_log, auditors, jobs, ci_store, catalog)


def _list(value):
    return {x.strip() for x in str(value or "").split(",") if x.strip()}


def audit_from_env(mailer=None):
    """The audit log and its housekeeping, unless PSAGENT_AUDIT=off. -> (Audit, Jobs) or (None, None)"""
    if os.environ.get("PSAGENT_AUDIT", "on").lower() in ("off", "0", "false", "no"):
        log.info("Audit log: off (PSAGENT_AUDIT=off)")
        return None, None
    directory = os.environ.get("PSAGENT_AUDIT_DIR", str(Path(__file__).resolve().parent.parent / "audit"))
    try:
        days = int(os.environ.get("PSAGENT_AUDIT_DAYS", "90"))
    except ValueError:
        days = 90
    try:
        budget = float(os.environ.get("PSAGENT_BUDGET") or 0) or None
    except ValueError:
        log.warning("PSAGENT_BUDGET must be a number of US dollars a month")
        budget = None
    a = audit.Audit(directory, days, audit.parse_prices(os.environ.get("PSAGENT_PRICES")))
    to = sorted(_list(os.environ.get("PSAGENT_AUDIT_TO"))) or (mailer.default_to if mailer else [])
    jobs = audit.Jobs(a, mailer, budget, to, os.environ.get("PSAGENT_AUDIT_WEEKLY", "Mon 08:00"), os.environ.get("PSAGENT_PS_URL", ""))
    log.info("Audit log (PSAGENT_AUDIT_DIR=%s): kept %d days; monthly budget %s; weekly email %s", a.path, days,
             f"${budget:,.0f}" if budget else "none (PSAGENT_BUDGET)",
             f"{os.environ.get('PSAGENT_AUDIT_WEEKLY', 'Mon 08:00')} to {', '.join(to)}" if mailer and to and jobs.weekly
             else "off (needs SMTP and PSAGENT_AUDIT_TO or PSAGENT_ALERT_TO)")
    return a, jobs


def mailer_from_env():
    from .watcher import Mailer  # noqa: PLC0415

    if not os.environ.get("PSAGENT_SMTP_HOST"):
        return None
    tls = os.environ.get("PSAGENT_SMTP_STARTTLS")
    return Mailer(os.environ["PSAGENT_SMTP_HOST"], os.environ.get("PSAGENT_SMTP_PORT", "25"),
                  os.environ.get("PSAGENT_SMTP_USER", ""), os.environ.get("PSAGENT_SMTP_PASSWORD", ""),
                  os.environ.get("PSAGENT_SMTP_FROM", ""), None if tls is None else tls.lower() in ("1", "true", "yes", "on"),
                  sorted(_list(os.environ.get("PSAGENT_ALERT_TO"))), os.environ.get("PSAGENT_PS_URL", ""))


class Regression:
    """Regression tests on a test environment: the saved suites, the run engine, the headless runner (or None)."""

    def __init__(self, store, runner, unattended=None):
        self.store, self.runner, self.unattended = store, runner, unattended


def regression_from_env(agent, mailer=None):
    """Regression tests, only where PSAGENT_TEST_ENV=on: test cases really save data."""
    from . import regress_runner  # noqa: PLC0415

    if os.environ.get("PSAGENT_TEST_ENV", "").lower() not in ("on", "1", "true", "yes"):
        log.info("Regression tests: off (set PSAGENT_TEST_ENV=on on a test environment)")
        return None
    store = regress.Store(os.environ.get("PSAGENT_REGRESSION_DIR", str(Path(__file__).resolve().parent.parent / "regression")))

    def on_report(report, suite):
        to = suite.get("notify") or []
        if mailer is not None and (report.get("mode") == "server" or to):
            mailer.send(to, *regress_runner.report_email(report, mailer.ps_url))

    runner = regress.Runner(store, lambda: agent.tasks, agent.operator, on_report)
    unattended = None
    user, ps_url = os.environ.get("PSAGENT_TEST_USER", ""), os.environ.get("PSAGENT_PS_URL", "")
    if not user:
        log.info("Regression tests: on (%d suites); unattended runs off (no PSAGENT_TEST_USER)", len(store.suites))
    elif not regress_runner.available():
        log.warning("Regression tests: unattended runs need Playwright: pip install playwright && python -m playwright install chromium")
    else:
        try:
            secrets = dict(x.split("=", 1) for x in _list(os.environ.get("PSAGENT_TEST_SECRETS")) if "=" in x)
            headless = regress_runner.Headless(ps_url, user, os.environ.get("PSAGENT_TEST_PASSWORD", ""), secrets)
            unattended = regress_runner.Unattended(runner, store, headless, agent.db, mailer)
            log.info("Regression tests: on (%d suites); unattended runs as %s", len(store.suites), user)
        except ValueError as e:
            log.warning("Regression tests: unattended runs off: %s", e)
    return Regression(store, runner, unattended)


def watcher_from_env(agent, mailer=None):
    """The watcher, unless there is no database or PSAGENT_WATCH=off."""
    from .watcher import ReportFiles, Watcher  # noqa: PLC0415

    if agent.db is None or os.environ.get("PSAGENT_WATCH", "on").lower() in ("off", "0", "false", "no"):
        log.info("Watcher: off (%s)", "no database" if agent.db is None else "PSAGENT_WATCH=off")
        return None
    directory = os.environ.get("PSAGENT_MONITORS_DIR", str(Path(__file__).resolve().parent.parent / "monitors"))
    ps_url = os.environ.get("PSAGENT_PS_URL", "")
    reports = None
    if os.environ.get("PSAGENT_SVC_USER"):
        try:
            reports = ReportFiles(ps_url, os.environ["PSAGENT_SVC_USER"], os.environ.get("PSAGENT_SVC_PASSWORD", ""))
        except ValueError as e:
            log.warning("Watcher log files: %s", e)
    try:
        cap = int(os.environ.get("PSAGENT_WATCH_MAX_INVESTIGATIONS", "20"))
    except ValueError:
        cap = 20
    log.info("Monitors (PSAGENT_MONITORS_DIR=%s)", directory)
    return Watcher(agent.db, agent.troubleshooter, directory, mailer, reports, cap,
                   os.environ.get("PSAGENT_WATCH_SUMMARY", "07:00"))
