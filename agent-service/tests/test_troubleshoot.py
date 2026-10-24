import json
import re
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from psagent import auth
from psagent.agent import Agent, Session
from psagent.db import QueryError, check_select, masked
from psagent.server import create_app
from psagent.tasks import load_tasks
from psagent.troubleshoot import numbered

from fakes import FakeLLM, response, text, tool

TASKS = load_tasks(Path(__file__).resolve().parent / "fixtures" / "tasks")

# Rows as they were on the real system for instance 557060 (AEMINITEST, division by zero in Step02)
AEMINITEST_PC = '''/* Declare local variables for validation test */
Local number &nTest = 0;
Local string &sName = "PSIQ";

&test = 1 / 0;
/* Increment &nTest by 1 */
&nTest = &nTest + 1;
'''


class FakeDB:
    """Answers the troubleshooter's queries from canned rows, and checks each one is read-only."""

    def __init__(self):
        self.queries = []

    def query(self, sql, params=None, max_rows=200, who="", why=""):
        check_select(sql)                       # every query the tools build must pass the read-only check
        self.queries.append((" ".join(sql.split()), params, why))
        s = sql.upper()
        if "FROM PSPRCSRQST WHERE OPRID" in s:
            return ["PRCSINSTANCE"], [[557060]], False
        if "FROM PS_CDM_LIST" in s:
            return (["URL", "CONTENTID", "FILENAME", "FILE_SIZE"],
                    [["http://ps:8000/psreports/ps", 85515, "AE_AEMINITEST_557060.stdout", 512],
                     ["http://ps:8000/psreports/ps", 85515, "index.html", 900]], False)
        if "FROM PSPRCSRQST WHERE PRCSINSTANCE" in s:
            return (["PRCSINSTANCE", "PRCSTYPE", "PRCSNAME", "RUNSTATUS", "OPRID"],
                    [[557060, "Application Engine", "AEMINITEST", "10", "PS"]], False)
        if "FROM PS_PRCSDEFN" in s:
            return ["DESCR"], [["Simple AE test program"]], False
        if "FROM PSPRCSPARMS" in s:
            return ["PARMLIST", "ORIGPARMLIST"], [['-CT ORACLE -CO "PS" -CP secret123 -I 557060', ' ']], False
        if "FROM PS_MESSAGE_LOG L" in s:
            return (["MESSAGE_SEQ", "MESSAGE_SET_NBR", "MESSAGE_NBR", "MESSAGE_SEVERITY", "DTTM_STAMP_SEC", "MESSAGE_TEXT"],
                    [[1, 65, 30, "E", None, "%1 %2 %3 %4"],
                     [2, 108, 524, "E", None, "Process %1 ABENDED at Step %2.%3.%4 (%5) -- RC = %6"]], False)
        if "FROM PS_MESSAGE_LOGPARM" in s:
            return (["MESSAGE_SEQ", "PARM_SEQ", "MESSAGE_PARM"],
                    [[1, 1, "Decimal division by zero occurred. (180,109) AEMINITEST.MAIN.GBL.default.1900-01-01.Step02.OnExecute"],
                     [1, 2, "  PCPC:108  Statement:3"],
                     [2, 1, "557060"], [2, 2, "AEMINITEST"], [2, 3, "MAIN"], [2, 4, "Step02"], [2, 5, "PeopleCode"], [2, 6, "8"]], False)
        if "FROM PSPRCSRQST WHERE PRCSNAME" in s:
            return ["PRCSINSTANCE", "RUNSTATUS", "RUNCNTLID", "OPRID", "ENDDTTM"], [[557053, "9", "AELHSB46", "PS", None]], False
        if "FROM PSPCMTXT" in s:
            if params != {"p": "AEMINITEST", "s": "MAIN", "t": "Step02"}:   # names as stored: steps keep their case
                return ["MARKET", "PROGSEQ", "PCTEXT"], [], False
            return ["MARKET", "PROGSEQ", "PCTEXT"], [["GBL", 0, AEMINITEST_PC]], False
        if "PS_JOB" in s:
            return ["EMPLID", "ANNUAL_RT"], [["K0LC15", "***"]], False
        raise QueryError(f"unexpected query: {sql[:80]}")


