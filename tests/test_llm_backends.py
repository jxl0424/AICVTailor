"""NIMProvider and OllamaProvider as the app actually constructs them.

The retry, backoff and schema-repair logic is covered against the shared base
class. These tests cover the wiring on top of it -- model resolution feeding
the per-role model map, parameters coming from models.yaml, the limiter being
attached (or deliberately not), and what availability() reports. That wiring is
what runs in production and was previously untested.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from aicvtailor.config import reload_config
from aicvtailor.llm import catalogue
from aicvtailor.llm.base import Role
from aicvtailor.llm.nim import NIMProvider
from aicvtailor.llm.ollama import OllamaProvider
from tests.fakes import FakeClient

LIVE = [
    "meta/llama-3.1-8b-instruct",
    "meta/llama-3.1-70b-instruct",
    "qwen/qwen3-235b-a22b",
]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(catalogue, "CACHE_FILE", tmp_path / "models.json")
    reload_config()
    yield
    reload_config()


class TestNIMProvider:
    def test_resolves_a_model_for_each_role_from_the_catalogue(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        provider = NIMProvider(client=FakeClient([]), catalogue=LIVE)

        assert provider.model_for(Role.EXTRACTOR) in LIVE
        assert provider.model_for(Role.REWRITER) in LIVE

    def test_sends_the_resolved_model_not_a_hardcoded_one(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        client = FakeClient(["done"])
        provider = NIMProvider(client=client, catalogue=LIVE)

        provider.complete("sys", "usr", role=Role.REWRITER)
        assert client.calls[0]["model"] == provider.model_for(Role.REWRITER)

    def test_env_override_reaches_the_wire(self, monkeypatch):
        """Swapping models without code changes is an explicit requirement."""
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        monkeypatch.setenv("REWRITER_MODEL", "qwen/qwen3-235b-a22b")
        reload_config()
        client = FakeClient(["done"])

        NIMProvider(client=client, catalogue=LIVE).complete("s", "u", role=Role.REWRITER)
        assert client.calls[0]["model"] == "qwen/qwen3-235b-a22b"

    def test_per_role_parameters_come_from_models_yaml(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        client = FakeClient(["a", "b"])
        provider = NIMProvider(client=client, catalogue=LIVE)

        provider.complete("s", "u", role=Role.EXTRACTOR)
        provider.complete("s", "u", role=Role.REWRITER)

        extractor, rewriter = client.calls
        assert extractor["temperature"] == 0.0  # deterministic extraction
        assert rewriter["temperature"] > 0.0  # some latitude for language
        assert extractor["max_tokens"] != rewriter["max_tokens"]

    def test_a_rate_limiter_is_attached(self, monkeypatch):
        """A hosted free tier without client-side pacing is the 429 the brief
        says must not happen."""
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        provider = NIMProvider(client=FakeClient([]), catalogue=LIVE)

        assert provider._limiter is not None
        assert provider._limiter.rpm <= 40

    def test_structured_output_works_through_the_concrete_provider(self, monkeypatch):
        class Shape(BaseModel):
            value: str

        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        client = FakeClient([json.dumps({"value": "ok"})])

        result = NIMProvider(client=client, catalogue=LIVE).complete("s", "u", Shape)
        assert result == {"value": "ok"}

    def test_availability_is_false_without_a_key(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "")
        reload_config()
        availability = NIMProvider(client=FakeClient([]), catalogue=LIVE).availability()

        assert not availability.ok
        assert "build.nvidia.com" in availability.fallback

    def test_availability_names_the_resolved_models(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        availability = NIMProvider(client=FakeClient([]), catalogue=LIVE).availability()

        assert availability.ok
        assert "extractor=" in availability.detail
        assert "rewriter=" in availability.detail

    def test_an_empty_catalogue_still_constructs(self, monkeypatch):
        """No key, no cache: the app must still start and report degraded."""
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        provider = NIMProvider(client=FakeClient([]), catalogue=[])

        assert provider.model_for(Role.EXTRACTOR)  # terminal fallback
        assert provider.availability().fallback  # and warns about it


class TestOllamaProvider:
    def test_uses_the_configured_model_for_both_roles(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
        reload_config()
        provider = OllamaProvider(client=FakeClient([]))

        assert provider.model_for(Role.EXTRACTOR) == "llama3.1:8b"
        assert provider.model_for(Role.REWRITER) == "llama3.1:8b"

    def test_has_no_rate_limiter_because_it_is_local(self, monkeypatch):
        reload_config()
        assert OllamaProvider(client=FakeClient([]))._limiter is None

    def test_completes_through_the_shared_path(self, monkeypatch):
        reload_config()
        client = FakeClient(["a local answer"])
        assert OllamaProvider(client=client).complete("s", "u") == "a local answer"

    def test_availability_is_false_when_no_server_responds(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:59999/v1")
        reload_config()
        availability = OllamaProvider(client=FakeClient([])).availability()

        assert not availability.ok
        assert "ollama serve" in availability.fallback

    def test_a_server_without_the_model_pulled_is_reported(self, monkeypatch):
        import aicvtailor.llm.ollama as ollama_mod

        class Response:
            def raise_for_status(self): ...

            def json(self):
                return {"models": [{"name": "some-other-model"}]}

        monkeypatch.setattr(ollama_mod.httpx, "get", lambda *a, **k: Response())
        monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
        reload_config()

        availability = OllamaProvider(client=FakeClient([])).availability()
        assert not availability.ok
        assert "ollama pull llama3.1:8b" in availability.fallback

    def test_a_ready_server_is_available(self, monkeypatch):
        import aicvtailor.llm.ollama as ollama_mod

        class Response:
            def raise_for_status(self): ...

            def json(self):
                return {"models": [{"name": "llama3.1:8b"}]}

        monkeypatch.setattr(ollama_mod.httpx, "get", lambda *a, **k: Response())
        monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
        reload_config()

        assert OllamaProvider(client=FakeClient([])).availability().ok


def test_both_backends_share_the_call_signature():
    """A/B'ing a tailoring run between providers depends on this."""
    import inspect

    assert inspect.signature(NIMProvider.complete) == inspect.signature(
        OllamaProvider.complete
    )
