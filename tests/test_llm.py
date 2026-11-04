from types import SimpleNamespace

import pytest
from google.genai import errors

from app.llm import GeminiClient, LLMError


def rate_limit_error(delay="3s"):
    return errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "quota",
                "status": "RESOURCE_EXHAUSTED",
                "details": [{"retryDelay": delay}],
            }
        },
    )


def busy_error():
    return errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})


def make_client(outcomes, **kwargs):
    """A real GeminiClient whose network call is replaced by scripted outcomes."""
    client = GeminiClient("fake-key", "gemini-test", **kwargs)
    waits = []
    client._sleep = waits.append

    def generate_content(**_):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)

    client._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return client, waits


def test_retries_rate_limits_then_succeeds():
    client, waits = make_client([rate_limit_error("5s"), busy_error(), "SELECT 1"], max_retries=3)
    assert client.generate("system", "prompt") == "SELECT 1"
    assert waits == [5.0, 4]  # uses Gemini's suggested wait when it gives one


def test_waits_are_capped():
    client, waits = make_client([rate_limit_error("90s"), "ok"], max_retries=1, max_wait_seconds=20)
    assert client.generate("s", "p") == "ok"
    assert waits == [20]


def test_gives_up_with_friendly_message():
    client, waits = make_client([rate_limit_error()] * 3, max_retries=2)
    with pytest.raises(LLMError, match="rate limit"):
        client.generate("s", "p")
    assert len(waits) == 2


def test_other_errors_are_not_retried():
    bad_request = errors.ClientError(400, {"error": {"code": 400, "message": "bad model", "status": "INVALID_ARGUMENT"}})
    client, waits = make_client([bad_request])
    with pytest.raises(LLMError, match="bad model"):
        client.generate("s", "p")
    assert waits == []


def test_empty_response_is_an_error():
    client, _ = make_client([""])
    with pytest.raises(LLMError, match="empty"):
        client.generate("s", "p")