def investigating_llm(*more):
    return FakeLLM(
        response(tool("troubleshoot_process", {"instance": "", "question": "why did my process fail?"})),
        response(text("Looking into process 557060 now.")),
        response(tool("get_process", {"instance": "557060"})),
        response(tool("get_peoplecode", {"program": "AEMINITEST", "section": "MAIN", "step": "Step02"}, id="p"),
                 tool("query", {"sql": "SELECT EMPLID, ANNUAL_RT FROM PS_JOB WHERE ROWNUM <= 1", "purpose": "sample job row"}, id="q")),
        response(tool("finish", {
            "category": "code", "confidence": "high",
            "summary": "AEMINITEST fails because Step02 divides by zero on purpose.",
            "where": "AEMINITEST.MAIN.Step02 (PeopleCode), statement 3, line 5",
            "root_cause": "&test = 1 / 0; always divides by zero.",
            "evidence": ["Message log: Decimal division by zero occurred. (180,109) ... Statement:3", "Line 5: &test = 1 / 0;"],
            "fix_steps": ["Remove line 5 in App Designer and save.", "Rerun AEMINITEST."],
            "code_fix": "Local number &nTest = 0;\nLocal string &sName = \"PSIQ\";\n&nTest = &nTest + 1;",
            "rerun": "Run AEMINITEST again from Request AE."}, id="f")),
        *more)


class GuardTest(unittest.TestCase):
    def test_only_single_selects(self):
        for ok in ["SELECT * FROM PS_JOB", "with x as (select 1 from dual) select * from x", "SELECT 'DELETE' FROM DUAL",
                   "SELECT DBMS_LOB.SUBSTR(PCTEXT, 100, 1) FROM PSPCMTXT", "select * from ps_job; "]:
            check_select(ok)
        for bad in ["DELETE FROM PS_JOB", "UPDATE PS_JOB SET X=1", "SELECT 1 FROM DUAL; DELETE FROM PS_JOB",
                    "SELECT * FROM PS_JOB FOR UPDATE", "BEGIN NULL; END;", "SELECT * FROM PS_JOB@REMOTE",
                    "SELECT DBMS_SCHEDULER.X FROM DUAL", "SELECT UTL_HTTP.REQUEST('x') FROM DUAL", "", "TRUNCATE TABLE PS_JOB",
                    "SELECT * FROM SYS.USER$", "WITH x AS (SELECT 1 FROM DUAL) DELETE FROM PS_JOB"]:
            with self.assertRaises(QueryError, msg=bad):
                check_select(bad)

    def test_sensitive_columns(self):
        for col in ["NATIONAL_ID", "BIRTHDATE", "ANNUAL_RT", "SUM(ANNUAL_RT)", "OPERPSWD", "BANK_CD", "ACCOUNT_NUM"]:
            self.assertTrue(masked(col), col)
        for col in ["EMPLID", "DEPTID", "COMPANY", "GRADE", "PRCSINSTANCE"]:
            self.assertFalse(masked(col), col)

    def test_statement_numbers(self):
        code = numbered(AEMINITEST_PC)
        self.assertIn("[stmt 3] &test = 1 / 0;", code)
        self.assertIn("[stmt 1] Local number", code)
        self.assertNotIn("[stmt", code.splitlines()[0])              # comments are not statements


