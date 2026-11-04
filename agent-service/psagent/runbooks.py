"""Runbooks: a checklist of steps (checks, tasks, processes, approvals, manual work) run in order.

A runbook is drafted by Claude from a checklist the user attaches (Excel, CSV or PDF), checked
here against the task catalog and the database, shown on a review card and saved as
runbooks/<id>.yaml. Nothing in a draft runs: check queries are test-run read-only so the
author sees what they return today.

Running one (step()): the widget calls in a loop. Checks run here, read-only, without Claude.
Every other step is handed to the widget and waits: a task shows its confirm card (the
password is typed there and never reaches the service) and runs on screen; a process is then
followed in PSPRCSRQST until it ends; an approval or a manual step waits for a click. The
run stops at the first failure (or the user's Stop), and every result is kept for the run log.

Step types:
  check     read-only query; expect: no_rows (passes when it returns nothing) or rows
  task      run a task from the catalog (its confirm card still asks for anything missing)
  process   a task that submits a process; the engine waits for Success in Process Monitor
  approval  wait for the owner's approval
  manual    a person does it outside the agent and ticks it off
"""

import csv
import io
import logging
import re
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

from .db import QueryError, _strip, check_select
from .recorder import TASK_ID, snake

STEP_TYPES = ("check", "task", "process", "approval", "manual")
EXPECT = ("no_rows", "rows")
MAX_STEPS = 40
MAX_INPUTS = 10
MAX_TEXT = 100_000          # characters of a spreadsheet sent to Claude
MAX_UNZIPPED = 30 << 20     # an .xlsx is a zip: refuse ones that expand beyond this
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
_BIND = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
PROCESS_TIMEOUT = 20 * 60   # seconds a process may take before the run stops waiting for it
# PSPRCSRQST.RUNSTATUS
_RUN_STATUS = {"1": "Cancel", "2": "Delete", "3": "Error", "4": "Hold", "5": "Queued", "6": "Initiated", "7": "Processing",
               "8": "Cancelled", "9": "Success", "10": "Not Successful", "16": "Pending", "17": "Success with Warning",
               "18": "Blocked", "19": "Restart"}
_SUCCESS, _FAILED = {"9", "17"}, {"1", "2", "3", "8", "10"}
_INSTANCE = re.compile(r"process instance (\d+)", re.I)
log = logging.getLogger("psagent")
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class DocError(ValueError):
    pass


# ----- spreadsheets as text for Claude -------------------------------------------

def _rows_text(title, rows):
    lines = [f"## Sheet: {title}"]
    for n, cells in rows:
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            lines.append(f"{n}: " + " | ".join(c.replace("\n", " ").strip() for c in cells))
    return "\n".join(lines)


def xlsx_text(raw):
    """Every sheet of an .xlsx as numbered rows of cells separated by ' | '."""
    text = "\n\n".join(_rows_text(title, rows) for title, rows in xlsx_sheets(raw)).strip()
    if not text:
        raise DocError("the workbook is empty")
    return text[:MAX_TEXT]


def xlsx_sheets(raw):
    """-> [(sheet name, [(row number, [cell text, ...]), ...]), ...] (numbers and dates as Excel stores them)"""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise DocError("the file is not a valid .xlsx workbook") from e
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
            raise DocError("the workbook is too large")
        names = set(z.namelist())
        try:
            shared = []
            if "xl/sharedStrings.xml" in names:
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_M + "si"):
                    shared.append("".join(t.text or "" for t in si.iter(_M + "t")))
            rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
            sheets = []
            for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(_M + "sheet"):
                target = rels.get(sh.get(_R + "id"), "")
                path = target.lstrip("/") if target.startswith("/") else "xl/" + target
                if path in names:
                    sheets.append((sh.get("name") or "Sheet", path))
            out = []
            for title, path in sheets:
                rows = []
                for row in ET.fromstring(z.read(path)).iter(_M + "row"):
                    cells = {}
                    for i, c in enumerate(row.iter(_M + "c")):
                        m = re.match(r"([A-Z]+)", c.get("r") or "")
                        col = _col(m.group(1)) if m else i
                        t, v = c.get("t"), c.find(_M + "v")
                        if t == "s" and v is not None:
                            val = shared[int(v.text)]
                        elif t == "inlineStr":
                            val = "".join(x.text or "" for x in c.iter(_M + "t"))
                        elif t == "b" and v is not None:
                            val = "TRUE" if v.text == "1" else "FALSE"
                        else:
                            val = v.text if v is not None and v.text else ""
                        cells[col] = val
                    if cells:
                        rows.append((row.get("r") or str(len(rows) + 1), [cells.get(k, "") for k in range(max(cells) + 1)]))
                out.append((title, rows))
        except (KeyError, ET.ParseError, IndexError, ValueError) as e:
            raise DocError("the workbook could not be read") from e
    return out


