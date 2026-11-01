import base64
import json
import sqlite3
import unittest

from fakes import FakeLLM, response, text, tool
from psagent import queries, xlsx
from psagent.agent import Agent, Session
from psagent.db import QueryError, check_select

# The PeopleTools tables Ask the data reads, in SQLite: the catalog SQL itself runs, so its security filter is tested.
# TESTER: permission list HCPPALL (query tree access group HR_ACCESS: JOB, PERSONAL_DATA, DEPT_TBL, COMPANY_TBL).
# BOSS: also PAYROLL_PL (PAY_ACCESS: PAY_CHECK). NOBODY: PTPT1000 (portal records only).
TABLES = """
CREATE TABLE PS_USERCLASS_VW (OPRID, ROLENAME, CLASSID);
CREATE TABLE PS_SCRTY_ACC_GRP (CLASSID, TREE_NAME, ACCESS_GROUP, ACCESSIBLE);
CREATE TABLE PSTREEDEFN (SETID, TREE_NAME, EFFDT, EFF_STATUS);
CREATE TABLE PSTREENODE (SETID, TREE_NAME, EFFDT, TREE_NODE, TREE_NODE_NUM, TREE_NODE_NUM_END, TREE_NODE_TYPE);
CREATE TABLE PSQRYDEFN (OPRID, QRYNAME, DESCR, DESCRLONG, QRYFOLDER, QRYTYPE);
CREATE TABLE PSQRYRECORD (OPRID, QRYNAME, RECNAME);
CREATE TABLE PSQRYFIELD (OPRID, QRYNAME, COLUMNNUM, HEADING, FIELDNAME, RECNAME, AGGREGATEFUNC);
CREATE TABLE PSQRYBIND (OPRID, QRYNAME, BNDNUM, BNDNAME, FIELDNAME, HEADING, FIELDTYPE, LENGTH, DECIMALPOS, FORMAT, EDITTABLE,
                        USEEDIT, QRYREQUIREDPROMPT);
CREATE TABLE PSDBFIELD (FIELDNAME, FIELDTYPE);
CREATE TABLE PSDBFLDLABL (FIELDNAME, LONGNAME, DEFAULT_LABEL);
CREATE TABLE PSRECDEFN (RECNAME, RECDESCR, SQLTABLENAME, RECTYPE);
CREATE TABLE PSRECFIELD (RECNAME, FIELDNAME, FIELDNUM, USEEDIT);
CREATE TABLE PSXLATITEM (FIELDNAME, FIELDVALUE, XLATLONGNAME, EFF_STATUS);
CREATE TABLE PS_COMPANY_TBL (COMPANY, EFFDT);
"""

