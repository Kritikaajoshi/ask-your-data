"""A tiny wrapper around the LLM so the rest of the app does not depend on one vendor.

Anything with a generate(system, prompt) method works, which makes testing easy:
the tests use a fake client that returns scripted answers.

Gemini sometimes rejects requests for a moment: 429 means the rate limit was hit, and
503 means the service is busy. Those are retried automatically with a growing wait.
"""

import re
import time
from typing import Protocol

RETRYABLE_CODES = {429, 500, 503, 504}


class LLMError(RuntimeError):
    """The language model could not be reached or returned nothing."""


class LLMClient(Protocol):
    def generate(self, system: str, prompt: str) -> str: ...


def _suggested_wait(error: Exception) -> float | None:
    """Gemini often says how long to wait, like retryDelay: '33s'."""
    match = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s", str(error))
    return float(match.group(1)) if match else None


def _friendly_message(code: int | None, error: Exception) -> str:
    if code == 429:
        return "Gemini's rate limit was reached. Wait a minute and try again."
    if code in (500, 503, 504):
        return "Gemini is busy right now. Try again in a moment."
    return f"The language model request failed: {error}"


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        max_wait_seconds: float = 20,
    ):
        from google import genai
        from google.genai import types

        self._types = types
        self.model = model
        self.max_retries = max_retries
        self.max_wait_seconds = max_wait_seconds
        self._sleep = time.sleep
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )

    def generate(self, system: str, prompt: str) -> str:
        config = self._types.GenerateContentConfig(system_instruction=system, temperature=0)
        attempt = 0
        while True:
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
                break
            except Exception as exc:  # the SDK raises several error types
                code = getattr(exc, "code", None)
                if code in RETRYABLE_CODES and attempt < self.max_retries:
                    backoff = 2 * (2**attempt)
                    wait = max(backoff, _suggested_wait(exc) or 0)
                    self._sleep(min(wait, self.max_wait_seconds))
                    attempt += 1
                    continue
                raise LLMError(_friendly_message(code, exc)) from exc

        text = (response.text or "").strip()
        if not text:
            raise LLMError("The language model returned an empty response.")
        return text
