"""NVIDIA NIM backend (the default).

OpenAI-compatible endpoint with a free tier. The only NIM-specific parts are
the base URL, the key, and resolving role models against the live catalogue.
"""

from __future__ import annotations

import logging

from ..config import get_model_prefs, get_settings
from .base import Availability, Role
from .catalogue import Resolution, resolve_all
from .limiter import TokenBucket
from .openai_compat import OpenAICompatProvider
from .runlog import RunLog

log = logging.getLogger(__name__)


class NIMProvider(OpenAICompatProvider):
    name = "nim"

    def __init__(self, *, runlog: RunLog | None = None, client=None, catalogue=None) -> None:
        settings = get_settings()
        self._resolutions: dict[Role, Resolution] = resolve_all(catalogue)

        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=settings.nim_base_url,
                api_key=settings.nvidia_api_key or "missing",
            )

        roles = get_model_prefs().get("roles", {})
        super().__init__(
            client,
            limiter=TokenBucket(settings.llm_rpm),
            runlog=runlog,
            model_for={role: r.model for role, r in self._resolutions.items()},
            params_for={
                role: {
                    k: v
                    for k, v in roles.get(role.value, {}).items()
                    if k in {"temperature", "max_tokens"}
                }
                for role in Role
            },
        )

    @property
    def resolutions(self) -> dict[Role, Resolution]:
        return self._resolutions

    def availability(self) -> Availability:
        settings = get_settings()
        if not settings.nvidia_api_key:
            return Availability(
                ok=False,
                detail="NVIDIA_API_KEY is not set.",
                fallback="Get a free key at build.nvidia.com, or run Ollama locally.",
            )

        warnings = [r.warning for r in self._resolutions.values() if r.warning]
        models = ", ".join(f"{r.role.value}={r.model}" for r in self._resolutions.values())
        return Availability(
            ok=True,
            detail=f"Resolved {models}.",
            fallback=" ".join(warnings),
        )
