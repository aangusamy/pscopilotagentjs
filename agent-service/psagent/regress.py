"""Regression tests: recorded tasks replayed as test cases, after a patch or a PUM image.

A test suite (regression/<suite>.yaml) is a list of cases: a task, the values to use
and the expected result: Saved, Process submitted, or an Error whose message contains
a text (negative tests). "{{n}}" in a value becomes a new number on every run, so
cases that create data don't collide with the last run's.

Suites come from the test plan template (Excel, generated from the tasks: a Suite
sheet, then one sheet per task with its fields as columns) uploaded in the chat;
Claude reads it (draft_test_suite) and the service checks every case against its
task. The smoke test needs no template: every task once, with its defaults.

Running a suite (the widget drives it in the browser, or the headless runner in
regress_runner.py drives the same widget code on the server):

  next     the service renders the next case's task into steps for the runner
  result   the runner's result is judged against the expected result
           - the page no longer matched a recorded step: the case is finished live
             (live.recovery_run, Save auto-approved: test environments only); reaching
             the expected result that way is "passed with changes", with the changes
  report   passed / passed with changes / failed, compared with the suite's last run;
           kept in regression/runs/<suite>-<run>.json and downloadable as Excel

Runs only happen where PSAGENT_TEST_ENV=on: test cases really save data.
"""

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from . import audit
from . import live as livemode
from . import xlsx
from .recorder import TASK_ID, snake

log = logging.getLogger("psagent")

EXPECT = ("saved", "process", "error")
EXPECT_LABEL = {"saved": "Saved", "process": "Process submitted", "error": "Error"}
MAX_CASES = 200
VERDICT = {"passed": "Passed", "changed": "Passed with changes", "failed": "Failed", "stopped": "Stopped"}
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_EMAIL = re.compile(r"[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}")


def default_expect(task):
    """What the recording ends with: expect_saved -> saved, expect_process -> process."""
    for st in reversed(task.playbook):
        if st.get("do") == "expect_process":
            return "process"
        if st.get("do") == "expect_saved":
            return "saved"
    return "saved"


def fill_n(value, n):
    if isinstance(value, list):
        return [fill_n(v, n) for v in value]
    return str(value).replace("{{n}}", str(n))


def _values(raw):
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    return {str(v.get("name")): v.get("value") for v in raw or [] if isinstance(v, dict)}


# ----- checking cases ---------------------------------------------------------------------

def check_case(raw, tasks):
    """One test case of Claude's draft -> (case, card, problems)."""
    problems = []
    title = str(raw.get("title") or "").strip()[:100]
    task = tasks.get(str(raw.get("task_id") or ""))
    case = {"id": snake(title, "case") if title else "", "title": title, "task": task.id if task else str(raw.get("task_id"))}
    if not title:
        problems.append("the case needs a name")
    if task is None:
        problems.append(f"there is no task {raw.get('task_id')!r}")
        return case, {"title": title, "task": case["task"], "task_title": "", "values": [], "expect": "", "problems": problems}, problems
    values, shown = {}, []
    for name, value in _values(raw.get("values")).items():
        if value in (None, "", []):
            continue
        f = task.fields.get(name)
        if f is None or f.sensitive:
            problems.append(f"{name!r} is not a field of {task.title}" + (" (passwords are typed when the run starts)" if f else ""))
            continue
        if f.type == "list" and not isinstance(value, list):
            value = [x.strip() for x in str(value).split(",") if x.strip()]
        _, err = f.normalize(fill_n(value, "0001"))
        if err:
            problems.append(f"{f.label}: {err}")
            continue
        values[name] = value if not isinstance(value, list) else [str(v) for v in value]
        shown.append({"label": f.label, "value": ", ".join(values[name]) if isinstance(values[name], list) else str(values[name])})
    missing = task.missing({k: fill_n(v, "0001") for k, v in values.items()})
    expect = raw.get("expect") if raw.get("expect") in EXPECT else default_expect(task)
    message = str(raw.get("message") or "").strip()[:200]
    if expect != "error":
        problems += [f"{task.fields[m].label} is required" for m in missing]
    elif not message:
        problems.append("an Error case needs the expected message (a part of it)")
    case.update(values=values, expect=expect)
    if message:
        case["message"] = message
    if str(raw.get("notes") or "").strip():
        case["notes"] = str(raw["notes"]).strip()[:300]
    card = {"title": title, "task": task.id, "task_title": task.title, "values": shown,
            "expect": EXPECT_LABEL[expect] + (f': "{message}"' if expect == "error" else ""), "problems": problems}
    return case, card, problems


