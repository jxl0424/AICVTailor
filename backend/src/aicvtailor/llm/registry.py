"""Provider selection and graceful degradation.

`get_provider()` honours LLM_PROVIDER, but falls through to whatever is
actually usable rather than failing outright: the acceptance criterion is that
the system runs with only a NIM key, and still runs with no key at all if
Ollama is up.
"""

from __future__ import annotations

import logging

from ..config import get_settings
from .base import Availability, LLMProvider, ProviderUnavailable
from .claude_cli import ClaudeCLIProvider
from .nim import NIMProvider
from .ollama import OllamaProvider
from .runlog import RunLog

log = logging.getLogger(__name__)

BUILDERS = {
    "nim": NIMProvider,
    "ollama": OllamaProvider,
    "claude_cli": ClaudeCLIProvider,
}

# Order tried when the configured provider is unusable. NIM first because it is
# the free hosted default; the local CLI last because it is opt-in.
FALLBACK_ORDER = ("nim", "ollama", "claude_cli")


def build(name: str, *, runlog: RunLog | None = None) -> LLMProvider:
    builder = BUILDERS.get(name)
    if builder is None:
        raise ProviderUnavailable(f"unknown provider '{name}'")
    return builder(runlog=runlog)


def availability_report() -> dict[str, Availability]:
    """Availability of every backend, for the health endpoint and the UI."""
    report: dict[str, Availability] = {}
    for name in BUILDERS:
        try:
            report[name] = build(name).availability()
        except Exception as exc:  # noqa: BLE001 -- a broken backend is a report line
            report[name] = Availability(
                ok=False,
                detail=f"{name} failed to initialise: {exc}",
                fallback="Other providers are unaffected.",
            )
    return report


def get_provider(name: str | None = None, *, runlog: RunLog | None = None) -> LLMProvider:
    """Return a usable provider, degrading rather than failing.

    Raises only when nothing at all works, since at that point no downstream
    stage can run either.
    """
    preferred = name or get_settings().llm_provider
    ordered = [preferred, *(n for n in FALLBACK_ORDER if n != preferred)]

    failures: list[str] = []
    for candidate in ordered:
        try:
            provider = build(candidate, runlog=runlog)
            availability = provider.availability()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{candidate}: {exc}")
            continue

        if availability.ok:
            if candidate != preferred:
                log.warning(
                    "provider '%s' unusable, falling back to '%s'", preferred, candidate
                )
            return provider
        failures.append(f"{candidate}: {availability.detail}")

    raise ProviderUnavailable(
        "no usable LLM provider. " + "; ".join(failures)
    )
