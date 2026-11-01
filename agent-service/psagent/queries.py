"""Ask the data: answers from curated PS Queries (named AI_...), never from SQL the agent writes.

  Catalog     the AI_ queries a user may run, with their columns and prompts: public ones and the user's own private
              ones, whose every record is in the user's query access groups (permission lists -> query trees ->
              access groups -> records), as Query Viewer decides it
  find        a short list of them for a question
  prompts     prompt values checked and formatted: dates YYYY-MM-DD (PeopleSoft reads 12/31/2026 or a typo as no rows,
              without an error), translate values, prompt table values, Uppercase fields
  read        PeopleSoft's Run to Excel answer -> rows (dates from Excel serial numbers)
  summary     what Claude sees: the row count and, per column, counts and totals; aggregate() for a breakdown,
              lookup() for the few rows matching a filter ("who has role X"). Never all the rows, never a sensitive
              column (national IDs, birth dates, pay, bank details)

The browser runs the query with Query Viewer's Run to Excel link (ICQryNameExcelURL=PUBLIC.<name>&BIND1=...) in the
user's own PeopleSoft session, so PeopleSoft applies the user's row security (query security records) and query
limits. The link does not check query trees (verified on PT 8.61), so the service builds links only for queries in
the user's catalog. Results are kept in memory for the conversation's follow-up questions, never written to disk.
"""

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .ci import prompt_values_found
from .db import QueryError, masked
from .runbooks import DocError, xlsx_sheets

log = logging.getLogger("psagent")

PREFIX = "AI_"
MAX_ROWS = 100000            # rows read from one answer
SHOW_ROWS = 200              # rows on the card
FIND_ROWS = 25               # matching rows Claude sees from lookup(): enough to name a few, not to read the result
USE_XLAT, USE_YESNO, USE_PROMPT = 0x200, 0x2000, 0x4000     # PSQRYBIND.USEEDIT bits (as PSRECFIELD)
TYPES = {0: "text", 1: "long text", 2: "number", 3: "signed number", 4: "date", 5: "time", 6: "datetime"}


class QueryAskError(ValueError):
    pass


# The records a user may query: their permission lists' access groups in the query trees (the current tree), and every
# record below each group. Then the AI_ queries, public or the user's own, with no record outside that set.
_ACCESS = """WITH G AS (SELECT DISTINCT S.TREE_NAME, S.ACCESS_GROUP FROM PS_SCRTY_ACC_GRP S, PS_USERCLASS_VW U
                WHERE U.OPRID = :o AND S.CLASSID = U.CLASSID AND S.ACCESSIBLE = 'Y'),
     T AS (SELECT D.TREE_NAME, MAX(D.EFFDT) AS EFFDT FROM PSTREEDEFN D WHERE D.SETID = ' ' AND D.EFFDT <= CURRENT_DATE
                AND D.EFF_STATUS = 'A' AND D.TREE_NAME IN (SELECT TREE_NAME FROM G) GROUP BY D.TREE_NAME),
     N AS (SELECT N.TREE_NAME, N.TREE_NODE, N.TREE_NODE_NUM, N.TREE_NODE_NUM_END FROM PSTREENODE N, T
                WHERE N.SETID = ' ' AND N.TREE_NAME = T.TREE_NAME AND N.EFFDT = T.EFFDT),
     R AS (SELECT DISTINCT C.TREE_NODE AS RECNAME FROM N P, N C, G WHERE P.TREE_NAME = G.TREE_NAME
                AND P.TREE_NODE = G.ACCESS_GROUP AND C.TREE_NAME = P.TREE_NAME
                AND C.TREE_NODE_NUM BETWEEN P.TREE_NODE_NUM AND P.TREE_NODE_NUM_END)"""