ROWS = {
    "PS_USERCLASS_VW": [("TESTER", "HR Analyst", "HCPPALL"), ("BOSS", "HR Analyst", "HCPPALL"), ("BOSS", "Payroll", "PAYROLL_PL"),
                        ("NOBODY", "PeopleSoft User", "PTPT1000")],
    "PS_SCRTY_ACC_GRP": [("HCPPALL", "QUERY_TREE_HR", "HR_ACCESS", "Y"), ("PAYROLL_PL", "QUERY_TREE_HR", "PAY_ACCESS", "Y"),
                         ("PTPT1000", "QUERY_TREE_PT", "PT_ACCESS", "Y"), ("HCPPALL", "QUERY_TREE_HR", "PAY_ACCESS", "N")],
    "PSTREEDEFN": [(" ", "QUERY_TREE_HR", "2000-01-01", "A"), (" ", "QUERY_TREE_HR", "2020-01-01", "A"),
                   (" ", "QUERY_TREE_HR", "2999-01-01", "A"), (" ", "QUERY_TREE_PT", "2000-01-01", "A")],
    "PSTREENODE": [
        # the current tree (2020)
        (" ", "QUERY_TREE_HR", "2020-01-01", "HR_ROOT", 1, 100, "G"),
        (" ", "QUERY_TREE_HR", "2020-01-01", "HR_ACCESS", 2, 20, "G"),
        (" ", "QUERY_TREE_HR", "2020-01-01", "JOB", 3, 3, "R"),
        (" ", "QUERY_TREE_HR", "2020-01-01", "PERSONAL_DATA", 4, 4, "R"),
        (" ", "QUERY_TREE_HR", "2020-01-01", "DEPT_TBL", 5, 5, "R"),
        (" ", "QUERY_TREE_HR", "2020-01-01", "COMPANY_TBL", 6, 6, "R"),
        (" ", "QUERY_TREE_HR", "2020-01-01", "PAY_ACCESS", 30, 40, "G"),
        (" ", "QUERY_TREE_HR", "2020-01-01", "PAY_CHECK", 31, 31, "R"),
        # an old version had PAY_CHECK under HR_ACCESS, a future one too: neither counts
        (" ", "QUERY_TREE_HR", "2000-01-01", "HR_ACCESS", 2, 20, "G"),
        (" ", "QUERY_TREE_HR", "2000-01-01", "PAY_CHECK", 3, 3, "R"),
        (" ", "QUERY_TREE_HR", "2999-01-01", "HR_ACCESS", 2, 20, "G"),
        (" ", "QUERY_TREE_HR", "2999-01-01", "PAY_CHECK", 3, 3, "R"),
        (" ", "QUERY_TREE_PT", "2000-01-01", "PT_ACCESS", 1, 10, "G"),
        (" ", "QUERY_TREE_PT", "2000-01-01", "PSPRSMDEFN", 2, 2, "R"),
    ],
    "PSQRYDEFN": [
        (" ", "AI_NEW_HIRES", "New Hires", "Employees hired between two dates, with department and location", " ", 1),
        (" ", "AI_HEADCOUNT", "Headcount", "", " ", 1),
        (" ", "AI_PAYCHECKS", "Paychecks", "", " ", 1),        # PAY_CHECK: BOSS only
        ("TESTER", "AI_MY_TEAM", "My team", "", " ", 1),      # TESTER's own private query
        ("OTHER", "AI_THEIRS", "Someone else's", "", " ", 1),  # another user's private query
        (" ", "NEW_HIRES", "Not curated", "", " ", 1),
        (" ", "AIXDEPT", "Not AI_ (an unescaped _ would match)", "", " ", 1),
        (" ", "AI_LEGACY", "Old prompts", "", " ", 1),
        (" ", "AI_ROLE", "A role query", "", " ", 2),
    ],
    "PSQRYRECORD": [(" ", "AI_NEW_HIRES", "JOB"), (" ", "AI_NEW_HIRES", "PERSONAL_DATA"), (" ", "AI_HEADCOUNT", "JOB"),
                    (" ", "AI_HEADCOUNT", "DEPT_TBL"), (" ", "AI_PAYCHECKS", "PAY_CHECK"), (" ", "AI_PAYCHECKS", "JOB"),
                    ("TESTER", "AI_MY_TEAM", "JOB"), ("OTHER", "AI_THEIRS", "JOB"), (" ", "NEW_HIRES", "JOB"), (" ", "AIXDEPT", "DEPT_TBL"),
                    (" ", "AI_LEGACY", "JOB"), (" ", "AI_ROLE", "JOB")],
    "PSQRYFIELD": [
        (" ", "AI_NEW_HIRES", 1, "ID", "EMPLID", "JOB", " "), (" ", "AI_NEW_HIRES", 2, "Name", "NAME", "PERSONAL_DATA", " "),
        (" ", "AI_NEW_HIRES", 3, " ", "HIRE_DT", "JOB", " "), (" ", "AI_NEW_HIRES", 4, "Department", "DEPTID", "JOB", " "),
        (" ", "AI_NEW_HIRES", 5, "Status", "EMPL_STATUS", "JOB", " "), (" ", "AI_NEW_HIRES", 6, "Annual Rate", "ANNUAL_RT", "JOB", " "),
        (" ", "AI_NEW_HIRES", 0, "hidden", "SETID_DEPT", "JOB", " "),
        (" ", "AI_HEADCOUNT", 1, "Department", "DEPTID", "JOB", " "), (" ", "AI_HEADCOUNT", 2, "Count", "EMPLID", "JOB", "C"),
    ],
    "PSQRYBIND": [
        (" ", "AI_NEW_HIRES", 1, "BIND1", "HIRE_DT", "Start Date", 4, 10, 0, 7, " ", 0, "Y"),
        (" ", "AI_NEW_HIRES", 2, "BIND2", "HIRE_DT", "End Date", 4, 10, 0, 7, " ", 0, "Y"),
        (" ", "AI_HEADCOUNT", 1, "BIND1", "COMPANY", "Company", 0, 3, 0, 0, "COMPANY_TBL", 0x4000, "Y"),
        (" ", "AI_HEADCOUNT", 2, "BIND2", "EMPL_STATUS", "Status", 0, 1, 0, 0, " ", 0x200, "N"),
        (" ", "AI_LEGACY", 1, ":1", "EFFDT", "As of", 4, 10, 0, 7, " ", 0, "Y"),
    ],
    "PSDBFIELD": [("EMPLID", 0), ("NAME", 0), ("HIRE_DT", 4), ("DEPTID", 0), ("EMPL_STATUS", 0), ("ANNUAL_RT", 3), ("COMPANY", 0)],
    "PSDBFLDLABL": [("HIRE_DT", "Hire Date", 1), ("DEPTID", "Department", 1)],
    "PSRECDEFN": [("JOB", "Job Data", " ", 0), ("PERSONAL_DATA", "Personal Data", " ", 0), ("DEPT_TBL", "Departments", " ", 0),
                  ("COMPANY_TBL", "Companies", " ", 0), ("PAY_CHECK", "Paychecks", " ", 0)],
    "PSRECFIELD": [("COMPANY_TBL", "COMPANY", 1, 1), ("COMPANY_TBL", "EFFDT", 2, 1)],
    "PSXLATITEM": [("EMPL_STATUS", "A", "Active", "A"), ("EMPL_STATUS", "T", "Terminated", "A")],
    "PS_COMPANY_TBL": [("KU1", "2000-01-01"), ("KU2", "2000-01-01")],
}


