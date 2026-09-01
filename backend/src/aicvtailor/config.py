"""Settings from .env, plus the YAML config files."""

from __future__ import annotations

import functools
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import paths


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=paths.ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Literal["nim", "claude_cli", "ollama"] = "nim"

    nvidia_api_key: str = ""
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"

    extractor_model: str = ""
    rewriter_model: str = ""
    llm_rpm: int = Field(default=30, ge=1, le=40)

    enable_claude_cli: bool = False
    claude_cli_path: str = "claude"

    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.1:8b"

    latex_engine: str = "auto"

    embeddings_enabled: bool = True
    embeddings_model: str = "minishlab/potion-base-8M"

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_port: int = 5173
    open_browser: bool = True
    log_level: str = "INFO"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _load_yaml(path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    return loaded if isinstance(loaded, dict) else dict(default)


@functools.lru_cache(maxsize=1)
def get_guardrails() -> dict[str, Any]:
    """Load guardrails, merging in an optional gitignored local override.

    `forbidden_claims` holds things like NDA'd project names, which are exactly
    the strings that must not end up in a committed file. So config/ ships the
    shape and config/guardrails.local.yaml carries the personal entries. List
    values concatenate; scalars from the local file win.
    """
    rails = _load_yaml(
        paths.GUARDRAILS_FILE,
        {
            "forbidden_claims": [],
            "never_reword": [],
            "max_bullet_length": 240,
            "max_pages": 1,
            "forbid_new_entities": True,
            "entity_allowlist": [],
        },
    )

    local = _load_yaml(paths.GUARDRAILS_LOCAL_FILE, {})
    for key, value in local.items():
        if isinstance(value, list) and isinstance(rails.get(key), list):
            rails[key] = [*rails[key], *value]
        else:
            rails[key] = value
    return rails


@functools.lru_cache(maxsize=1)
def get_skills() -> dict[str, Any]:
    return _load_yaml(paths.SKILLS_FILE, {"terms": [], "stoplist": []})


@functools.lru_cache(maxsize=1)
def get_model_prefs() -> dict[str, Any]:
    return _load_yaml(paths.MODELS_FILE, {"roles": {}, "catalogue_ttl_hours": 24})


def reload_config() -> None:
    """Drop cached config so edited YAML is picked up without a restart."""
    get_settings.cache_clear()
    get_guardrails.cache_clear()
    get_skills.cache_clear()
    get_model_prefs.cache_clear()
