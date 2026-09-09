"""Tailoring endpoints: run, inspect the diff, download .tex and .pdf."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, col, select

from .. import paths
from ..db import get_engine
from ..latex import parse as parse_tex
from ..llm.runlog import RunLog
from ..models import (
    JobDescription,
    MasterResume,
    Suggestion as SuggestionRow,
    SuggestionAction,
    SuggestionStatus,
    TailoredResume,
)
from ..tailor import detect_engine, tailor

router = APIRouter(prefix="/api", tags=["tailoring"])


class TailorRequest(BaseModel):
    jd_id: int
    master_id: int | None = None
    notes: str | None = None


@router.post("/tailor")
def run_tailoring(request: TailorRequest) -> dict[str, Any]:
    """Apply every accepted suggestion for a JD and produce a tailored resume."""
    with Session(get_engine()) as session:
        jd = session.get(JobDescription, request.jd_id)
        if jd is None:
            raise HTTPException(404, f"job description {request.jd_id} not found")

        master = (
            session.get(MasterResume, request.master_id)
            if request.master_id is not None
            else session.exec(
                select(MasterResume).order_by(MasterResume.is_active.desc())
            ).first()
        )
        if master is None or not master.tex_source:
            raise HTTPException(409, "no tailorable master resume available")
        if not master.tailorable:
            raise HTTPException(
                409,
                f"'{master.filename}' is analysis-only: tailoring needs a .tex master.",
            )

        accepted = session.exec(
            select(SuggestionRow).where(
                SuggestionRow.jd_id == request.jd_id, SuggestionRow.accepted == True  # noqa: E712
            )
        ).all()
        rows = [
            {
                "target_id": row.target_id,
                "proposed_text": row.proposed_text,
                "term": row.term,
                "source_bullet_id": row.source_bullet_id,
            }
            for row in accepted
        ]
        source = master.tex_source
        stored_terms = jd.extracted_terms or []
        master_id, company, role = master.id, jd.company, jd.role

    document = parse_tex(source)
    result = tailor(document, rows, stored_terms=stored_terms, runlog=RunLog())

    pdf_path = None
    if result.pdf_bytes:
        paths.ensure_dirs()
        filename = f"tailored_{request.jd_id}_{result.run_id}.pdf"
        target = paths.OUTPUT_DIR / filename
        target.write_bytes(result.pdf_bytes)
        pdf_path = str(target)

    with Session(get_engine()) as session:
        record = TailoredResume(
            jd_id=request.jd_id,
            master_id=master_id,
            company_snapshot=company,
            role_snapshot=role,
            tex_output=result.tex,
            pdf_path=pdf_path,
            diff={"changes": [c.as_dict() for c in result.changes]},
            coverage_before=result.coverage_before,
            coverage_after=result.coverage_after,
            provider=None,
            model=None,
            compiled=result.compiled,
            compile_error=result.compile_error or None,
            notes=request.notes,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        tailored_id = record.id

    payload = result.as_dict()
    payload.update({"tailored_id": tailored_id, "applied": len(result.changes)})
    return payload


@router.get("/tailored")
def list_tailored(
    company: str | None = None,
    role: str | None = None,
    since: date | None = None,
    until: date | None = None,
    compiled_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """The library. Filters are applied server-side so the list stays usable
    once there are hundreds of versions."""
    with Session(get_engine()) as session:
        statement = select(TailoredResume)
        if company:
            statement = statement.where(
                col(TailoredResume.company_snapshot).ilike(f"%{company}%")
            )
        if role:
            statement = statement.where(col(TailoredResume.role_snapshot).ilike(f"%{role}%"))
        if since:
            statement = statement.where(
                TailoredResume.created_at >= datetime.combine(since, time.min)
            )
        if until:
            statement = statement.where(
                TailoredResume.created_at <= datetime.combine(until, time.max)
            )
        if compiled_only:
            statement = statement.where(TailoredResume.compiled == True)  # noqa: E712

        rows = session.exec(
            statement.order_by(TailoredResume.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": r.id,
                "jd_id": r.jd_id,
                "company": r.company_snapshot,
                "role": r.role_snapshot,
                "created_at": r.created_at,
                "compiled": r.compiled,
                "coverage_before": r.coverage_before,
                "coverage_after": r.coverage_after,
                "changes": len((r.diff or {}).get("changes", [])),
                "has_pdf": bool(r.pdf_path),
                "coverage_delta": (
                    round((r.coverage_after or 0) - (r.coverage_before or 0), 1)
                    if r.coverage_after is not None and r.coverage_before is not None
                    else None
                ),
                # An orphan is a tailored resume whose JD was deleted. History
                # is kept deliberately rather than cascading away.
                "orphaned": r.jd_id is None,
            }
            for r in rows
        ]


@router.get("/tailored/{tailored_id}")
def get_tailored(tailored_id: int) -> dict[str, Any]:
    with Session(get_engine()) as session:
        record = session.get(TailoredResume, tailored_id)
        if record is None:
            raise HTTPException(404, f"tailored resume {tailored_id} not found")
        return {
            "id": record.id,
            "jd_id": record.jd_id,
            "company": record.company_snapshot,
            "role": record.role_snapshot,
            "compiled": record.compiled,
            "compile_error": record.compile_error,
            "coverage_before": record.coverage_before,
            "coverage_after": record.coverage_after,
            "diff": record.diff or {"changes": []},
            "has_pdf": bool(record.pdf_path),
            "created_at": record.created_at,
            "notes": record.notes,
        }


@router.get("/tailored/{tailored_id}/download.tex")
def download_tex(tailored_id: int) -> Response:
    with Session(get_engine()) as session:
        record = session.get(TailoredResume, tailored_id)
        if record is None or not record.tex_output:
            raise HTTPException(404, "no .tex for that tailored resume")
        name = f"{(record.company_snapshot or 'resume').replace(' ', '_')}_{tailored_id}.tex"
        return Response(
            content=record.tex_output,
            media_type="application/x-tex",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )


@router.get("/tailored/{tailored_id}/download.pdf")
def download_pdf(tailored_id: int) -> Response:
    with Session(get_engine()) as session:
        record = session.get(TailoredResume, tailored_id)
        if record is None:
            raise HTTPException(404, f"tailored resume {tailored_id} not found")
        if not record.pdf_path or not paths.Path(record.pdf_path).exists():
            engine = detect_engine()
            raise HTTPException(
                409,
                "No PDF was produced for this version."
                + (
                    " No LaTeX engine is installed; download the .tex instead."
                    if engine is None
                    else f" The compile failed: {record.compile_error}"
                ),
            )
        name = f"{(record.company_snapshot or 'resume').replace(' ', '_')}_{tailored_id}.pdf"
        return Response(
            content=paths.Path(record.pdf_path).read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )


class DuplicateRequest(BaseModel):
    jd_id: int


@router.post("/tailored/{tailored_id}/duplicate")
def duplicate(tailored_id: int, request: DuplicateRequest) -> dict[str, Any]:
    """Seed a new job description with the edits from a past tailored version.

    Applying to a similar role should not mean starting from nothing. The
    accepted edits from the earlier version are copied onto the new JD as
    pre-accepted suggestions -- but only where their target span still exists
    in the current master resume, since the master may have been edited since.
    Anything that no longer resolves is reported rather than silently dropped.
    """
    with Session(get_engine()) as session:
        origin = session.get(TailoredResume, tailored_id)
        if origin is None:
            raise HTTPException(404, f"tailored resume {tailored_id} not found")
        target_jd = session.get(JobDescription, request.jd_id)
        if target_jd is None:
            raise HTTPException(404, f"job description {request.jd_id} not found")

        master = session.exec(
            select(MasterResume).order_by(MasterResume.is_active.desc())
        ).first()
        if master is None or not master.tex_source:
            raise HTTPException(409, "no tailorable master resume available")

        editable = set(parse_tex(master.tex_source).editable_spans())
        changes = (origin.diff or {}).get("changes", [])

        copied, stale = [], []
        for change in changes:
            target_id = change.get("target_id")
            if target_id not in editable:
                stale.append(target_id)
                continue
            terms = [t for t in change.get("target_terms", []) if t]
            session.add(
                SuggestionRow(
                    jd_id=request.jd_id,
                    term=terms[0] if terms else "(carried over)",
                    category="carried",
                    weight=0.0,
                    status=SuggestionStatus.IMPLIED,
                    action=SuggestionAction.REWORD,
                    proposed_text=change.get("after"),
                    source_bullet_id=change.get("source_bullet_id"),
                    target_id=target_id,
                    rationale=(
                        f"Carried over from the version made for "
                        f"{origin.company_snapshot or 'an earlier role'}."
                    ),
                    accepted=True,
                )
            )
            copied.append(target_id)
        session.commit()

    return {
        "from_tailored_id": tailored_id,
        "jd_id": request.jd_id,
        "copied": copied,
        "stale": stale,
        "note": (
            f"{len(stale)} edit(s) could not be carried over because their bullet no "
            "longer exists in the master resume."
            if stale
            else ""
        ),
    }


@router.post("/tailored/{tailored_id}/reject-change")
def reject_change(tailored_id: int, target_id: str) -> dict[str, Any]:
    """Un-accept the suggestion behind one change, so the next run drops it.

    The brief asks to be able to reject a single change and regenerate. The
    edit is not undone in place -- the accepted set is changed and the run is
    repeated, so the compile gate and verification apply to the new result too.
    """
    with Session(get_engine()) as session:
        origin = session.get(TailoredResume, tailored_id)
        if origin is None:
            raise HTTPException(404, f"tailored resume {tailored_id} not found")
        if origin.jd_id is None:
            raise HTTPException(
                409,
                "This version's job description was deleted, so there are no "
                "suggestions left to reject. Re-analyse the posting to start again.",
            )

        row = session.exec(
            select(SuggestionRow).where(
                SuggestionRow.jd_id == origin.jd_id,
                SuggestionRow.target_id == target_id,
            )
        ).first()
        if row is None:
            raise HTTPException(404, f"no suggestion targeting {target_id}")

        row.accepted = False
        session.add(row)
        session.commit()
        jd_id = origin.jd_id

    return run_tailoring(TailorRequest(jd_id=jd_id))
