"""Audit log and usage: every Claude call, database query, task run, save and feature event, in one SQLite file
(PSAGENT_AUDIT_DIR/psagent.db), for the admin dashboard (PeopleSoft role PSAGENT_AUDITOR).

What is kept: who, when, which feature, what happened and how it ended; for Claude calls the tokens and the
estimated cost; for queries the SQL and the row count; for task runs the confirmed values (passwords never reach
the service). Not kept: what users type in the chat, or Claude's answers.

Recording is global, like logging: install() an Audit once, then record() / claude() / query() from anywhere.
Who and which feature come from the request's context (enter(), scope()); without an installed Audit they do
nothing. A failure to write never stops the agent: it is logged once.

  PSAGENT_AUDIT_DIR      where psagent.db is kept (default agent-service/audit)
  PSAGENT_AUDIT_DAYS     how long events are kept (default 90)
  PSAGENT_PRICES         price overrides, USD per million tokens: model=input/output/cache write/cache read;...
  PSAGENT_BUDGET         monthly Claude budget in USD: email at 80% and 100% (needs SMTP)
  PSAGENT_AUDIT_TO       who gets the budget alerts and the weekly usage email (default PSAGENT_ALERT_TO)
  PSAGENT_AUDIT_WEEKLY   when the weekly usage email goes out (default "Mon 08:00"; empty = never)
"""

import contextlib
import contextvars
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

try:
    import sqlite3
except ImportError:                    # a Python built without SQLite (PeopleSoft's on Linux): pip install pysqlite3-binary
    import pysqlite3 as sqlite3

log = logging.getLogger("psagent")

# USD per million tokens: input, output, cache write (5 minutes), cache read. Anthropic's list prices on
# 2026-10-06; the invoice is the real figure.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-fable-5-1": (10.0, 50.0, 12.5, 0.25),
}
FEATURES = {
    "chat": "Chat", "tasks": "Tasks", "live": "Live mode", "troubleshoot": "Troubleshooter", "security": "Security access",
    "approvals": "Absence approvals", "recorder": "Recorder", "runbooks": "Runbooks", "watcher": "Watcher",
    "regression": "Regression tests", "ci": "Excel to CI", "data": "Ask the data", "audit": "Audit", "system": "System",
}
SYSTEM_USERS = ("PSAGENT_WATCH", "PSAGENT_REGRESS", "PSAGENT")   # the service's own work, not a person
FAILED = ("failed", "error")
# kinds of events for the audit log's filter
KINDS = {"actions": "event NOT IN ('claude', 'query')", "queries": "event = 'query'", "claude": "event = 'claude'",
         "all": "1 = 1"}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_ctx = contextvars.ContextVar("psagent_audit", default=None)
_sink = None


def install(audit):
    """Make `audit` the one that record() writes to (None: stop recording)."""
    global _sink
    _sink = audit


def current():
    return _sink


def enter(oprid, feature, session=""):
    """Who and which feature, for the rest of this request (its threads included)."""
    _ctx.set({"oprid": oprid or "", "feature": feature or "chat", "session": session or ""})


def bind(**kw):
    """Change the context's feature or session from here on (e.g. once the chat session is known). Outside a request
    or scope() it does nothing, so nothing lingers on a thread."""
    if _ctx.get() is None:
        return
    c = dict(_ctx.get())
    c.update({k: v for k, v in kw.items() if v is not None})
    _ctx.set(c)


@contextlib.contextmanager
def scope(oprid, feature, session=""):
    """Who and which feature for a block: the watcher's rounds, the regression scheduler."""
    token = _ctx.set({"oprid": oprid, "feature": feature, "session": session})
    try:
        yield
    finally:
        _ctx.reset(token)


def record(event, detail="", outcome="ok", ref="", data=None, **kw):
    """One event. kw: oprid / feature / session (else from the context), ms, n."""
    if _sink is not None:
        _sink.add(event, detail, outcome, ref, data, **kw)


def claude(model, usage, seconds, error=None):
    """A Claude call: its tokens and estimated cost (usage: the response's usage, or None on an error)."""
    if _sink is None:
        return
    u = {k: int(getattr(usage, k, 0) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                     "cache_read_input_tokens")} if usage is not None else {}
    _sink.add("claude", (str(error)[:300] if error else model), "error" if error else "ok", "", None,
              ms=int(seconds * 1000), model=model, tin=u.get("input_tokens", 0), tout=u.get("output_tokens", 0),
              tcw=u.get("cache_creation_input_tokens", 0), tcr=u.get("cache_read_input_tokens", 0),
              cost=_sink.cost(model, u))