def check_suite(draft, tasks, who="", existing=None, document=None):
    """Clean and verify a whole test suite. -> (suite to save, card for the widget, problems for Claude)."""
    problems = []
    title = str(draft.get("title") or "").strip()[:80]
    suite_id = snake(title, "suite") if title else ""
    if not TASK_ID.fullmatch(suite_id):
        problems.append("the test suite needs a short name")
    rows = draft.get("cases") or []
    if not rows:
        problems.append("the test suite has no cases")
    cases, cards, seen = [], [], set()
    for n, raw in enumerate(rows[:MAX_CASES], 1):
        case, card, probs = check_case(raw, tasks)
        label = f"case {n} ({case['title'] or 'no name'})"
        problems += [f"{label}: {p}" for p in probs]
        if case["id"] in seen:
            problems.append(f"{label}: two cases are called {case['title']!r}")
        seen.add(case["id"])
        card["n"] = n
        cases.append(case)
        cards.append(card)
    suite = {"id": suite_id, "title": title, "summary": str(draft.get("summary") or "").strip()[:300],
             "source": {"by": who, "on": date.today().isoformat(), **({"document": document} if document else {})},
             "cases": cases}
    sched = str(draft.get("run_at") or "").strip()
    if sched:
        if _TIME.fullmatch(sched):
            suite["run_at"] = sched
        else:
            problems.append("Run unattended at must be HH:MM (24 hours)")
    if draft.get("after_patch") is True:
        suite["after_patch"] = True
    notify = [str(a).strip().lower() for a in draft.get("notify") or [] if str(a).strip()]
    problems += [f"{a!r} is not an email address" for a in notify if not _EMAIL.fullmatch(a)]
    suite["notify"] = [a for a in notify if _EMAIL.fullmatch(a)]
    card = {"id": suite_id, "title": title, "summary": suite["summary"], "cases": cards, "source": document or "",
            "run_at": suite.get("run_at", ""), "after_patch": bool(suite.get("after_patch")), "notify": suite["notify"],
            "questions": [str(q)[:300] for q in draft.get("questions") or []][:10]}
    if existing:
        before = {c["id"]: c for c in existing.get("cases") or []}
        after = {c["id"]: c for c in cases}
        card["changes"] = {"added": [after[i]["title"] for i in after if i not in before],
                           "removed": [before[i]["title"] for i in before if i not in after],
                           "changed": [after[i]["title"] for i in after if i in before and before[i] != after[i]],
                           "unchanged": len([i for i in after if i in before and before[i] == after[i]])}
    return suite, card, problems


def _added_keys(task):
    """Fields that are the key of a record the task adds: a new value each run is right for them. That is the keys of
    an open step in add mode, or what is typed on the Add page of a navigate step with Action=A before Add is pressed."""
    out, adding = set(), False
    for st in task.playbook:
        if st.get("do") == "open" and st.get("mode") == "add":
            for k in st.get("keys") or []:
                out |= set(re.findall(r"\{\{(\w+)\}\}", str(k.get("value"))))
        elif st.get("do") == "navigate":
            adding = str((st.get("query") or {}).get("Action", "")).upper() == "A"
        elif st.get("do") == "set" and adding:
            out |= set(re.findall(r"\{\{(\w+)\}\}", str(st.get("value"))))
        else:
            adding = False
    return out


