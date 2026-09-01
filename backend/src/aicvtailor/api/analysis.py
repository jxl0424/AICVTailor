"""Analysis endpoints: ingest a JD, analyse it against a master resume."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import paths
from ..analysis import IngestError, analyse, from_text, from_url
from ..db import get_engine
from ..latex import parse as parse_tex
from ..llm.registry import get_provider
from ..llm.runlog import RunLog
from ..models import JobDescription, MasterResume

router = APIRouter(prefix="/api", tags=["analysis"])


class AnalyseRequest(BaseModel):
    text: str | None = None
    url: str | None = None
    master_id: int | None = None
    use_model_for_fields: bool = Field(
        default=True,
        description="Allow one extractor call for JD fields regex could not find",
    )
    persist: bool = True


def _load_master(session: Session, master_id: int | None) -> MasterResume:
    """The requested master, else the active one, else the newest."""
    if master_id is not None:
        master = session.get(MasterResume, master_id)
        if master is None:
            raise HTTPException(404, f"master resume {master_id} not found")
        return master

    statement = select(MasterResume).order_by(
        MasterResume.is_active.desc(), MasterResume.created_at.desc()
    )
    master = session.exec(statement).first()
    if master is None:
        raise HTTPException(
            409,
            "No master resume has been imported. Put your master.tex in "
            "data/master/ and POST /api/masters/import.",
        )
    return master


@router.post("/masters/import")
def import_masters() -> dict[str, Any]:
    """Load every .tex in data/master/ into the database.

    Non-.tex files are recorded as analysis-only: there are no spans to replace
    in a PDF or a Word file, so they can be scored against a posting but never
    tailored.
    """
    paths.ensure_dirs()
    imported: list[dict[str, Any]] = []

    with Session(get_engine()) as session:
        for path in sorted(paths.MASTER_DIR.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".tex", ".pdf", ".docx"}:
                continue

            existing = session.exec(
                select(MasterResume).where(MasterResume.filename == path.name)
            ).first()

            is_tex = path.suffix.lower() == ".tex"
            source = path.read_text(encoding="utf-8") if is_tex else None
            parsed_ir = None
            if is_tex:
                document = parse_tex(source)
                parsed_ir = {
                    "sections": len(document.sections),
                    "entries": len(list(document.entries())),
                    "bullets": [
                        {"id": b.id, "fingerprint": b.fingerprint, "text": b.text}
                        for b in document.bullets()
                    ],
                }

            record = existing or MasterResume(filename=path.name)
            record.source_format = path.suffix.lower().lstrip(".")
            record.tailorable = is_tex
            record.tex_source = source
            record.parsed_ir = parsed_ir
            record.is_active = record.is_active or (is_tex and existing is None)
            session.add(record)
            session.commit()
            session.refresh(record)

            imported.append(
                {
                    "id": record.id,
                    "filename": record.filename,
                    "format": record.source_format,
                    "tailorable": record.tailorable,
                    "bullets": len(parsed_ir["bullets"]) if parsed_ir else 0,
                }
            )

    return {"imported": imported, "count": len(imported)}


@router.get("/masters")
def list_masters() -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        records = session.exec(select(MasterResume)).all()
        return [
            {
                "id": r.id,
                "filename": r.filename,
                "format": r.source_format,
                "tailorable": r.tailorable,
                "is_active": r.is_active,
                "reason": (
                    ""
                    if r.tailorable
                    else "Analysis only: tailoring needs a .tex master, since a "
                    "PDF or Word file has no spans to edit and no preamble to preserve."
                ),
            }
            for r in records
        ]


@router.post("/analyse")
def run_analysis(request: AnalyseRequest) -> dict[str, Any]:
    """Ingest a JD and score it against a master resume. No rewriting."""
    if not request.text and not request.url:
        raise HTTPException(422, "provide either text or url")

    try:
        ingested = from_url(request.url) if request.url else from_text(request.text or "")
    except IngestError as exc:
        raise HTTPException(422, str(exc)) from exc

    with Session(get_engine()) as session:
        master = _load_master(session, request.master_id)
        if not master.tex_source:
            raise HTTPException(
                409,
                f"'{master.filename}' is analysis-only and has no LaTeX source to "
                "match against.",
            )
        document = parse_tex(master.tex_source)
        master_id = master.id

    runlog = RunLog()
    provider = None
    if request.use_model_for_fields:
        try:
            provider = get_provider(runlog=runlog)
        except Exception as exc:  # noqa: BLE001 -- analysis is deterministic anyway
            runlog.write("provider", available=False, error=str(exc))

    result = analyse(ingested.text, document, provider=provider, runlog=runlog)

    jd_id = None
    if request.persist:
        with Session(get_engine()) as session:
            record = JobDescription(
                company=result.parsed.company,
                role=result.parsed.role,
                location=result.parsed.location,
                source_url=ingested.source_url,
                raw_text=ingested.text,
                parsed=result.parsed.as_dict(),
                extracted_terms=[r.as_dict() for r in result.ranked],
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            jd_id = record.id

    payload = result.as_dict()
    payload.update(
        {
            "jd_id": jd_id,
            "master_id": master_id,
            "ingest": {"method": ingested.method, "warning": ingested.warning},
            "provider_used": bool(provider),
        }
    )
    if ingested.warning:
        payload["warnings"] = [*payload.get("warnings", []), ingested.warning]
    return payload


@router.get("/jds")
def list_jds(limit: int = 50) -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        statement = select(JobDescription).order_by(
            JobDescription.ingested_at.desc()
        ).limit(limit)
        return [
            {
                "id": r.id,
                "company": r.company,
                "role": r.role,
                "location": r.location,
                "ingested_at": r.ingested_at,
                "term_count": len(r.extracted_terms or []),
            }
            for r in session.exec(statement).all()
        ]
