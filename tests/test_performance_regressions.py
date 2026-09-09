"""Guards for the three causes of slow analysis.

Each of these made every Analyse pay avoidable network latency. They are cheap
to reintroduce and invisible in a unit test that only checks correctness, so
they get explicit tests.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from aicvtailor.analysis import jd_parse
from aicvtailor.config import reload_config
from aicvtailor.llm import catalogue, registry

JD = Path(__file__).parent / "fixtures" / "jd_ai_engineer.txt"


class CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *args, **kwargs):
        self.calls += 1
        return {"company": "X", "role": "Y", "location": "Z"}


class TestExtractorCallIsAvoided:
    def test_no_model_call_when_regex_found_everything(self):
        """The posting states company, role and location plainly. An earlier
        version still called a model to second-guess the role, adding a network
        round trip to nearly every analysis."""
        provider = CountingProvider()
        jd_parse.parse(JD.read_text(encoding="utf-8"), provider)
        assert provider.calls == 0

    def test_a_model_call_still_happens_for_a_real_gap(self):
        provider = CountingProvider()
        jd_parse.parse("We need someone who knows Python.\n" * 5, provider)
        assert provider.calls == 1

    def test_a_first_line_role_is_trusted(self):
        parsed = jd_parse.parse_regex(JD.read_text(encoding="utf-8"))
        assert parsed.role == "AI Engineer (LLM Systems)"
        assert parsed.resolved_by["role"] == "regex:first-line"


class TestProviderIsCached:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch, tmp_path):
        monkeypatch.setattr(catalogue, "CACHE_FILE", tmp_path / "models.json")
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        registry.clear_cache()
        yield
        registry.clear_cache()
        reload_config()

    def test_building_twice_returns_the_same_instance(self):
        """Constructing a provider resolves models and can fetch the
        catalogue. Doing that per request is pure latency."""
        assert registry.build("nim") is registry.build("nim")

    def test_the_cache_can_be_bypassed(self):
        assert registry.build("nim") is not registry.build("nim", use_cache=False)

    def test_a_config_reload_clears_it(self):
        first = registry.build("nim")
        reload_config()
        assert registry.build("nim") is not first

    def test_a_cached_provider_still_gets_the_current_run_log(self, tmp_path, monkeypatch):
        from aicvtailor import paths
        from aicvtailor.llm.runlog import RunLog

        monkeypatch.setattr(paths, "RUNS_DIR", tmp_path)
        first = registry.build("nim", runlog=RunLog("run-one"))
        second = registry.build("nim", runlog=RunLog("run-two"))

        assert first is second
        assert second._runlog.run_id == "run-two"


class TestFailedFetchIsNotRetried:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch, tmp_path):
        monkeypatch.setattr(catalogue, "CACHE_FILE", tmp_path / "models.json")
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
        reload_config()
        catalogue._last_failure_at = 0.0
        yield
        catalogue._last_failure_at = 0.0
        reload_config()

    def test_a_failure_suppresses_the_next_attempt(self):
        class Failing:
            def __init__(self) -> None:
                self.attempts = 0

            def get(self, *a, **k):
                self.attempts += 1
                raise OSError("unreachable")

            def close(self): ...

        client = Failing()
        for _ in range(5):
            catalogue.fetch_catalogue(client=client)

        assert client.attempts == 1, "the network was retried on every call"

    def test_force_overrides_the_cooldown(self):
        """`aicvtailor models --refresh` must always actually try."""
        class Failing:
            def __init__(self) -> None:
                self.attempts = 0

            def get(self, *a, **k):
                self.attempts += 1
                raise OSError("unreachable")

            def close(self): ...

        client = Failing()
        catalogue.fetch_catalogue(client=client)
        catalogue.fetch_catalogue(client=client, force=True)
        assert client.attempts == 2

    def test_the_cooldown_expires(self, monkeypatch):
        class Failing:
            def __init__(self) -> None:
                self.attempts = 0

            def get(self, *a, **k):
                self.attempts += 1
                raise OSError("unreachable")

            def close(self): ...

        client = Failing()
        catalogue.fetch_catalogue(client=client)
        catalogue._last_failure_at = time.time() - catalogue.FAILED_FETCH_COOLDOWN_SECONDS - 1
        catalogue.fetch_catalogue(client=client)

        assert client.attempts == 2
