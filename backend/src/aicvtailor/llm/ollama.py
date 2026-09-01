"""Ollama backend: fully offline fallback.

Same OpenAI-compatible shape as NIM. Quality will be lower, which is why the
provider that produced a tailored resume is stored on the record and badged in
the UI rather than left implicit.
"""

from __future__ import annotations

import httpx

from ..config import get_settings
from .base import Availability, Role
from .openai_compat import OpenAICompatProvider
from .runlog import RunLog


class OllamaProvider(OpenAICompatProvider):
    name = "ollama"

    def __init__(self, *, runlog: RunLog | None = None, client=None) -> None:
        settings = get_settings()
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=settings.ollama_base_url, api_key="ollama")

        # A local server has no quota, so no limiter. Both roles share the one
        # model most people will have pulled.
        super().__init__(
            client,
            limiter=None,
            runlog=runlog,
            model_for={role: settings.ollama_model for role in Role},
        )

    def availability(self) -> Availability:
        settings = get_settings()
        root = settings.ollama_base_url.rstrip("/").removesuffix("/v1")
        try:
            resp = httpx.get(f"{root}/api/tags", timeout=1.5)
            resp.raise_for_status()
            tags = [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception:
            return Availability(
                ok=False,
                detail=f"No Ollama server responding at {root}.",
                fallback="Start `ollama serve` to enable offline operation.",
            )

        if settings.ollama_model not in tags:
            return Availability(
                ok=False,
                detail=f"Server up but '{settings.ollama_model}' is not pulled.",
                fallback=f"Run `ollama pull {settings.ollama_model}`.",
            )
        return Availability(ok=True, detail=f"Ollama serving {settings.ollama_model}.")
