"""Suggestion API: generation, persistence, and the accept/reject rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicvtailor import paths
from aicvtailor.api import suggestions as suggestions_api
from aicvtailor.main import create_app

JD = Path(__file__).parent / "fixtures" / "jd_ai_engineer.txt"


class StubProvider:
    """Returns the source bullet unchanged, which always passes guardrails."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, system, user, schema=None, *, role=None, **kwargs):
        source = user.split("Source bullet:\n", 1)[1].split("\n\n", 1)[0]
        self.calls.append(source)
        return {"rewritten": source}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path / "runs")
    import aicvtailor.db as db

    monkeypatch.setattr(db, "_engine", None)
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def analysed(client):
    client.post("/api/masters/import")
    response = client.post(
        "/api/analyse",
        json={"text": JD.read_text(encoding="utf-8"), "use_model_for_fields": False},
    )
    if response.status_code == 409:
        pytest.skip("no master resume available in this checkout")
    return response.json()


@pytest.fixture
def stub(monkeypatch):
    provider = StubProvider()
    monkeypatch.setattr(suggestions_api, "get_provider", lambda **kwargs: provider)
    return provider


class TestGeneration:
    def test_suggestions_are_generated_and_persisted(self, client, analysed, stub):
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()
        assert created["suggestions"]

        listed = client.get(f"/api/jds/{analysed['jd_id']}/suggestions").json()
        assert len(listed) == len(created["suggestions"])

    def test_regenerating_replaces_rather_than_duplicates(self, client, analysed, stub):
        first = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()
        client.post("/api/suggest", json={"jd_id": analysed["jd_id"]})

        listed = client.get(f"/api/jds/{analysed['jd_id']}/suggestions").json()
        assert len(listed) == len(first["suggestions"])

    def test_every_reword_carries_its_source_bullet(self, client, analysed, stub):
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()
        for suggestion in created["suggestions"]:
            if suggestion["action"] == "REWORD":
                assert suggestion["source_bullet_id"]
                assert suggestion["target_id"] == suggestion["source_bullet_id"]

    def test_gaps_are_never_applicable(self, client, analysed, stub):
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()
        for suggestion in created["suggestions"]:
            if suggestion["action"] == "GAP":
                assert suggestion["applicable"] is False
                assert suggestion["target_id"] is None

    def test_works_without_a_provider_by_producing_gaps(self, client, analysed, monkeypatch):
        """No key, no Ollama: the run still reports honestly rather than
        failing."""

        def unavailable(**kwargs):
            raise RuntimeError("no usable provider")

        monkeypatch.setattr(suggestions_api, "get_provider", unavailable)
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()

        assert created["provider_available"] is False
        assert created["provider_error"]
        assert all(s["action"] in {"GAP", "RELOCATE"} for s in created["suggestions"])

    def test_an_unknown_jd_is_a_404(self, client):
        assert client.post("/api/suggest", json={"jd_id": 99999}).status_code == 404


class TestDecisions:
    def test_an_applicable_suggestion_can_be_accepted(self, client, analysed, stub):
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()
        applicable = [s for s in created["suggestions"] if s["applicable"]]
        if not applicable:
            pytest.skip("this JD produced no applicable suggestions")

        response = client.patch(
            f"/api/suggestions/{applicable[0]['id']}", json={"accepted": True}
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is True

    def test_accepting_a_gap_is_refused_with_the_reason(self, client, analysed, stub):
        """The acceptance criterion: a term marked missing can never become
        resume text, not even by an explicit click."""
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()
        gap = next(s for s in created["suggestions"] if s["action"] == "GAP")

        response = client.patch(f"/api/suggestions/{gap['id']}", json={"accepted": True})
        assert response.status_code == 409
        assert "nothing to apply" in response.json()["detail"]

    def test_a_gap_can_still_be_rejected(self, client, analysed, stub):
        """Rejecting is just dismissing it from the list."""
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()
        gap = next(s for s in created["suggestions"] if s["action"] == "GAP")

        assert (
            client.patch(f"/api/suggestions/{gap['id']}", json={"accepted": False}).status_code
            == 200
        )

    def test_decisions_survive_a_reload(self, client, analysed, stub):
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()
        applicable = [s for s in created["suggestions"] if s["applicable"]]
        if not applicable:
            pytest.skip("this JD produced no applicable suggestions")

        client.patch(f"/api/suggestions/{applicable[0]['id']}", json={"accepted": True})
        listed = client.get(f"/api/jds/{analysed['jd_id']}/suggestions").json()

        assert next(s for s in listed if s["id"] == applicable[0]["id"])["accepted"] is True

    def test_an_unknown_suggestion_is_a_404(self, client):
        assert client.patch("/api/suggestions/99999", json={"accepted": False}).status_code == 404