CATALOG_SQL = _ACCESS + """
SELECT D.OPRID, D.QRYNAME, D.DESCR, D.DESCRLONG, D.QRYFOLDER FROM PSQRYDEFN D
 WHERE D.QRYNAME LIKE :p ESCAPE '\\' AND D.OPRID IN (' ', :o) AND D.QRYTYPE = 1
   AND EXISTS (SELECT 1 FROM PSQRYRECORD Q WHERE Q.OPRID = D.OPRID AND Q.QRYNAME = D.QRYNAME)
   AND NOT EXISTS (SELECT 1 FROM PSQRYRECORD Q WHERE Q.OPRID = D.OPRID AND Q.QRYNAME = D.QRYNAME
                    AND Q.RECNAME NOT IN (SELECT RECNAME FROM R))
 ORDER BY D.QRYNAME, D.OPRID"""

COLUMNS_SQL = """SELECT F.OPRID, F.QRYNAME, F.COLUMNNUM, F.HEADING, F.FIELDNAME, F.RECNAME, F.AGGREGATEFUNC, B.FIELDTYPE, L.LONGNAME
  FROM PSQRYFIELD F LEFT JOIN PSDBFIELD B ON B.FIELDNAME = F.FIELDNAME
  LEFT JOIN PSDBFLDLABL L ON L.FIELDNAME = F.FIELDNAME AND L.DEFAULT_LABEL = 1
 WHERE F.COLUMNNUM > 0 AND F.OPRID IN ({owners}) AND F.QRYNAME IN ({names}) ORDER BY F.OPRID, F.QRYNAME, F.COLUMNNUM"""

PROMPTS_SQL = """SELECT OPRID, QRYNAME, BNDNUM, BNDNAME, FIELDNAME, HEADING, FIELDTYPE, LENGTH, DECIMALPOS, FORMAT, EDITTABLE,
       USEEDIT, QRYREQUIREDPROMPT
  FROM PSQRYBIND WHERE OPRID IN ({owners}) AND QRYNAME IN ({names}) ORDER BY OPRID, QRYNAME, BNDNUM"""

RECORDS_SQL = """SELECT Q.OPRID, Q.QRYNAME, Q.RECNAME, R.RECDESCR FROM PSQRYRECORD Q LEFT JOIN PSRECDEFN R ON R.RECNAME = Q.RECNAME
 WHERE Q.OPRID IN ({owners}) AND Q.QRYNAME IN ({names}) ORDER BY Q.OPRID, Q.QRYNAME, Q.RECNAME"""


def _s(v):
    return str(v if v is not None else "").strip()


