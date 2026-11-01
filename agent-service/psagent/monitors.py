"""Monitors ("watchdogs"): what the watcher (watcher.py) checks on its own, day and night.

Watchdogs are saved in watch plans: monitors/<plan>.yaml holds a plan's title and its
watchdogs. A plan comes from the watch plan template (Excel, one row per watchdog)
uploaded in the chat, or from one watchdog set up in the chat; users with the
PSAGENT_MONITOR or PSAGENT_ADMIN role do this. Claude reads the sheet (draft_watch_plan,
draft_monitor), the service checks every watchdog against the system and shows what it
would have caught, and Confirm writes the plan. A saved plan downloads as the same
template, to edit and upload again. Four kinds:

  failures   process requests that ended in Error or No Success, optionally only
             some processes (PRCSNAME, % = any characters) or some users' requests;
             each failure is investigated by the troubleshooter
  stuck      requests still Queued, Pending or Initiated N minutes after their run time
  deadline   a process must have ended in Success in the 24 hours before a time of day
             (catches a job that never ran, as well as one that failed or is stuck)
  check      a read-only SELECT that returns the rows that are WRONG (like a runbook
             check), run every N minutes or at set times of day

Everything a watchdog does is read-only. Alerts go to the agent panel and, when
email is configured, to the watchdog's recipients with the analysis and the action needed.
"""

import logging
import re
from datetime import date
from pathlib import Path

import yaml

from . import xlsx
from .db import QueryError, check_select
from .recorder import TASK_ID, snake

log = logging.getLogger("psagent")

KINDS = ("failures", "stuck", "deadline", "check")
EXPECT = ("no_rows", "rows")
FAILED = ("3", "10")                 # Error, No Success (Cancelled is a person's choice)
STUCK = ("5", "6", "16")             # Queued, Initiated, Pending
SUCCESS = ("9", "17")                # Success, Success With Warning
MAX_PATTERNS = 20
MAX_WATCHDOGS = 40
PREVIEW_DAYS = 30
_PRCSNAME = re.compile(r"[A-Z0-9_$#%-]{1,30}")
_OPRID = re.compile(r"[A-Z0-9_.@-]{1,30}")
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_EMAIL = re.compile(r"[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}")


class MonitorError(ValueError):
    pass


# ----- the SQL each kind runs -------------------------------------------------------

def request_filter(monitor):
    """-> (extra WHERE text, binds) for the monitor's processes and users."""
    where, binds = "", {}
    pats = monitor.get("processes") or []
    if pats:
        where += " AND (" + " OR ".join(f"R.PRCSNAME LIKE :p{i}" for i in range(len(pats))) + ")"
        binds.update({f"p{i}": p for i, p in enumerate(pats)})
    users = monitor.get("users") or []
    if users:
        where += " AND R.OPRID IN (" + ", ".join(f":u{i}" for i in range(len(users))) + ")"
        binds.update({f"u{i}": u for i, u in enumerate(users)})
    return where, binds


_COLS = "R.PRCSINSTANCE, R.PRCSTYPE, R.PRCSNAME, R.OPRID, R.RUNCNTLID, R.RUNSTATUS"


def failures_sql(monitor):
    """Failed requests that ended in the last :m minutes."""
    where, binds = request_filter(monitor)
    return (f"SELECT {_COLS}, NVL(R.ENDDTTM, R.LASTUPDDTTM) ENDED FROM PSPRCSRQST R WHERE R.RUNSTATUS IN "
            f"({', '.join(repr(s) for s in FAILED)}) AND NVL(R.ENDDTTM, R.LASTUPDDTTM) > SYSDATE - :m / 1440{where} "
            "ORDER BY R.PRCSINSTANCE"), binds


def stuck_sql(monitor):
    """Requests still waiting :n minutes after their run time."""
    where, binds = request_filter(monitor)
    return (f"SELECT {_COLS}, R.RUNDTTM, R.SERVERNAMERQST FROM PSPRCSRQST R WHERE R.RUNSTATUS IN "
            f"({', '.join(repr(s) for s in STUCK)}) AND R.RUNDTTM < SYSDATE - :n / 1440{where} "
            "ORDER BY R.PRCSINSTANCE"), binds


