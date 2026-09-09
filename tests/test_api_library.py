"""Library: filtering, duplication, and per-change rejection."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from aicvtailor import paths
from aicvtailor.db import get_engine
from aicvtailor.main import create_app
from aicvtailor.models import Suggestion, SuggestionAction, SuggestionStatus, TailoredResume
from aicvtailor.tailor.compile import detect_engine

JD = Path(__file__).parent / "fixtures" / "jd_ai_engineer.txt"
needs_latex = pytest.mark.skipif(detect_engine() is None, reason="no LaTeX engine installed")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(paths, "OUTPUT_DIR", tmp_path / "output")
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
def tailored(client, analysed):
    """A JD with one accepted edit, tailored."""
    from aicvtailor.latex import parse

    masters = client.get("/api/masters").json()
    document = parse((paths.MASTER_DIR / masters[0]["filename"]).read_text(encoding="utf-8"))
    bullet = next(iter(document.bullets()))

    with Session(get_engine()) as session:
        session.add(
            Suggestion(
                jd_id=analysed["jd_id"],
                term="retrieval",
                category="method",
                weight=5.0,
                status=SuggestionStatus.IMPLIED,
                action=SuggestionAction.REWORD,
                proposed_text=bullet.text + " using retrieval",
                source_bullet_id=bullet.id,
                target_id=bullet.id,
                rationale="test",
                accepted=True,
            )
        )
        session.commit()

    return client.post("/api/tailor", json={"jd_id": analysed["jd_id"]}).json()


@needs_latex
class TestFiltering:
    def test_lists_versions_newest_first(self, client, tailored, analysed):
        client.post("/api/tailor", json={"jd_id": analysed["jd_id"]})
        rows = client.get("/api/tailored").json()

        assert len(rows) == 2
        assert rows[0]["created_at"] >= rows[1]["created_at"]

    def test_filters_by_company_substring(self, client, tailored):
        assert client.get("/api/tailored", params={"company": "Northwind"}).json()
        assert client.get("/api/tailored", params={"company": "Nonexistent"}).json() == []

    def test_filters_by_role_substring(self, client, tailored):
        assert client.get("/api/tailored", params={"role": "Engineer"}).json()
        assert client.get("/api/tailored", params={"role": "Chef"}).json() == []

    def test_company_filter_is_case_insensitive(self, client, tailored):
        assert client.get("/api/tailored", params={"company": "northwind"}).json()

    def test_filters_by_date_range(self, client, tailored):
        today = date.today()
        assert client.get(
            "/api/tailored", params={"since": str(today - timedelta(days=1))}
        ).json()
        assert (
            client.get(
                "/api/tailored", params={"until": str(today - timedelta(days=1))}
            ).json()
            == []
        )

    def test_reports_the_coverage_delta(self, client, tailored):
        row = client.get("/api/tailored").json()[0]
        assert row["coverage_delta"] is not None
        assert row["coverage_delta"] == pytest.approx(
            row["coverage_after"] - row["coverage_before"], abs=0.1
        )

    def test_compiled_only_filter(self, client, tailored):
        assert client.get("/api/tailored", params={"compiled_only": True}).json()


@needs_latex
class TestDuplicate:
    def test_carries_edits_onto_a_new_jd(self, client, tailored, analysed):
        second = client.post(
            "/api/analyse",
            json={"text": JD.read_text(encoding="utf-8"), "use_model_for_fields": False},
        ).json()

        result = client.post(
            f"/api/tailored/{tailored['tailored_id']}/duplicate",
            json={"jd_id": second["jd_id"]},
        ).json()

        assert result["copied"]
        assert result["stale"] == []

        carried = client.get(f"/api/jds/{second['jd_id']}/suggestions").json()
        assert carried
        assert all(s["accepted"] for s in carried)

    def test_the_duplicate_can_be_tailored_straight_away(self, client, tailored, analysed):
        second = client.post(
            "/api/analyse",
            json={"text": JD.read_text(encoding="utf-8"), "use_model_for_fields": False},
        ).json()
        client.post(
            f"/api/tailored/{tailored['tailored_id']}/duplicate",
            json={"jd_id": second["jd_id"]},
        )

        run = client.post("/api/tailor", json={"jd_id": second["jd_id"]}).json()
        assert run["applied"] >= 1
        assert run["compiled"]

    def test_edits_whose_bullet_no_longer_exists_are_reported_not_dropped(
        self, client, tailored, analysed
    ):
        """The master may have been edited since the earlier version was made."""
        second = client.post(
            "/api/analyse",
            json={"text": JD.read_text(encoding="utf-8"), "use_model_for_fields": False},
        ).json()

        with Session(get_engine()) as session:
            origin = session.get(TailoredResume, tailored["tailored_id"])
            diff = dict(origin.diff)
            diff["changes"] = [
                {**c, "target_id": "s99.e99.b99"} for c in diff["changes"]
            ]
            origin.diff = diff
            session.add(origin)
            session.commit()

        result = client.post(
            f"/api/tailored/{tailored['tailored_id']}/duplicate",
            json={"jd_id": second["jd_id"]},
        ).json()

        assert result["copied"] == []
        assert result["stale"] == ["s99.e99.b99"]
        assert "no longer exists" in result["note"]

    def test_an_unknown_source_is_a_404(self, client, analysed):
        assert (
            client.post("/api/tailored/9999/duplicate", json={"jd_id": analysed["jd_id"]}).status_code
            == 404
        )


@needs_latex
class TestRejectChange:
    def test_rejecting_a_change_regenerates_without_it(self, client, tailored):
        target = tailored["changes"][0]["target_id"]
        assert tailored["applied"] == 1

        rerun = client.post(
            f"/api/tailored/{tailored['tailored_id']}/reject-change",
            params={"target_id": target},
        ).json()

        assert rerun["applied"] == 0
        assert rerun["compiled"], "the regenerated version must still compile"
        assert rerun["tailored_id"] != tailored["tailored_id"]

    def test_the_rejected_suggestion_is_marked_unaccepted(self, client, tailored, analysed):
        target = tailored["changes"][0]["target_id"]
        client.post(
            f"/api/tailored/{tailored['tailored_id']}/reject-change",
            params={"target_id": target},
        )

        rows = client.get(f"/api/jds/{analysed['jd_id']}/suggestions").json()
        assert not any(s["accepted"] for s in rows if s["target_id"] == target)

    def test_rejecting_an_unknown_target_is_a_404(self, client, tailored):
        assert (
            client.post(
                f"/api/tailored/{tailored['tailored_id']}/reject-change",
                params={"target_id": "s99.e0.b0"},
            ).status_code
            == 404
        )

    def test_rejecting_on_an_orphan_explains_why_it_cannot(self, client, tailored, analysed):
        """An orphaned version has no suggestions left to un-accept."""
        client.delete(f"/api/jds/{analysed['jd_id']}")

        response = client.post(
            f"/api/tailored/{tailored['tailored_id']}/reject-change",
            params={"target_id": tailored["changes"][0]["target_id"]},
        )
        assert response.status_code == 409
        assert "deleted" in response.json()["detail"]