class Catalog:
    """The AI_ queries of each user, read from PeopleTools tables (read-only) and kept a few minutes."""

    def __init__(self, db, prefix=PREFIX, ttl=600):
        self.db, self.prefix, self.ttl = db, prefix, ttl
        self._cache, self._lock = {}, threading.Lock()

    def for_user(self, oprid, refresh=False):
        with self._lock:
            hit = self._cache.get(oprid)
        if hit and not refresh and time.time() - hit[0] < self.ttl:
            return hit[1]
        out = self._read(oprid)
        with self._lock:
            self._cache[oprid] = (time.time(), out)
        return out

    def get(self, oprid, name):
        name = _s(name).upper()
        mine = [q for q in self.for_user(oprid) if q["name"] == name]
        return next((q for q in mine if q["private"]), mine[0] if mine else None)

    def _read(self, oprid):
        pattern = self.prefix.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%") + "%"
        _, rows, _ = self.db.query(CATALOG_SQL, {"o": oprid, "p": pattern}, max_rows=500, who=oprid, why="query catalog")
        queries = {}
        for owner, name, descr, long_descr, folder in rows:
            key = (_s(owner), _s(name))
            queries[key] = {"name": key[1], "private": bool(key[0]), "owner": key[0], "title": _s(descr) or key[1],
                            "description": _s(long_descr)[:600], "folder": _s(folder), "columns": [], "prompts": [], "records": []}
        if not queries:
            return []
        owners = sorted({k[0] or " " for k in queries} | {" "})
        names = sorted({k[1] for k in queries})
        binds = {f"w{i}": o for i, o in enumerate(owners)} | {f"n{i}": n for i, n in enumerate(names)}
        fmt = {"owners": ", ".join(f":w{i}" for i in range(len(owners))), "names": ", ".join(f":n{i}" for i in range(len(names)))}
        _, rows, _ = self.db.query(COLUMNS_SQL.format(**fmt), binds, max_rows=20000, who=oprid, why="query columns")
        for owner, name, _num, heading, fld, rec, agg, ftype, label in rows:
            q = queries.get((_s(owner), _s(name)))
            if q is not None:
                q["columns"].append({"heading": _s(heading) or _s(label) or _s(fld) or f"Column {len(q['columns']) + 1}",
                                     "field": _s(fld), "record": _s(rec), "type": int(ftype) if ftype is not None else 0,
                                     "aggregate": _s(agg), "sensitive": masked(_s(fld)) or masked(_s(heading))})
        _, rows, _ = self.db.query(PROMPTS_SQL.format(**fmt), binds, max_rows=5000, who=oprid, why="query prompts")
        xlat = set()
        for owner, name, _num, bnd, fld, heading, ftype, length, dec, form, edit, use, req in rows:
            q = queries.get((_s(owner), _s(name)))
            if q is None:
                continue
            use = int(use or 0)
            p = {"name": _s(bnd), "label": _s(heading) or _s(fld), "field": _s(fld), "type": int(ftype or 0),
                 "length": int(length or 0), "decimals": int(dec or 0), "upper": int(form or 0) == 0 and int(ftype or 0) == 0,
                 "edit": _s(edit) if use & USE_PROMPT and not _s(edit).startswith("%") else "",
                 "required": _s(req).upper() != "N", "values": [("Y", "Yes"), ("N", "No")] if use & USE_YESNO else []}
            if use & USE_XLAT:
                xlat.add(p["field"])
                p["xlat"] = True
            q["prompts"].append(p)
        _, rows, _ = self.db.query(RECORDS_SQL.format(**fmt), binds, max_rows=5000, who=oprid, why="query records")
        for owner, name, rec, descr in rows:
            q = queries.get((_s(owner), _s(name)))
            if q is not None:
                q["records"].append({"name": _s(rec), "descr": _s(descr)})
        if xlat:
            values = {}
            fb = {f"f{i}": f for i, f in enumerate(sorted(xlat))}
            try:
                _, rows, _ = self.db.query("SELECT FIELDNAME, FIELDVALUE, XLATLONGNAME FROM PSXLATITEM WHERE EFF_STATUS = 'A' AND FIELDNAME IN ("
                                           + ", ".join(":" + k for k in fb) + ") ORDER BY FIELDNAME, FIELDVALUE", fb, max_rows=3000,
                                           who=oprid, why="query translate values")
                for f, v, label in rows:
                    if (_s(v), _s(label)) not in values.setdefault(_s(f), []):
                        values[_s(f)].append((_s(v), _s(label)))
            except QueryError as e:
                log.warning("query translate values: %s", e)
            for q in queries.values():
                for p in q["prompts"]:
                    if p.pop("xlat", False):
                        p["values"] = values.get(p["field"], [])
        out = []
        for q in queries.values():
            # a prompt without a name (":1", queries from old PeopleTools) cannot be filled from the link
            if any(not re.fullmatch(r"\w+", p["name"]) for p in q["prompts"]):
                q["problem"] = "a prompt has no name (recreate the prompts in Query Manager)"
            out.append(q)
        return out


