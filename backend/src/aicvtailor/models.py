"""Database schema.

Cascade rule that matters: deleting a JobDescription does NOT delete the
TailoredResume rows that reference it. They become orphans with jd_id set to
NULL, because losing application history is worse than keeping a dangling row.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SuggestionStatus(str, Enum):
    PRESENT = "present"
    IMPLIED = "implied"
    MISSING = "missing"


class SuggestionAction(str, Enum):
    REWORD = "REWORD"
    RELOCATE = "RELOCATE"
    GAP = "GAP"


class ApplicationStatus(str, Enum):
    SAVED = "saved"
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW_1 = "interview_1"
    INTERVIEW_2 = "interview_2"
    TAKE_HOME = "take_home"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"
    WITHDRAWN = "withdrawn"


class MasterResume(SQLModel, table=True):
    __tablename__ = "master_resume"

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    source_format: str = Field(default="tex")  # tex | pdf | docx
    # Non-.tex masters are analysis-only: coverage and suggestions work, but
    # tailored output is disabled because there are no spans to replace.
    tailorable: bool = Field(default=True)
    tex_source: Optional[str] = Field(default=None, sa_column=Column(Text))
    plain_text: Optional[str] = Field(default=None, sa_column=Column(Text))
    parsed_ir: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    is_active: bool = Field(default=False, index=True)


class JobDescription(SQLModel, table=True):
    __tablename__ = "job_description"

    id: Optional[int] = Field(default=None, primary_key=True)
    company: Optional[str] = Field(default=None, index=True)
    role: Optional[str] = Field(default=None, index=True)
    location: Optional[str] = None
    source_url: Optional[str] = None
    raw_text: str = Field(sa_column=Column(Text))
    parsed: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    extracted_terms: Optional[list[Any]] = Field(default=None, sa_column=Column(JSON))
    ingested_at: datetime = Field(default_factory=utcnow)


class TailoredResume(SQLModel, table=True):
    __tablename__ = "tailored_resume"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Nullable so a deleted JD orphans rather than destroys this row.
    jd_id: Optional[int] = Field(default=None, foreign_key="job_description.id", index=True)
    master_id: Optional[int] = Field(default=None, foreign_key="master_resume.id")
    # Denormalised so an orphaned row still says what it was made for.
    company_snapshot: Optional[str] = None
    role_snapshot: Optional[str] = None
    tex_output: Optional[str] = Field(default=None, sa_column=Column(Text))
    pdf_path: Optional[str] = None
    diff: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    coverage_before: Optional[float] = None
    coverage_after: Optional[float] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    compiled: bool = Field(default=False)
    compile_error: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow)
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))


class Suggestion(SQLModel, table=True):
    __tablename__ = "suggestion"

    id: Optional[int] = Field(default=None, primary_key=True)
    jd_id: Optional[int] = Field(default=None, foreign_key="job_description.id", index=True)
    term: str
    category: Optional[str] = None
    weight: float = 0.0
    status: SuggestionStatus = SuggestionStatus.MISSING
    action: SuggestionAction = SuggestionAction.GAP
    proposed_text: Optional[str] = Field(default=None, sa_column=Column(Text))
    # REWORD must always carry one. Enforced at the service layer, not just here.
    source_bullet_id: Optional[str] = None
    # The editable span an accepted suggestion writes to. Null for GAP, which
    # has nothing to apply.
    target_id: Optional[str] = None
    # Recorded so a rejected rewrite can be inspected rather than just lost.
    guardrail_violations: Optional[list[Any]] = Field(default=None, sa_column=Column(JSON))
    rationale: Optional[str] = Field(default=None, sa_column=Column(Text))
    accepted: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)


class Application(SQLModel, table=True):
    __tablename__ = "application"

    id: Optional[int] = Field(default=None, primary_key=True)
    company: str = Field(index=True)
    role: str
    jd_id: Optional[int] = Field(default=None, foreign_key="job_description.id")
    tailored_resume_id: Optional[int] = Field(
        default=None, foreign_key="tailored_resume.id"
    )
    status: ApplicationStatus = Field(default=ApplicationStatus.SAVED, index=True)
    applied_on: Optional[date] = None
    source: Optional[str] = None  # job board | referral | direct
    salary_range: Optional[str] = None
    contact_name: Optional[str] = None
    next_action: Optional[str] = None
    next_action_date: Optional[date] = None
    # Bumped on every status change so the >14d stale highlight has a basis.
    last_movement_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))
