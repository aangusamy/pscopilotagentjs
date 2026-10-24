import base64
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from psagent import auth
from psagent.agent import Agent, Session, check_attachment
from psagent.server import create_app
from psagent.tasks import load_tasks

from fakes import FakeLLM, response, text, tool

TASKS = load_tasks(Path(__file__).resolve().parent / "fixtures" / "tasks")
PDF = {"name": "access request.pdf", "media_type": "application/pdf",
       "data": base64.b64encode(b"%PDF-1.4 fake access request").decode()}


def batch_llm(*more):
    """Claude reads a form listing two new users; one role is written in words (unsure)."""
    return FakeLLM(
        response(tool("prepare_batch", {"task_id": "create_user_profile", "items": [
            {"label": "JSMITH - Jane Smith", "fields": [{"name": "user_id", "value": "jsmith"},
                                                        {"name": "description", "value": "Jane Smith"},
                                                        {"name": "roles", "value": "EMPLOYEE"}], "unsure": []},
            {"label": "RKUMAR - Ravi Kumar", "fields": [{"name": "user_id", "value": "rkumar"},
                                                        {"name": "roles", "value": "Manager"}],
             "unsure": [{"name": "roles", "note": "form says 'manager access'"}]},
        ]}), tool("request_confirmation", {}, id="c")),
        response(text("Two people found. Please review the table.")), *more)


class DocumentTest(unittest.TestCase):
    def test_attachment_reaches_claude_as_a_document(self):
        llm = FakeLLM(response(text("I read the form.")))
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        att, err = check_attachment(PDF)
        self.assertIsNone(err)
        agent.chat(s, "create these users", None, att)
        first = llm.calls[0][0]["content"]
        self.assertEqual(first[0]["type"], "document")
        self.assertEqual(first[0]["source"]["media_type"], "application/pdf")
        self.assertIn("[attached: access request.pdf]\ncreate these users", first[1]["text"])

    def test_images_and_limits(self):
        png = {"name": "scan.png", "media_type": "image/png", "data": base64.b64encode(b"\x89PNG....").decode()}
        att, _ = check_attachment(png)
        llm = FakeLLM(response(text("ok")))
        Agent(llm, TASKS).chat(Session(id="s", oprid="PS"), "x", None, att)
        self.assertEqual(llm.calls[0][0]["content"][0]["type"], "image")
        for bad in [{**PDF, "media_type": "application/msword"}, {**PDF, "data": "not base64!"},
                    {**PDF, "data": ""}, {**PDF, "media_type": "image/png", "data": base64.b64encode(b"x" * (6 << 20)).decode()}]:
            self.assertIsNone(check_attachment(bad)[0], bad["media_type"])

    def test_review_table_edit_untick_and_run(self):
        llm = batch_llm(response(text("Created RKUMAR. JSMITH was skipped by you.")))
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        r = agent.chat(s, "create these", None, check_attachment(PDF)[0])
        card = r.batch
        self.assertEqual([row["label"] for row in card["rows"]], ["JSMITH - Jane Smith", "RKUMAR - Ravi Kumar"])
        self.assertEqual(card["rows"][0]["values"]["user_id"], "JSMITH")              # normalized (upper)
        self.assertEqual(card["rows"][0]["values"]["symbolic_id"], "SYSADM1")         # defaults shown
        self.assertEqual(card["rows"][1]["missing"], ["description"])                 # not on the form
        self.assertEqual(card["rows"][1]["unsure"], {"roles": "form says 'manager access'"})
        self.assertEqual(card["secrets"], [{"name": "password", "label": "Password"}])
        # missing value not filled: refused, table shown again
        again = agent.decide(s, card["batch_id"], True, [{"index": 0, "include": False}])
        self.assertIn("Description missing", again.messages[0])
        card = again.batch
        self.assertEqual(len(card["rows"]), 1)
        # filled in the table: runs only the included row, with the edit
        done = agent.decide(s, card["batch_id"], True, [{"index": 0, "include": True, "values": {"description": "Ravi Kumar"}}])
        items = done.batch_action["items"]
        self.assertEqual([i["label"] for i in items], ["RKUMAR - Ravi Kumar"])
        desc = next(st for st in items[0]["steps"] if st.get("field", {}).get("label") == "Description")
        self.assertEqual(desc["value"], "Ravi Kumar")
        event = s.messages[-1]["content"]
        self.assertIn("Description=Ravi Kumar", event)                       # the chat knows the edited values
        self.assertNotIn("Password", event)
        out = agent.report_batch(s, done.batch_action["batch_id"],
                                 [{"label": "RKUMAR - Ravi Kumar", "status": "success", "message": "Saved"}])
        self.assertEqual(out.messages, ["Created RKUMAR. JSMITH was skipped by you."])
        self.assertIn("- RKUMAR - Ravi Kumar: success", llm.calls[-1][-1]["content"])

    def test_bad_edit_is_refused(self):
        agent, s = Agent(batch_llm(), TASKS), Session(id="s", oprid="PS")
        card = agent.chat(s, "create these", None, check_attachment(PDF)[0]).batch
        r = agent.decide(s, card["batch_id"], True, [{"index": 0, "values": {"user_id": "has space"}},
                                                      {"index": 1, "include": False}])
        self.assertIsNone(r.batch_action)
        self.assertIn("JSMITH - Jane Smith:", r.messages[0])

    def test_single_item_unsure_is_highlighted(self):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "VELU"},
                                                    {"name": "description", "value": "Velu A"}]}, id="b"),
                     tool("mark_unsure", {"fields": [{"name": "description", "note": "handwritten"}]}, id="u"),
                     tool("request_confirmation", {}, id="c")),
            response(text("Please confirm.")))
        r = Agent(llm, TASKS).chat(Session(id="s", oprid="PS"), "from this form", None, check_attachment(PDF)[0])
        rows = {x["label"]: x for x in r.confirm["rows"]}
        self.assertEqual(rows["Description"]["unsure"], "handwritten")
        self.assertNotIn("unsure", rows["User ID"])

    def test_api(self):
        c = TestClient(create_app(Agent(batch_llm(), TASKS), "k", []))
        h = {"Authorization": f"Bearer {auth.make_token('k', 'PS')}"}
        self.assertEqual(c.post("/api/chat", json={"message": "", "attachment": {**PDF, "media_type": "text/plain"}},
                                headers=h).status_code, 400)
        r = c.post("/api/chat", json={"message": "", "attachment": PDF}, headers=h).json()   # file alone is fine
        self.assertEqual(len(r["batch"]["rows"]), 2)
        r2 = c.post("/api/batch/result", json={"session_id": r["session_id"], "batch_id": "nope", "results": []}, headers=h)
        self.assertIn("don't have that batch", r2.json()["messages"][0])


if __name__ == "__main__":
    unittest.main()
