import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient

from psagent import auth
from psagent.agent import Agent
from psagent.server import create_app
from psagent.tasks import load_tasks

from fakes import FakeLLM, response, text, tool

TASKS = load_tasks(Path(__file__).resolve().parent.parent / "tasks")
SECRET = "s3cret"
ORIGIN = "https://ps.example.com"


class AuthTest(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(auth.verify(auth.make_token(SECRET, "VELU"), SECRET), "VELU")

    def test_known_digest(self):
        # printf "s3cret|PS|20261022" | openssl dgst -sha256 -binary | base64  (= PeopleCode HashSHA256)
        self.assertEqual(auth.sign(SECRET, "PS", "20261022"), "jXkjQlKRymNCGipEiNbctUj84t1nBcQzrZJptXCRgsg=")

    def test_rejections(self):
        good = auth.make_token(SECRET, "PS")
        for bad in ["", "garbage", good[:-2] + "xx", auth.make_token("other", "PS"),
                    auth.make_token(SECRET, "PS", (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y%m%d")),
                    auth.make_token(SECRET, "A|B")]:
            with self.assertRaises(auth.AuthError, msg=bad):
                auth.verify(bad, SECRET)


class ServerTest(unittest.TestCase):
    def client(self, *responses):
        app = create_app(Agent(FakeLLM(*responses), TASKS), SECRET, [ORIGIN])
        return TestClient(app)

    def headers(self, oprid="PS"):
        return {"Authorization": f"Bearer {auth.make_token(SECRET, oprid)}"}

    def test_requires_token(self):
        c = self.client()
        self.assertEqual(c.post("/api/chat", json={"message": "hi"}).status_code, 401)
        bad = {"Authorization": "Bearer PS.20261022.nope"}
        self.assertEqual(c.post("/api/chat", json={"message": "hi"}, headers=bad).status_code, 401)

    def test_full_flow_and_session_isolation(self):
        c = self.client(
            response(tool("start_task", {"task_id": "create_user_profile"}),
                     tool("set_fields", {"fields": [{"name": "user_id", "value": "VELU"},
                                                    {"name": "description", "value": "Velu"}]}, id="b"),
                     tool("request_confirmation", {}, id="c")),
            response(text("Please confirm.")),
            response(text("Created VELU.")),
        )
        r = c.post("/api/chat", json={"message": "create user VELU, Velu"}, headers=self.headers()).json()
        sid, run = r["session_id"], r["confirm"]["run_id"]
        self.assertEqual(r["messages"], ["Please confirm."])

        # another user cannot confirm this run
        other = c.post("/api/confirm", json={"session_id": sid, "run_id": run, "approved": True},
                       headers=self.headers("HACKER"))
        self.assertEqual(other.status_code, 404)

        r = c.post("/api/confirm", json={"session_id": sid, "run_id": run, "approved": True},
                   headers=self.headers()).json()
        self.assertEqual(r["action"]["run_id"], run)
        self.assertTrue(r["action"]["steps"])

        r = c.post("/api/result", json={"session_id": sid, "run_id": run, "status": "success", "message": "Saved"},
                   headers=self.headers()).json()
        self.assertEqual(r["messages"], ["Created VELU."])

    def test_validation(self):
        c = self.client()
        h = self.headers()
        self.assertEqual(c.post("/api/chat", json={"message": " "}, headers=h).status_code, 400)
        self.assertEqual(c.post("/api/chat", content=b"not json", headers=h).status_code, 400)
        self.assertEqual(c.post("/api/result", json={"run_id": "x", "status": "maybe"}, headers=h).status_code, 400)

    def test_cors_preflight(self):
        r = self.client().options("/api/chat", headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), ORIGIN)
        r = self.client().options("/api/chat", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertNotEqual(r.headers.get("access-control-allow-origin"), "https://evil.example")


if __name__ == "__main__":
    unittest.main()
