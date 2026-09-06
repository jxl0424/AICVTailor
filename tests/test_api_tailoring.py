"""Tailoring API: run, persist, download, and the orphan rule."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from aicvtailor import paths
from aicvtailor.db import get_engine
from aicvtailor.main import create_app
from aicvtailor.models import Suggestion, SuggestionAction, SuggestionStatus
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
def prepared(client):
    """A JD with one accepted, applicable suggestion."""
    client.post("/api/masters/import")
    response = client.post(
        "/api/analyse",
        json={"text": JD.read_text(encoding="utf-8"), "use_model_for_fields": False},
    )
    if response.status_code == 409:
        pytest.skip("no master resume available in this checkout")
    jd = response.json()

    from aicvtailor.latex import parse

    masters = client.get("/api/masters").json()
    document = parse(Path(paths.MASTER_DIR / masters[0]["filename"]).read_text(encoding="utf-8"))
    bullet = next(iter(document.bullets()))

    with Session(get_engine()) as session:
        session.add(
            Suggestion(
                jd_id=jd["jd_id"],
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
    return jd


class TestRun:
    @needs_latex
    def test_applies_accepted_suggestions_and_persists(self, client, prepared):
        run = client.post("/api/tailor", json={"jd_id": prepared["jd_id"]}).json()

        assert run["applied"] == 1
        assert run["compiled"]
        assert run["tailored_id"]

        listed = client.get("/api/tailored").json()
        assert listed[0]["id"] == run["tailored_id"]
        assert listed[0]["changes"] == 1

    @needs_latex
    def test_every_change_records_its_source_bullet(self, client, prepared):
        run = client.post("/api/tailor", json={"jd_id": prepared["jd_id"]}).json()
        for change in run["changes"]:
            assert change["source_bullet_id"]
            assert change["target_terms"]

    @needs_latex
    def test_the_run_verifies_terms_reached_the_pdf(self, client, prepared):
        run = client.post("/api/tailor", json={"jd_id": prepared["jd_id"]}).json()
        assert "retrieval" in run["verification"]["surviving_terms"]

    def test_an_unknown_jd_is_a_404(self, client):
        assert client.post("/api/tailor", json={"jd_id": 9999}).status_code == 404

    @needs_latex
    def test_running_with_nothing_accepted_produces_the_original(self, client):
        client.post("/api/masters/import")
        response = client.post(
            "/api/analyse",
            json={"text": JD.read_text(encoding="utf-8"), "use_model_for_fields": False},
        )
        if response.status_code == 409:
            pytest.skip("no master resume available")

        run = client.post("/api/tailor", json={"jd_id": response.json()["jd_id"]}).json()
        assert run["applied"] == 0
        assert run["compiled"]


class TestDownloads:
    @needs_latex
    def test_tex_downloads_with_a_filename(self, client, prepared):
        run = client.post("/api/tailor", json={"jd_id": prepared["jd_id"]}).json()
        response = client.get(f"/api/tailored/{run['tailored_id']}/download.tex")

        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert response.text.startswith("\\documentclass")

    @needs_latex
    def test_pdf_downloads_when_one_was_produced(self, client, prepared):
        run = client.post("/api/tailor", json={"jd_id": prepared["jd_id"]}).json()
        response = client.get(f"/api/tailored/{run['tailored_id']}/download.pdf")

        assert response.status_code == 200
        assert response.content[:4] == b"%PDF"

    @needs_latex
    def test_the_downloaded_tex_is_the_tailored_one(self, client, prepared):
        run = client.post("/api/tailor", json={"jd_id": prepared["jd_id"]}).json()
        text = client.get(f"/api/tailored/{run['tailored_id']}/download.tex").text
        assert "using retrieval" in text

    def test_downloading_an_unknown_version_is_a_404(self, client):
        assert client.get("/api/tailored/9999/download.tex").status_code == 404


class TestOrphanRule:
    @needs_latex
    def test_deleting_a_jd_keeps_its_tailored_resumes(self, client, prepared):
        """Losing application history is worse than a dangling reference."""
        run = client.post("/api/tailor", json={"jd_id": prepared["jd_id"]}).json()

        deletion = client.delete(f"/api/jds/{prepared['jd_id']}").json()
        assert deletion["orphaned_resumes"] == 1
        assert deletion["discarded_suggestions"] >= 1

        listed = client.get("/api/tailored").json()
        row = next(r for r in listed if r["id"] == run["tailored_id"])
        assert row["orphaned"]
        # The snapshot keeps an orphan meaningful.
        assert row["company"] or row["role"]

        assert client.get(f"/api/tailored/{run['tailored_id']}/download.tex").status_code == 200


    @needs_latex
    def test_the_jd_is_actually_gone(self, client, prepared):
        client.post("/api/tailor", json={"jd_id": prepared["jd_id"]})
        client.delete(f"/api/jds/{prepared['jd_id']}")

        assert client.post("/api/tailor", json={"jd_id": prepared["jd_id"]}).status_code == 404
        assert prepared["jd_id"] not in [r["id"] for r in client.get("/api/jds").json()]

    def test_deleting_an_unknown_jd_is_a_404(self, client):
        assert client.delete("/api/jds/9999").status_code == 404
