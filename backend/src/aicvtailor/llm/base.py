"""Provider-agnostic contract for LLM calls.

Every backend implements the same `complete()` so a tailoring run can be A/B'd
between NVIDIA NIM, a local Claude CLI and Ollama without the call sites
knowing which is in play.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class Role(str, Enum):
    """Which of the two model roles a call belongs to.

    Split because the workloads differ: extraction runs many cheap structured
    calls, rewriting runs a few that need better language judgement.
    """

    EXTRACTOR = "extractor"
    REWRITER = "rewriter"


class LLMError(RuntimeError):
    """A call failed in a way the caller cannot paper over."""


class ProviderUnavailable(LLMError):
    """The backend is not usable at all -- no key, no binary, no server."""


class SchemaValidationError(LLMError):
    """The model returned JSON that does not satisfy the requested schema."""

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True, slots=True)
class Availability:
    ok: bool
    detail: str
    fallback: str = ""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Completion:
    """Everything a caller or the run log might want about one call."""

    text: str
    model: str
    provider: str
    role: Role
    usage: Usage = field(default_factory=Usage)
    attempts: int = 1
    latency_ms: float = 0.0
    parsed: dict[str, Any] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def availability(self) -> Availability: ...

    def complete(
        self,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        *,
        role: Role = Role.EXTRACTOR,
    ) -> dict[str, Any] | str: ...


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """Pull a JSON object out of a model response.

    Models wrap JSON in markdown fences or prefix it with a sentence of
    commentary even when told not to. Rather than failing the call over
    packaging, find the payload; genuinely malformed JSON still raises later
    during parsing, where the schema-repair retry can quote the error back.
    """
    stripped = text.strip()
    if fenced := _FENCE_RE.search(stripped):
        stripped = fenced.group(1).strip()

    # raw_decode reads one JSON value and reports where it ended, which handles
    # commentary on either side of the payload. A model that says "Here is the
    # JSON:" before it, or "Hope that helps!" after, is not a schema failure.
    candidates = [i for i in (stripped.find("{"), stripped.find("[")) if i != -1]
    if candidates:
        start = min(candidates)
        try:
            _, offset = json.JSONDecoder().raw_decode(stripped[start:])
        except ValueError:
            return stripped
        return stripped[start : start + offset]
    return stripped


def schema_instructions(schema: type[BaseModel]) -> str:
    """Prompt fragment describing the required JSON shape."""
    return (
        "Respond with a single JSON object and nothing else. No prose, no "
        "markdown fences. It must validate against this JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}"
    )
