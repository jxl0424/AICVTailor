"""Startup health probes.

Every probe answers three things: is it usable, why not if not, and what the
app will do instead. A component being absent is never fatal -- the whole
design is that the app degrades rather than fails. The UI renders this
verbatim, so the `detail` strings are user-facing copy.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import httpx

from . import paths
from .config import get_settings

Status = Literal["ok", "degraded", "unavailable"]

# Preference order matters: tectonic needs no TeX installation and fetches what
# it needs on first run, so it is the least painful for a fresh machine.
LATEX_ENGINES = ("tectonic", "latexmk", "pdflatex")


@dataclass
class Probe:
    name: str
    status: Status
    detail: str
    fallback: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _version_of(binary: str) -> str:
    """Best-effort version string. Never raises."""
    for flag in ("--version", "-version"):
        try:
            out = subprocess.run(
                [binary, flag],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        blob = (out.stdout or out.stderr or "").strip()
        if blob:
            return blob.splitlines()[0][:120]
    return "unknown version"


def probe_latex() -> Probe:
    settings = get_settings()
    configured = (settings.latex_engine or "auto").strip().lower()

    if configured == "none":
        return Probe(
            name="latex",
            status="degraded",
            detail="PDF generation disabled by LATEX_ENGINE=none.",
            fallback="Tailored resumes are offered as .tex only.",
        )

    candidates = LATEX_ENGINES if configured == "auto" else (configured,)
    for engine in candidates:
        found = shutil.which(engine)
        if found:
            return Probe(
                name="latex",
                status="ok",
                detail=f"Using {engine} ({_version_of(engine)}).",
                meta={"engine": engine, "path": found},
            )

    if configured != "auto":
        return Probe(
            name="latex",
            status="unavailable",
            detail=f"LATEX_ENGINE={configured} but that binary is not on PATH.",
            fallback="Set LATEX_ENGINE=auto, or install it. .tex download still works.",
        )

    return Probe(
        name="latex",
        status="unavailable",
        detail="No LaTeX engine found (looked for tectonic, latexmk, pdflatex).",
        fallback=(
            "PDF generation and PDF-text verification are skipped. "
            "Tailored .tex is still produced and downloadable. "
            "Install tectonic for the least-effort fix."
        ),
    )


def probe_nim() -> Probe:
    settings = get_settings()
    if not settings.nvidia_api_key:
        return Probe(
            name="nim",
            status="unavailable",
            detail="NVIDIA_API_KEY is not set.",
            fallback="Get a free key at build.nvidia.com, or run Ollama locally.",
        )

    cache = paths.CACHE_DIR / "nim_models.json"
    meta: dict[str, Any] = {"base_url": settings.nim_base_url}
    if cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            meta["cached_models"] = len(payload.get("data", []))
            meta["cached_at"] = payload.get("_fetched_at")
        except (OSError, json.JSONDecodeError):
            meta["cached_models"] = 0

    # Resolve against the cached catalogue only. Health is polled by the UI and
    # must not make a network call or spend the free tier.
    try:
        from .llm import catalogue as cat

        cached = cat.load_cached()
        resolutions = {r.role.value: r for r in cat.resolve_all(cached).values()}
        meta["models"] = {role: r.model for role, r in resolutions.items()}
        warnings = [r.warning for r in resolutions.values() if r.warning]
    except Exception as exc:  # noqa: BLE001 -- resolution never blocks health
        return Probe(
            name="nim",
            status="degraded",
            detail=f"Key present, but model resolution failed: {exc}",
            fallback="Calls will use the configured defaults.",
            meta=meta,
        )

    models = ", ".join(f"{role}={r.model}" for role, r in resolutions.items())
    if not cached:
        return Probe(
            name="nim",
            status="degraded",
            detail=f"Key present, but no model catalogue cached yet ({models}).",
            fallback="Run `aicvtailor models --refresh` to fetch the live list.",
            meta=meta,
        )
    if warnings:
        return Probe(
            name="nim",
            status="degraded",
            detail=f"Key present. Resolved {models}.",
            fallback=" ".join(warnings),
            meta=meta,
        )
    return Probe(
        name="nim",
        status="ok",
        detail=f"Key present. Resolved {models}.",
        meta=meta,
    )


def probe_claude_cli() -> Probe:
    settings = get_settings()
    binary = shutil.which(settings.claude_cli_path)

    if not settings.enable_claude_cli:
        return Probe(
            name="claude_cli",
            status="degraded",
            detail="Disabled (ENABLE_CLAUDE_CLI=false).",
            fallback="Set ENABLE_CLAUDE_CLI=true to A/B a run against the local CLI.",
            meta={"binary_present": bool(binary)},
        )
    if not binary:
        return Probe(
            name="claude_cli",
            status="unavailable",
            detail=f"Enabled, but '{settings.claude_cli_path}' is not on PATH.",
            fallback="The adapter reports unavailable; other providers are unaffected.",
        )
    return Probe(
        name="claude_cli",
        status="ok",
        detail=f"Found at {binary}.",
        meta={"path": binary},
    )


def probe_ollama() -> Probe:
    settings = get_settings()
    # /v1 is the OpenAI-compatible shim; the tag list lives on the native API.
    root = settings.ollama_base_url.rstrip("/").removesuffix("/v1")
    try:
        resp = httpx.get(f"{root}/api/tags", timeout=1.5)
        resp.raise_for_status()
        tags = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception:
        return Probe(
            name="ollama",
            status="unavailable",
            detail=f"No Ollama server responding at {root}.",
            fallback="Offline fallback is off. Start `ollama serve` to enable it.",
        )

    if settings.ollama_model not in tags:
        return Probe(
            name="ollama",
            status="degraded",
            detail=f"Server is up but '{settings.ollama_model}' is not pulled.",
            fallback=f"Run `ollama pull {settings.ollama_model}`.",
            meta={"available": tags},
        )
    return Probe(
        name="ollama",
        status="ok",
        detail=f"Server up, {settings.ollama_model} available.",
        meta={"available": tags},
    )


def probe_embeddings() -> Probe:
    settings = get_settings()
    if not settings.embeddings_enabled:
        return Probe(
            name="embeddings",
            status="degraded",
            detail="Disabled by EMBEDDINGS_ENABLED=false.",
            fallback="Matching is lexical only; implied_by detection will be weaker.",
        )
    try:
        import model2vec  # noqa: F401
    except ImportError:
        return Probe(
            name="embeddings",
            status="degraded",
            detail="model2vec is not installed.",
            fallback=(
                "Falling back to lexical matching. Terms only covered semantically "
                "(not by a skills.yaml synonym) will read as missing."
            ),
        )
    return Probe(
        name="embeddings",
        status="ok",
        detail=f"Static embeddings via {settings.embeddings_model}.",
    )


def probe_database() -> Probe:
    from sqlalchemy import text

    from .db import get_engine

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return Probe(
            name="database",
            status="unavailable",
            detail=f"SQLite unreachable at {paths.DB_PATH}: {exc}",
            fallback="Check that data/ is writable.",
        )
    size = paths.DB_PATH.stat().st_size if paths.DB_PATH.exists() else 0
    return Probe(
        name="database",
        status="ok",
        detail=f"SQLite at {paths.DB_PATH.relative_to(paths.ROOT)} ({size} bytes).",
    )


def probe_master_resumes() -> Probe:
    paths.ensure_dirs()
    tex = sorted(p.name for p in paths.MASTER_DIR.glob("*.tex"))
    other = sorted(
        p.name
        for p in paths.MASTER_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".pdf", ".docx"}
    )

    if not tex and not other:
        return Probe(
            name="master_resume",
            status="unavailable",
            detail="No master resume found in data/master/.",
            fallback="Drop your master.tex into data/master/ to get started.",
        )
    if not tex:
        return Probe(
            name="master_resume",
            status="degraded",
            detail=f"Only non-LaTeX masters found: {', '.join(other)}.",
            fallback=(
                "These are analysis-only: coverage and suggestions work, but "
                "tailored output needs a .tex master."
            ),
            meta={"tex": [], "other": other},
        )
    return Probe(
        name="master_resume",
        status="ok",
        detail=f"{len(tex)} LaTeX master(s): {', '.join(tex)}.",
        meta={"tex": tex, "other": other},
    )


def _overall(probes: list[Probe]) -> Status:
    """The app is only 'unavailable' if it genuinely cannot do its job.

    Missing LaTeX or a missing master resume is degraded, not fatal. No usable
    LLM provider at all is fatal, because nothing downstream can run.
    """
    by_name = {p.name: p for p in probes}

    if by_name["database"].status == "unavailable":
        return "unavailable"

    provider_names = ("nim", "claude_cli", "ollama")
    if not any(by_name[n].status == "ok" for n in provider_names):
        return "unavailable"

    if any(p.status != "ok" for p in probes):
        return "degraded"
    return "ok"


def run_all() -> dict[str, Any]:
    probes = [
        probe_database(),
        probe_master_resumes(),
        probe_latex(),
        probe_nim(),
        probe_claude_cli(),
        probe_ollama(),
        probe_embeddings(),
    ]
    settings = get_settings()
    return {
        "status": _overall(probes),
        "provider": settings.llm_provider,
        "probes": [asdict(p) for p in probes],
    }
