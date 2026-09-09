"""Application tracking logic.

The rates here are the same kind of number as the coverage score: easy to
compute, easy to mislead yourself with. So each one ships with the definition
it was computed from, and the UI shows it, rather than presenting a bare
percentage.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import Application, ApplicationStatus

# How far through a process each status is. Used for "reached at least"
# questions; it is an ordering, not a score.
RANK: dict[ApplicationStatus, int] = {
    ApplicationStatus.SAVED: 0,
    ApplicationStatus.APPLIED: 1,
    ApplicationStatus.SCREENING: 2,
    ApplicationStatus.INTERVIEW_1: 3,
    ApplicationStatus.INTERVIEW_2: 4,
    ApplicationStatus.TAKE_HOME: 4,
    ApplicationStatus.OFFER: 5,
    # Outcomes sit outside the ladder; they are classified explicitly below.
    ApplicationStatus.REJECTED: -1,
    ApplicationStatus.GHOSTED: -1,
    ApplicationStatus.WITHDRAWN: -1,
}

# An application that has finished is not "sitting without movement".
TERMINAL = {
    ApplicationStatus.OFFER,
    ApplicationStatus.REJECTED,
    ApplicationStatus.GHOSTED,
    ApplicationStatus.WITHDRAWN,
}

# A rejection is a response. Silence is not.
RESPONDED = {
    ApplicationStatus.SCREENING,
    ApplicationStatus.INTERVIEW_1,
    ApplicationStatus.INTERVIEW_2,
    ApplicationStatus.TAKE_HOME,
    ApplicationStatus.OFFER,
    ApplicationStatus.REJECTED,
}

INTERVIEWED = {
    ApplicationStatus.INTERVIEW_1,
    ApplicationStatus.INTERVIEW_2,
    ApplicationStatus.TAKE_HOME,
    ApplicationStatus.OFFER,
}

STALE_AFTER_DAYS = 14


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat them as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def days_since_movement(application: Application, *, now: datetime | None = None) -> int:
    return ((now or _now()) - _aware(application.last_movement_at)).days


def is_stale(application: Application, *, now: datetime | None = None) -> bool:
    """Sitting more than two weeks without movement, and still live.

    A rejection that happened a year ago is not stale, it is finished.
    """
    if application.status in TERMINAL:
        return False
    return days_since_movement(application, now=now) > STALE_AFTER_DAYS


def reached(status: ApplicationStatus, threshold: ApplicationStatus) -> bool:
    """Whether a status got at least as far as `threshold` on the ladder."""
    return RANK[status] >= RANK[threshold] > 0


@dataclass
class CompanyHistory:
    company: str
    applications: int
    statuses: list[str]
    last_applied: str | None
    responded: int


@dataclass
class Stats:
    """Counts, and the definitions behind each rate."""

    total: int = 0
    sent: int = 0
    responded: int = 0
    interviewed: int = 0
    offers: int = 0
    active: int = 0
    stale: int = 0
    response_rate: float = 0.0
    interview_rate: float = 0.0
    offer_rate: float = 0.0
    by_status: dict[str, int] = field(default_factory=dict)
    per_company: list[CompanyHistory] = field(default_factory=list)
    definitions: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["per_company"] = [asdict(c) for c in self.per_company]
        return data


DEFINITIONS = {
    "sent": "Applications whose status is anything other than 'saved'.",
    "responded": (
        "Reached screening or beyond, including a rejection. A rejection is a "
        "response; being ghosted is not."
    ),
    "interviewed": "Reached a first interview, a take-home, or beyond.",
    "response_rate": "responded ÷ sent.",
    "interview_rate": "interviewed ÷ sent.",
    "stale": f"Still live and untouched for more than {STALE_AFTER_DAYS} days.",
}


def compute_stats(applications: Iterable[Application], *, now: datetime | None = None) -> Stats:
    rows = list(applications)
    now = now or _now()

    sent = [a for a in rows if a.status is not ApplicationStatus.SAVED]
    responded = [a for a in sent if a.status in RESPONDED]
    interviewed = [a for a in sent if a.status in INTERVIEWED]
    offers = [a for a in sent if a.status is ApplicationStatus.OFFER]

    by_status: dict[str, int] = {}
    for application in rows:
        by_status[application.status.value] = by_status.get(application.status.value, 0) + 1

    companies: dict[str, list[Application]] = {}
    for application in rows:
        companies.setdefault(application.company.strip(), []).append(application)

    per_company = [
        CompanyHistory(
            company=name,
            applications=len(group),
            statuses=[a.status.value for a in group],
            last_applied=max(
                (a.applied_on.isoformat() for a in group if a.applied_on), default=None
            ),
            responded=sum(1 for a in group if a.status in RESPONDED),
        )
        for name, group in sorted(companies.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    def rate(numerator: list[Application]) -> float:
        return round(100 * len(numerator) / len(sent), 1) if sent else 0.0

    return Stats(
        total=len(rows),
        sent=len(sent),
        responded=len(responded),
        interviewed=len(interviewed),
        offers=len(offers),
        active=sum(1 for a in rows if a.status not in TERMINAL),
        stale=sum(1 for a in rows if is_stale(a, now=now)),
        response_rate=rate(responded),
        interview_rate=rate(interviewed),
        offer_rate=rate(offers),
        by_status=by_status,
        per_company=per_company,
        definitions=DEFINITIONS,
    )


CSV_COLUMNS = [
    "company",
    "role",
    "status",
    "applied_on",
    "days_since_movement",
    "stale",
    "source",
    "salary_range",
    "contact_name",
    "next_action",
    "next_action_date",
    "notes",
]


def to_csv(applications: Iterable[Application], *, now: datetime | None = None) -> str:
    """Export for a spreadsheet.

    Computed columns are included because they are the reason to open the
    export at all -- days since movement and the stale flag do not exist in the
    database, they are derived at read time.
    """
    now = now or _now()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()

    for application in applications:
        writer.writerow(
            {
                "company": application.company,
                "role": application.role,
                "status": application.status.value,
                "applied_on": application.applied_on.isoformat() if application.applied_on else "",
                "days_since_movement": days_since_movement(application, now=now),
                "stale": "yes" if is_stale(application, now=now) else "",
                "source": application.source or "",
                "salary_range": application.salary_range or "",
                "contact_name": application.contact_name or "",
                "next_action": application.next_action or "",
                "next_action_date": (
                    application.next_action_date.isoformat()
                    if application.next_action_date
                    else ""
                ),
                "notes": (application.notes or "").replace("\n", " "),
            }
        )
    return buffer.getvalue()
