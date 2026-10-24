import json
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from psagent import auth
from psagent.agent import Agent, Session
from psagent.server import create_app
from psagent.tasks import load_tasks

from fakes import FakeLLM, response, text, tool

TASKS = load_tasks(Path(__file__).resolve().parent / "fixtures" / "tasks")

# What the widget sends: the User Profiles page in the workspace (shape of snapshotPage() in psagent.js)
PAGE = {
    "component": "MAINTAIN_SECURITY.USERMAINT.GBL", "phase": "page", "title": "User Profiles",
    "tabs": [{"label": "General", "active": True}, {"label": "ID"}],
    "fields": [
        {"id": "PSOPRDEFN_SYMBOLICID", "label": "Symbolic ID", "type": "select", "value": "", "options": ["", "SYSADM1"]},
        {"id": "PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT", "label": "New Password", "type": "password", "value": ""},
        {"id": "PSOPRDEFN_OPRID", "label": "User ID", "type": "text", "value": "VELU", "readonly": True},
    ],
    "buttons": [{"id": "#ICSave", "text": "Save"}, {"id": "PRCSRQSTDLG_WRK_LOADPRCSRQSTDLGPB", "text": "Run"},
                {"id": "popup:#ICSave", "text": "OK"}],
}


def act(*actions, note="working"):
    return tool("act", {"note": note, "actions": [{"kind": k, "target": t, "value": v} for k, t, v in actions]})


def start(llm):
    """A chat session whose agent just started a live run."""
    agent = Agent(llm, TASKS)
    s = Session(id="s", oprid="PS")
    r = agent.chat(s, "lock the account of VELU")
    return agent, s, r.live["run_id"]


class LiveTest(unittest.TestCase):
    def test_chat_starts_a_live_run(self):
        llm = FakeLLM(response(tool("work_live", {"goal": "Lock user VELU", "details": "user VELU"})),
                      response(text("On it.")))
        agent, s, run_id = start(llm)
        self.assertTrue(run_id.startswith("live-"))
        self.assertEqual(s.running, {"run_id": run_id, "task_id": "live"})

    def test_steps_answers_and_finish(self):
        llm = FakeLLM(
            response(tool("work_live", {"goal": "Set up VELU", "details": ""})), response(text("On it.")),
            response(act(("set", "PSOPRDEFN_SYMBOLICID", "SYSADM1"), ("tab", "ID", ""), note="symbolic ID")),
            response(tool("ask_user", {"question": "Which description?"})),
            response(tool("finish", {"success": True, "summary": "Done."})),
        )
        agent, s, run_id = start(llm)
        a = agent.live_step(s, run_id, PAGE)
        self.assertEqual(a, {"type": "do", "note": "symbolic ID", "steps": [
            {"do": "set", "field": {"id": "PSOPRDEFN_SYMBOLICID", "label": "Symbolic ID"}, "value": "SYSADM1"},
            {"do": "tab", "label": "ID"}]})
        a = agent.live_step(s, run_id, PAGE, result={"ok": True})
        self.assertEqual(a, {"type": "ask", "question": "Which description?"})
        a = agent.live_step(s, run_id, PAGE, answer="Velu A")
        self.assertEqual(a["type"], "done")
        prompt = llm.calls[-1][0]["content"]
        self.assertIn("user answered: Velu A", prompt)                     # history reaches the model
        self.assertIn('"PSOPRDEFN_SYMBOLICID"', prompt)                    # and the page
        self.assertEqual(agent.live_step(s, run_id, PAGE)["type"], "fail")  # run is over

    def test_save_needs_the_users_approval(self):
        llm = FakeLLM(
            response(tool("work_live", {"goal": "g", "details": ""})), response(text("On it.")),
            response(act(("click", "#ICSave", ""))),                        # tries to save directly: refused
            response(tool("request_commit", {"target": "#ICSave", "summary": [{"label": "User", "value": "VELU"}]})),
            response(tool("finish", {"success": True, "summary": "Saved."})),
        )
        agent, s, run_id = start(llm)
        a = agent.live_step(s, run_id, PAGE)
        self.assertEqual(a["type"], "confirm")
        self.assertEqual((a["button"], a["rows"]), ("Save", [{"label": "User", "value": "VELU"}]))
        self.assertIn("REJECTED act", llm.calls[-1][0]["content"])         # the model was told why
        # the Save click is only handed out against the approval id
        self.assertEqual(agent.live_step(s, run_id, PAGE, approved=a["confirm_id"]),
                         {"type": "do", "note": "click Save", "steps": [{"do": "click", "button": {"id": "#ICSave", "text": "Save"}}]})
        self.assertEqual(agent.live_step(s, run_id, PAGE, result={"ok": True})["type"], "done")

    def test_declined_commit_is_not_clicked(self):
        llm = FakeLLM(
            response(tool("work_live", {"goal": "g", "details": ""})), response(text("On it.")),
            response(tool("request_commit", {"target": "#ICSave", "summary": []})),
            response(tool("finish", {"success": False, "summary": "Not saved."})),
        )
        agent, s, run_id = start(llm)
        a = agent.live_step(s, run_id, PAGE)
        b = agent.live_step(s, run_id, PAGE, approved="wrong-id", declined=True)
        self.assertEqual(b["type"], "fail")
        self.assertIn("user DECLINED", llm.calls[-1][0]["content"])

    def test_invalid_moves_are_refused(self):
        bad = [act(("set", "NOPE", "x")),                                   # not on the page
               act(("set", "PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT", "hunter2")),  # passwords: type_secret only
               act(("set", "PSOPRDEFN_OPRID", "X")),                        # read-only
               act(("click", "PRCSRQSTDLG_WRK_LOADPRCSRQSTDLGPB", ""), ("set", "PSOPRDEFN_SYMBOLICID", "SYSADM1")),
               act(("click", "#ICSave", "")),                               # Save
               act(("click", "popup:#ICSave", ""))]                         # OK on the Process Scheduler Request
        for move in bad:
            llm = FakeLLM(response(tool("work_live", {"goal": "g", "details": ""})), response(text("On it.")),
                          response(move), response(tool("finish", {"success": False, "summary": "x"})))
            agent, s, run_id = start(llm)
            self.assertEqual(agent.live_step(s, run_id, PAGE)["type"], "fail", move)
            self.assertIn("REJECTED", llm.calls[-1][0]["content"])

    def test_password_goes_through_the_user(self):
        llm = FakeLLM(response(tool("work_live", {"goal": "g", "details": ""})), response(text("On it.")),
                      response(tool("type_secret", {"target": "PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT", "label": "Password"})))
        agent, s, run_id = start(llm)
        self.assertEqual(agent.live_step(s, run_id, PAGE), {
            "type": "secret", "field": {"id": "PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT", "label": "Password"}, "popup": False})

    def test_result_comes_back_to_the_chat(self):
        llm = FakeLLM(response(tool("work_live", {"goal": "g", "details": ""})), response(text("On it.")),
                      response(act(("tab", "ID", ""))), response(text("Done: the account is locked.")))
        agent, s, run_id = start(llm)
        agent.live_step(s, run_id, PAGE)
        r = agent.report(s, run_id, "success", "Locked")
        self.assertEqual(r.messages, ["Done: the account is locked."])
        self.assertIn("What you did:", llm.calls[-1][-1]["content"])
        self.assertIsNone(s.live)

    def test_api(self):
        llm = FakeLLM(response(tool("work_live", {"goal": "g", "details": ""})), response(text("On it.")),
                      response(act(("tab", "ID", ""))))
        c = TestClient(create_app(Agent(llm, TASKS), "k", []))
        h = {"Authorization": f"Bearer {auth.make_token('k', 'PS')}"}
        r = c.post("/api/chat", json={"message": "do it"}, headers=h).json()
        run_id = r["live"]["run_id"]
        out = c.post("/api/live/step", json={"session_id": r["session_id"], "run_id": run_id, "snapshot": PAGE},
                     headers=h).json()
        self.assertEqual(out["action"]["steps"], [{"do": "tab", "label": "ID"}])
        other = {"Authorization": f"Bearer {auth.make_token('k', 'X')}"}
        self.assertEqual(c.post("/api/live/step", json={"session_id": r["session_id"], "run_id": run_id},
                                headers=other).status_code, 404)


