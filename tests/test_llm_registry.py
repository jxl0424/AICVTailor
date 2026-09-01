"""Provider selection, degradation, and the Claude CLI adapter."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from aicvtailor.config import reload_config
from aicvtailor.llm import registry
from aicvtailor.llm.base import Availability, ProviderUnavailable, Role
from aicvtailor.llm.claude_cli import ClaudeCLIProvider


class Shape(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _clean():
    reload_config()
    yield
    reload_config()


class TestClaudeCLI:
    def test_reports_unavailable_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CLI", "false")
        reload_config()
        availability = ClaudeCLIProvider().availability()

        assert not availability.ok
        assert "Disabled" in availability.detail

    def test_missing_binary_reports_rather_than_crashing(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CLI", "true")
        monkeypatch.setenv("CLAUDE_CLI_PATH", "definitely-not-a-real-binary")
        reload_config()
        availability = ClaudeCLIProvider().availability()

        assert not availability.ok
        assert "not on PATH" in availability.detail

    def test_calling_while_disabled_raises_a_clear_error(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CLI", "false")
        reload_config()
        with pytest.raises(ProviderUnavailable):
            ClaudeCLIProvider().complete("sys", "usr")

    @pytest.mark.parametrize(
        "stdout",
        [
            '{"result": "the answer"}',
            '{"text": "the answer"}',
            '{"content": [{"type": "text", "text": "the answer"}]}',
        ],
    )
    def test_parses_the_known_cli_envelope_shapes(self, monkeypatch, stdout):
        """The CLI's JSON envelope has changed across versions, so the adapter
        checks each known key rather than assuming one."""
        monkeypatch.setenv("ENABLE_CLAUDE_CLI", "true")
        reload_config()
        provider = ClaudeCLIProvider(runner=lambda prompt: stdout)

        assert provider.complete("sys", "usr") == "the answer"

    def test_unrecognised_output_falls_back_to_raw_text(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CLI", "true")
        reload_config()
        provider = ClaudeCLIProvider(runner=lambda prompt: "plain text, not json")

        assert provider.complete("sys", "usr") == "plain text, not json"

    def test_schema_repair_works_the_same_as_the_hosted_path(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CLI", "true")
        reload_config()
        responses = iter(
            [
                json.dumps({"result": json.dumps({"wrong": "shape"})}),
                json.dumps({"result": json.dumps({"value": "fixed"})}),
            ]
        )
        provider = ClaudeCLIProvider(runner=lambda prompt: next(responses))

        assert provider.complete("sys", "usr", Shape) == {"value": "fixed"}

    def test_the_call_signature_matches_the_hosted_providers(self):
        """A/B'ing a run between providers only works if the call sites are
        identical."""
        from aicvtailor.llm.openai_compat import OpenAICompatProvider

        import inspect

        hosted = inspect.signature(OpenAICompatProvider.complete)
        local = inspect.signature(ClaudeCLIProvider.complete)
        assert list(hosted.parameters) == list(local.parameters)


class TestRegistry:
    def test_falls_through_to_a_usable_backend(self, monkeypatch):
        """LLM_PROVIDER names a preference, not a requirement."""
        monkeypatch.setattr(
            registry,
            "BUILDERS",
            {
                "nim": lambda runlog=None: _Stub("nim", ok=False),
                "ollama": lambda runlog=None: _Stub("ollama", ok=True),
                "claude_cli": lambda runlog=None: _Stub("claude_cli", ok=False),
            },
        )
        assert registry.get_provider("nim").name == "ollama"

    def test_prefers_the_configured_backend_when_it_works(self, monkeypatch):
        monkeypatch.setattr(
            registry,
            "BUILDERS",
            {
                "nim": lambda runlog=None: _Stub("nim", ok=True),
                "ollama": lambda runlog=None: _Stub("ollama", ok=True),
                "claude_cli": lambda runlog=None: _Stub("claude_cli", ok=True),
            },
        )
        assert registry.get_provider("ollama").name == "ollama"

    def test_a_backend_that_explodes_on_construction_is_skipped(self, monkeypatch):
        def boom(runlog=None):
            raise RuntimeError("bad config")

        monkeypatch.setattr(
            registry,
            "BUILDERS",
            {
                "nim": boom,
                "ollama": lambda runlog=None: _Stub("ollama", ok=True),
                "claude_cli": lambda runlog=None: _Stub("claude_cli", ok=False),
            },
        )
        assert registry.get_provider("nim").name == "ollama"

    def test_no_usable_backend_raises_with_every_reason(self, monkeypatch):
        monkeypatch.setattr(
            registry,
            "BUILDERS",
            {
                "nim": lambda runlog=None: _Stub("nim", ok=False, detail="no key"),
                "ollama": lambda runlog=None: _Stub("ollama", ok=False, detail="not running"),
                "claude_cli": lambda runlog=None: _Stub("claude_cli", ok=False, detail="off"),
            },
        )
        with pytest.raises(ProviderUnavailable) as excinfo:
            registry.get_provider("nim")

        message = str(excinfo.value)
        for reason in ("no key", "not running", "off"):
            assert reason in message

    def test_availability_report_covers_every_backend(self):
        report = registry.availability_report()
        assert set(report) == {"nim", "ollama", "claude_cli"}
        assert all(isinstance(a, Availability) for a in report.values())

    def test_report_survives_a_backend_that_cannot_initialise(self, monkeypatch):
        def boom(runlog=None):
            raise RuntimeError("kaboom")

        monkeypatch.setitem(registry.BUILDERS, "nim", boom)
        report = registry.availability_report()

        assert not report["nim"].ok
        assert "kaboom" in report["nim"].detail


class _Stub:
    def __init__(self, name: str, *, ok: bool, detail: str = "") -> None:
        self.name = name
        self._ok = ok
        self._detail = detail or name

    def availability(self) -> Availability:
        return Availability(ok=self._ok, detail=self._detail)

    def complete(self, system, user, schema=None, *, role=Role.EXTRACTOR):
        return ""
