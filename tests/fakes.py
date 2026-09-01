"""Fake OpenAI-compatible transport.

Every provider test runs against this. No test in the suite touches the
network, so the suite is fast, offline, and does not spend the free tier.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Iterable


class FakeHTTPError(Exception):
    """Mimics the SDK's status-carrying exceptions."""

    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def _response(content: str, prompt_tokens: int = 11, completion_tokens: int = 7):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
    )


class FakeCompletions:
    def __init__(self, script: Iterable[Any]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any):
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError(
                f"fake client ran out of scripted responses after {len(self.calls)} calls"
            )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _response(item) if isinstance(item, str) else item


class FakeClient:
    """Stands in for `openai.OpenAI`.

    `script` is consumed in order; a string becomes a successful response, an
    Exception is raised, which is how the 429 and 5xx paths get exercised.
    """

    def __init__(self, script: Iterable[Any]) -> None:
        self.completions = FakeCompletions(script)
        self.chat = SimpleNamespace(completions=self.completions)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.completions.calls


class RecordingSleep:
    """Captures backoff delays instead of actually waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class FakeClock:
    """Manually advanced monotonic clock for rate-limiter tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
