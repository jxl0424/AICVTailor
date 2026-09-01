"""Live model resolution.

The brief lists preferred model ids that are unlikely to all exist. The point
of this layer is that a stale hint degrades to a warning and a working
fallback, never to a crash or a silently wrong model.
"""

from __future__ import annotations

import json
import time

import pytest

from aicvtailor.config import reload_config
from aicvtailor.llm import catalogue
from aicvtailor.llm.base import Role

LIVE = [
    "meta/llama-3.1-8b-instruct",
    "meta/llama-3.1-70b-instruct",
    "qwen/qwen3-235b-a22b",
    "deepseek-ai/deepseek-r1",
]


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(catalogue, "CACHE_FILE", tmp_path / "nim_models.json")
    reload_config()
    yield
    reload_config()


class TestMatching:
    def test_prefix_match_survives_a_vendor_suffix(self):
        """A hint of `qwen/qwen3` must still find `qwen/qwen3-235b-a22b`."""
        assert catalogue._matches("qwen/qwen3", "qwen/qwen3-235b-a22b")

    def test_prefix_match_does_not_match_a_different_vendor(self):
        assert not catalogue._matches("qwen/qwen3", "meta/llama-3.1-8b-instruct")

    def test_glob_hints_are_supported(self):
        assert catalogue._matches("qwen/qwen3.5-*", "qwen/qwen3.5-plus")
        assert not catalogue._matches("qwen/qwen3.5-*", "qwen/qwen3-235b-a22b")


class TestResolution:
    def test_walks_the_preference_list_in_order(self, monkeypatch):
        monkeypatch.setattr(
            catalogue,
            "get_model_prefs",
            lambda: {
                "roles": {
                    "extractor": {
                        "prefer": ["nonexistent/model-a", "qwen/qwen3", "meta/llama-3.1-8b"],
                        "terminal_fallback": "meta/llama-3.1-8b-instruct",
                    }
                }
            },
        )
        result = catalogue.resolve(Role.EXTRACTOR, LIVE)

        assert result.model == "qwen/qwen3-235b-a22b"
        assert result.source == "preference"
        assert result.verified
        assert result.skipped == ("nonexistent/model-a",)
        assert "unavailable" in result.warning

    def test_falls_back_when_nothing_preferred_is_live(self, monkeypatch):
        """The expected outcome for the brief's speculative model ids."""
        monkeypatch.setattr(
            catalogue,
            "get_model_prefs",
            lambda: {
                "roles": {
                    "rewriter": {
                        "prefer": ["nvidia/nemotron-3-super", "zhipuai/glm-5.1"],
                        "terminal_fallback": "meta/llama-3.1-70b-instruct",
                    }
                }
            },
        )
        result = catalogue.resolve(Role.REWRITER, LIVE)

        assert result.model == "meta/llama-3.1-70b-instruct"
        assert result.source == "terminal_fallback"
        assert result.verified
        assert "fell back" in result.warning

    def test_an_unverifiable_fallback_still_resolves_but_warns(self, monkeypatch):
        monkeypatch.setattr(
            catalogue,
            "get_model_prefs",
            lambda: {
                "roles": {
                    "extractor": {"prefer": ["a/b"], "terminal_fallback": "c/d"}
                }
            },
        )
        result = catalogue.resolve(Role.EXTRACTOR, LIVE)

        assert result.model == "c/d"
        assert not result.verified
        assert "not in the live catalogue" in result.warning

    def test_env_override_beats_the_preference_list(self, monkeypatch):
        monkeypatch.setenv("EXTRACTOR_MODEL", "deepseek-ai/deepseek-r1")
        reload_config()
        result = catalogue.resolve(Role.EXTRACTOR, LIVE)

        assert result.model == "deepseek-ai/deepseek-r1"
        assert result.source == "env"
        assert result.verified

    def test_env_override_is_honoured_even_if_unlisted(self, monkeypatch):
        """The user may know about a model this endpoint has not advertised."""
        monkeypatch.setenv("REWRITER_MODEL", "private/preview-model")
        reload_config()
        result = catalogue.resolve(Role.REWRITER, LIVE)

        assert result.model == "private/preview-model"
        assert not result.verified

    def test_shipped_config_resolves_both_roles_against_a_live_catalogue(self):
        """The config committed to the repo must work, not just test doubles."""
        for role, resolution in catalogue.resolve_all(LIVE).items():
            assert resolution.model, f"{role} resolved to nothing"
            assert resolution.verified, f"{role} could not be satisfied by {LIVE}"


class TestCaching:
    def test_fetch_writes_a_cache(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()

        class FakeResponse:
            status_code = 200

            def raise_for_status(self): ...

            def json(self):
                return {"data": [{"id": m} for m in LIVE]}

        class FakeClient:
            def get(self, url, headers=None):
                assert url.endswith("/models")
                assert headers["Authorization"] == "Bearer test-key"
                return FakeResponse()

            def close(self): ...

        ids = catalogue.fetch_catalogue(force=True, client=FakeClient())
        assert ids == LIVE
        assert catalogue.CACHE_FILE.exists()
        assert json.loads(catalogue.CACHE_FILE.read_text())["_fetched_at"] > 0

    def test_a_fresh_cache_is_reused_without_a_request(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        catalogue.CACHE_FILE.write_text(
            json.dumps({"_fetched_at": time.time(), "data": [{"id": m} for m in LIVE]})
        )

        class ExplodingClient:
            def get(self, *a, **k):
                raise AssertionError("should not hit the network with a fresh cache")

            def close(self): ...

        assert catalogue.fetch_catalogue(client=ExplodingClient()) == LIVE

    def test_a_stale_cache_is_used_when_the_network_fails(self, monkeypatch):
        """A dead endpoint must not stop the app from starting."""
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        catalogue.CACHE_FILE.write_text(
            json.dumps({"_fetched_at": 0, "data": [{"id": m} for m in LIVE]})
        )

        class BrokenClient:
            def get(self, *a, **k):
                raise OSError("network down")

            def close(self): ...

        assert catalogue.fetch_catalogue(client=BrokenClient()) == LIVE

    def test_no_key_means_no_request(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "")
        reload_config()

        class ExplodingClient:
            def get(self, *a, **k):
                raise AssertionError("should not call the API without a key")

            def close(self): ...

        assert catalogue.fetch_catalogue(client=ExplodingClient()) == []