def latest_sql(monitor):
    """The process's latest requests, any status: what happened before a deadline was missed."""
    where, binds = request_filter(monitor)
    return (f"SELECT * FROM (SELECT {_COLS}, R.RUNDTTM, NVL(R.ENDDTTM, R.LASTUPDDTTM) ENDED FROM PSPRCSRQST R "
            f"WHERE 1 = 1{where} ORDER BY R.PRCSINSTANCE DESC) WHERE ROWNUM <= 5"), binds


def succeeded_sql(monitor):
    """Successful requests that ended in the last :m minutes."""
    where, binds = request_filter(monitor)
    return (f"SELECT R.PRCSINSTANCE, NVL(R.ENDDTTM, R.LASTUPDDTTM) ENDED FROM PSPRCSRQST R WHERE R.RUNSTATUS IN "
            f"({', '.join(repr(s) for s in SUCCESS)}) AND NVL(R.ENDDTTM, R.LASTUPDDTTM) > SYSDATE - :m / 1440{where} "
            "ORDER BY R.PRCSINSTANCE DESC"), binds


# ----- drafting one watchdog ------------------------------------------------------------

def check(draft, db=None, who="", email=False, default_to=()):
    """Clean and verify one watchdog of Claude's draft. -> (watchdog to save, card for the widget, problems for
    Claude). email: whether the service can send email; default_to: PSAGENT_ALERT_TO, used when it names nobody."""
    problems = []
    title = str(draft.get("title") or "").strip()[:80]
    mon_id = snake(title, "watchdog") if title else ""
    if not TASK_ID.fullmatch(mon_id):
        problems.append("the watchdog needs a short title")
    kind = draft.get("kind")
    if kind not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)}")
    mon = {"id": mon_id, "title": title, "kind": kind}
    if str(draft.get("summary") or "").strip():
        mon["summary"] = str(draft["summary"]).strip()[:300]

    pats, users = [], []
    for p in draft.get("processes") or []:
        p = str(p).strip().upper().replace("*", "%")
        if not p:
            continue
        if not _PRCSNAME.fullmatch(p):
            problems.append(f"process name {p!r} is not a Process Scheduler name (use % for any characters)")
        elif p not in pats:
            pats.append(p)
    for u in draft.get("users") or []:
        u = str(u).strip().upper()
        if u and not _OPRID.fullmatch(u):
            problems.append(f"user {u!r} is not a user ID")
        elif u and u not in users:
            users.append(u)
    if len(pats) > MAX_PATTERNS or len(users) > MAX_PATTERNS:
        problems.append(f"at most {MAX_PATTERNS} processes and {MAX_PATTERNS} users")
    if kind in ("failures", "stuck", "deadline"):
        mon["processes"], mon["users"] = pats[:MAX_PATTERNS], users[:MAX_PATTERNS]

    if kind == "stuck":
        mon["stuck_minutes"] = _int(draft.get("stuck_minutes"), 30, 5, 24 * 60)
    if kind in ("failures", "deadline"):
        mon["investigate"] = draft.get("investigate") is not False
    if kind == "deadline":
        deadline = str(draft.get("deadline") or "").strip() or next(iter(draft.get("at") or []), "")
        if len(pats) != 1 or "%" in pats[0]:
            problems.append("a 'must succeed by' watchdog names exactly one process (no %)")
        if not _TIME.fullmatch(deadline):
            problems.append("a 'must succeed by' watchdog needs the time, HH:MM (24 hours)")
        mon["deadline"] = deadline
        mon["at"] = [deadline]
    if kind == "check":
        sql = str(draft.get("sql") or "").strip()
        try:
            check_select(sql)
        except QueryError as e:
            problems.append(f"the check query is not allowed: {e}")
        if re.search(r"(?<!:):[A-Za-z_]\w*", re.sub(r"'[^']*'", "''", sql)):
            problems.append("a watchdog check has no inputs: use literals or SYSDATE, not :binds")
        expect = draft.get("expect") if draft.get("expect") in EXPECT else "no_rows"
        mon.update(sql=sql, expect=expect, rule=str(draft.get("rule") or "").strip()[:300])

    if kind != "deadline":
        at = sorted({t for t in (str(x).strip() for x in draft.get("at") or []) if t})
        bad = [t for t in at if not _TIME.fullmatch(t)]
        if bad:
            problems.append(f"times must be HH:MM (24 hours): {', '.join(bad)}")
        if at and kind != "check":
            problems.append("set times of day only for checks and 'must succeed by'; failures and stuck run every few minutes")
        if at and kind == "check":
            mon["at"] = at
        else:
            mon["every_minutes"] = _int(draft.get("every_minutes"), 5 if kind != "check" else 60, 1, 24 * 60)

    notify, warnings = [], []
    for a in draft.get("notify") or []:
        a = str(a).strip().lower()
        if not a:
            continue
        if not _EMAIL.fullmatch(a):
            problems.append(f"{a!r} is not an email address")
        elif a not in notify:
            notify.append(a)
    notify = notify[:MAX_PATTERNS]
    if not email:
        warnings.append("Email is not set up on the agent service (PSAGENT_SMTP_HOST): alerts show in the agent panel only")
    elif not notify and not default_to:
        warnings.append("No recipients: alerts show in the agent panel only")
    elif not notify:
        warnings.append(f"Alerts go to the default recipients ({', '.join(default_to)})")
    mon["notify"] = notify
    if draft.get("paused") is True:
        mon["paused"] = True
    if str(draft.get("notes") or "").strip():
        mon["notes"] = str(draft["notes"]).strip()[:300]
    mon["source"] = {"by": who, "on": date.today().isoformat()}

    card = {"id": mon_id, "title": title, "kind": kind, "summary": mon.get("summary", ""), "when": describe_when(mon),
            "what": describe_what(mon), "notify": notify, "warnings": warnings, "paused": bool(mon.get("paused"))}
    if not problems and kind in KINDS:
        if db is not None and kind != "check":
            missing = [p for p in pats if "%" not in p and not _defined(db, p, who)]
            problems += [f"process {p} is not defined in Process Scheduler" for p in missing]
        card["preview"] = preview(mon, db, who)
        if card["preview"].get("error"):
            problems.append(f"the watchdog's query failed: {card['preview']['error']}")
    return mon, card, problems