class TroubleshootTest(unittest.TestCase):
    def test_from_question_to_diagnosis(self):
        db, llm = FakeDB(), investigating_llm()
        agent, s = Agent(llm, TASKS, db), Session(id="s", oprid="PS")
        r = agent.chat(s, "my process failed, can you check?")
        t = r.troubleshoot
        self.assertEqual(t["instance"], "557060")                     # the user's latest failed request
        self.assertEqual(t["logs"], [{"name": "AE_AEMINITEST_557060.stdout", "size": 512,
                                      "path": "/psreports/ps/85515/AE_AEMINITEST_557060.stdout"}])
        logs = [{"name": "AE_AEMINITEST_557060.stdout", "text": "Process 557060 ABENDED at Step AEMINITEST.MAIN.Step02"}]
        a = agent.troubleshoot_step(s, t["run_id"], logs)
        self.assertEqual(a["steps"], ["Read process 557060: status, message log, earlier runs"])
        opening = llm.calls[2][0]["content"]
        self.assertIn("<log_file name=\"AE_AEMINITEST_557060.stdout\">", opening)   # the posted log reached Claude
        result = json.loads(s.investigation.messages[-1]["content"][0]["content"])   # what Claude gets back
        self.assertEqual(result["request"]["RUNSTATUS"], "10 (No Success)")
        self.assertEqual(" ".join(result["message_log"][0]["text"].split()),
                         "Decimal division by zero occurred. (180,109) AEMINITEST.MAIN.GBL.default.1900-01-01.Step02.OnExecute "
                         "PCPC:108 Statement:3")
        self.assertEqual(result["message_log"][1]["text"],
                         "Process 557060 ABENDED at Step AEMINITEST.MAIN.Step02 (PeopleCode) -- RC = 8")
        self.assertIn("-CP ***", result["parameters"][0]["PARMLIST"])   # password parameter hidden
        b = agent.troubleshoot_step(s, t["run_id"])
        self.assertEqual(b["steps"], ["Read PeopleCode AEMINITEST.MAIN.Step02", "Query: sample job row (1 rows)"])
        c = agent.troubleshoot_step(s, t["run_id"])
        self.assertTrue(c["done"])
        d = c["diagnosis"]
        self.assertEqual((d["category"], d["instance"]), ("code", "557060"))
        self.assertEqual(d["queries"], [{"purpose": "sample job row", "sql": "SELECT EMPLID, ANNUAL_RT FROM PS_JOB WHERE ROWNUM <= 1", "rows": 1}])
        self.assertIn("[troubleshoot result] process 557060", s.messages[-1]["content"])   # follow-ups know it
        self.assertIsNone(s.investigation)
        self.assertTrue(all(q[0].upper().startswith(("SELECT", "WITH")) for q in db.queries))

    def test_refused_query_goes_back_to_claude(self):
        llm = FakeLLM(response(tool("troubleshoot_process", {"instance": "557060", "question": ""})), response(text("ok")),
                      response(tool("query", {"sql": "DELETE FROM PS_JOB", "purpose": "oops"})))
        agent, s = Agent(llm, TASKS, FakeDB()), Session(id="s", oprid="PS")
        t = agent.chat(s, "check 557060").troubleshoot
        step = agent.troubleshoot_step(s, t["run_id"])
        self.assertIn("refused", step["steps"][0])
        self.assertEqual(s.investigation.messages[-1]["content"][0]["is_error"], True)

    def test_without_database(self):
        llm = FakeLLM(response(tool("troubleshoot_process", {"instance": "557060", "question": ""})),
                      response(text("I can't troubleshoot without database access.")))
        r = Agent(llm, TASKS).chat(Session(id="s", oprid="PS"), "why did 557060 fail")
        self.assertIsNone(r.troubleshoot)
        self.assertIn("PSAGENT_DB_", json.dumps(llm.calls[-1][-1]["content"]))

    def test_api(self):
        app = create_app(Agent(investigating_llm(), TASKS, FakeDB()), "k", [])
        c = TestClient(app)
        h = {"Authorization": f"Bearer {auth.make_token('k', 'PS')}"}
        r = c.post("/api/chat", json={"message": "my process failed"}, headers=h).json()
        sid, run = r["session_id"], r["troubleshoot"]["run_id"]
        done = False
        for _ in range(4):
            out = c.post("/api/troubleshoot/step", json={"session_id": sid, "run_id": run, "logs": []}, headers=h).json()
            done = out["done"]
            if done:
                break
        self.assertTrue(done)
        self.assertEqual(out["diagnosis"]["category"], "code")
        other = {"Authorization": f"Bearer {auth.make_token('k', 'X')}"}
        self.assertEqual(c.post("/api/troubleshoot/step", json={"session_id": sid, "run_id": run}, headers=other).status_code, 404)


if __name__ == "__main__":
    unittest.main()
