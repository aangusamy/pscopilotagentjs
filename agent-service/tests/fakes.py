"""Scripted stand-in for ClaudeLLM: returns canned responses in order."""

from types import SimpleNamespace


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool(name, input, id=None):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id or f"toolu_{name}")


def response(*blocks, stop_reason=None):
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, system, messages, tools):
        self.calls.append(list(messages))
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