def _col(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def csv_text(raw):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):   # latin-1 decodes anything
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = [(str(i), r) for i, r in enumerate(csv.reader(io.StringIO(text)), 1)]
    out = _rows_text("CSV", rows)
    if out.count("\n") == 0:
        raise DocError("the CSV file is empty")
    return out[:MAX_TEXT]


# ----- checking Claude's draft ----------------------------------------------------

def _binds(sql):
    return {b.lower() for b in _BIND.findall(_strip(sql))}


def _values(raw):
    return {str(v.get("name") or "").strip(): str(v.get("value") or "").strip()
            for v in raw or [] if isinstance(v, dict) and str(v.get("name") or "").strip()}


def check(draft, tasks, db=None, who="", source=None):
    """Clean and verify Claude's draft. -> (runbook dict to save, card for the widget, problems for Claude)."""
    problems = []
    title = str(draft.get("title") or "").strip()[:80]
    rb_id = snake(title, "runbook") if title else ""
    if not TASK_ID.fullmatch(rb_id):
        problems.append("the runbook needs a short title")

    inputs, defaults = {}, {}
    for raw in (draft.get("inputs") or [])[:MAX_INPUTS]:
        name = snake(raw.get("name") or raw.get("label") or "", "input")
        if name in inputs:
            problems.append(f"input {name} is listed twice")
            continue
        spec = {"label": str(raw.get("label") or name)[:60], "required": True}
        default = str(raw.get("default") or "").strip()[:100]
        if raw.get("upper"):
            spec["upper"] = True
            default = default.upper()
        if default:
            spec["default"] = default
            defaults[name] = default
        if str(raw.get("hint") or "").strip():
            spec["hint"] = str(raw["hint"]).strip()[:200]
        inputs[name] = spec

    steps, cards = [], []
    raw_steps = draft.get("steps") or []
    if not raw_steps:
        problems.append("the runbook has no steps")
    if len(raw_steps) > MAX_STEPS:
        problems.append(f"at most {MAX_STEPS} steps")
    for n, raw in enumerate(raw_steps[:MAX_STEPS], 1):
        kind = raw.get("type")
        label = f"step {n} ({str(raw.get('title') or kind)[:60]})"
        if kind not in STEP_TYPES:
            problems.append(f"{label}: type must be one of {', '.join(STEP_TYPES)}")
            continue
        st = {"id": f"s{n}_" + snake(raw.get("title") or kind, kind)[:30].rstrip("_"),
              "title": str(raw.get("title") or kind).strip()[:100]}
        if str(raw.get("owner") or "").strip():
            st["owner"] = str(raw["owner"]).strip()[:60]
        card = {"n": n, "type": kind, "title": st["title"], "owner": st.get("owner", "")}

        if kind in ("task", "process"):
            task = tasks.get(str(raw.get("task_id") or ""))
            if task is None:
                problems.append(f"{label}: unknown task {raw.get('task_id')!r}; use a task_id from the task list, "
                                "or make it a manual step and ask the user to record the task")
                continue
            if kind == "process" and not any(s.get("do") == "expect_process" for s in task.playbook):
                problems.append(f"{label}: task {task.id} does not submit a process; use type task")
            values, shown = {}, []
            for name, value in _values(raw.get("values")).items():
                f = task.fields.get(name)
                if f is None or f.sensitive:
                    problems.append(f"{label}: {name!r} is not a field of task {task.id} "
                                    f"(fields: {', '.join(x for x, y in task.fields.items() if not y.sensitive)})")
                    continue
                unknown = [p for p in _PLACEHOLDER.findall(value) if p not in inputs]
                if unknown:
                    problems.append(f"{label}: {{{{{unknown[0]}}}}} is not one of the runbook inputs")
                    continue
                if not _PLACEHOLDER.search(value):
                    value, err = f.normalize(value)
                    if err:
                        problems.append(f"{label}: {err}")
                        continue
                if value not in ("", []):
                    values[name] = value
                    shown.append({"label": f.label, "value": ", ".join(value) if isinstance(value, list) else value})
            asks = [task.fields[x].label for x in task.missing(values)] + [x["label"] for x in task.secrets()]
            body = {"task": task.id, "values": values}
            if kind == "process":
                body = {"task": task.id, "process": str(raw.get("process_name") or "").strip().upper()[:30], "values": values}
                if not body["process"]:
                    body.pop("process")
            st[kind] = body
            card.update(task=task.title, values=shown, asks=asks)

        elif kind == "check":
            rule = str(raw.get("rule") or "").strip()[:300]
            sql = str(raw.get("sql") or "").strip()
            expect = raw.get("expect") if raw.get("expect") in EXPECT else "no_rows"
            try:
                sql = check_select(sql)
            except QueryError as e:
                problems.append(f"{label}: {e}")
                continue
            binds = _binds(sql)
            unknown = sorted(binds - set(inputs))
            if unknown:
                problems.append(f"{label}: :{unknown[0]} is not one of the runbook inputs ({', '.join(inputs) or 'none'})")
                continue
            st.update(check=rule or st["title"], sql=sql, expect=expect)
            card.update(rule=st["check"], sql=sql, expect=expect, test=_test(db, sql, binds, defaults, expect, who, title))
            if card["test"].get("error"):
                problems.append(f"{label}: the query fails on the database: {card['test']['error']}")

        else:
            text = str(raw.get("instructions") or "").strip()[:400] or st["title"]
            if kind == "approval" and not st.get("owner"):
                problems.append(f"{label}: an approval needs an owner (the role that approves)")
            st[kind] = text
            card["instructions"] = text
        if kind != "approval" and raw.get("stop_on_fail") is False:
            st["on_fail"] = "warn"
            card["warn_only"] = True
        steps.append(st)
        cards.append(card)

    runbook = {"id": rb_id, "title": title, "summary": str(draft.get("summary") or "").strip()[:300],
               "source": {**({"document": source} if source else {}), "by": who, "on": date.today().isoformat()},
               "inputs": inputs, "steps": steps}
    card = {"id": rb_id, "title": title, "summary": runbook["summary"], "source": source or "",
            "inputs": [{"name": k, **v} for k, v in inputs.items()], "steps": cards,
            "questions": [str(q)[:300] for q in (draft.get("questions") or [])[:10] if str(q).strip()]}
    return runbook, card, problems


