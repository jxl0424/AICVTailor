"""Project path resolution.

Everything is anchored to the repo root so the app behaves the same whether it
is started from the root, from backend/, or by an editor's test runner.
"""

from __future__ import annotations

from pathlib import Path


def _find_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "config").is_dir():
            return parent
    # Installed non-editable, or an unusual layout: fall back to cwd.
    return Path.cwd()


ROOT = _find_root()

CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

MASTER_DIR = DATA_DIR / "master"
CACHE_DIR = DATA_DIR / "cache"
RUNS_DIR = DATA_DIR / "runs"
OUTPUT_DIR = DATA_DIR / "output"

DB_PATH = DATA_DIR / "app.db"

GUARDRAILS_FILE = CONFIG_DIR / "guardrails.yaml"
# Gitignored. Holds personal or NDA'd entries that must not be committed.
GUARDRAILS_LOCAL_FILE = CONFIG_DIR / "guardrails.local.yaml"
SKILLS_FILE = CONFIG_DIR / "skills.yaml"
MODELS_FILE = CONFIG_DIR / "models.yaml"


def ensure_dirs() -> None:
    """Create the writable data directories. Safe to call repeatedly."""
    for d in (DATA_DIR, MASTER_DIR, CACHE_DIR, RUNS_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
