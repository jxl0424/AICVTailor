"""Suggestion endpoints: generate, list, accept/reject."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from ..analysis.match import Match, MatchStatus, ResumeLocation
from ..analysis.pipeline import RankedTerm
from ..analysis.sections import SectionKind
from ..analysis.terms import Mention, Term
from ..analysis.weight import score
from ..db import get_engine
from ..latex import parse as parse_tex
from ..llm.registry import get_provider
from ..llm.runlog import RunLog
from ..models import JobDescription, MasterResume, Suggestion as SuggestionRow
from ..suggest import GapSuggestion, RewordSuggestion, as_dict, generate

router = APIRouter(prefix="/api", tags=["suggestions"])


class SuggestRequest(BaseModel):
    jd_id: int
    master_id: int | None = None
    limit: int = 12


class DecisionRequest(BaseModel):
    accepted: bool


def _rebuild_ranked(stored: list[dict[str, Any]]) -> list[RankedTerm]:
    """Rebuild ranked terms from the analysis stored on the JD.

    Re-running extraction would be wasteful and could drift from what the user
    was shown, so the persisted analysis is the source of truth.
    """
    rebuilt: list[RankedTerm] = []
    for row in stored:
        term = Term(
            canonical=row["term"],
            category=row.get("category", "unknown"),
            in_dictionary=row.get("in_dictionary", True),
            mentions=[
                Mention(
                    surface=row["term"],
                    section=SectionKind(section),
                    required=None,
                    start=0,
                )
                for section in row.get("sections", ["other"])
            ],
        )
        match = Match(
            term=row["term"],
            status=MatchStatus(row["status"]),
            location=ResumeLocation(row["location"]) if row.get("location") else None,
            evidence=row.get("evidence", ""),
            bullet_id=row.get("bullet_id"),
            score=row.get("match_score", 0.0),
        )
        # Trust the stored weight rather than recomputing it from a lossy
        # reconstruction of the mentions.
        breakdown = score(term)
        object.__setattr__(breakdown, "weight", row["weight"])
        rebuilt.append(RankedTerm(term=term, weight=breakdown, match=match))
    return rebuilt


@router.post("/suggest")
def create_suggestions(request: SuggestRequest) -> dict[str, Any]:
    """Generate suggestions for a previously analysed job description."""
    with Session(get_engine()) as session:
        jd = session.get(JobDescription, request.jd_id)
        if jd is None:
            raise HTTPException(404, f"job description {request.jd_id} not found")
        if not jd.extracted_terms:
            raise HTTPException(409, "that job description has no stored analysis")

        master = (
            session.get(MasterResume, request.master_id)
            if request.master_id is not None
            else session.exec(
                select(MasterResume).order_by(MasterResume.is_active.desc())
            ).first()
        )
        if master is None or not master.tex_source:
            raise HTTPException(409, "no tailorable master resume available")

        document = parse_tex(master.tex_source)
        ranked = _rebuild_ranked(jd.extracted_terms)

    runlog = RunLog()
    provider = None
    provider_error = ""
    try:
        provider = get_provider(runlog=runlog)
    except Exception as exc:  # noqa: BLE001 -- gaps are still worth producing
        provider_error = str(exc)
        runlog.write("provider", available=False, error=provider_error)

    suggestions = generate(
        ranked, document, provider=provider, runlog=runlog, limit=request.limit
    )

    with Session(get_engine()) as session:
        session.exec(delete(SuggestionRow).where(SuggestionRow.jd_id == request.jd_id))
        rows: list[SuggestionRow] = []
        for suggestion in suggestions:
            payload = as_dict(suggestion)
            row = SuggestionRow(
                jd_id=request.jd_id,
                term=payload["term"],
                category=payload["category"],
                weight=payload["weight"],
                status=payload["status"],
                action=payload["action"],
                proposed_text=payload["proposed_text"] or None,
                source_bullet_id=payload["source_bullet_id"],
                target_id=payload["target_id"],
                rationale=payload["rationale"],
                guardrail_violations=(
                    payload.get("guardrails", {}).get("violations") or None
                ),
                accepted=False,
            )
            session.add(row)
            rows.append(row)
        session.commit()
        for row in rows:
            session.refresh(row)

        return {
            "run_id": runlog.run_id,
            "provider_available": provider is not None,
            "provider_error": provider_error,
            "suggestions": [_row_dict(r, s) for r, s in zip(rows, suggestions)],
        }


def _row_dict(row: SuggestionRow, suggestion=None) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "term": row.term,
        "category": row.category,
        "weight": row.weight,
        "status": row.status.value if hasattr(row.status, "value") else row.status,
        "action": row.action.value if hasattr(row.action, "value") else row.action,
        "proposed_text": row.proposed_text,
        "source_bullet_id": row.source_bullet_id,
        "target_id": row.target_id,
        "rationale": row.rationale,
        "guardrail_violations": row.guardrail_violations,
        "accepted": row.accepted,
        "applicable": row.target_id is not None,
    }
    if isinstance(suggestion, GapSuggestion):
        payload["what_it_would_take"] = suggestion.what_it_would_take
    if isinstance(suggestion, RewordSuggestion):
        payload["original_text"] = suggestion.original_text
    return payload


@router.get("/jds/{jd_id}/suggestions")
def list_suggestions(jd_id: int) -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        rows = session.exec(
            select(SuggestionRow)
            .where(SuggestionRow.jd_id == jd_id)
            .order_by(SuggestionRow.weight.desc())
        ).all()
        return [_row_dict(r) for r in rows]


@router.patch("/suggestions/{suggestion_id}")
def decide(suggestion_id: int, request: DecisionRequest) -> dict[str, Any]:
    """Accept or reject one suggestion.

    A GAP has no target span, so accepting one would mean nothing. The API
    refuses rather than storing a decision that cannot be acted on.
    """
    with Session(get_engine()) as session:
        row = session.get(SuggestionRow, suggestion_id)
        if row is None:
            raise HTTPException(404, f"suggestion {suggestion_id} not found")

        if request.accepted and row.target_id is None:
            raise HTTPException(
                409,
                f"'{row.term}' is a GAP: nothing in your resume supports it, so there "
                "is nothing to apply. Accepting it would mean claiming experience you "
                "do not have.",
            )

        row.accepted = request.accepted
        session.add(row)
        session.commit()
        session.refresh(row)
        return _row_dict(row)