def _test(db, sql, binds, defaults, expect, who, title):
    """Run a check query now, read-only, with the inputs' defaults: what it returns today."""
    if db is None:
        return {"skipped": "no database access"}
    missing = sorted(b for b in binds if b not in defaults)
    if missing:
        return {"skipped": f"no default for {', '.join(missing)}"}
    try:
        cols, rows, more = db.query(sql, {b: defaults[b] for b in binds}, max_rows=5, who=who,
                                    why=f"runbook draft check ({title[:60]})")
    except QueryError as e:
        return {"error": str(e)}
    passes = not rows if expect == "no_rows" else bool(rows)
    return {"passes": passes, "rows": len(rows), "more": more, "columns": cols[:8],
            "sample": [r[:8] for r in rows[:3]], "with": {b: defaults[b] for b in binds}}


def to_yaml(runbook):
    head = (f"# Runbook drafted by the agent{' from ' + runbook['source']['document'] if runbook['source'].get('document') else ''}"
            f" and published by {runbook['source'].get('by')} on {runbook['source'].get('on')}.\n"
            "# Steps run in order; task and process steps use the tasks in tasks/.\n")
    return head + yaml.safe_dump(runbook, sort_keys=False, allow_unicode=True, width=120)


# ----- published runbooks -----------------------------------------------------------

def load_runbooks(directory):
    """Published runbooks (runbooks/*.yaml; not the templates/ or design/ folders). Broken files are skipped."""
    out = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            rb = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            log.warning("runbook %s skipped: %s", path.name, e)
            continue
        if isinstance(rb, dict) and TASK_ID.fullmatch(str(rb.get("id") or "")) and isinstance(rb.get("steps"), list):
            out[rb["id"]] = rb
        else:
            log.warning("runbook %s skipped: not a runbook", path.name)
    return out


