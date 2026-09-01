"""Shared implementation for the two OpenAI-compatible backends.

NIM and Ollama speak the same wire protocol, so the retry policy, rate
limiting and schema-repair loop live here once rather than twice.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from .base import (
    Availability,
    Completion,
    LLMError,
    Role,
    SchemaValidationError,
    Usage,
    extract_json,
    schema_instructions,
)
from .limiter import TokenBucket
from .runlog import RunLog

log = logging.getLogger(__name__)

MAX_429_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0


def _status_of(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )


class OpenAICompatProvider:
    """Chat-completions client with pacing, retries and schema repair."""

    name = "openai-compatible"

    def __init__(
        self,
        client: Any,
        *,
        limiter: TokenBucket | None = None,
        runlog: RunLog | None = None,
        model_for: dict[Role, str] | None = None,
        params_for: dict[Role, dict[str, Any]] | None = None,
        sleep=time.sleep,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._runlog = runlog
        self._model_for = model_for or {}
        self._params_for = params_for or {}
        self._sleep = sleep

    # -- plumbing ----------------------------------------------------------
    def availability(self) -> Availability:
        return Availability(ok=True, detail=f"{self.name} ready")

    def model_for(self, role: Role) -> str:
        return self._model_for.get(role, "")

    def _params(self, role: Role) -> dict[str, Any]:
        defaults = {"temperature": 0.0, "max_tokens": 2048}
        return {**defaults, **self._params_for.get(role, {})}

    def _send(self, messages: list[dict[str, str]], role: Role, json_mode: bool) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model_for(role),
            "messages": messages,
            **self._params(role),
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return self._client.chat.completions.create(**kwargs)

    def _call_with_retries(
        self, messages: list[dict[str, str]], role: Role, json_mode: bool
    ) -> tuple[str, Usage, int]:
        """One network call, with 429 backoff and a single 5xx retry.

        The limiter should keep 429s from happening at all; the backoff exists
        for the case where another process shares the same key.
        """
        attempts = 0
        server_error_retried = False

        while True:
            attempts += 1
            if self._limiter is not None:
                waited = self._limiter.acquire()
                if waited > 0:
                    log.debug("rate limiter paused %.2fs", waited)

            try:
                response = self._send(messages, role, json_mode)
            except Exception as exc:  # noqa: BLE001 -- classified by status below
                status = _status_of(exc)

                if status == 429 and attempts < MAX_429_ATTEMPTS:
                    delay = min(
                        BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
                    )
                    delay += random.uniform(0, delay * 0.1)  # jitter, avoid lockstep
                    log.warning("429 from provider, backing off %.1fs", delay)
                    self._sleep(delay)
                    continue

                if status is not None and 500 <= status < 600 and not server_error_retried:
                    server_error_retried = True
                    log.warning("server error %s, retrying once", status)
                    self._sleep(BACKOFF_BASE_SECONDS)
                    continue

                raise LLMError(f"{self.name} call failed: {exc}") from exc

            choice = response.choices[0]
            text = choice.message.content or ""
            raw_usage = getattr(response, "usage", None)
            usage = Usage(
                prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
            )
            return text, usage, attempts

    # -- public API --------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        *,
        role: Role = Role.EXTRACTOR,
        stage: str = "llm",
    ) -> dict[str, Any] | str:
        result = self.complete_verbose(system, user, schema, role=role, stage=stage)
        if schema is None:
            return result.text
        assert result.parsed is not None  # complete_verbose raises otherwise
        return result.parsed

    def complete_verbose(
        self,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        *,
        role: Role = Role.EXTRACTOR,
        stage: str = "llm",
    ) -> Completion:
        """Run a call and return everything about it.

        With a schema, the response is validated and one repair attempt is made
        with the validation error appended, because models usually fix their
        own shape errors when shown them.
        """
        started = time.perf_counter()
        effective_system = system if schema is None else f"{system}\n\n{schema_instructions(schema)}"
        messages = [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": user},
        ]

        total_attempts = 0
        error: str | None = None
        text = ""
        parsed: dict[str, Any] | None = None
        usage = Usage()

        try:
            for repair in range(2):
                text, usage, attempts = self._call_with_retries(
                    messages, role, json_mode=schema is not None
                )
                total_attempts += attempts

                if schema is None:
                    parsed = None
                    break

                try:
                    payload = schema.model_validate_json(extract_json(text))
                    parsed = payload.model_dump()
                    break
                except (ValidationError, ValueError) as exc:
                    if repair == 1:
                        raise SchemaValidationError(
                            f"schema validation failed after one repair attempt: {exc}",
                            raw=text,
                        ) from exc
                    log.warning("schema validation failed, requesting a repair")
                    messages = [
                        *messages,
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": (
                                "That response did not validate. Fix it and return "
                                f"only the corrected JSON object.\n\nError:\n{exc}"
                            ),
                        },
                    ]
        except LLMError as exc:
            error = str(exc)
            raise
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            if self._runlog is not None:
                self._runlog.call(
                    stage=stage,
                    provider=self.name,
                    model=self.model_for(role),
                    role=role.value,
                    system=effective_system,
                    user=user,
                    response=text,
                    attempts=total_attempts,
                    latency_ms=latency_ms,
                    error=error,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    schema=schema.__name__ if schema else None,
                )

        return Completion(
            text=text,
            model=self.model_for(role),
            provider=self.name,
            role=role,
            usage=usage,
            attempts=total_attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
            parsed=parsed,
        )