class PTDB:
    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.executescript(TABLES)
        for t, rows in ROWS.items():
            self.con.executemany(f"INSERT INTO {t} VALUES ({', '.join('?' * len(rows[0]))})", rows)
        self.queries = []

    def query(self, sql, params=None, max_rows=200, who="", why="", **kw):
        check_select(sql)
        self.queries.append(why)
        try:
            cur = self.con.execute(sql, params or {})
        except sqlite3.Error as e:
            raise QueryError(str(e)) from e
        rows = [list(r) for r in cur.fetchmany(max_rows + 1)]
        return [d[0] for d in cur.description], rows[:max_rows], len(rows) > max_rows


def ps_answer(title, heads, rows):
    """Like PeopleSoft's Run to Excel file: the title and row count, the headings, the rows (numbers as Excel writes them)."""
    return xlsx.write([{"name": "sheet1", "rows": [[title, f" {len(rows)}"], heads, *rows]}])


HIRES = [["K001", "Anita Rao", "46023.0", "10200", "A", "50000.0"], ["K002", "Mark Chen", "46054.0", "10200", "A", "62000.0"],
         ["K003", "Li Wei", "46100.0", "10300", "A", "58000.0"], ["K004", "Ana Silva", "46150.0", "10200", "T", "41000.0"],
         ["K005", "Raj Iyer", "46200.0", "10300", "A", "70000.0"], ["K006", "Eva Novak", "46210.0", "10200", "A", "52000.0"],
         ["K007", "Tom Berg", "46220.0", "10400", "A", "48000.0"]]