class RecoveryTest(unittest.TestCase):
    """A recorded task hits a page that changed: it is finished live from the failed step."""

    def confirmed_run(self, *more):
        llm = FakeLLM(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "VELU"},
                                                    {"name": "description", "value": "Velu A"}]}, id="b"),
                     tool("request_confirmation", {}, id="c")),
            response(text("Please confirm.")), *more)
        agent, s = Agent(llm, TASKS), Session(id="s", oprid="PS")
        run_id = agent.chat(s, "create user VELU, Velu A").confirm["run_id"]
        steps = agent.decide(s, run_id, True).action["steps"]
        return llm, agent, s, run_id, steps

    def test_finishes_live_from_the_failed_step(self):
        llm, agent, s, run_id, steps = self.confirmed_run(
            response(act(("set", "PSOPRDEFN_SYMBOLICID", "SYSADM1"))),
            response(text("Created VELU; the Description field had moved, I found it.")))
        failed = next(i for i, st in enumerate(steps) if st.get("field", {}).get("label") == "Description")
        live = agent.recover(s, run_id, failed, 'Could not find "Description" on the page')
        self.assertEqual((live["goal"], live["recovering"]), ("Finish Create User Profile", "create_user_profile"))
        self.assertEqual(s.live.max_rounds, 20)                              # your limit for a recovery
        self.assertEqual(s.live.title, "Create User Profile")                # cards show the task, not the brief
        hist = "\n".join(s.live.history)
        self.assertIn(f"recorded step {failed + 1} FAILED: set Description", hist)
        self.assertIn("recorded step 2 done: set User ID", hist)
        self.assertIn("still to do (adapt it): click Save", hist)
        self.assertIn("(password: use type_secret)", hist)                   # secrets never written out
        self.assertIn("- User ID: VELU", s.live.details)
        a = agent.live_step(s, live["run_id"], PAGE)
        self.assertEqual(a["type"], "do")
        r = agent.report(s, live["run_id"], "success", "Saved")
        self.assertEqual(r.messages, ["Created VELU; the Description field had moved, I found it."])
        self.assertIn("stopped at step", str(llm.calls[-1]))                 # the chat knows what happened

    def test_only_the_running_task_can_be_recovered(self):
        llm, agent, s, run_id, steps = self.confirmed_run()
        self.assertIsNone(agent.recover(s, "other", 3, "x"))
        self.assertIsNone(agent.recover(s, run_id, 999, "x"))
        self.assertIsNotNone(agent.recover(s, run_id, 3, "x"))
        self.assertIsNone(agent.recover(s, run_id, 3, "x"))                 # once

    def test_api_refuses_without_a_run(self):
        c = TestClient(create_app(Agent(FakeLLM(response(text("hi"))), TASKS), "k", []))
        h = {"Authorization": f"Bearer {auth.make_token('k', 'PS')}"}
        sid = c.post("/api/chat", json={"message": "hi"}, headers=h).json()["session_id"]
        r = c.post("/api/live/recover", json={"session_id": sid, "run_id": "x", "failed_index": 1}, headers=h)
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