def view(q):
    """A query as Claude and the widget see it."""
    return {"name": q["name"], "title": q["title"], "description": q["description"], "private": q["private"],
            "columns": [c["heading"] + (f" ({c['aggregate']})" if c["aggregate"] else "") for c in q["columns"]],
            "prompts": [{"name": p["name"], "label": p["label"], "type": TYPES.get(p["type"], "text"), "required": p["required"],
                         **({"values": [f"{v} = {lab}" if lab else v for v, lab in p["values"][:20]]} if p["values"] else {})}
                        for p in q["prompts"]],
            "records": [r["name"] for r in q["records"]], **({"problem": q["problem"]} if q.get("problem") else {})}


def find(catalog, question, limit=12):
    """The queries that best fit a question; all of them when there are few (a curated set usually is)."""
    if len(catalog) <= limit:
        return list(catalog)
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}

    def score(q):
        text = " ".join([q["name"], q["title"], q["description"], *[c["heading"] for c in q["columns"]],
                         *[p["label"] for p in q["prompts"]], *[r["name"] + " " + r["descr"] for r in q["records"]]]).lower()
        return sum(2 if w in q["title"].lower() or w in q["name"].lower() else 1 for w in words if w in text)
    return sorted(catalog, key=lambda q: -score(q))[:limit]


# ----- prompts ------------------------------------------------------------------------------------

def _date(v):
    if re.fullmatch(r"\d{1,5}(\.0+)?", v):
        try:
            return date(1899, 12, 30) + timedelta(days=int(float(v)))
        except (ValueError, OverflowError):
            return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%b-%Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


def check_prompts(q, given, db=None, who=""):
    """given: {prompt name or label: value} -> ([(prompt name, value for the link)], problems). Every prompt is in the
    link, an optional one empty, so PeopleSoft never stops at its prompt page."""
    by = {}
    for k, v in (given or {}).items():
        by[_s(k).upper()] = _s(v)
    known = {p["name"].upper() for p in q["prompts"]} | {p["label"].upper() for p in q["prompts"]}
    problems = [f"{q['name']} has no prompt {k}" for k in by if k not in known]
    params, lookups = [], {}
    for p in q["prompts"]:
        v = by.get(p["name"].upper(), by.get(p["label"].upper(), ""))
        if v == "":
            if p["required"]:
                problems.append(f"{p['label']} is required")
            params.append((p["name"], ""))
            continue
        t = p["type"]
        if t == 4:
            d = _date(v)
            if d is None:
                problems.append(f"{p['label']} {v} is not a date (use YYYY-MM-DD)")
                continue
            v = d.isoformat()                 # PeopleSoft reads 12/31/2026 as no rows, without an error
        elif t in (2, 3):
            try:
                n = Decimal(v.replace(",", ""))
            except InvalidOperation:
                n = None
            if n is None or not n.is_finite() or (t == 2 and n < 0):
                problems.append(f"{p['label']} must be a number, not {v}")
                continue
            v = format(n.normalize(), "f") if n != n.to_integral() else str(int(n))
        else:
            if p["upper"]:
                v = v.upper()
            if p["values"]:
                code = next((c for c, lab in p["values"] if v.upper() == c.upper() or (lab and v.lower() == lab.lower())), None)
                if code is None:
                    problems.append(f"{p['label']} {v} is not one of: " + ", ".join(f"{c} ({lab})" if lab else c for c, lab in p["values"][:12]))
                    continue
                v = code
            elif p["length"] and len(v) > p["length"]:
                problems.append(f"{p['label']} is longer than {p['length']} characters")
                continue
            elif p["edit"] and p["field"] and "%" not in v:
                lookups.setdefault((p["edit"], p["field"]), []).append((p, v))
        params.append((p["name"], v))
    if lookups and db is not None:
        found = prompt_values_found(db, {k: {v for _, v in items} for k, items in lookups.items()}, who, "query")
        for k, items in lookups.items():
            ok = found.get(k)
            for p, v in items:
                if ok is not None and v not in ok and v.upper() not in ok:
                    problems.append(f"{p['label']} {v} does not exist (prompt table {k[0]})")
    return params, problems


