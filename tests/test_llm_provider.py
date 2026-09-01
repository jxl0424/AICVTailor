"""Retry policy, schema validation and the run log.

All against a fake transport: the suite never touches the network, never
spends the free tier, and runs in milliseconds.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from aicvtailor.llm.base import (
    LLMError,
    Role,
    SchemaValidationError,
    extract_json,
)
from aicvtailor.llm.limiter import TokenBucket
from aicvtailor.llm.openai_compat import OpenAICompatProvider
from aicvtailor.llm.runlog import RunLog
from tests.fakes import FakeClient, FakeClock, FakeHTTPError, RecordingSleep


class Terms(BaseModel):
    terms: list[str]
    confidence: float


def make_provider(script, *, runlog=None, limiter=None):
    sleep = RecordingSleep()
    client = FakeClient(script)
    provider = OpenAICompatProvider(
        client,
        limiter=limiter,
        runlog=runlog,
        model_for={Role.EXTRACTOR: "test/extractor", Role.REWRITER: "test/rewriter"},
        sleep=sleep,
    )
    return provider, client, sleep


class TestPlainCompletion:
    def test_returns_text_when_no_schema_is_requested(self):
        provider, _, _ = make_provider(["a rewritten bullet"])
        assert provider.complete("sys", "usr") == "a rewritten bullet"

    def test_makes_exactly_one_call_per_completion(self):
        """Regression: an early version evaluated the call twice, doubling
        every request against a rate-limited free tier."""
        provider, client, _ = make_provider(["once"])
        provider.complete("sys", "usr")
        assert len(client.calls) == 1

    def test_role_selects_the_model_and_parameters(self):
        sleep = RecordingSleep()
        client = FakeClient(["ok"])
        provider = OpenAICompatProvider(
            client,
            model_for={Role.EXTRACTOR: "test/extractor", Role.REWRITER: "test/rewriter"},
            params_for={Role.REWRITER: {"temperature": 0.2, "max_tokens": 1024}},
            sleep=sleep,
        )
        provider.complete("sys", "usr", role=Role.REWRITER)

        call = client.calls[0]
        assert call["model"] == "test/rewriter"
        assert call["temperature"] == 0.2
        assert call["max_tokens"] == 1024


class TestRetryPolicy:
    def test_429_backs_off_and_retries(self):
        provider, client, sleep = make_provider(
            [FakeHTTPError(429), FakeHTTPError(429), "recovered"]
        )
        assert provider.complete("sys", "usr") == "recovered"
        assert len(client.calls) == 3
        assert sleep.delays == pytest.approx([1.0, 2.0], rel=0.2)  # exponential

    def test_429_backoff_has_jitter(self):
        """Identical delays across processes sharing a key would resynchronise
        the collision the backoff is meant to break up."""
        delays = []
        for _ in range(6):
            provider, _, sleep = make_provider([FakeHTTPError(429), "ok"])
            provider.complete("sys", "usr")
            delays.append(sleep.delays[0])
        assert len(set(delays)) > 1
        assert all(1.0 <= d <= 1.1 for d in delays)

    def test_429_eventually_gives_up(self):
        provider, client, _ = make_provider([FakeHTTPError(429)] * 6)
        with pytest.raises(LLMError):
            provider.complete("sys", "usr")
        assert len(client.calls) == 5  # MAX_429_ATTEMPTS

    def test_server_error_is_retried_exactly_once(self):
        provider, client, _ = make_provider([FakeHTTPError(503), "recovered"])
        assert provider.complete("sys", "usr") == "recovered"
        assert len(client.calls) == 2

    def test_a_second_server_error_is_fatal(self):
        provider, client, _ = make_provider([FakeHTTPError(500), FakeHTTPError(500)])
        with pytest.raises(LLMError):
            provider.complete("sys", "usr")
        assert len(client.calls) == 2

    def test_client_errors_are_not_retried(self):
        """A 400 will fail identically however many times it is sent."""
        provider, client, _ = make_provider([FakeHTTPError(400)])
        with pytest.raises(LLMError):
            provider.complete("sys", "usr")
        assert len(client.calls) == 1

    def test_the_limiter_paces_calls(self):
        clock = FakeClock()

        def advancing_sleep(seconds):
            clock.advance(seconds)

        limiter = TokenBucket(60, clock=clock, sleep=advancing_sleep)
        for _ in range(60):
            limiter.acquire()

        provider, _, _ = make_provider(["ok"], limiter=limiter)
        provider.complete("sys", "usr")
        assert clock.now > 0, "call should have waited for a permit"


class TestStructuredOutput:
    def test_valid_json_is_parsed_against_the_schema(self):
        provider, client, _ = make_provider(
            [json.dumps({"terms": ["python"], "confidence": 0.9})]
        )
        assert provider.complete("sys", "usr", Terms) == {
            "terms": ["python"],
            "confidence": 0.9,
        }
        assert client.calls[0]["response_format"] == {"type": "json_object"}

    def test_the_schema_is_described_in_the_prompt(self):
        provider, client, _ = make_provider(
            [json.dumps({"terms": [], "confidence": 0.0})]
        )
        provider.complete("sys", "usr", Terms)
        assert "JSON Schema" in client.calls[0]["messages"][0]["content"]

    def test_invalid_json_triggers_one_repair_retry(self):
        provider, client, _ = make_provider(
            [
                json.dumps({"terms": "not a list", "confidence": 0.9}),
                json.dumps({"terms": ["fixed"], "confidence": 0.9}),
            ]
        )
        assert provider.complete("sys", "usr", Terms)["terms"] == ["fixed"]
        assert len(client.calls) == 2

    def test_the_repair_prompt_quotes_the_validation_error(self):
        provider, client, _ = make_provider(
            [
                json.dumps({"terms": "not a list", "confidence": 0.9}),
                json.dumps({"terms": ["fixed"], "confidence": 0.9}),
            ]
        )
        provider.complete("sys", "usr", Terms)

        repair = client.calls[1]["messages"][-1]["content"]
        assert "did not validate" in repair
        assert "terms" in repair

    def test_a_second_failure_raises_rather_than_returning_junk(self):
        bad = json.dumps({"terms": "still wrong", "confidence": 0.9})
        provider, client, _ = make_provider([bad, bad])

        with pytest.raises(SchemaValidationError) as excinfo:
            provider.complete("sys", "usr", Terms)
        assert len(client.calls) == 2
        assert excinfo.value.raw == bad


class TestJSONExtraction:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"a": 1}',
            '```json\n{"a": 1}\n```',
            '```\n{"a": 1}\n```',
            'Here is the JSON you asked for:\n{"a": 1}',
            '{"a": 1}\nHope that helps!',
        ],
    )
    def test_models_wrap_json_in_prose_and_fences(self, raw):
        """Packaging is not a failure worth burning a repair retry on."""
        assert json.loads(extract_json(raw)) == {"a": 1}

    def test_genuinely_broken_output_is_passed_through_to_fail_loudly(self):
        assert extract_json("no json at all") == "no json at all"


class TestRunLog:
    def test_records_a_call_with_model_and_timing(self, tmp_path, monkeypatch):
        from aicvtailor import paths

        monkeypatch.setattr(paths, "RUNS_DIR", tmp_path)
        runlog = RunLog("testrun")
        provider, _, _ = make_provider(["done"], runlog=runlog)
        provider.complete("sys", "usr", stage="rewrite")

        (record,) = runlog.read()
        assert record["stage"] == "rewrite"
        assert record["model"] == "test/extractor"
        assert record["attempts"] == 1
        assert record["latency_ms"] >= 0

    def test_records_retries_so_a_slow_run_is_explicable(self, tmp_path, monkeypatch):
        from aicvtailor import paths

        monkeypatch.setattr(paths, "RUNS_DIR", tmp_path)
        runlog = RunLog("retryrun")
        provider, _, _ = make_provider([FakeHTTPError(429), "ok"], runlog=runlog)
        provider.complete("sys", "usr")

        assert runlog.read()[0]["attempts"] == 2

    def test_records_the_error_when_a_call_fails(self, tmp_path, monkeypatch):
        from aicvtailor import paths

        monkeypatch.setattr(paths, "RUNS_DIR", tmp_path)
        runlog = RunLog("failrun")
        provider, _, _ = make_provider([FakeHTTPError(400)], runlog=runlog)

        with pytest.raises(LLMError):
            provider.complete("sys", "usr")
        assert "400" in runlog.read()[0]["error"]

    def test_prompts_can_be_withheld(self, tmp_path, monkeypatch):
        from aicvtailor import paths

        monkeypatch.setattr(paths, "RUNS_DIR", tmp_path)
        runlog = RunLog("quiet", log_prompts=False)
        provider, _, _ = make_provider(["done"], runlog=runlog)
        provider.complete("sys", "secret cv text")

        record = runlog.read()[0]
        assert "user" not in record
        assert record["prompt_chars"] > 0
