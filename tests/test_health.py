"""Health probes must degrade, never raise.

The whole point of the health layer is that a missing component produces a
usable report rather than a crash, so these tests strip the environment bare
and assert the probes still answer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicvtailor import health
from aicvtailor.config import reload_config
from aicvtailor.main import create_app

PROBE_NAMES = {
    "database",
    "master_resume",
    "latex",
    "nim",
    "claude_cli",
    "ollama",
    "embeddings",
}


@pytest.fixture
def bare_env(monkeypatch):
    """No key, no LaTeX, no Ollama, no CLI -- the worst realistic machine."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("ENABLE_CLAUDE_CLI", "false")
    monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("LATEX_ENGINE", "none")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:59999/v1")
    reload_config()
    yield
    reload_config()


def test_run_all_reports_every_probe(bare_env):
    report = health.run_all()
    assert {p["name"] for p in report["probes"]} == PROBE_NAMES


def test_probes_never_raise_on_a_bare_machine(bare_env):
    for probe in health.run_all()["probes"]:
        assert probe["status"] in {"ok", "degraded", "unavailable"}


def test_missing_component_explains_the_fallback(bare_env):
    """A degraded or unavailable probe must say what happens instead, because
    the UI renders that string verbatim."""
    for probe in health.run_all()["probes"]:
        if probe["status"] != "ok":
            assert probe["fallback"], f"{probe['name']} gives no fallback guidance"
            assert probe["detail"], f"{probe['name']} gives no reason"


def test_no_provider_is_fatal_but_no_latex_is_not(bare_env):
    """Missing LaTeX only costs PDF output, so it must not take the app down.
    No usable provider at all does, because nothing downstream can run."""
    report = health.run_all()
    latex = next(p for p in report["probes"] if p["name"] == "latex")

    assert latex["status"] != "ok"
    assert report["status"] == "unavailable"  # driven by providers, not LaTeX


def test_latex_engine_none_is_degraded_not_unavailable(bare_env):
    """An explicit opt-out is a choice, not a broken machine."""
    probe = health.probe_latex()
    assert probe.status == "degraded"
    assert "tex" in probe.fallback.lower()


def test_health_endpoint_serves_the_report(bare_env):
    with TestClient(create_app()) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert {p["name"] for p in resp.json()["probes"]} == PROBE_NAMES


def test_reload_endpoint_rereads_config(bare_env, monkeypatch):
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json()["provider"] == "nim"
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        assert client.post("/api/health/reload").json()["provider"] == "ollama"