def query(who, why, sql, rows, seconds, error=None):
    """A read-only database query: why, the SQL, how many rows, how long."""
    if _sink is None:
        return
    oprid = (_ctx.get() or {}).get("oprid") or who
    _sink.add("query", (why or "")[:300], "error" if error else "ok", "",
              {"sql": " ".join(str(sql).split())[:4000], **({"error": str(error)[:300]} if error else {}),
               **({"as": who} if who and who != oprid else {})},
              oprid=oprid, ms=int(seconds * 1000), n=rows)


def parse_prices(text):
    """"claude-opus-5-5=4/20/5/0.2;..." -> {model: (input, output, cache write, cache read)}"""
    out = {}
    for part in str(text or "").split(";"):
        if "=" not in part:
            continue
        model, nums = part.split("=", 1)
        try:
            p = tuple(float(x) for x in nums.split("/"))
        except ValueError:
            log.warning("PSAGENT_PRICES: %r is not model=input/output/cache write/cache read", part)
            continue
        if len(p) == 2:
            p += (round(p[0] * 1.25, 6), round(p[0] * 0.1, 6))
        if len(p) == 4:
            out[model.strip()] = p
    return out


def _day(ts):
    return ts.strftime("%Y-%m-%d")


class Audit:
    COLUMNS = ("ts", "oprid", "feature", "event", "detail", "outcome", "ref", "session", "ms", "n", "model",
               "tin", "tout", "tcw", "tcr", "cost", "data")

    def __init__(self, directory, days=90, prices=None):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "psagent.db"
        self.days = max(1, int(days or 90))
        self.prices = {**PRICES, **(prices or {})}
        self._lock = threading.Lock()
        self._warned = False
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")   # with WAL: safe, and no disk flush per event
            self._db.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, oprid TEXT, feature TEXT, event TEXT,
                detail TEXT, outcome TEXT, ref TEXT, session TEXT, ms INTEGER, n INTEGER, model TEXT,
                tin INTEGER DEFAULT 0, tout INTEGER DEFAULT 0, tcw INTEGER DEFAULT 0, tcr INTEGER DEFAULT 0,
                cost REAL DEFAULT 0, data TEXT)""")
            for col in ("ts", "oprid", "feature", "event"):
                self._db.execute(f"CREATE INDEX IF NOT EXISTS events_{col} ON events ({col})")
            self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            self._db.commit()

    # ----- writing ------------------------------------------------------------------------------
    def cost(self, model, u):
        p = self.prices.get(model) or next((v for k, v in self.prices.items() if model and model.startswith(k)), None)
        if p is None or not u:
            return 0.0
        return round((u.get("input_tokens", 0) * p[0] + u.get("output_tokens", 0) * p[1]
                      + u.get("cache_creation_input_tokens", 0) * p[2] + u.get("cache_read_input_tokens", 0) * p[3]) / 1e6, 6)

    def add(self, event, detail="", outcome="ok", ref="", data=None, ts=None, **kw):
        c = _ctx.get() or {}
        row = {"ts": (ts or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
               "oprid": kw.pop("oprid", None) or c.get("oprid") or "PSAGENT",
               "feature": kw.pop("feature", None) or c.get("feature") or "system",
               "session": kw.pop("session", None) or c.get("session") or "",
               "event": event, "detail": str(detail or "")[:1000], "outcome": outcome or "ok", "ref": str(ref or "")[:100],
               "data": json.dumps(data, default=str)[:20000] if data else None, **kw}
        cols = [k for k in self.COLUMNS if k in row]
        try:
            with self._lock:
                self._db.execute(f"INSERT INTO events ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                 [row[k] for k in cols])
                self._db.commit()
        except sqlite3.Error as e:
            if not self._warned:
                log.warning("audit event not written (%s): %s; the agent carries on", self.path, e)
                self._warned = True

    def meta(self, key, value=None):
        with self._lock:
            if value is None:
                r = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
                return r[0] if r else None
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))
            self._db.commit()
            return value

    def purge(self, now=None):
        """Remove events older than the retention period. -> how many."""
        cut = ((now or datetime.now()) - timedelta(days=self.days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            n = self._db.execute("DELETE FROM events WHERE ts < ?", (cut,)).rowcount
            self._db.commit()
        if n:
            log.info("audit: %d events older than %d days removed", n, self.days)
        return n

    # ----- reading --------------------------------------------------------------------------
    def _all(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _since(self, days, now=None):
        now = now or datetime.now()
        start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    def month_cost(self, now=None):
        now = now or datetime.now()
        r = self._all("SELECT COALESCE(SUM(cost), 0) AS c FROM events WHERE ts >= ?", (now.strftime("%Y-%m-01 00:00:00"),))
        return round(r[0]["c"], 4)

    def overview(self, days=7, now=None, budget=None):
        start, now = self._since(days, now)
        prev = start - timedelta(days=days)
        s, p = start.strftime("%Y-%m-%d %H:%M:%S"), prev.strftime("%Y-%m-%d %H:%M:%S")
        people = "oprid NOT IN (%s)" % ", ".join("?" * len(SYSTEM_USERS))
        t = self._all(f"""SELECT
              COUNT(DISTINCT CASE WHEN {people} AND event IN ('signin', 'chat') THEN oprid END) AS users,
              COUNT(DISTINCT CASE WHEN event = 'chat' THEN session END) AS conversations,
              SUM(event = 'chat') AS messages,
              SUM(event IN ('task.run', 'batch.run')) AS runs,
              SUM(event IN ('task.run', 'batch.run') AND outcome = 'ok') AS runs_ok,
              SUM(event IN ('task.run', 'batch.run') AND outcome IN ('failed', 'error')) AS runs_failed,
              SUM(event = 'claude') AS claude_calls, COALESCE(SUM(cost), 0) AS cost,
              COALESCE(SUM(tin + tout + tcw + tcr), 0) AS tokens,
              SUM(event = 'query') AS queries,
              SUM(outcome IN ('failed', 'error')) AS failures,
              SUM(outcome = 'denied') AS denied,
              SUM(event = 'incident') AS incidents,
              SUM(event = 'regression.run') AS test_runs,
              SUM(event = 'regression.run' AND outcome = 'ok') AS test_runs_ok
            FROM events WHERE ts >= ?""", (*SYSTEM_USERS, s))[0]
        t = {k: (round(v, 4) if isinstance(v, float) else (v or 0)) for k, v in t.items()}
        before = self._all("SELECT COALESCE(SUM(cost), 0) AS c FROM events WHERE ts >= ? AND ts < ?", (p, s))[0]["c"]
        daily = {r["d"]: r for r in self._all("""SELECT substr(ts, 1, 10) AS d, COALESCE(SUM(cost), 0) AS cost,
              SUM(event = 'chat') AS messages, SUM(event IN ('task.run', 'batch.run')) AS runs,
              SUM(outcome IN ('failed', 'error')) AS failures
            FROM events WHERE ts >= ? GROUP BY d""", (s,))}
        series = []
        for i in range(days):
            d = _day(start + timedelta(days=i))
            r = daily.get(d) or {}
            series.append({"day": d, "cost": round(r.get("cost") or 0, 4), "messages": r.get("messages") or 0,
                           "runs": r.get("runs") or 0, "failures": r.get("failures") or 0})
        features = self._all("""SELECT feature, COALESCE(SUM(cost), 0) AS cost, SUM(event = 'claude') AS calls
            FROM events WHERE ts >= ? GROUP BY feature HAVING cost > 0 OR calls > 0 ORDER BY cost DESC""", (s,))
        users = self._all(f"""SELECT oprid, COALESCE(SUM(cost), 0) AS cost, SUM(event = 'chat') AS messages,
              SUM(event IN ('task.run', 'batch.run')) AS runs
            FROM events WHERE ts >= ? AND {people} GROUP BY oprid ORDER BY cost DESC LIMIT 5""", (s, *SYSTEM_USERS))
        month = self.month_cost(now)
        return {"days": days, "from": _day(start), "to": _day(now), "totals": t, "cost_before": round(before, 4),
                "daily": series, "features": [dict(f, label=FEATURES.get(f["feature"], f["feature"]), cost=round(f["cost"], 4))
                                             for f in features],
                "top_users": [dict(u, cost=round(u["cost"], 4)) for u in users],
                "month": {"cost": month, "budget": budget, "share": round(month / budget, 3) if budget else None,
                          "name": now.strftime("%B %Y")}}

    def usage(self, days=30, by="user", now=None):
        start, now = self._since(days, now)
        col = "feature" if by == "feature" else "oprid"
        rows = self._all(f"""SELECT {col} AS k, SUM(event = 'claude') AS calls, COALESCE(SUM(tin), 0) AS tin,
              COALESCE(SUM(tout), 0) AS tout, COALESCE(SUM(tcw), 0) AS tcw, COALESCE(SUM(tcr), 0) AS tcr,
              COALESCE(SUM(cost), 0) AS cost, SUM(event = 'chat') AS messages,
              COUNT(DISTINCT CASE WHEN event = 'chat' THEN session END) AS conversations,
              SUM(event IN ('task.run', 'batch.run')) AS runs, SUM(outcome IN ('failed', 'error')) AS failures,
              SUM(event = 'query') AS queries, MAX(ts) AS last
            FROM events WHERE ts >= ? GROUP BY k ORDER BY cost DESC, messages DESC""", (start.strftime("%Y-%m-%d %H:%M:%S"),))
        for r in rows:
            r["cost"] = round(r["cost"], 4)
            r["per_conversation"] = round(r["cost"] / r["conversations"], 4) if r["conversations"] else None
            r["label"] = FEATURES.get(r["k"], r["k"]) if by == "feature" else r["k"]
            r["system"] = by != "feature" and r["k"] in SYSTEM_USERS
        return {"days": days, "by": "feature" if by == "feature" else "user", "from": _day(start), "to": _day(now), "rows": rows,
                "total": round(sum(r["cost"] for r in rows), 4)}

    def events(self, user="", feature="", kind="actions", outcome="", text="", since="", until="", before=None, limit=100):
        """The audit log, newest first. -> {events, more}. kind: actions (no Claude calls or queries), queries,
        claude, all. before: an event id, for the next page."""
        where, params = [KINDS.get(kind, KINDS["actions"])], []
        if user:
            where.append("oprid = ?")
            params.append(user.strip().upper())
        if feature:
            where.append("feature = ?")
            params.append(feature)
        if outcome == "failed":
            where.append("outcome IN ('failed', 'error')")
        elif outcome:
            where.append("outcome = ?")
            params.append(outcome)
        if text:
            where.append("(detail LIKE ? OR ref LIKE ? OR data LIKE ? OR event LIKE ?)")
            params += [f"%{text}%"] * 4
        if since:
            where.append("ts >= ?")
            params.append(since[:10] + " 00:00:00")
        if until:
            where.append("ts <= ?")
            params.append(until[:10] + " 23:59:59")
        if before:                       # the next page: older than that event (by time, then id)
            b = self._all("SELECT ts FROM events WHERE id = ?", (int(before),))
            if b:
                where.append("(ts < ? OR (ts = ? AND id < ?))")
                params += [b[0]["ts"], b[0]["ts"], int(before)]
        limit = max(1, min(int(limit or 100), 1000))
        rows = self._all(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT ?", (*params, limit + 1))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r.get("data") else None
            r["feature_label"] = FEATURES.get(r["feature"], r["feature"])
        return {"events": rows[:limit], "more": len(rows) > limit}

    def failures(self, days=7, now=None, limit=200):
        start, now = self._since(days, now)
        rows = self._all("""SELECT * FROM events WHERE ts >= ? AND outcome IN ('failed', 'error')
            ORDER BY ts DESC, id DESC LIMIT ?""", (start.strftime("%Y-%m-%d %H:%M:%S"), limit))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r.get("data") else None
            r["feature_label"] = FEATURES.get(r["feature"], r["feature"])
        by = {}
        for r in rows:
            by[r["feature_label"]] = by.get(r["feature_label"], 0) + 1
        return {"days": days, "from": _day(start), "to": _day(now), "failures": rows,
                "by_feature": sorted(by.items(), key=lambda x: -x[1])}

    def users(self):
        return [r["oprid"] for r in self._all("SELECT DISTINCT oprid FROM events ORDER BY oprid")]


# ----- Excel exports -------------------------------------------------------------------------

def events_xlsx(events):
    from . import xlsx  # noqa: PLC0415

    rows = [["Time", "User", "Feature", "Event", "Details", "Outcome", "Reference", "Rows", "Seconds", "Cost (USD)", "SQL / values"]]
    for e in events:
        d = e.get("data") or {}
        extra = d.get("sql") or ("; ".join(f"{v['label']}={v['value']}" for v in d.get("values", []) if isinstance(v, dict))
                                 if d.get("values") else json.dumps(d) if d else "")
        rows.append([e["ts"], e["oprid"], e.get("feature_label") or e["feature"], e["event"], e.get("detail") or "",
                     e["outcome"], e.get("ref") or "", e.get("n") if e.get("n") is not None else "",
                     round(e["ms"] / 1000, 1) if e.get("ms") is not None else "", round(e.get("cost") or 0, 4) or "", extra])
    return xlsx.write([{"name": "Audit log", "rows": rows, "header": 0, "widths": [19, 14, 16, 18, 60, 9, 16, 7, 8, 10, 80]}])


def usage_xlsx(u):
    from . import xlsx  # noqa: PLC0415

    head = ["User" if u["by"] == "user" else "Feature", "Messages", "Conversations", "Claude calls", "Input tokens",
            "Output tokens", "Cache write", "Cache read", "Cost (USD)", "Per conversation", "Task runs", "Failures", "Queries", "Last"]
    rows = [head] + [[r["label"], r["messages"] or 0, r["conversations"] or 0, r["calls"] or 0, r["tin"], r["tout"], r["tcw"],
                      r["tcr"], r["cost"], r["per_conversation"] or "", r["runs"] or 0, r["failures"] or 0, r["queries"] or 0,
                      r["last"]] for r in u["rows"]]
    rows.append(["Total", "", "", "", "", "", "", "", u["total"]])
    return xlsx.write([{"name": f"Usage {u['from']} to {u['to']}"[:31], "rows": rows, "header": 0,
                        "widths": [18, 10, 13, 12, 13, 13, 12, 12, 11, 15, 10, 9, 9, 19]}])


# ----- budget alerts and the weekly email -------------------------------------------------------

def _money(x):
    return f"${x:,.2f}"


def weekly_email(audit, now=None, ps_url=""):
    """-> (subject, html, text) for the last 7 days."""
    o = audit.overview(7, now)
    u = audit.usage(7, "user", now)
    f = audit.failures(7, now, limit=10)
    t = o["totals"]
    subject = f"[PeopleSoft Agent] Week of {o['from']}: {t['users']} users, {t['runs']} task runs, {_money(t['cost'])}"
    change = ""
    if o["cost_before"]:
        pct = (t["cost"] - o["cost_before"]) / o["cost_before"] * 100
        change = f" ({'+' if pct >= 0 else ''}{pct:.0f}% on the week before)"
    lines = [f"{t['users']} people used the agent: {t['messages']} messages in {t['conversations']} conversations.",
             f"{t['runs']} task runs: {t['runs_ok']} succeeded, {t['runs_failed']} failed.",
             f"Claude: {t['claude_calls']} calls, {_money(t['cost'])}{change}.",
             f"Watcher: {t['incidents']} alerts. Regression tests: {t['test_runs']} runs, {t['test_runs_ok']} all passed."]
    if o["month"]["budget"]:
        lines.append(f"{o['month']['name']} so far: {_money(o['month']['cost'])} of the {_money(o['month']['budget'])} budget "
                     f"({o['month']['share'] * 100:.0f}%).")
    h = ['<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:720px">',
         f'<h2 style="margin:0 0 6px">PeopleSoft Agent: week of {escape(o["from"])} to {escape(o["to"])}</h2>',
         "".join(f'<p style="margin:0 0 4px">{escape(x)}</p>' for x in lines),
         '<h3 style="margin:14px 0 4px">Top users</h3><table style="border-collapse:collapse;font-size:13px">',
         '<tr><th style="text-align:left;padding:2px 8px">User</th><th style="text-align:right;padding:2px 8px">Messages</th>'
         '<th style="text-align:right;padding:2px 8px">Task runs</th><th style="text-align:right;padding:2px 8px">Cost</th></tr>']
    text = ["PeopleSoft Agent: week of " + o["from"] + " to " + o["to"], *lines, "", "Top users:"]
    for r in [r for r in u["rows"] if not r["system"]][:8]:
        h.append(f'<tr><td style="padding:2px 8px">{escape(r["k"])}</td><td style="text-align:right;padding:2px 8px">{r["messages"] or 0}</td>'
                 f'<td style="text-align:right;padding:2px 8px">{r["runs"] or 0}</td><td style="text-align:right;padding:2px 8px">{_money(r["cost"])}</td></tr>')
        text.append(f"  {r['k']}: {r['messages'] or 0} messages, {r['runs'] or 0} runs, {_money(r['cost'])}")
    h.append("</table>")
    if f["failures"]:
        h.append('<h3 style="margin:14px 0 4px">Latest failures</h3><ul style="margin:0;padding-left:18px">')
        text += ["", "Latest failures:"]
        for x in f["failures"][:10]:
            h.append(f'<li>{escape(x["ts"][:16])} {escape(x["oprid"])}, {escape(x["feature_label"])}: {escape(x["detail"] or x["event"])}</li>')
            text.append(f"  {x['ts'][:16]} {x['oprid']}, {x['feature_label']}: {x['detail'] or x['event']}")
        h.append("</ul>")
    if ps_url:
        h.append(f'<p><a href="{escape(ps_url.rstrip("/") + "/h/?tab=DEFAULT")}">Open PeopleSoft</a> and choose ＋ Features › Audit '
                 "for the full dashboard.</p>")
    h.append("</div>")
    return subject, "".join(h), "\n".join(text)


def budget_email(cost, budget, level, month, ps_url=""):
    word = "reached" if level >= 1 else f"passed {level * 100:.0f}% of"
    subject = f"[PeopleSoft Agent] Claude spend has {word} the {month} budget: {_money(cost)} of {_money(budget)}"
    body = (f"Claude spend for {month} is {_money(cost)}, {cost / budget * 100:.0f}% of the monthly budget of {_money(budget)} "
            "(PSAGENT_BUDGET). The agent keeps working; this is a notice only. The admin dashboard (＋ Features › Audit) "
            "shows which users and features the spend comes from.")
    html = (f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;max-width:640px">'
            f'<h2 style="margin:0 0 6px;color:{"#b91c1c" if level >= 1 else "#92400e"}">{escape(subject[20:])}</h2>'
            f"<p>{escape(body)}</p>"
            + (f'<p><a href="{escape(ps_url.rstrip("/") + "/h/?tab=DEFAULT")}">Open PeopleSoft</a></p>' if ps_url else "")
            + "</div>")
    return subject, html, body


class Jobs:
    """Background housekeeping: the daily purge, budget alerts (80% and 100%, once each per month), the weekly email."""

    def __init__(self, audit, mailer=None, budget=None, to=(), weekly="Mon 08:00", ps_url=""):
        self.audit, self.mailer, self.budget, self.to, self.ps_url = audit, mailer, budget, list(to), ps_url
        self.weekly = None
        w = str(weekly or "").strip().lower().split()
        if len(w) == 2 and w[0][:3] in WEEKDAYS:
            try:
                h, m = map(int, w[1].split(":"))
                self.weekly = (WEEKDAYS.index(w[0][:3]), h, m)
            except ValueError:
                log.warning("PSAGENT_AUDIT_WEEKLY=%r: use e.g. Mon 08:00", weekly)
        self._stop = threading.Event()
        self._thread = None

    def start(self, interval=60):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, args=(interval,), name="psagent-audit", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self, interval):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("audit housekeeping failed")
            self._stop.wait(interval)

    def _send(self, subject, html, text):
        return bool(self.mailer) and self.mailer.send(self.to, subject, html, text)

    def tick(self, now=None):
        """-> what was done, e.g. ["purged", "budget 0.8", "weekly"]"""
        now = now or datetime.fromtimestamp(time.time())
        done = []
        today = _day(now)
        if self.audit.meta("purged") != today:
            self.audit.purge(now)
            self.audit.meta("purged", today)
            done.append("purged")
        if self.budget:
            cost, month = self.audit.month_cost(now), now.strftime("%Y-%m")
            for level in (1.0, 0.8):        # the highest level crossed; 100% also settles 80%
                if cost < self.budget * level:
                    continue
                if not self.audit.meta(f"budget_{month}_{level}"):
                    for lv in (0.8, 1.0) if level == 1.0 else (0.8,):
                        self.audit.meta(f"budget_{month}_{lv}", today)
                    sent = self._send(*budget_email(cost, self.budget, level, now.strftime("%B %Y"), self.ps_url))
                    record("budget.alert", f"{level * 100:.0f}% of the {_money(self.budget)} budget: {_money(cost)}"
                           + ("" if sent else " (no email: SMTP not set up)"), "ok", month, oprid="PSAGENT", feature="audit")
                    done.append(f"budget {level}")
                break
        if self.weekly:
            day, h, m = self.weekly
            week = now.strftime("%G-W%V")
            if now.weekday() == day and (now.hour, now.minute) >= (h, m) and self.audit.meta("weekly") != week:
                self.audit.meta("weekly", week)
                sent = self._send(*weekly_email(self.audit, now, self.ps_url))
                record("weekly.email", "Weekly usage email" + ("" if sent else " not sent (no SMTP or no recipients)"),
                       "ok", week, oprid="PSAGENT", feature="audit")
                done.append("weekly")
        return done