def _words(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def find(runbooks, name):
    """The runbook the user named: its id or title, else the only one whose title has all the words."""
    want = re.sub(r"^(the )?(runbook )?|( runbook)$", "", _words(name)).strip()
    if not want:
        return None
    for rb in runbooks.values():
        if want in (_words(rb["id"]), _words(rb.get("title"))):
            return rb
    hits = [rb for rb in runbooks.values() if all(w in _words(rb.get("title")).split() for w in want.split())]
    return hits[0] if len(hits) == 1 else None


# ----- running one ------------------------------------------------------------------

@dataclass
class Run:
    run_id: str
    runbook: dict
    inputs: dict = field(default_factory=dict)
    index: int = 0
    waiting: str = "inputs"      # inputs | task | running | process | approval | manual | done
    status: str = "ready"        # ready | running | success | failed | stopped
    results: list = field(default_factory=list)   # [{n, title, type, status, detail}]
    pending: dict | None = None  # the step in progress: task values, process instance
    started: float = field(default_factory=time.time)

    @property
    def steps(self):
        return self.runbook["steps"]


def new_run(runbook):
    return Run(run_id="rbk-" + uuid.uuid4().hex[:10], runbook=runbook)


def _kind(st):
    return next((k for k in STEP_TYPES if k in st), "manual")


def start_card(run):
    rb = run.runbook
    return {"run_id": run.run_id, "id": rb["id"], "title": rb.get("title") or rb["id"], "summary": rb.get("summary") or "",
            "auto_run": rb.get("auto_run") is True,
            "inputs": [{"name": k, "label": v.get("label") or k, "default": v.get("default") or "", "hint": v.get("hint") or ""}
                       for k, v in (rb.get("inputs") or {}).items()],
            "steps": [{"n": i + 1, "type": _kind(st), "title": st.get("title") or _kind(st), "owner": st.get("owner") or ""}
                      for i, st in enumerate(run.steps)]}


def _fill_inputs(value, inputs):
    if isinstance(value, list):
        return [_fill_inputs(v, inputs) for v in value]
    return _PLACEHOLDER.sub(lambda m: str(inputs.get(m.group(1), "")), str(value))


def _record(run, st, status, detail):
    r = {"n": run.index + 1, "title": st.get("title") or _kind(st), "type": _kind(st), "status": status, "detail": detail[:500]}
    run.results.append(r)
    return r


def _finish(run, status, events, extra=None):
    run.status, run.waiting, run.pending = status, "done", None
    out = {"events": events, "next": None, "done": True, "status": status, "results": run.results}
    out.update(extra or {})
    return out


def _begin_task(run, task, values, db, who, events):
    """Hand the task to the widget to run on screen."""
    run.pending["values"] = values
    if "process" in run.steps[run.index]:
        run.pending["baseline"] = _latest_instance(db, who)
    run.waiting = "running"
    n = run.index + 1
    return {"events": events, "done": False, "next": {"type": "run", "n": n, "action": {
        "run_id": f"{run.run_id}-{n}", "task_id": task.id, "title": task.title, "component": task.component,
        "steps": task.render(values)}}}


def step(run, req, tasks, db=None, who=""):
    """One call of the widget's loop. req: inputs | fill + confirm | result | poll | approve | done | cancel."""
    events = []
    if run.waiting == "done":
        return {"error": "this run has ended"}
    if req.get("cancel"):
        st = run.steps[run.index] if run.index < len(run.steps) else {}
        events.append(_record(run, st, "stopped", f"Stopped by {who}"))
        return _finish(run, "stopped", events)

    if run.waiting == "inputs":
        given = req.get("inputs") if isinstance(req.get("inputs"), dict) else {}
        missing = []
        for name, spec in (run.runbook.get("inputs") or {}).items():
            v = str(given.get(name) or spec.get("default") or "").strip()[:100]
            if spec.get("upper"):
                v = v.upper()
            if not v and spec.get("required", True):
                missing.append(spec.get("label") or name)
            run.inputs[name] = v
        if missing:
            return {"error": "needed to start: " + ", ".join(missing)}
        run.status = "running"
        log.info("runbook %s run %s started by %s: %s", run.runbook["id"], run.run_id, who, run.inputs)

    elif run.waiting == "task":
        if req.get("confirm") is not True:
            return {"error": "confirm the task, or stop the run"}
        task = tasks.get(run.pending["task_id"])
        if task is None:
            return _finish(run, "failed", [_record(run, run.steps[run.index], "failed", "the task no longer exists")])
        values = dict(run.pending["values"])
        for name, raw in (req.get("fill") or {}).items():
            f = task.fields.get(name)
            if f is None or f.sensitive:
                continue
            v, err = f.normalize(raw)
            if err:
                return {"error": err}
            if v not in ("", []):
                values[name] = v
        missing = [task.fields[n].label for n in task.missing(values)]
        if missing:
            return {"error": "still needed: " + ", ".join(missing)}
        return _begin_task(run, task, values, db, who, [])

    elif run.waiting == "running":
        res = req.get("result") if isinstance(req.get("result"), dict) else None
        if res is None:
            return {"error": "send the task result"}
        st = run.steps[run.index]
        msg = str(res.get("message") or "")[:400]
        if res.get("status") != "success":
            where = f" at {res['failed_step']}" if res.get("failed_step") else ""
            status = "failed" if res.get("status") == "error" else "stopped"
            events.append(_record(run, st, status, f"Stopped{where}: {msg}"))
            return _finish(run, status, events)
        if "process" not in st:
            events.append(_record(run, st, "ok", msg or "Done"))
            run.index += 1
        else:
            m = _INSTANCE.search(msg)
            run.pending.update(instance=m.group(1) if m else None, since=time.time(), said=msg)
            run.waiting = "process"
            return _wait(run, tasks, db, who, events)

    elif run.waiting == "process":
        if not req.get("poll"):
            return {"error": "the run is waiting for the process"}
        return _wait(run, tasks, db, who, events)

    elif run.waiting == "approval":
        if req.get("approve") is None:
            return {"error": "approve or reject"}
        st = run.steps[run.index]
        if req.get("approve") is not True:
            events.append(_record(run, st, "stopped", f"Rejected by {who}"))
            return _finish(run, "stopped", events)
        events.append(_record(run, st, "ok", f"Approved by {who}"))
        run.index += 1

    elif run.waiting == "manual":
        if req.get("done") is not True:
            return {"error": "tick the manual step off, or stop the run"}
        events.append(_record(run, run.steps[run.index], "ok", f"Done by {who}"))
        run.index += 1

    return _advance(run, tasks, db, who, events)


def _advance(run, tasks, db, who, events):
    """Run checks until a step needs the widget or the user."""
    while run.index < len(run.steps):
        st, n = run.steps[run.index], run.index + 1
        kind = _kind(st)
        if kind == "check":
            ok, detail = _run_check(run, st, db, who)
            warn = not ok and st.get("on_fail") == "warn"
            events.append(_record(run, st, "ok" if ok else "warn" if warn else "failed", detail))
            if not ok and not warn:
                return _finish(run, "failed", events)
            run.index += 1
            continue
        if kind in ("task", "process"):
            body = st[kind] if isinstance(st[kind], dict) else {}
            task = tasks.get(body.get("task"))
            if task is None:
                events.append(_record(run, st, "failed", f"the task {body.get('task')!r} does not exist (any more)"))
                return _finish(run, "failed", events)
            values = {}
            for name, raw in (body.get("values") or {}).items():
                f = task.fields.get(name)
                if f is None or f.sensitive:
                    continue
                v, err = f.normalize(_fill_inputs(raw, run.inputs))
                if err:
                    events.append(_record(run, st, "failed", err))
                    return _finish(run, "failed", events)
                if v not in ("", []):
                    values[name] = v
            run.pending, run.waiting = {"task_id": task.id, "values": values}, "task"
            # auto_run: only set by hand in the published YAML. A password or a missing value still asks.
            if run.runbook.get("auto_run") is True and not task.secrets() and not task.missing(values):
                log.info("runbook %s run %s step %d runs without a confirm card (auto_run), started by %s", run.runbook["id"], run.run_id, n, who)
                return _begin_task(run, task, values, db, who, events)
            return {"events": events, "done": False, "next": {
                "type": "task", "n": n, "title": st.get("title") or task.title, "task_title": task.title,
                "process": kind == "process", "rows": task.summary_rows(values), "secrets": task.secrets(),
                "missing": [{"name": x, "label": task.fields[x].label, "hint": task.fields[x].hint} for x in task.missing(values)]}}
        run.waiting = kind
        return {"events": events, "done": False, "next": {
            "type": kind, "n": n, "title": st.get("title") or kind, "owner": st.get("owner") or "",
            "instructions": st.get(kind) if isinstance(st.get(kind), str) else ""}}
    return _finish(run, "success", events)


def _run_check(run, st, db, who):
    if db is None:
        return False, "could not run: no database access"
    sql = st.get("sql") or ""
    try:
        binds = _binds(check_select(sql))
        cols, rows, more = db.query(sql, {b: run.inputs.get(b, "") for b in binds}, max_rows=5, who=who,
                                    why=f"runbook check {run.runbook['id']} step {run.index + 1}")
    except QueryError as e:
        return False, f"the query failed: {e}"
    ok = not rows if (st.get("expect") or "no_rows") == "no_rows" else bool(rows)
    if ok:
        return True, (st.get("check") or "Passed") + (f" ({len(rows)}{'+' if more else ''} found)" if rows else "")
    shown = "; ".join(", ".join(f"{c}={v}" for c, v in zip(cols[:4], r[:4])) for r in rows[:3])
    return False, (st.get("check") or "Check") + (f": failed, found {shown}" if rows else ": failed, nothing found")


def _latest_instance(db, who):
    """The newest process instance before a process step submits: its request is the first one after it."""
    if db is None:
        return None
    try:
        _, rows, _ = db.query("SELECT MAX(PRCSINSTANCE) FROM PSPRCSRQST", max_rows=1, who=who, why="runbook process baseline")
        return int(rows[0][0] or 0) if rows else 0
    except (QueryError, TypeError, ValueError):
        return None


def _wait(run, tasks, db, who, events):
    """Follow the submitted process in Process Monitor (PSPRCSRQST) until it ends."""
    st, p, n = run.steps[run.index], run.pending, run.index + 1
    name = (st.get("process") or {}).get("process") or ""
    if db is None:
        events.append(_record(run, st, "ok", (p.get("said") or "Submitted") + " (not followed: no database access)"))
        run.index += 1
        return _advance(run, tasks, db, who, events)
    rows = []
    try:
        if p.get("instance"):
            _, rows, _ = db.query("SELECT PRCSINSTANCE, PRCSNAME, RUNSTATUS FROM PSPRCSRQST WHERE PRCSINSTANCE = :i",
                                  {"i": int(p["instance"])}, max_rows=1, who=who, why="runbook process status")
        else:
            sql = ("SELECT PRCSINSTANCE, PRCSNAME, RUNSTATUS FROM PSPRCSRQST WHERE OPRID = :o AND PRCSINSTANCE > :b"
                   + (" AND PRCSNAME = :p" if name else "") + " ORDER BY PRCSINSTANCE DESC")
            params = {"o": who, "b": p.get("baseline") or 0, **({"p": name} if name else {})}
            _, rows, _ = db.query(sql, params, max_rows=1, who=who, why="runbook process status")
    except QueryError as e:
        log.warning("runbook %s: process status: %s", run.run_id, e)
    if rows:
        inst, status = str(rows[0][0]), str(rows[0][2])
        p["instance"] = inst
        label = f"{rows[0][1]} {inst}: {_RUN_STATUS.get(status, status)}"
        if status in _SUCCESS:
            events.append(_record(run, st, "ok", label))
            run.index += 1
            return _advance(run, tasks, db, who, events)
        if status in _FAILED:
            events.append(_record(run, st, "failed", label))
            return _finish(run, "failed", events, {"failed_instance": inst})
    else:
        label = f"waiting for {name or 'the process'} to show up in Process Monitor"
    if time.time() - p["since"] > PROCESS_TIMEOUT:
        events.append(_record(run, st, "failed", f"{label}: not finished after {PROCESS_TIMEOUT // 60} minutes"))
        return _finish(run, "failed", events, {"failed_instance": p["instance"]} if p.get("instance") else None)
    return {"events": events, "done": False, "next": {"type": "wait", "n": n, "title": st.get("title") or name, "text": label}}
