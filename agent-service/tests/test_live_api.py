"""The real Claude API takes every tool list the service sends. The other tests use a fake Claude and cannot see the
API's own limits (at most 20 strict tools; "compiled grammar is too large"). Costs a few cents, so it runs only with
PSAGENT_LIVE_API=1 and ANTHROPIC_API_KEY set (or in .env):

    PSAGENT_LIVE_API=1 python -m unittest tests.test_live_api
"""
import os
import unittest
from pathlib import Path

from psagent.server import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
LIVE = os.environ.get("PSAGENT_LIVE_API") == "1" and os.environ.get("ANTHROPIC_API_KEY")


@unittest.skipUnless(LIVE, "set PSAGENT_LIVE_API=1 to call the real Claude API")
class LiveToolListsTest(unittest.TestCase):
    def test_the_api_takes_every_tool_list(self):
        import anthropic
        from psagent import approvals, live, security, troubleshoot
        from psagent.agent import build_tools
        client = anthropic.Anthropic()
        model = os.environ.get("PSAGENT_MODEL", "claude-opus-5-5")
        for name, tools in (("agent", build_tools()), ("troubleshoot", troubleshoot.TOOLS), ("live", live.LIVE_TOOLS),
                            ("security", security.TOOLS), ("approvals", approvals.TOOLS)):
            with self.subTest(name):
                client.beta.messages.create(model=model, max_tokens=16, tools=tools,
                                            messages=[{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