def smoke_suite(tasks):
    """Every task once, with its defaults. A required field without a default gets a {{n}} value only when it is the
    key of a record the task adds (a new user ID) or free text (a description); otherwise the task needs a test case
    in the template. -> (suite, skipped)."""
    cases, skipped = [], []
    for t in tasks.values():
        values, keys = {}, _added_keys(t)
        for n, f in t.fields.items():
            if f.sensitive or not f.required or f.default:
                continue
            if n in keys and f.type != "list" and not f.choices:
                v = "RT{{n}}"
            elif not f.upper and f.type != "list" and not f.choices and not f.pattern:
                v = "Regression test {{n}}"
            else:
                skipped.append(f"{t.title}: {f.label} needs a real value (add a case in the template)")
                values = None
                break
            values[n] = v[:f.max_length] if f.max_length else v
        if values is not None:
            cases.append({"id": snake(t.title, "case"), "title": f"{t.title} (smoke)", "task": t.id, "values": values,
                          "expect": default_expect(t)})
    return {"id": "smoke_test", "title": "Smoke test", "summary": "Every task once, with its defaults.",
            "source": {"by": "agent", "on": date.today().isoformat()}, "cases": cases}, skipped


# ----- the Excel template -----------------------------------------------------------------

def _columns(task):
    return [(n, f) for n, f in task.fields.items() if not f.sensitive]


def to_xlsx(suite, tasks):
    """The suite as the test plan template (a Suite sheet, then one sheet per task): download, edit, upload again.
    Tasks without cases get a sheet too, so new cases can be added."""
    suite_rows = [["Test suite", suite.get("title", "")], ["Summary", suite.get("summary", "")],
                  ["Run unattended at", suite.get("run_at", "")], ["Run after a patch", "Yes" if suite.get("after_patch") else "No"],
                  ["Email the report to", ", ".join(suite.get("notify") or [])], [],
                  ["How to fill"],
                  ["One sheet per task. One row per test case: its name, the values to use, the expected result."],
                  ["{{n}} in a value becomes a new number on every run (e.g. TEST{{n}} -> TEST0412), so cases that create data "
                   "don't collide with the last run's."],
                  ["Expected result: Saved, Process submitted, or Error with the Expected message (a part of PeopleSoft's "
                   "message, e.g. already exists) for negative tests. Empty = what the task normally ends with."],
                  ["Passwords are not in the sheet: they are typed when the run starts (or come from the service settings "
                   "for unattended runs). Fields marked * are required."],
                  ["Run unattended at (HH:MM) and Run after a patch need the headless runner on the agent service."]]
    sheets = [{"name": "Suite", "rows": suite_rows, "widths": [22, 100], "title_rows": [6]}]
    by_task = {}
    for c in suite.get("cases") or []:
        by_task.setdefault(c["task"], []).append(c)
    for tid, t in tasks.items():
        cols = _columns(t)
        head = ["Test case"] + [f"{f.label}{' *' if f.required and not f.default else ''} ({n})" for n, f in cols] + [
            "Expected result", "Expected message", "Notes"]
        rows = [["Task", t.id, t.title], [], head]
        for c in by_task.get(tid, []):
            vals = c.get("values") or {}
            rows.append([c["title"]] + [", ".join(vals[n]) if isinstance(vals.get(n), list) else vals.get(n, "") for n, _ in cols]
                        + [EXPECT_LABEL.get(c.get("expect"), ""), c.get("message", ""), c.get("notes", "")])
        sheets.append({"name": tid[:31], "rows": rows, "widths": [28] + [18] * len(cols) + [18, 28, 30], "header": 2,
                       "title_rows": [0], "lists": [(len(cols) + 1, list(EXPECT_LABEL.values()))]})
    return xlsx.write(sheets)


def template_xlsx(tasks):
    return to_xlsx(smoke_suite(tasks)[0] | {"title": "My regression tests", "summary": "", "id": "my_regression_tests"}, tasks)