# ----- the answer ---------------------------------------------------------------------------------

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read(raw, q, max_rows=MAX_ROWS):
    """PeopleSoft's Run to Excel file -> {title, count, headings, rows, truncated}. Row 1: the title and the row
    count; row 2: the headings; then the rows. Dates come as Excel serial numbers."""
    try:
        sheets = xlsx_sheets(raw)
    except DocError as e:
        raise QueryAskError(f"PeopleSoft's answer is not a readable Excel file: {e}") from e
    if not sheets or len(sheets[0][1]) < 2:
        raise QueryAskError("PeopleSoft's answer has no headings")
    rows = [list(c) for _, c in sheets[0][1]]
    title = _s(rows[0][0]) if rows[0] else q["title"]
    total = int(_num(_s(rows[0][1])) or 0) if len(rows[0]) > 1 else 0
    heads = [_s(h) for h in rows[1]]
    types = [c["type"] for c in q["columns"]] if len(q["columns"]) == len(heads) else [0] * len(heads)
    data = []
    for r in rows[2:2 + max_rows]:
        out = []
        for i in range(len(heads)):
            v = _s(r[i]) if i < len(r) else ""
            n = _num(v) if v else None
            if n is not None and types[i] in (4, 6) and 0 < n < 2958466:
                d = datetime(1899, 12, 30) + timedelta(days=n)
                v = d.strftime("%Y-%m-%d") if types[i] == 4 or d.time() == datetime.min.time() else d.strftime("%Y-%m-%d %H:%M")
            elif n is not None and types[i] in (0, 2, 3) and re.fullmatch(r"-?\d+\.0+", v):
                v = v.split(".")[0]           # Excel's numbers: 15.0 -> 15
            out.append(v)
        data.append(out)
    return {"title": title, "count": max(total, len(data)), "headings": heads, "rows": data,
            "truncated": len(rows) - 2 > max_rows}


def _column(result, q, name):
    """A column by heading or field name -> (index, sensitive)."""
    n = _s(name).lower()
    for i, h in enumerate(result["headings"]):
        col = q["columns"][i] if len(q["columns"]) == len(result["headings"]) else {"field": "", "sensitive": masked(h)}
        if n in (h.lower(), col["field"].lower()):
            return i, col["sensitive"] or masked(h)
    raise QueryAskError(f"{q['name']} has no column {name}; its columns: " + ", ".join(result["headings"]))


def _grouping(distinct, values):
    """A column whose values repeat (status, department, country) can be broken down; one that tells the rows apart
    (names, IDs, addresses) cannot: its values would be the rows themselves."""
    return 0 < distinct <= 25 and distinct <= values // 2 or 25 < distinct <= values // 4


def summary(result, q):
    """What Claude sees: the row count and, per column, how many distinct values, the most frequent ones (when
    there are few), totals for numbers and the range of dates. Sensitive columns: only that they exist."""
    cols = []
    for i, h in enumerate(result["headings"]):
        col = q["columns"][i] if len(q["columns"]) == len(result["headings"]) else {"type": 0, "sensitive": masked(h), "field": ""}
        info = {"column": h}
        if col["sensitive"] or masked(h):
            info["hidden"] = "sensitive: not shown to you"
            cols.append(info)
            continue
        values = [r[i] for r in result["rows"] if r[i] != ""]
        counts = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        info["distinct"] = len(counts)
        if _grouping(len(counts), len(values)):
            info["counts"] = [[v, n] for v, n in sorted(counts.items(), key=lambda x: -x[1])[:12]]
        nums = [_num(v) for v in values]
        if col["type"] in (2, 3) and values and all(n is not None for n in nums):
            info |= {"sum": round(sum(nums), 2), "min": min(nums), "max": max(nums)}
        if col["type"] in (4, 6) and values:
            info |= {"from": min(values), "to": max(values)}
        cols.append(info)
    return {"query": q["name"], "title": result["title"], "rows": result["count"], "columns": cols,
            **({"note": f"only the first {len(result['rows'])} rows were read"} if result["truncated"] else {})}


