"""Health endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import health
from ..config import reload_config

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def get_health() -> dict[str, Any]:
    """Full component report. Cheap enough to poll -- no network LLM calls."""
    return health.run_all()


@router.post("/health/reload")
def post_reload() -> dict[str, Any]:
    """Re-read .env and config/*.yaml, then re-probe. Saves a restart while
    you are editing skills.yaml or guardrails.yaml."""
    reload_config()
    return health.run_all()