def report_xlsx(report):
    rows = [["Regression run", report["title"]], ["Started", report["started"]], ["Ended", report.get("ended", "")],
            ["Run by", report["by"] + (" (unattended)" if report.get("mode") == "server" else "")],
            ["Result", report["headline"]], [],
            ["#", "Test case", "Task", "Expected", "Result", "Details", "Last run", "Seconds"]]
    for r in report["results"]:
        rows.append([r["n"], r["title"], r["task_title"], r["expect"], VERDICT.get(r["verdict"], r["verdict"]),
                     "\n".join([r.get("detail", "")] + r.get("changes", [])).strip(), VERDICT.get(r.get("previous"), r.get("previous") or ""),
                     r.get("seconds", "")])
    return xlsx.write([{"name": "Results", "rows": rows, "widths": [5, 34, 26, 22, 18, 70, 16, 9], "header": 6, "title_rows": [0]}])


# ----- saved suites and runs -----------------------------------------------------------------

class Store:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.suites = {}
        for path in sorted(self.dir.glob("*.yaml")) if self.dir.is_dir() else []:
            try:
                s = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(s, dict) and TASK_ID.fullmatch(str(s.get("id"))) and isinstance(s.get("cases"), list):
                    self.suites[s["id"]] = s
            except (OSError, yaml.YAMLError) as e:
                log.warning("test suite %s skipped: %s", path.name, e)
        self._lock = threading.Lock()

    def save(self, suite):
        self.dir.mkdir(parents=True, exist_ok=True)
        src = suite.get("source") or {}
        head = (f"# Regression test suite set up in the agent by {src.get('by')} on {src.get('on')}"
                + (f" from {src['document']}" if src.get("document") else "") + ".\n"
                "# Each case runs a task with these values and checks the result. Test environments only.\n")
        tmp = self.dir / f"{suite['id']}.yaml.tmp"
        tmp.write_text(head + yaml.safe_dump(suite, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
        tmp.replace(self.dir / f"{suite['id']}.yaml")
        with self._lock:
            self.suites = {**self.suites, suite["id"]: suite}

    def delete(self, suite_id):
        path = self.dir / f"{suite_id}.yaml"
        if path.exists():
            path.unlink()
        with self._lock:
            self.suites = {k: v for k, v in self.suites.items() if k != suite_id}

    def next_number(self):
        """The {{n}} of a new run: 4 digits, one more than the last."""
        path = self.dir / "counter.txt"
        with self._lock:
            try:
                n = int(path.read_text().strip()) + 1
            except (OSError, ValueError):
                n = 1
            self.dir.mkdir(parents=True, exist_ok=True)
            path.write_text(str(n))
        return f"{n:04d}"

    def save_report(self, report):
        d = self.dir / "runs"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{report['suite']}-{report['run_id']}.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")

    def reports(self, suite_id=None, limit=20):
        d = self.dir / "runs"
        out = []
        for p in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []:
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if suite_id is None or r.get("suite") == suite_id:
                out.append(r)
            if len(out) >= limit:
                break
        return out

    def report(self, run_id):
        d = self.dir / "runs"
        for p in d.glob(f"*-{run_id}.json") if d.is_dir() else []:
            return json.loads(p.read_text(encoding="utf-8"))
        return None


# ----- running ------------------------------------------------------------------------------

@dataclass
class TestRun:
    run_id: str
    suite: dict
    owner: str
    n: str
    mode: str = "browser"                         # browser | server
    index: int = 0                                # the case being run
    results: list = field(default_factory=list)
    started: float = field(default_factory=time.time)
    case_started: float = 0.0
    action: dict | None = None                    # the current case's steps, as handed to the runner
    values: dict = field(default_factory=dict)    # the current case's values, {{n}} filled in
    live: object | None = None                    # live.LiveRun finishing the current case after a page change
    changes: list = field(default_factory=list)   # what live recovery did differently, for the current case
    done: bool = False
    report: dict | None = None


class Runner:
    """The run engine: one case at a time, results judged here, the report kept in the store."""

    def __init__(self, store, tasks_ref, operator=None, on_report=None):
        self.store, self.tasks_ref, self.operator, self.on_report = store, tasks_ref, operator, on_report
        self.runs = {}
        self._lock = threading.Lock()

    def start(self, suite, owner, mode="browser"):
        run = TestRun(run_id=f"rt-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}", suite=suite, owner=owner,
                      n=self.store.next_number(), mode=mode)
        with self._lock:
            for k in [k for k, r in self.runs.items() if r.done and time.time() - r.started > 6 * 3600]:
                del self.runs[k]
            self.runs[run.run_id] = run
        log.info("regression run %s of %s started by %s (%s, n=%s)", run.run_id, suite["id"], owner, mode, run.n)
        return run

    def get(self, run_id, owner):
        run = self.runs.get(run_id)
        return run if run and run.owner == owner else None

    def secrets(self, suite):
        """Password fields the suite's tasks need, typed once when the run starts."""
        tasks, out, seen = self.tasks_ref(), [], set()
        for c in suite["cases"]:
            t = tasks.get(c["task"])
            for s in t.secrets() if t else []:
                if s["name"] not in seen:
                    seen.add(s["name"])
                    out.append(s)
        return out

    def next(self, run):
        """-> {"case": {...action}} or {"done": True, "report": ...}"""
        if run.done:
            return {"done": True, "report": run.report}
        tasks = self.tasks_ref()
        while run.index < len(run.suite["cases"]):
            case = run.suite["cases"][run.index]
            task = tasks.get(case["task"])
            if task is None:
                self._record(run, case, None, "failed", f"the task {case['task']} no longer exists")
                continue
            values, err = {}, None
            for name, raw in (case.get("values") or {}).items():
                f = task.fields.get(name)
                if f is None:
                    err = f"the task has no field {name} any more"
                    break
                v, e = f.normalize(fill_n(raw, run.n))
                if e:
                    err = e
                    break
                values[name] = v
            if err:
                self._record(run, case, task, "failed", err)
                continue
            run.case_started, run.live, run.changes = time.time(), None, []
            run.values = values
            run.action = {"run_id": f"{run.run_id}:{run.index + 1}", "task_id": task.id, "title": task.title,
                          "component": task.component, "steps": task.render(values)}
            return {"case": {"n": run.index + 1, "of": len(run.suite["cases"]), "title": case["title"], "task": task.title,
                             "expect": EXPECT_LABEL[case["expect"]], "values": task.summary_rows(values), "action": run.action}}
        return {"done": True, "report": self._finish(run)}

    def result(self, run, n, res):
        """The runner's result for case n -> {"verdict": ...} or {"recover": {...}} (the page changed: finish it live)."""
        if run.done or n != run.index + 1 or run.action is None:
            return {"error": "that case is not the one running"}
        case = run.suite["cases"][run.index]
        task = self.tasks_ref().get(case["task"])
        status, message = res.get("status"), str(res.get("message") or "")[:1000]
        if status == "stopped":
            rec = self._record(run, case, task, "stopped", "Stopped by the user")
            self._finish(run)
            return {"verdict": rec, "stopped": True}
        if (status == "error" and res.get("mismatch") and case["expect"] != "error" and self.operator is not None
                and isinstance(res.get("failed_index"), int) and 0 <= res["failed_index"] < len(run.action["steps"])):
            i = res["failed_index"]
            run.live = livemode.recovery_run(task, task.summary_rows(run.values), run.action["steps"], i, message)
            run.changes = [f"Step {i + 1} ({livemode.describe_step(run.action['steps'][i])}) no longer matched the page: {message}"]
            return {"recover": {"run_id": run.live.run_id, "goal": f"Finish {task.title}"}}
        verdict, detail = judge(case, status, message, res.get("failed_step"))
        return {"verdict": self._record(run, case, task, verdict, detail)}

    def live_step(self, run, snapshot, result=None, approved=None):
        """One step of finishing the current case live. Save is approved at once (a test environment); a question
        fails the case. -> {"action": ...} for the runner, or {"verdict": ...} when it ended."""
        if run.live is None:
            return {"error": "this case is not being finished live"}
        case = run.suite["cases"][run.index]
        task = self.tasks_ref().get(case["task"])
        action = self.operator.step(run.live, snapshot, result, None, approved, None)
        kind = action.get("type")
        if kind == "do" and action.get("note"):
            run.changes.append(f"Live: {action['note']}")
        if kind == "ask":
            return {"verdict": self._record(run, case, task, "failed",
                                            f"The page changed and the agent needed an answer to go on: {action.get('question')}")}
        if kind == "done":
            run.changes.append(f"Finished live: {action.get('summary') or 'done'}")
            return {"verdict": self._record(run, case, task, "changed", f"Reached {EXPECT_LABEL[case['expect']]} after the page change")}
        if kind == "fail":
            return {"verdict": self._record(run, case, task, "failed",
                                            f"The page changed and finishing it live failed: {action.get('summary')}")}
        return {"action": action}

    def stop(self, run):
        if not run.done:
            case = run.suite["cases"][run.index] if run.index < len(run.suite["cases"]) else None
            if case:
                self._record(run, case, self.tasks_ref().get(case["task"]), "stopped", "Stopped by the user")
            self._finish(run)
        return run.report

    def _record(self, run, case, task, verdict, detail):
        r = {"n": run.index + 1, "id": case["id"], "title": case["title"], "task": case["task"],
             "task_title": task.title if task else case["task"], "expect": EXPECT_LABEL.get(case.get("expect"), ""),
             "verdict": verdict, "detail": detail, "changes": list(run.changes),
             "seconds": round(time.time() - run.case_started, 1) if run.case_started else 0}
        run.results.append(r)
        run.index += 1
        run.action, run.live, run.changes = None, None, []
        return r

    def _finish(self, run):
        if run.done:
            return run.report
        run.done = True
        previous = next(iter(self.store.reports(run.suite["id"], limit=1)), None)
        before = {r["id"]: r["verdict"] for r in (previous or {}).get("results", [])}
        counts = {v: 0 for v in VERDICT}
        for r in run.results:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            r["previous"] = before.get(r["id"])
        newly = [r["title"] for r in run.results if r["verdict"] == "failed" and before.get(r["id"]) in ("passed", "changed")]
        fixed = [r["title"] for r in run.results if r["verdict"] in ("passed", "changed") and before.get(r["id"]) == "failed"]
        total = len(run.results)
        headline = (f"{counts['passed']} of {total} passed" + (f", {counts['changed']} passed with changes" if counts["changed"] else "")
                    + (f", {counts['failed']} failed" if counts["failed"] else "") + (f", {counts['stopped']} stopped" if counts["stopped"] else ""))
        run.report = {"run_id": run.run_id, "suite": run.suite["id"], "title": run.suite["title"], "by": run.owner, "mode": run.mode,
                      "n": run.n, "started": datetime.fromtimestamp(run.started).strftime("%Y-%m-%d %H:%M:%S"),
                      "ended": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "counts": counts, "headline": headline,
                      "newly_failing": newly, "fixed": fixed, "previous_run": (previous or {}).get("run_id"),
                      "previous_started": (previous or {}).get("started"), "results": run.results}
        try:
            self.store.save_report(run.report)
        except OSError as e:
            log.warning("regression report %s not saved: %s", run.run_id, e)
        log.info("regression run %s of %s: %s", run.run_id, run.suite["id"], headline)
        audit.record("regression.run", f"{run.suite['title']}: {headline}" + (" (unattended)" if run.mode == "server" else ""),
                     "failed" if counts.get("failed") else "ok", run.run_id,
                     {"newly_failing": run.report.get("newly_failing"), "fixed": run.report.get("fixed")},
                     oprid=run.owner, feature="regression")
        if self.on_report:
            try:
                self.on_report(run.report, run.suite)
            except Exception:       # the report is saved either way
                log.exception("regression report %s: sending failed", run.run_id)
        return run.report


def judge(case, status, message, failed_step=None):
    """The runner's result against the case's expected result -> (verdict, detail)."""
    where = f" at {failed_step}" if failed_step else ""
    if case["expect"] == "error":
        want = case.get("message", "")
        if status == "success":
            return "failed", f'Expected an error containing "{want}", but it went through: {message}'
        if want.lower() in message.lower():
            return "passed", f"PeopleSoft said: {message}"
        return "failed", f'Expected an error containing "{want}"; PeopleSoft said{where}: {message}'
    if status == "success":
        return "passed", message or EXPECT_LABEL[case["expect"]]
    return "failed", f"Stopped{where}: {message}"
