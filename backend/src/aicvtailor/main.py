"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import health as health_probes
from . import paths
from .api.analysis import router as analysis_router
from .api.health import router as health_router
from .api.suggestions import router as suggestions_router
from .config import get_settings
from .db import init_db

log = logging.getLogger("aicvtailor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    paths.ensure_dirs()
    init_db()

    report = health_probes.run_all()
    log.info("health: %s (provider=%s)", report["status"], report["provider"])
    for probe in report["probes"]:
        level = logging.INFO if probe["status"] == "ok" else logging.WARNING
        log.log(level, "  %-15s %-12s %s", probe["name"], probe["status"], probe["detail"])
        if probe["status"] != "ok" and probe["fallback"]:
            log.log(level, "  %-15s %-12s -> %s", "", "", probe["fallback"])

    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AICVTailor",
        version="0.1.0",
        description="Local-first resume tailoring. Nothing leaves this machine "
        "except resume text and JD text sent to the configured LLM provider.",
        lifespan=lifespan,
    )

    # The Vite dev server runs on a different port; in production the frontend
    # is served from the same origin and this is a no-op.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://localhost:{settings.frontend_port}",
            f"http://127.0.0.1:{settings.frontend_port}",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(analysis_router)
    app.include_router(suggestions_router)
    return app


app = create_app()
