"""Tailoring endpoints: run, inspect the diff, download .tex and .pdf."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import paths
from ..db import get_engine
from ..latex import parse as parse_tex
from ..llm.runlog import RunLog
from ..models import JobDescription, MasterResume, Suggestion as SuggestionRow, TailoredResume
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
def list_tailored(limit: int = 50) -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        rows = session.exec(
            select(TailoredResume).order_by(TailoredResume.created_at.desc()).limit(limit)
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