def matching(result, q, filters):
    """The rows matching every filter [{column, value}]: equal ignoring case, % = any characters (PSAGENT_%). Filters
    on sensitive columns are refused: they would tell Claude their values. -> (rows, [[heading, value]])"""
    rows, used = result["rows"], []
    for f in filters if isinstance(filters, list) else []:      # from a tool without strict mode: anything may come
        if not isinstance(f, dict):
            raise QueryAskError("each filter is {column, value}")
        name, value = _s(f.get("column")), _s(f.get("value"))
        if not name:
            continue
        i, sensitive = _column(result, q, name)
        if sensitive:
            raise QueryAskError(f"{name} is a sensitive column: no filtering on it")
        pattern = re.compile(".*".join(re.escape(p) for p in value.split("%")), re.I | re.S)
        rows = [r for r in rows if pattern.fullmatch(r[i].strip())]
        used.append([result["headings"][i], value])
    return rows, used


def lookup(result, q, filters, limit=FIND_ROWS):
    """The rows matching filters, for Claude to name them: at most `limit`, without sensitive columns."""
    rows, used = matching(result, q, filters)
    if not used:
        raise QueryAskError("give at least one filter: a column and the value to look for")
    known = len(q["columns"]) == len(result["headings"])
    keep = [i for i, h in enumerate(result["headings"]) if not (masked(h) or known and q["columns"][i]["sensitive"])]
    return {"filters": used, "matches": len(rows), "columns": [result["headings"][i] for i in keep],
            "rows": [[r[i] for i in keep] for r in rows[:limit]], **({"more": len(rows) - limit} if len(rows) > limit else {}),
            **({"note": f"only the first {len(result['rows'])} rows were read"} if result["truncated"] else {})}


def aggregate(result, q, group_by, measure="", op="count", limit=50, filters=None):
    """A breakdown for Claude: rows grouped by one or two columns, counted or summed, of the rows matching filters.
    -> {by, value, filters, rows: [[..., v]]}"""
    picked, used = matching(result, q, filters)
    result = {**result, "rows": picked}
    group_by = [g for g in ([group_by] if isinstance(group_by, str) else group_by or []) if _s(g)][:2]
    if not group_by:
        raise QueryAskError("group by at least one column")
    idx = []
    for g in group_by:
        i, sensitive = _column(result, q, g)
        if sensitive:
            raise QueryAskError(f"{g} is a sensitive column: no breakdown by it")
        values = [r[i] for r in result["rows"] if r[i] != ""]
        if not _grouping(len(set(values)), len(values)):
            raise QueryAskError(f"{g} tells the rows apart (names, IDs): no breakdown by it; the user sees the rows in the table")
        idx.append(i)
    m = None
    op = op if op in ("count", "sum", "avg", "min", "max") else "count"
    if op != "count":
        m, sensitive = _column(result, q, measure)
        if sensitive:
            raise QueryAskError(f"{measure} is a sensitive column: no totals of it")
    groups = {}
    for r in result["rows"]:
        k = tuple(r[i] or "(blank)" for i in idx)
        if op == "count":
            groups[k] = groups.get(k, 0) + 1
        else:
            n = _num(r[m])
            if n is not None:
                groups.setdefault(k, []).append(n)
    if op != "count":
        fn = {"sum": sum, "min": min, "max": max, "avg": lambda xs: sum(xs) / len(xs)}[op]
        groups = {k: round(fn(v), 2) for k, v in groups.items() if v}
    rows = sorted(groups.items(), key=lambda x: -x[1])
    label = "rows" if op == "count" else f"{op} of {result['headings'][m]}"
    return {"by": [result["headings"][i] for i in idx], "value": label, **({"filters": used} if used else {}),
            "rows": [[*k, v] for k, v in rows[:limit]], **({"more": len(rows) - limit} if len(rows) > limit else {})}
