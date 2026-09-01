"""Live model resolution.

Model ids go stale. Rather than hardcoding them, the configured preferences in
config/models.yaml are treated as ordered hints and matched against whatever
`GET /v1/models` actually serves. The resolved id is logged and surfaced in the
UI so there is never any doubt which model produced a given tailored resume.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .. import paths
from ..config import get_model_prefs, get_settings
from .base import Role

log = logging.getLogger(__name__)

CACHE_FILE = paths.CACHE_DIR / "nim_models.json"


@dataclass(frozen=True, slots=True)
class Resolution:
    """How a role's model was chosen, including when the choice was a fallback."""

    role: Role
    model: str
    matched: str
    source: str  # "env" | "preference" | "terminal_fallback"
    verified: bool  # present in the live catalogue
    skipped: tuple[str, ...] = ()

    @property
    def warning(self) -> str:
        if not self.verified:
            return (
                f"{self.role.value}: '{self.model}' is not in the live catalogue. "
                "Calls may fail; set a model explicitly in .env."
            )
        if self.source == "terminal_fallback":
            return (
                f"{self.role.value}: no preferred model matched, fell back to "
                f"'{self.model}'. Quality may be lower than intended."
            )
        if self.skipped:
            return (
                f"{self.role.value}: preferred {', '.join(self.skipped)} "
                f"unavailable, using '{self.model}'."
            )
        return ""


def _cache_age_hours() -> float | None:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        fetched = float(payload.get("_fetched_at", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return (time.time() - fetched) / 3600.0


def load_cached() -> list[str]:
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [m["id"] for m in payload.get("data", []) if isinstance(m, dict) and "id" in m]


def _store(models: list[dict[str, Any]]) -> None:
    paths.ensure_dirs()
    CACHE_FILE.write_text(
        json.dumps({"_fetched_at": time.time(), "data": models}, indent=2),
        encoding="utf-8",
    )


def fetch_catalogue(*, force: bool = False, client: httpx.Client | None = None) -> list[str]:
    """Return the live model ids, using the cache while it is fresh.

    A network failure is not fatal: a stale cache beats no catalogue, and an
    empty catalogue still lets resolution fall through to the terminal
    fallback rather than blocking the app from starting.
    """
    settings = get_settings()
    ttl = float(get_model_prefs().get("catalogue_ttl_hours", 24))

    age = _cache_age_hours()
    if not force and age is not None and age < ttl:
        return load_cached()

    if not settings.nvidia_api_key:
        return load_cached()

    url = settings.nim_base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {settings.nvidia_api_key}"}
    try:
        owns_client = client is None
        client = client or httpx.Client(timeout=10.0)
        try:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        finally:
            if owns_client:
                client.close()
    except Exception as exc:  # noqa: BLE001 -- any failure falls back to cache
        cached = load_cached()
        log.warning(
            "model catalogue fetch failed (%s); using %d cached ids", exc, len(cached)
        )
        return cached

    _store(data)
    ids = [m["id"] for m in data if isinstance(m, dict) and "id" in m]
    log.info("fetched %d models from %s", len(ids), url)
    return ids


def _matches(pattern: str, model_id: str) -> bool:
    """Glob if the hint contains a wildcard, otherwise prefix match.

    Prefix matching is what makes a hint like `qwen/qwen3` survive the vendor
    appending a size or date suffix to the real id.
    """
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatch(model_id, pattern)
    return model_id.startswith(pattern)


def resolve(role: Role, catalogue: list[str] | None = None) -> Resolution:
    """Pick the model for a role against the live catalogue."""
    settings = get_settings()
    prefs = get_model_prefs().get("roles", {}).get(role.value, {})
    catalogue = catalogue if catalogue is not None else fetch_catalogue()

    override = (
        settings.extractor_model if role is Role.EXTRACTOR else settings.rewriter_model
    ).strip()

    if override:
        # An explicit choice is honoured even if the catalogue disagrees: the
        # user may know about a model this endpoint has not listed.
        exact = next((m for m in catalogue if _matches(override, m)), None)
        return Resolution(
            role=role,
            model=exact or override,
            matched=override,
            source="env",
            verified=exact is not None,
        )

    skipped: list[str] = []
    for hint in prefs.get("prefer", []):
        match = next((m for m in catalogue if _matches(hint, m)), None)
        if match:
            return Resolution(
                role=role,
                model=match,
                matched=hint,
                source="preference",
                verified=True,
                skipped=tuple(skipped),
            )
        skipped.append(hint)

    terminal = prefs.get("terminal_fallback", "")
    match = next((m for m in catalogue if _matches(terminal, m)), None)
    return Resolution(
        role=role,
        model=match or terminal,
        matched=terminal,
        source="terminal_fallback",
        verified=match is not None,
        skipped=tuple(skipped),
    )


def resolve_all(catalogue: list[str] | None = None) -> dict[Role, Resolution]:
    catalogue = catalogue if catalogue is not None else fetch_catalogue()
    resolutions = {role: resolve(role, catalogue) for role in Role}
    for resolution in resolutions.values():
        if resolution.warning:
            log.warning("%s", resolution.warning)
        else:
            log.info("%s -> %s", resolution.role.value, resolution.model)
    return resolutions
