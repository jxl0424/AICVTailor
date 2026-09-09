"""Application tracker API: CRUD, inline editing, stats, export."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from aicvtailor import paths
from aicvtailor.db import get_engine
from aicvtailor.main import create_app
from aicvtailor.models import Application


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path / "runs")
    import aicvtailor.db as db

    monkeypatch.setattr(db, "_engine", None)
    with TestClient(create_app()) as client:
        yield client


def create(client, **kwargs):
    body = {"company": "Acme", "role": "AI Engineer", **kwargs}
    response = client.post("/api/applications", json=body)
    assert response.status_code == 201, response.text
    return response.json()


class TestCRUD:
    def test_create_and_list(self, client):
        created = create(client)
        rows = client.get("/api/applications").json()

        assert len(rows) == 1
        assert rows[0]["id"] == created["id"]
        assert rows[0]["status"] == "saved"

    def test_new_rows_are_not_stale(self, client):
        assert create(client)["stale"] is False
        assert create(client)["days_since_movement"] == 0

    def test_inline_edit_of_a_single_field(self, client):
        created = create(client)
        updated = client.patch(
            f"/api/applications/{created['id']}", json={"next_action": "chase recruiter"}
        ).json()

        assert updated["next_action"] == "chase recruiter"
        assert updated["company"] == "Acme"  # untouched

    def test_delete(self, client):
        created = create(client)
        assert client.delete(f"/api/applications/{created['id']}").status_code == 200
        assert client.get("/api/applications").json() == []

    def test_unknown_ids_are_404s(self, client):
        assert client.patch("/api/applications/999", json={"notes": "x"}).status_code == 404
        assert client.delete("/api/applications/999").status_code == 404


class TestMovement:
    def test_a_status_change_resets_the_staleness_clock(self, client):
        created = create(client)
        with Session(get_engine()) as session:
            row = session.get(Application, created["id"])
            row.last_movement_at = datetime.now(timezone.utc) - timedelta(days=30)
            session.add(row)
            session.commit()

        assert client.get("/api/applications").json()[0]["stale"] is True

        updated = client.patch(
            f"/api/applications/{created['id']}", json={"status": "screening"}
        ).json()
        assert updated["stale"] is False
        assert updated["days_since_movement"] == 0

    def test_editing_a_note_does_not_reset_the_clock(self, client):
        """Otherwise tidying your notes would hide an application that has
        actually gone quiet."""
        created = create(client)
        with Session(get_engine()) as session:
            row = session.get(Application, created["id"])
            row.last_movement_at = datetime.now(timezone.utc) - timedelta(days=30)
            session.add(row)
            session.commit()

        updated = client.patch(
            f"/api/applications/{created['id']}", json={"notes": "tidied up"}
        ).json()
        assert updated["stale"] is True

    def test_leaving_saved_records_the_applied_date(self, client):
        created = create(client)
        assert created["applied_on"] is None

        updated = client.patch(
            f"/api/applications/{created['id']}", json={"status": "applied"}
        ).json()
        assert updated["applied_on"] == date.today().isoformat()

    def test_an_existing_applied_date_is_not_overwritten(self, client):
        created = create(client, status="applied", applied_on="2026-01-15")
        updated = client.patch(
            f"/api/applications/{created['id']}", json={"status": "screening"}
        ).json()
        assert updated["applied_on"] == "2026-01-15"


class TestFilters:
    def test_by_status(self, client):
        create(client, status="applied")
        create(client, status="rejected")
        assert len(client.get("/api/applications", params={"status": "applied"}).json()) == 1

    def test_by_company(self, client):
        create(client, company="Acme")
        create(client, company="Globex")
        assert len(client.get("/api/applications", params={"company": "glob"}).json()) == 1

    def test_stale_only(self, client):
        fresh = create(client, status="applied")
        old = create(client, status="applied")
        with Session(get_engine()) as session:
            row = session.get(Application, old["id"])
            row.last_movement_at = datetime.now(timezone.utc) - timedelta(days=30)
            session.add(row)
            session.commit()

        rows = client.get("/api/applications", params={"stale_only": True}).json()
        assert [r["id"] for r in rows] == [old["id"]]
        assert fresh["id"] not in [r["id"] for r in rows]


class TestStats:
    def test_reports_rates_with_definitions(self, client):
        create(client, status="applied")
        create(client, status="screening")
        stats = client.get("/api/applications/stats").json()

        assert stats["sent"] == 2
        assert stats["response_rate"] == 50.0
        assert stats["definitions"]["response_rate"]

    def test_per_company_history_shows_repeats(self, client):
        create(client, company="Acme", role="Engineer", status="applied")
        create(client, company="Acme", role="Senior Engineer", status="rejected")
        stats = client.get("/api/applications/stats").json()

        acme = next(c for c in stats["per_company"] if c["company"] == "Acme")
        assert acme["applications"] == 2

    def test_empty_stats_are_safe(self, client):
        stats = client.get("/api/applications/stats").json()
        assert stats["total"] == 0 and stats["response_rate"] == 0.0


class TestExport:
    def test_csv_downloads_with_a_filename(self, client):
        create(client, status="applied")
        response = client.get("/api/applications/export.csv")

        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert "applications.csv" in response.headers["content-disposition"]

    def test_csv_contains_the_rows_and_derived_columns(self, client):
        create(client, company="Globex", status="applied")
        rows = list(csv.DictReader(io.StringIO(client.get("/api/applications/export.csv").text)))

        assert rows[0]["company"] == "Globex"
        assert rows[0]["days_since_movement"] == "0"


class TestFromTailored:
    def test_starts_tracking_from_a_tailored_version(self, client, tmp_path):
        from aicvtailor.models import TailoredResume

        with Session(get_engine()) as session:
            tailored = TailoredResume(
                company_snapshot="Northwind", role_snapshot="AI Engineer", tex_output="x"
            )
            session.add(tailored)
            session.commit()
            session.refresh(tailored)
            tailored_id = tailored.id

        created = client.post(
            "/api/applications/from-tailored", json={"tailored_id": tailored_id}
        ).json()

        assert created["company"] == "Northwind"
        assert created["tailored_resume_id"] == tailored_id
        assert created["applied_on"] == date.today().isoformat()

    def test_works_for_an_orphan(self, client):
        """A version whose JD was deleted keeps its snapshots, so tracking it
        still works."""
        from aicvtailor.models import TailoredResume

        with Session(get_engine()) as session:
            tailored = TailoredResume(
                jd_id=None, company_snapshot="Harbour", role_snapshot="ML Engineer"
            )
            session.add(tailored)
            session.commit()
            session.refresh(tailored)
            tailored_id = tailored.id

        created = client.post(
            "/api/applications/from-tailored", json={"tailored_id": tailored_id}
        ).json()
        assert created["company"] == "Harbour"
        assert created["jd_id"] is None

    def test_unknown_tailored_id_is_a_404(self, client):
        assert (
            client.post("/api/applications/from-tailored", json={"tailored_id": 999}).status_code
            == 404
        )