HEADS = ["ID", "Name", "Hire Date", "Department", "Status", "Annual Rate"]


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.db = PTDB()
        self.cat = queries.Catalog(self.db)

    def test_only_the_users_ai_queries(self):
        self.assertEqual(sorted(q["name"] for q in self.cat.for_user("TESTER")), ["AI_HEADCOUNT", "AI_LEGACY", "AI_MY_TEAM", "AI_NEW_HIRES"])
        self.assertIn("AI_PAYCHECKS", [q["name"] for q in self.cat.for_user("BOSS")], "PAY_CHECK is in BOSS's access groups")
        self.assertNotIn("AI_MY_TEAM", [q["name"] for q in self.cat.for_user("BOSS")], "a private query is its owner's only")
        self.assertEqual(self.cat.for_user("NOBODY"), [])
        self.assertIsNone(self.cat.get("TESTER", "AI_PAYCHECKS"))
        self.assertTrue(self.cat.get("TESTER", "ai_my_team")["private"])

    def test_columns_prompts_values(self):
        q = self.cat.get("TESTER", "AI_NEW_HIRES")
        v = queries.view(q)
        self.assertEqual(v["columns"], ["ID", "Name", "Hire Date", "Department", "Status", "Annual Rate"])
        self.assertEqual([c["sensitive"] for c in q["columns"]], [False, False, False, False, False, True])
        self.assertEqual(v["prompts"], [{"name": "BIND1", "label": "Start Date", "type": "date", "required": True},
                                        {"name": "BIND2", "label": "End Date", "type": "date", "required": True}])
        h = queries.view(self.cat.get("TESTER", "AI_HEADCOUNT"))
        self.assertEqual(h["columns"], ["Department", "Count (C)"])
        self.assertEqual(h["prompts"][1]["values"], ["A = Active", "T = Terminated"])
        self.assertIn("no name", self.cat.get("TESTER", "AI_LEGACY")["problem"])

    def test_find(self):
        cat = self.cat.for_user("TESTER")
        self.assertEqual(len(queries.find(cat, "anything")), 4, "a few curated queries: all of them")
        self.assertEqual(queries.find(cat, "new hires by department", limit=1)[0]["name"], "AI_NEW_HIRES")


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.db = PTDB()
        self.cat = queries.Catalog(self.db)

    def test_dates_always_iso(self):
        q = self.cat.get("TESTER", "AI_NEW_HIRES")
        for start in ("2026-01-01", "20260101", "01/01/2026", "Jan 1, 2026", "46023"):
            self.assertEqual(queries.check_prompts(q, {"BIND1": start, "End Date": "2026-10-31"}),
                             ([("BIND1", "2026-01-01"), ("BIND2", "2026-10-31")], []), start)
        params, problems = queries.check_prompts(q, {"BIND1": "soon"})
        self.assertEqual(problems, ["Start Date soon is not a date (use YYYY-MM-DD)", "End Date is required"])
        self.assertIn("has no prompt BIND9", queries.check_prompts(q, {"BIND9": "x", "BIND1": "2026-01-01", "BIND2": "2026-02-01"})[1][0])

    def test_prompt_table_translate_uppercase_optional(self):
        q = self.cat.get("TESTER", "AI_HEADCOUNT")
        self.assertEqual(queries.check_prompts(q, {"Company": "ku1", "Status": "Active"}, self.db, "TESTER"),
                         ([("BIND1", "KU1"), ("BIND2", "A")], []))
        self.assertEqual(queries.check_prompts(q, {"BIND1": "KU1"}, self.db, "TESTER"), ([("BIND1", "KU1"), ("BIND2", "")], []),
                         "an optional prompt goes in the link empty")
        _, problems = queries.check_prompts(q, {"BIND1": "KU9", "BIND2": "X"}, self.db, "TESTER")
        self.assertEqual(problems, ["Status X is not one of: A (Active), T (Terminated)", "Company KU9 does not exist (prompt table COMPANY_TBL)"])


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self.q = queries.Catalog(PTDB()).get("TESTER", "AI_NEW_HIRES")
        self.res = queries.read(ps_answer("New Hires", HEADS, HIRES), self.q)

    def test_read(self):
        self.assertEqual((self.res["title"], self.res["count"], self.res["headings"]), ("New Hires", 7, HEADS))
        self.assertEqual(self.res["rows"][0], ["K001", "Anita Rao", "2026-01-01", "10200", "A", "50000"])

    def test_claude_sees_totals_never_rows(self):
        s = queries.summary(self.res, self.q)
        text_ = json.dumps(s)
        for secret in ("Anita", "K001", "50000", "62000"):
            self.assertNotIn(secret, text_)
        cols = {c["column"]: c for c in s["columns"]}
        self.assertEqual(cols["Department"]["counts"], [["10200", 4], ["10300", 2], ["10400", 1]])
        self.assertEqual((cols["Hire Date"]["from"], cols["Hire Date"]["to"]), ("2026-01-01", "2026-07-17"))
        self.assertNotIn("counts", cols["Name"], "names tell the rows apart")
        self.assertEqual(cols["Annual Rate"], {"column": "Annual Rate", "hidden": "sensitive: not shown to you"})

    def test_breakdowns(self):
        self.assertEqual(queries.aggregate(self.res, self.q, ["Department"]),
                         {"by": ["Department"], "value": "rows", "rows": [["10200", 4], ["10300", 2], ["10400", 1]]})
        self.assertEqual(queries.aggregate(self.res, self.q, ["Department", "Status"])["rows"][0], ["10200", "A", 3])
        for bad, why in ((["Name"], "tells the rows apart"), (["ID"], "tells the rows apart")):
            with self.assertRaisesRegex(queries.QueryAskError, why):
                queries.aggregate(self.res, self.q, bad)
        with self.assertRaisesRegex(queries.QueryAskError, "sensitive"):
            queries.aggregate(self.res, self.q, ["Department"], "Annual Rate", "sum")
        with self.assertRaisesRegex(queries.QueryAskError, "no column Salary"):
            queries.aggregate(self.res, self.q, ["Salary"])
        only = queries.aggregate(self.res, self.q, ["Department"], filters=[{"column": "Status", "value": "a"}])
        self.assertEqual((only["filters"], only["rows"]), ([["Status", "a"]], [["10200", 3], ["10300", 2], ["10400", 1]]))

    def test_lookup_names_the_few_matching_rows(self):
        """"Who has role X?": the matches, at most FIND_ROWS of them and never a sensitive column."""
        got = queries.lookup(self.res, self.q, [{"column": "Department", "value": "10300"}])
        self.assertEqual(got, {"filters": [["Department", "10300"]], "matches": 2, "columns": HEADS[:5],
                               "rows": [["K003", "Li Wei", "2026-03-19", "10300", "A"], ["K005", "Raj Iyer", "2026-06-27", "10300", "A"]]})
        self.assertNotIn("58000", json.dumps(got))
        got = queries.lookup(self.res, self.q, [{"column": "Name", "value": "%a%"}, {"column": "status", "value": "A"}], limit=2)
        self.assertEqual((got["matches"], len(got["rows"]), got["more"]), (4, 2, 2), "% = any characters, case ignored")
        self.assertEqual(queries.lookup(self.res, self.q, [{"column": "ID", "value": "K9%"}])["matches"], 0)
        with self.assertRaisesRegex(queries.QueryAskError, "sensitive"):
            queries.lookup(self.res, self.q, [{"column": "Annual Rate", "value": "50000"}])
        with self.assertRaisesRegex(queries.QueryAskError, "at least one filter"):
            queries.lookup(self.res, self.q, [])
        # find_rows is not strict: whatever Claude sends is checked here
        with self.assertRaisesRegex(queries.QueryAskError, "at least one filter"):
            queries.lookup(self.res, self.q, "Department=10300")
        with self.assertRaisesRegex(queries.QueryAskError, "each filter is"):
            queries.lookup(self.res, self.q, ["Department=10300"])
        self.assertEqual(queries.aggregate(self.res, self.q, "Department", filters=None)["rows"][0], ["10200", 4])


