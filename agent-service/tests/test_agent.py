import json
import unittest
from pathlib import Path

from psagent.agent import Agent, Session
from psagent.llm import LLMError
from psagent.tasks import load_tasks

from fakes import FakeLLM, response, text, tool

TASKS = load_tasks(Path(__file__).resolve().parent / "fixtures" / "tasks")


def results_of(messages):
    """tool_result payloads in the last user message, decoded."""
    return [json.loads(r["content"]) for r in messages[-1]["content"]]


class AgentFlowTest(unittest.TestCase):
    def test_create_user_happy_path(self):
        llm = FakeLLM(
            # turn 1: "create user profile named velu"
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "velu"}]}, id="t2")),
            response(text("What description should I use? I'll use Symbolic ID **SYSADM1**.")),
            # turn 2: "Velu Arumugam, role EMPLOYEE"
            response(tool("set_fields", {"fields": [{"name": "description", "value": "Velu Arumugam"},
                                                     {"name": "roles", "value": "EMPLOYEE"}]})),
            response(tool("request_confirmation", {})),
            response(text("Please review the card and click **Confirm**.")),
            # turn 3: automation result
            response(text("Done: user **VELU** was created.")),
        )
        agent, s = Agent(llm, TASKS), Session(id="s1", oprid="PS")

        r1 = agent.chat(s, "create user profile named velu", {"title": "Homepage"})
        self.assertEqual(r1.messages, ["What description should I use? I'll use Symbolic ID **SYSADM1**."])
        self.assertIsNone(r1.confirm)
        self.assertEqual(s.values, {"user_id": "VELU"})
        self.assertIn("<page_context>\ntitle: Homepage", llm.calls[0][0]["content"])
        start, setf = results_of(llm.calls[1])
        self.assertEqual(start["missing_required"], ["User ID", "Description"])
        self.assertEqual(setf["missing_required"], ["Description"])

        r2 = agent.chat(s, "Velu Arumugam, role EMPLOYEE")
        self.assertEqual(r2.confirm["title"], "Create User Profile")
        self.assertIn({"label": "Symbolic ID", "value": "SYSADM1"}, r2.confirm["rows"])
        self.assertEqual(r2.confirm["secrets"], [{"name": "password", "label": "Password"}])
        self.assertIsNone(r2.action)  # nothing runs before the user clicks Confirm

        r3 = agent.decide(s, r2.confirm["run_id"], approved=True)
        self.assertEqual(r3.action["task_id"], "create_user_profile")
        self.assertEqual(r3.action["component"]["component"], "USERMAINT")
        grid = next(st for st in r3.action["steps"] if st["do"] == "grid")
        self.assertEqual(grid["values"], ["EMPLOYEE"])
        self.assertEqual(len(llm.responses), 1)  # confirming needs no LLM call

        r4 = agent.report(s, r2.confirm["run_id"], "success", "Saved")
        self.assertEqual(r4.messages, ["Done: user **VELU** was created."])
        self.assertIsNone(s.task_id)
        self.assertIn("[automation result]", llm.calls[-1][-1]["content"])

    def test_confirmation_refused_while_required_missing(self):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("request_confirmation", {}, id="t2")),
            response(text("What User ID and description?")),
        )
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        r = agent.chat(s, "create a user")
        self.assertIsNone(r.confirm)
        last = llm.calls[-1][-1]["content"]
        self.assertTrue(last[1]["is_error"])
        self.assertEqual(json.loads(last[1]["content"])["missing_required"], ["User ID", "Description"])

    def test_changing_values_invalidates_card(self):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "VELU"},
                                                    {"name": "description", "value": "Velu"}]}, id="b"),
                     tool("request_confirmation", {}, id="c")),
            response(text("Please confirm.")),
            response(tool("set_fields", {"fields": [{"name": "user_id", "value": "VELU2"}]})),
            response(text("Changed. Want a new summary?")),
        )
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        old = agent.chat(s, "create user VELU, Velu").confirm["run_id"]
        agent.chat(s, "actually use VELU2")
        r = agent.decide(s, old, approved=True)
        self.assertIsNone(r.action)
        self.assertIn("no longer valid", r.messages[0])

    def test_invalid_value_rejected(self):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "velu kumar"}]}, id="b")),
            response(text("User IDs can't contain spaces.")),
        )
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        agent.chat(s, "create user velu kumar")
        self.assertEqual(s.values, {})
        self.assertTrue(llm.calls[-1][-1]["content"][1]["is_error"])

    def test_cancel_on_card_returns_to_llm(self):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "VELU"},
                                                    {"name": "description", "value": "Velu"}]}, id="b"),
                     tool("request_confirmation", {}, id="c")),
            response(text("Please confirm.")),
            response(text("No problem, what should I change?")),
        )
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        run = agent.chat(s, "create user VELU, Velu").confirm["run_id"]
        r = agent.decide(s, run, approved=False)
        self.assertIsNone(r.action)
        self.assertEqual(r.messages, ["No problem, what should I change?"])
        self.assertEqual(s.values["user_id"], "VELU")  # kept so the user can tweak

    def test_failed_run_keeps_values_for_retry(self):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "VELU"},
                                                    {"name": "description", "value": "Velu"}]}, id="b"),
                     tool("request_confirmation", {}, id="c")),
            response(text("Please confirm.")),
            response(text("User ID VELU already exists. Use VELU2?")),
        )
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        run = agent.chat(s, "create user VELU, Velu").confirm["run_id"]
        agent.decide(s, run, approved=True)
        agent.report(s, run, "error", "The User ID already exists.", "Add")
        self.assertEqual(s.task_id, "create_user_profile")
        self.assertIn("stopped at: Add", llm.calls[-1][-1]["content"])
        self.assertIn("already exists", llm.calls[-1][-1]["content"])
        self.assertIn("don't have a run", agent.report(s, run, "success", "").messages[0])  # no replay

    def test_llm_failure_is_reported(self):
        agent, s = Agent(FakeLLM(LLMError("boom")), TASKS), Session(id="s", oprid="PS")
        self.assertIn("unavailable", agent.chat(s, "hi").messages[0])

    def test_refusal_is_not_appended(self):
        agent, s = Agent(FakeLLM(response(stop_reason="refusal")), TASKS), Session(id="s", oprid="PS")
        self.assertIn("can't help", agent.chat(s, "hi").messages[0])
        self.assertEqual([m["role"] for m in s.messages], ["user"])

    def test_password_cannot_be_set_through_chat(self):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "password", "value": "hunter2"}]}, id="b")),
            response(text("Please type the password on the card.")),
        )
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        agent.chat(s, "password is hunter2")
        self.assertNotIn("password", s.values)
        out = json.loads(llm.calls[-1][-1]["content"][1]["content"])
        [rej] = out["rejected"]
        self.assertEqual((rej["name"], rej["error"]), ("password", "not a field of this task"))
        self.assertIn("user_id", rej["valid_names"])
        self.assertNotIn("password", rej["valid_names"])

    def test_working_notes_between_tool_calls_are_hidden(self):
        llm = FakeLLM(
            response(text("Field name should be user_id."),
                     tool("start_task", {"task_id": "create_user_profile"})),
            response(text("What description should I use?")),
        )
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        self.assertEqual(agent.chat(s, "create user velu").messages, ["What description should I use?"])

    def test_last_note_shown_when_final_answer_is_empty(self):
        llm = FakeLLM(
            response(text("Please review the card."), tool("start_task", {"task_id": "create_user_profile"})),
            response(),
        )
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        self.assertEqual(agent.chat(s, "go").messages, ["Please review the card."])

    def test_tools_are_strict_and_fixed(self):
        agent = Agent(FakeLLM(), TASKS)
        before = json.dumps(agent.tools)
        agent.add_task(TASKS["create_user_profile"])
        self.assertEqual(json.dumps(agent.tools), before)
        self.assertTrue(all(t["strict"] for t in agent.tools))

if __name__ == "__main__":
    unittest.main()
