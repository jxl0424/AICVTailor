"""Application tracker endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, col, select

from ..applications import (
    compute_stats,
    days_since_movement,
    is_stale,
    to_csv,
)
from ..applications import _now as now_utc
from ..db import get_engine
from ..models import Application, ApplicationStatus, TailoredResume

router = APIRouter(prefix="/api", tags=["applications"])


class ApplicationCreate(BaseModel):
    company: str
    role: str
    jd_id: int | None = None
    tailored_resume_id: int | None = None
    status: ApplicationStatus = ApplicationStatus.SAVED
    applied_on: date | None = None
    source: str | None = None
    salary_range: str | None = None
    contact_name: str | None = None
    next_action: str | None = None
    next_action_date: date | None = None
    notes: str | None = None


class ApplicationUpdate(BaseModel):
    """Every field optional: the table is edited inline, one cell at a time."""

    company: str | None = None
    role: str | None = None
    status: ApplicationStatus | None = None
    applied_on: date | None = None
    source: str | None = None
    salary_range: str | None = None
    contact_name: str | None = None
    next_action: str | None = None
    next_action_date: date | None = None
    notes: str | None = None


class FromTailoredRequest(BaseModel):
    tailored_id: int
    status: ApplicationStatus = ApplicationStatus.APPLIED


def _row(application: Application) -> dict[str, Any]:
    return {
        "id": application.id,
        "company": application.company,
        "role": application.role,
        "jd_id": application.jd_id,
        "tailored_resume_id": application.tailored_resume_id,
        "status": application.status.value,
        "applied_on": application.applied_on,
        "source": application.source,
        "salary_range": application.salary_range,
        "contact_name": application.contact_name,
        "next_action": application.next_action,
        "next_action_date": application.next_action_date,
        "notes": application.notes,
        "created_at": application.created_at,
        "last_movement_at": application.last_movement_at,
        # Derived at read time; deliberately not stored.
        "days_since_movement": days_since_movement(application),
        "stale": is_stale(application),
    }


@router.get("/applications")
def list_applications(
    status: ApplicationStatus | None = None,
    company: str | None = None,
    stale_only: bool = False,
) -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        statement = select(Application)
        if status is not None:
            statement = statement.where(Application.status == status)
        if company:
            statement = statement.where(col(Application.company).ilike(f"%{company}%"))

        rows = session.exec(statement.order_by(Application.last_movement_at.desc())).all()
        result = [_row(a) for a in rows]
        return [r for r in result if r["stale"]] if stale_only else result


@router.post("/applications", status_code=201)
def create_application(request: ApplicationCreate) -> dict[str, Any]:
    with Session(get_engine()) as session:
        application = Application(**request.model_dump())
        session.add(application)
        session.commit()
        session.refresh(application)
        return _row(application)


@router.post("/applications/from-tailored", status_code=201)
def create_from_tailored(request: FromTailoredRequest) -> dict[str, Any]:
    """Start tracking straight from a tailored resume.

    The company and role come from the version's own snapshots, so this still
    works for an orphan whose job description was deleted.
    """
    with Session(get_engine()) as session:
        tailored = session.get(TailoredResume, request.tailored_id)
        if tailored is None:
            raise HTTPException(404, f"tailored resume {request.tailored_id} not found")

        application = Application(
            company=tailored.company_snapshot or "(unknown)",
            role=tailored.role_snapshot or "(unknown)",
            jd_id=tailored.jd_id,
            tailored_resume_id=tailored.id,
            status=request.status,
            applied_on=date.today() if request.status is not ApplicationStatus.SAVED else None,
        )
        session.add(application)
        session.commit()
        session.refresh(application)
        return _row(application)


@router.patch("/applications/{application_id}")
def update_application(application_id: int, request: ApplicationUpdate) -> dict[str, Any]:
    """Inline edit.

    A status change is movement, so it resets the staleness clock. Editing a
    note is not -- otherwise tidying up your notes would hide an application
    that has actually gone quiet.
    """
    with Session(get_engine()) as session:
        application = session.get(Application, application_id)
        if application is None:
            raise HTTPException(404, f"application {application_id} not found")

        changes = request.model_dump(exclude_unset=True)
        status_changed = (
            "status" in changes and changes["status"] is not application.status
        )

        for key, value in changes.items():
            setattr(application, key, value)

        if status_changed:
            application.last_movement_at = now_utc()
            # Moving out of 'saved' for the first time is the day you applied.
            if (
                application.status is not ApplicationStatus.SAVED
                and application.applied_on is None
            ):
                application.applied_on = date.today()

        session.add(application)
        session.commit()
        session.refresh(application)
        return _row(application)


@router.delete("/applications/{application_id}")
def delete_application(application_id: int) -> dict[str, Any]:
    with Session(get_engine()) as session:
        application = session.get(Application, application_id)
        if application is None:
            raise HTTPException(404, f"application {application_id} not found")
        session.delete(application)
        session.commit()
        return {"deleted": application_id}


@router.get("/applications/stats")
def application_stats() -> dict[str, Any]:
    with Session(get_engine()) as session:
        return compute_stats(session.exec(select(Application)).all()).as_dict()


@router.get("/applications/export.csv")
def export_csv() -> Response:
    with Session(get_engine()) as session:
        rows = session.exec(select(Application).order_by(Application.created_at)).all()
        return Response(
            content=to_csv(rows),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="applications.csv"'
            },
        )
