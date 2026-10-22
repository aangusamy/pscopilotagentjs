"""Claude client used by the agent. Imported lazily so tests run without the SDK."""

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

    def create(self, system, messages, tools):
        a = self._anthropic
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
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
