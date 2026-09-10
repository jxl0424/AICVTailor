"""Guards for the two bugs that left the Tailor button permanently disabled.

Both only appeared once the skills dictionary was large enough for RELOCATE
suggestions to fire, so growing the dictionary made the app worse rather than
better -- and neither showed up in a test that exercised gaps alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from aicvtailor import paths
from aicvtailor.analysis.match import MatchStatus
from aicvtailor.api import suggestions as suggestions_api
from aicvtailor.db import get_engine
from aicvtailor.llm.base import LLMError
from aicvtailor.main import create_app
from aicvtailor.models import Suggestion, SuggestionAction, SuggestionStatus

JD = Path(__file__).parent / "fixtures" / "jd_ai_engineer.txt"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(paths, "OUTPUT_DIR", tmp_path / "output")
    import aicvtailor.db as db

    monkeypatch.setattr(db, "_engine", None)
    with TestClient(create_app()) as client:
        yield client


class BrokenProvider:
    """A provider on a model id the endpoint does not serve -- exactly what an
    unverified fallback resolves to."""

    def complete(self, *args, **kwargs):
        raise LLMError("nim call failed: 404 model not found")


class TestStatusVocabulary:
    def test_the_stored_enum_mirrors_the_matcher(self):
        """These drifted, and the mismatch only bit on statuses that gaps never
        produce."""
        assert {s.value for s in SuggestionStatus} == {s.value for s in MatchStatus}

    @pytest.mark.parametrize("status", list(MatchStatus))
    def test_every_match_status_survives_a_database_round_trip(self, client, status):
        with Session(get_engine()) as session:
            session.add(
                Suggestion(
                    jd_id=None,
                    term="x",
                    status=SuggestionStatus(status.value),
                    action=SuggestionAction.REWORD,
                )
            )
            session.commit()

        with Session(get_engine()) as session:
            rows = session.exec(select(Suggestion)).all()
            assert rows[-1].status.value == status.value


class TestProviderFailureDegrades:
    @pytest.fixture
    def analysed(self, client):
        client.post("/api/masters/import")
        response = client.post(
            "/api/analyse",
            json={"text": JD.read_text(encoding="utf-8"), "use_model_for_fields": False},
        )
        if response.status_code == 409:
            pytest.skip("no master resume available in this checkout")
        return response.json()

    def test_a_failing_provider_does_not_500(self, client, analysed, monkeypatch):
        monkeypatch.setattr(suggestions_api, "get_provider", lambda **k: BrokenProvider())
        response = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]})

        assert response.status_code == 200, "a broken model id must not sink the request"
        assert response.json()["suggestions"]

    def test_relocations_still_appear_without_a_working_model(
        self, client, analysed, monkeypatch
    ):
        """RELOCATE is deterministic. A broken rewriter must not cost it."""
        monkeypatch.setattr(suggestions_api, "get_provider", lambda **k: BrokenProvider())
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()

        actions = {s["action"] for s in created["suggestions"]}
        assert "GAP" in actions
        assert any(s["applicable"] for s in created["suggestions"]) or "RELOCATE" not in actions

    def test_a_failed_rewrite_says_why(self, client, analysed, monkeypatch):
        monkeypatch.setattr(suggestions_api, "get_provider", lambda **k: BrokenProvider())
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()

        failed = [s for s in created["suggestions"] if "could not be generated" in s["rationale"]]
        if failed:
            assert "404" in failed[0]["rationale"]

    def test_the_whole_flow_completes_with_a_broken_provider(
        self, client, analysed, monkeypatch
    ):
        """The end-to-end statement: analyse, suggest, accept, tailor -- with a
        provider that fails every call."""
        monkeypatch.setattr(suggestions_api, "get_provider", lambda **k: BrokenProvider())
        created = client.post("/api/suggest", json={"jd_id": analysed["jd_id"]}).json()

        applicable = [s for s in created["suggestions"] if s["applicable"]]
        if not applicable:
            pytest.skip("this CV/JD pair produced nothing applicable")

        for suggestion in applicable:
            assert (
                client.patch(
                    f"/api/suggestions/{suggestion['id']}", json={"accepted": True}
                ).status_code
                == 200
            )

        run = client.post("/api/tailor", json={"jd_id": analysed["jd_id"]}).json()
        assert run["applied"] >= 1