class AgentQueryTest(unittest.TestCase):
    def setUp(self):
        self.db = PTDB()
        self.llm = FakeLLM()
        self.agent = Agent(self.llm, {}, self.db)
        self.agent.queries = queries.Catalog(self.db)
        self.s = Session(id="s1", oprid="TESTER")

    def test_ask_run_answer_breakdown(self):
        self.llm.responses += [
            response(tool("find_queries", {"question": "how many new hires this year by department"}, id="f")),
            response(tool("run_query", {"name": "AI_NEW_HIRES", "prompts": [{"name": "BIND1", "value": "2026-01-01"},
                                                                            {"name": "End Date", "value": "10/31/2026"}]}, id="r")),
            response(text("Running **AI_NEW_HIRES** (New Hires) from 2026-01-01 to 2026-10-31.")),
            response(tool("summarize_result", {"group_by": ["Department"], "op": "count", "measure": ""}, id="g")),
            response(text("**7** new hires this year: **4** in 10200, 2 in 10300 and 1 in 10400 (AI_NEW_HIRES).")),
        ]
        r = self.agent.chat(self.s, "how many new hires this year by department?")
        found = json.loads(self.llm.calls[1][-1]["content"][0]["content"])
        self.assertEqual([q["name"] for q in found["queries"]], ["AI_HEADCOUNT", "AI_LEGACY", "AI_MY_TEAM", "AI_NEW_HIRES"])
        run = r.query_run
        self.assertEqual((run["name"], run["private"], run["params"]), ("AI_NEW_HIRES", False, [("BIND1", "2026-01-01"), ("BIND2", "2026-10-31")]))
        self.assertEqual(run["shown"], [{"label": "Start Date", "value": "2026-01-01"}, {"label": "End Date", "value": "2026-10-31"}])
        r = self.agent.query_result(self.s, run["run_id"], ps_answer("New Hires", HEADS, HIRES))
        self.assertEqual(r.messages, ["**7** new hires this year: **4** in 10200, 2 in 10300 and 1 in 10400 (AI_NEW_HIRES)."])
        self.assertEqual((r.query_result["count"], r.query_result["rows"][1][1]), (7, "Mark Chen"), "the card has the rows")
        self.assertEqual(r.query_chart["rows"], [["10200", 4], ["10300", 2], ["10400", 1]])
        sent = json.dumps([m["content"] for m in self.llm.calls[-1] if m["role"] == "user"], default=str)
        for secret in ("Anita", "Mark Chen", "K001", "62000"):
            self.assertNotIn(secret, sent, "Claude never sees the rows")
        self.assertEqual(self.agent.query_result(self.s, run["run_id"], b"x").messages, ["I don't have that query run any more. Ask me again."])

    def test_who_has_a_value(self):
        self.llm.responses += [
            response(tool("run_query", {"name": "AI_NEW_HIRES", "prompts": [{"name": "BIND1", "value": "2026-01-01"},
                                                                            {"name": "BIND2", "value": "2026-12-31"}]}, id="r")),
            response(text("Running AI_NEW_HIRES.")),
            response(tool("find_rows", {"filters": [{"column": "Department", "value": "10300"}]}, id="f")),
            response(text("Two of them joined 10300: **Li Wei** and **Raj Iyer**.")),
        ]
        r = self.agent.chat(self.s, "who joined department 10300 this year?")
        r = self.agent.query_result(self.s, r.query_run["run_id"], ps_answer("New Hires", HEADS, HIRES))
        self.assertEqual(r.messages, ["Two of them joined 10300: **Li Wei** and **Raj Iyer**."])
        self.assertEqual((r.query_rows["count"], r.query_rows["filters"], r.query_rows["headings"]), (2, [["Department", "10300"]], HEADS))
        self.assertEqual(r.query_rows["rows"][1][5], "70000", "the user's card has every column")
        sent = json.loads(self.llm.calls[-1][-1]["content"][0]["content"])
        self.assertEqual((sent["matches"], sent["rows"][0][1]), (2, "Li Wei"))
        self.assertNotIn("70000", json.dumps(sent), "Claude never sees a sensitive column")

    def test_refused_queries_and_prompts(self):
        self.llm.responses += [
            response(tool("run_query", {"name": "AI_PAYCHECKS", "prompts": []}, id="a"),
                     tool("run_query", {"name": "AI_NEW_HIRES", "prompts": [{"name": "BIND1", "value": "soon"}]}, id="b"),
                     tool("run_query", {"name": "AI_LEGACY", "prompts": []}, id="c")),
            response(text("Which dates?")),
        ]
        r = self.agent.chat(self.s, "paychecks and new hires")
        self.assertIsNone(r.query_run)
        out = [json.loads(x["content"]) for x in self.llm.calls[1][-1]["content"]]
        self.assertIn("not one of the user's AI_ queries", out[0]["error"])
        self.assertIn("not a date", out[1]["error"])
        self.assertIn("cannot be run from here", out[2]["error"])

    def test_peoplesoft_shows_something_else(self):
        self.llm.responses += [
            response(tool("run_query", {"name": "AI_HEADCOUNT", "prompts": [{"name": "BIND1", "value": "KU1"}]}, id="a")),
            response(text("Running it.")),
            response(text("PeopleSoft could not run AI_HEADCOUNT: the query definition is missing.")),
        ]
        r = self.agent.chat(self.s, "headcount for KU1")
        r = self.agent.query_result(self.s, r.query_run["run_id"], None, "[CQryStmt::Prepare] QDM could not load query definition")
        self.assertIn("could not load query definition", self.llm.calls[-1][-1]["content"])
        self.assertIsNone(r.query_result)


