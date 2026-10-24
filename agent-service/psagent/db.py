"""Read-only access to the PeopleSoft database, for troubleshooting.

Read-only is enforced on three levels, so it holds even with a privileged
account (use a SELECT-only account in production anyway):
  1. every statement is checked: one SELECT or WITH statement, no DML, DDL,
     PL/SQL, locking or database links;
  2. every session runs SET TRANSACTION READ ONLY before the query;
  3. every query is rolled back and the connection released.
Results are capped (rows, time), sensitive columns come back masked, and every
query is logged with who asked and why.
"""

import logging
import re

log = logging.getLogger("psagent")

MAX_ROWS = 200
TIMEOUT_MS = 20000
MAX_CELL = 2000  # characters per value in ad-hoc query results (CLOBs are cut)

# Columns whose values are never returned: national IDs, birth dates, pay, bank details, passwords
_SENSITIVE = re.compile(
    r"NATIONAL_ID|BIRTH|SSN|PSWD|PASSWORD|BANK|ACCOUNT_NUM|ACCOUNT_EC_ID|BRANCH_EC_ID|IBAN|SWIFT|"
    r"ANNUAL_RT|MONTHLY_RT|HOURLY_RT|DAILY_RT|COMPRATE|CHANGE_AMT|SALARY", re.I)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|DROP|ALTER|CREATE|TRUNCATE|RENAME|GRANT|REVOKE|COMMIT|ROLLBACK|SAVEPOINT|"
    r"LOCK|EXECUTE|EXEC|CALL|BEGIN|DECLARE|PURGE|FLASHBACK|AUDIT|ANALYZE)\b|\bFOR\s+UPDATE\b|\bDBMS_(?!LOB\.(?:SUBSTR|INSTR|GETLENGTH)\b)\w+|"
    r"\bUTL_\w+|\bSYS\.|@\w", re.I)


class QueryError(ValueError):
    pass


def _strip(sql):
    """SQL without comments and string literals (so keywords inside strings don't count)."""
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    s = re.sub(r"--[^\n]*", " ", s)
    return re.sub(r"'(?:[^']|'')*'", "''", s)


def check_select(sql):
    """Raise QueryError unless sql is a single read-only SELECT / WITH statement."""
    text = str(sql or "").strip().rstrip(";").strip()
    bare = _strip(text)
    if not text:
        raise QueryError("empty query")
    if ";" in bare:
        raise QueryError("one statement only")
    if not re.match(r"^\s*(SELECT|WITH)\b", bare, re.I):
        raise QueryError("only SELECT or WITH queries are allowed")
    bad = _FORBIDDEN.search(bare)
    if bad:
        raise QueryError(f"not allowed in a read-only query: {bad.group(0).strip()}")
    return text


def masked(name):
    return bool(_SENSITIVE.search(str(name)))


def _cell(v, max_cell=MAX_CELL):
    if v is None:
        return None
    if hasattr(v, "read"):  # LOB
        v = v.read()
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ")
    if isinstance(v, (int, float)):
        return v
    s = str(v)
    return s if len(s) <= max_cell else s[:max_cell] + " …(cut)"


class ReadOnlyDB:
    """Oracle (python-oracledb, thin mode: nothing to install on the database server)."""

    def __init__(self, dsn, user, password, schema=None):
        import oracledb  # noqa: PLC0415 - optional dependency

        self._oracledb = oracledb
        self._pool = oracledb.create_pool(user=user, password=password, dsn=dsn, min=0, max=3, increment=1)
        self.schema = schema
        self.label = f"{user}@{dsn}"

    def query(self, sql, params=None, max_rows=MAX_ROWS, who="", why="", max_cell=MAX_CELL):
        """-> (columns, rows, truncated). Raises QueryError. max_cell: longer for reading program code."""
        sql = check_select(sql)
        log.info("db query by %s (%s): %s", who or "?", why[:120], " ".join(sql.split())[:500])
        try:
            with self._pool.acquire() as conn:
                conn.call_timeout = TIMEOUT_MS
                cur = conn.cursor()
                try:
                    cur.execute("SET TRANSACTION READ ONLY")
                    if self.schema:
                        cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {re.sub(r'[^A-Za-z0-9_$#]', '', self.schema)}")
                    cur.execute(sql, params or {})
                    cols = [d[0] for d in cur.description or []]
                    raw = cur.fetchmany(max_rows + 1)
                finally:
                    conn.rollback()
        except self._oracledb.Error as e:
            raise QueryError(str(e).splitlines()[0][:300]) from e
        hide = [masked(c) for c in cols]
        rows = [[("***" if hide[i] and v is not None else _cell(v, max_cell)) for i, v in enumerate(r)] for r in raw[:max_rows]]
        return cols, rows, len(raw) > max_rows


def from_env(env):
    """A ReadOnlyDB when PSAGENT_DB_DSN/USER/PASSWORD are set, else None."""
    dsn, user, pwd = env.get("PSAGENT_DB_DSN"), env.get("PSAGENT_DB_USER"), env.get("PSAGENT_DB_PASSWORD")
    if not (dsn and user and pwd) or "<" in dsn:
        return None
    return ReadOnlyDB(dsn, user, pwd, env.get("PSAGENT_DB_SCHEMA") or None)