def _defined(db, name, who):
    try:
        _, rows, _ = db.query("SELECT PRCSTYPE FROM PS_PRCSDEFN WHERE PRCSNAME = :p", {"p": name}, max_rows=5, who=who,
                              why="monitor draft process")
    except QueryError:
        return True          # can't tell: the preview query reports real problems
    return bool(rows)


def _int(v, default, lo, hi):
    """A whole number within lo..hi; 0, empty or not a number means the default (the tool says "0 for the default")."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v)) if v else default


def describe_what(mon):
    procs = ", ".join(mon.get("processes") or []) or "any process"
    users = f" run by {', '.join(mon['users'])}" if mon.get("users") else ""
    if mon.get("kind") == "failures":
        return f"Failures (Error, No Success) of {procs}{users}" + (", each investigated" if mon.get("investigate") else "")
    if mon.get("kind") == "stuck":
        return f"Requests of {procs}{users} still Queued, Pending or Initiated {mon.get('stuck_minutes')} minutes after their run time"
    if mon.get("kind") == "deadline":
        return f"{procs}{users} must end in Success by {mon.get('deadline')} (in the 24 hours before)"
    return mon.get("rule") or "Rows returned by the check query"


def describe_when(mon):
    if mon.get("kind") == "deadline":
        return f"Every day at {mon.get('deadline')}"
    if mon.get("at"):
        return "Every day at " + ", ".join(mon["at"])
    n = mon.get("every_minutes", 5)
    return f"Every {n} minutes" if n != 1 else "Every minute"


def preview(mon, db, who):
    """What the watchdog would catch: failures in the last 30 days, what is stuck now, the days a deadline would
    have been missed, what the check returns now."""
    if db is None:
        return {"skipped": "no database access"}
    try:
        if mon["kind"] == "failures":
            sql, binds = failures_sql(mon)
            cols, rows, more = db.query(sql.replace("ORDER BY R.PRCSINSTANCE", "ORDER BY R.PRCSINSTANCE DESC"),
                                        {**binds, "m": PREVIEW_DAYS * 1440}, max_rows=200, who=who,
                                        why="monitor draft preview (failures)")
            n = f"{len(rows)}{'+' if more else ''}"
            return {"label": (f"Would have alerted {n} time{'s' if n != '1' else ''} in the last {PREVIEW_DAYS} days" if rows
                              else f"No failures in the last {PREVIEW_DAYS} days"),
                    "count": len(rows), "more": more, "columns": ["Instance", "Process", "User", "Status", "Ended"],
                    "sample": [[r[0], r[2], r[3], _status(r[5]), str(r[6])[:16]] for r in rows[:5]]}
        if mon["kind"] == "stuck":
            sql, binds = stuck_sql(mon)
            cols, rows, more = db.query(sql, {**binds, "n": mon["stuck_minutes"]}, max_rows=200, who=who,
                                        why="monitor draft preview (stuck)")
            n = f"{len(rows)}{'+' if more else ''}"
            return {"label": "1 request is stuck right now" if n == "1" else f"{n} requests are stuck right now",
                    "count": len(rows), "more": more, "columns": ["Instance", "Process", "User", "Status", "Run time"],
                    "sample": [[r[0], r[2], r[3], _status(r[5]), str(r[6])[:16]] for r in rows[:5]]}
        if mon["kind"] == "deadline":
            where, binds = request_filter(mon)
            _, days, _ = db.query("SELECT COUNT(DISTINCT TRUNC(NVL(R.ENDDTTM, R.LASTUPDDTTM))) FROM PSPRCSRQST R WHERE "
                                  f"R.RUNSTATUS IN ({', '.join(repr(s) for s in SUCCESS)}) AND "
                                  f"NVL(R.ENDDTTM, R.LASTUPDDTTM) > SYSDATE - {PREVIEW_DAYS}{where}", binds, max_rows=1,
                                  who=who, why="monitor draft preview (deadline)")
            ok = int(days[0][0] or 0) if days else 0
            sql, binds = latest_sql(mon)
            _, rows, _ = db.query(sql, binds, max_rows=5, who=who, why="monitor draft preview (latest runs)")
            return {"label": f"Succeeded on {ok} of the last {PREVIEW_DAYS} days: would have alerted on about "
                             f"{PREVIEW_DAYS - ok} days", "count": PREVIEW_DAYS - ok,
                    "columns": ["Instance", "Process", "User", "Status", "Ended"],
                    "sample": [[r[0], r[2], r[3], _status(r[5]), str(r[7])[:16]] for r in rows[:5]]}
        cols, rows, more = db.query(mon["sql"], {}, max_rows=5, who=who, why=f"monitor draft check ({mon['title'][:60]})")
        wrong = bool(rows) if mon["expect"] == "no_rows" else not rows
        return {"label": ("Would alert now" if wrong else "Passes now") + f": {len(rows)}{'+' if more else ''} rows",
                "count": len(rows), "more": more, "alerts_now": wrong, "columns": cols[:8], "sample": [r[:8] for r in rows[:3]]}
    except QueryError as e:
        return {"error": str(e)}


_STATUS = {"1": "Cancel", "2": "Delete", "3": "Error", "5": "Queued", "6": "Initiated", "7": "Processing", "8": "Cancelled",
           "9": "Success", "10": "No Success", "16": "Pending", "17": "Success With Warning"}


def _status(code):
    return _STATUS.get(str(code), str(code))


# ----- drafting a watch plan ------------------------------------------------------------

def check_plan(draft, db=None, who="", email=False, default_to=(), existing=None, document=None):
    """Clean and verify a whole watch plan (every row of the template). -> (plan to save, card, problems).
    existing: the saved plan with the same name, if any: the card then says what changes."""
    problems, warnings = [], []
    title = str(draft.get("title") or "").strip()[:80]
    plan_id = snake(title, "plan") if title else ""
    if not TASK_ID.fullmatch(plan_id):
        problems.append("the watch plan needs a short name")
    rows = draft.get("watchdogs") or []
    if not rows:
        problems.append("the watch plan has no watchdogs")
    if len(rows) > MAX_WATCHDOGS:
        problems.append(f"at most {MAX_WATCHDOGS} watchdogs in one plan")
    dogs, cards, seen = [], [], set()
    for n, raw in enumerate(rows[:MAX_WATCHDOGS], 1):
        mon, card, probs = check(raw, db, who, email, default_to)
        label = f"row {n} ({mon['title'] or 'no name'})"
        problems += [f"{label}: {p}" for p in probs]
        if mon["id"] in seen:
            problems.append(f"{label}: two watchdogs are called {mon['title']!r}; give each its own name")
        seen.add(mon["id"])
        mon.pop("source", None)
        warnings += [w for w in card.pop("warnings") if w not in warnings]
        card.update(n=n, problems=probs)
        dogs.append(mon)
        cards.append(card)
    plan = {"id": plan_id, "title": title, "summary": str(draft.get("summary") or "").strip()[:300],
            "source": {"by": who, "on": date.today().isoformat(), **({"document": document} if document else {})},
            "watchdogs": dogs}
    card = {"id": plan_id, "title": title, "summary": plan["summary"], "watchdogs": cards, "warnings": warnings,
            "questions": [str(q)[:300] for q in (draft.get("questions") or [])][:10], "source": document or ""}
    if existing:
        card["changes"] = diff(existing, plan)
    return plan, card, problems


_COMPARED = ("kind", "processes", "users", "stuck_minutes", "deadline", "at", "every_minutes", "sql", "rule", "expect",
             "notify", "paused", "investigate")


def diff(old, new):
    """What a re-uploaded plan changes: watchdogs added, removed and changed (by name)."""
    before = {d["id"]: d for d in old.get("watchdogs") or []}
    after = {d["id"]: d for d in new.get("watchdogs") or []}
    changed = []
    for i in after.keys() & before.keys():
        fields = [k for k in _COMPARED if before[i].get(k) != after[i].get(k)]
        if fields:
            changed.append({"title": after[i]["title"], "fields": fields})
    return {"added": [after[i]["title"] for i in after if i not in before],
            "removed": [before[i]["title"] for i in before if i not in after],
            "changed": sorted(changed, key=lambda c: c["title"]),
            "unchanged": len([i for i in after.keys() & before.keys()]) - len(changed)}


def single_plan(mon):
    """A watchdog set up in the chat is saved as a plan of its own."""
    dog = {k: v for k, v in mon.items() if k != "source"}
    return {"id": mon["id"], "title": mon["title"], "summary": mon.get("summary", ""), "source": mon.get("source", {}),
            "watchdogs": [dog]}


# ----- saved plans ------------------------------------------------------------------------

def to_yaml(plan):
    src = plan.get("source") or {}
    head = (f"# Watch plan set up in the agent by {src.get('by')} on {src.get('on')}"
            + (f" from {src['document']}" if src.get("document") else "") + ".\n"
            "# The watcher runs its watchdogs on its own; everything they do is read-only.\n")
    return head + yaml.safe_dump(plan, sort_keys=False, allow_unicode=True, width=120)


def _valid_dog(d):
    if not isinstance(d, dict) or d.get("kind") not in KINDS or not TASK_ID.fullmatch(str(d.get("id"))):
        raise MonitorError("not a watchdog")
    if d["kind"] == "check":
        check_select(d.get("sql") or "")
    if d["kind"] == "deadline" and not _TIME.fullmatch(str(d.get("deadline"))):
        raise MonitorError("a 'must succeed by' watchdog without its time")


def load_plans(directory):
    """Saved plans (monitors/*.yaml). A file with a single monitor (saved before plans) counts as a plan of one.
    Broken files are skipped."""
    out = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "watchdogs" not in data:
                _valid_dog(data)
                data = single_plan(data)
            if not isinstance(data, dict) or not TASK_ID.fullmatch(str(data.get("id"))) or not data.get("watchdogs"):
                raise MonitorError("not a watch plan")
            for d in data["watchdogs"]:
                _valid_dog(d)
        except (OSError, yaml.YAMLError, MonitorError, QueryError) as e:
            log.warning("watch plan %s skipped: %s", path.name, e)
            continue
        if path.stem != data["id"]:
            log.warning("watch plan %s skipped: its id is %s", path.name, data["id"])
            continue
        out[data["id"]] = data
    return out


def flatten(plans):
    """Every watchdog of every plan, keyed "<plan>.<watchdog>", with plan and wd telling where it belongs."""
    out = {}
    for pid, plan in plans.items():
        for d in plan["watchdogs"]:
            out[f"{pid}.{d['id']}"] = {**d, "id": f"{pid}.{d['id']}", "plan": pid, "wd": d["id"],
                                       "plan_title": plan["title"]}
    return out


def save_plan(directory, plan):
    path = Path(directory) / f"{plan['id']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(to_yaml(plan), encoding="utf-8")
    tmp.replace(path)
    return path


def delete_plan(directory, plan_id):
    path = Path(directory) / f"{plan_id}.yaml"
    if path.exists():
        path.unlink()


# ----- the Excel template -----------------------------------------------------------------

COLUMNS = [("Watchdog", 30), ("Process", 14), ("Watch for", 16), ("Schedule", 17), ("Time", 8),
           ("Stuck after (minutes)", 11), ("Run by (user IDs)", 14), ("Rule (data checks)", 34),
           ("Query (data checks, optional)", 40), ("Email", 30), ("Status", 9), ("Notes", 40)]
WATCH_FOR = {"failures": "Failure", "stuck": "Stuck", "deadline": "Must succeed by", "check": "Data check"}
SCHEDULES = ["Every 5 minutes", "Every 10 minutes", "Every 15 minutes", "Every 30 minutes", "Every hour", "Daily at"]
STATUSES = ["Active", "Paused"]

SAMPLE = {
    "title": "Nightly jobs",
    "summary": "Batch jobs the support team watches overnight. Change the rows, add your own, upload it in the agent.",
    "watchdogs": [
        {"title": "AE test program failures", "kind": "failures", "processes": ["AEMINITEST"], "every_minutes": 5,
         "notify": ["ops@example.com"], "notes": "Application Engine. Each failure is investigated and emailed with the fix."},
        {"title": "Cross-reference report failures", "kind": "failures", "processes": ["XRFWIN"], "every_minutes": 5,
         "notify": ["ops@example.com"], "notes": "SQR report (also defined as XML Publisher)."},
        {"title": "Cross-reference report stuck", "kind": "stuck", "processes": ["XRFWIN"], "every_minutes": 5,
         "stuck_minutes": 30, "notify": ["ops@example.com"], "notes": "Still waiting 30 minutes after its run time."},
        {"title": "Portal security sync by 07:00", "kind": "deadline", "processes": ["PORTAL_CSS"], "deadline": "07:00",
         "notify": ["security@example.com"], "notes": "Alerts if it did not end in Success in the 24 hours before 07:00, "
                                                      "including when it never ran."},
    ],
}

HELP = [
    ["How to fill the watch plan"],
    [],
    ["One row is one watchdog. Fill the name of the plan at the top, one row per thing to watch, then upload the file "
     "in the agent (＋ Features > Watch jobs). The agent checks every row against PeopleSoft and shows what each "
     "watchdog would have caught before you confirm. Everything a watchdog does is read-only."],
    [],
    ["Column", "What to put", "Example"],
    ["Watchdog", "A short name. Each row needs its own.", "Payroll calc failures"],
    ["Process", "The process name in Process Scheduler (Process Monitor shows it). % = any characters. "
                "Empty for data checks; empty with Failure or Stuck = every process.", "AEMINITEST, PSP%"],
    ["Watch for", "Failure: the process ends in Error or No Success; each failure is investigated and emailed with the "
                  "analysis and the action needed.\nStuck: still Queued, Pending or Initiated N minutes after its run time.\n"
                  "Must succeed by: it must have ended in Success in the 24 hours before Time (also catches a job that "
                  "never ran).\nData check: a rule the data must meet.", "Failure"],
    ["Schedule", "How often it looks. Failure and Stuck: every few minutes. Data check: every N minutes, or Daily at "
                 "Time. Must succeed by: checked daily at Time.", "Every 5 minutes"],
    ["Time", "HH:MM, 24 hours: the deadline of Must succeed by, or when Daily at runs. Several times: 06:00, 18:00.", "07:00"],
    ["Stuck after (minutes)", "Stuck only: minutes after the run time.", "30"],
    ["Run by (user IDs)", "Optional: only requests run by these users.", "PS, BATCHUSER"],
    ["Rule (data checks)", "Data check only: the rule in words. The agent writes the query if Query is empty.",
     "No user account is locked"],
    ["Query (data checks, optional)", "A read-only SELECT that returns the rows that break the rule.",
     "SELECT OPRID FROM PSOPRDEFN WHERE ACCTLOCK = 1"],
    ["Email", "Who gets the alert, comma separated. Empty = the team's default address.", "ops@example.com"],
    ["Status", "Active, or Paused to keep the row without running it.", "Active"],
    ["Notes", "Anything for the people reading the plan.", ""],
    [],
    ["To change a saved plan: in the agent, open the watchdogs (the ◉ count at the bottom of the chat), Download as "
     "Excel, edit it and upload it again. The agent shows what changes before you confirm."],
]


def _row(d):
    sched, time = "", ""
    if d.get("kind") == "deadline":
        sched, time = "Daily at", d.get("deadline", "")
    elif d.get("at"):
        sched, time = "Daily at", ", ".join(d["at"])
    else:
        n = d.get("every_minutes", 5)
        sched = "Every hour" if n == 60 else f"Every {n} minutes" if n != 1 else "Every minute"
    return [d.get("title", ""), ", ".join(d.get("processes") or []), WATCH_FOR.get(d.get("kind"), ""), sched, time,
            d.get("stuck_minutes", "") if d.get("kind") == "stuck" else "", ", ".join(d.get("users") or []),
            d.get("rule", ""), d.get("sql", ""), ", ".join(d.get("notify") or []),
            "Paused" if d.get("paused") else "Active", d.get("notes", "")]


def to_xlsx(plan):
    """The plan as the watch plan template, filled in: download, edit, upload again."""
    rows = [["Watch plan", plan.get("title", "")], ["Summary", plan.get("summary", "")], [],
            [c for c, _ in COLUMNS]] + [_row(d) for d in plan.get("watchdogs") or []]
    lists = [(2, list(WATCH_FOR.values())), (3, SCHEDULES), (10, STATUSES)]
    return xlsx.write([
        {"name": "Watch plan", "rows": rows, "widths": [w for _, w in COLUMNS], "header": 3, "title_rows": [0], "lists": lists},
        {"name": "How to fill", "rows": HELP, "widths": [26, 90, 34], "header": 4, "title_rows": [0], "freeze": False},
    ])


def template_xlsx():
    return to_xlsx(SAMPLE)