class ApiTest(unittest.TestCase):
    def test_list_and_result(self):
        from starlette.testclient import TestClient
        from psagent import auth
        from psagent.server import create_app
        db, llm = PTDB(), FakeLLM()
        c = TestClient(create_app(Agent(llm, {}, db), "s3cret", ["http://ps"], query_catalog=queries.Catalog(db)))
        h = lambda o: {"Authorization": "Bearer " + auth.make_token("s3cret", o, roles=["USER"])}
        self.assertTrue(c.get("/api/me", headers=h("TESTER")).json()["ask_data"])
        names = [q["name"] for q in c.get("/api/queries", headers=h("TESTER")).json()["queries"]]
        self.assertEqual(names, ["AI_HEADCOUNT", "AI_LEGACY", "AI_MY_TEAM", "AI_NEW_HIRES"])
        self.assertEqual(c.get("/api/queries", headers=h("NOBODY")).json()["queries"], [])
        llm.responses += [response(tool("run_query", {"name": "AI_NEW_HIRES", "prompts": [
            {"name": "BIND1", "value": "2026-01-01"}, {"name": "BIND2", "value": "2026-12-31"}]}, id="r")),
            response(text("Running it.")), response(text("7 new hires."))]
        r = c.post("/api/chat", json={"message": "new hires in 2026"}, headers=h("TESTER")).json()
        body = {"session_id": r["session_id"], "run_id": r["query_run"]["run_id"],
                "data": base64.b64encode(ps_answer("New Hires", HEADS, HIRES)).decode()}
        self.assertEqual(c.post("/api/query/result", json=body, headers=h("BOSS")).status_code, 404, "another user's session")
        r = c.post("/api/query/result", json=body, headers=h("TESTER")).json()
        self.assertEqual((r["messages"], r["query_result"]["count"]), (["7 new hires."], 7))


if __name__ == "__main__":
    unittest.main()
