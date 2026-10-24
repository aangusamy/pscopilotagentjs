"""Claude client used by the agent. Imported lazily so tests run without the SDK."""

import json
import logging

log = logging.getLogger("psagent")


class LLMError(RuntimeError):
    pass


class ClaudeLLM:
    def __init__(self, model="claude-opus-5-5", effort="low", max_tokens=16000):
        import anthropic  # noqa: PLC0415 - optional dependency for tests

        self._anthropic = anthropic
        # Reads ANTHROPIC_API_KEY (or an `ant auth login` profile) from the environment.
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def create(self, system, messages, tools, tool_choice=None, cache="conversation"):
        """cache="conversation": cache everything up to the last message (the chat grows append-only).
        cache="prefix": cache only tools + system (live mode sends a new page snapshot every call)."""
        a = self._anthropic
        caching = ({"cache_control": {"type": "ephemeral"}} if cache == "conversation" else {})
        if cache == "prefix":
            system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
                **({"tool_choice": tool_choice} if tool_choice else {}),
                output_config={"effort": self.effort},
                **caching,
                # On a safety-classifier decline, retry server-side on Anthropic's recommended model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.RateLimitError as e:
            raise LLMError(f"rate limited: {e.message}") from e
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"connection error: {e}") from e
        log.debug("claude %s stop=%s usage=%s", resp._request_id, resp.stop_reason, resp.usage)
        return resp

    def structured(self, system, prompt, schema):
        """One-shot JSON answer matching `schema` (used to draft recorded tasks)."""
        a = self._anthropic
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"connection error: {e}") from e
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise LLMError(f"no usable answer (stop_reason={resp.stop_reason})")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError as e:
            raise LLMError("answer was not valid JSON") from e
