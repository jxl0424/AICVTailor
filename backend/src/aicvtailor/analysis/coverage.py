"""Keyword coverage.

Explicitly NOT an ATS score. Real applicant tracking systems are proprietary
and vary by vendor, so a single confident-looking number would be a fiction.
This measures one auditable thing: how much of the weight this JD puts on its
terms is accounted for by text that is actually in the resume.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .match import Match, MatchStatus

# What each status contributes. `implied` is half credit: a bullet that gestures
# at a skill is worth something to a human reader and nothing to a keyword
# filter, so it should neither be ignored nor counted as a hit.
CREDIT: dict[MatchStatus, float] = {
    MatchStatus.PRESENT_EXACT: 1.0,
    MatchStatus.PRESENT_AS_SYNONYM: 1.0,
    MatchStatus.IMPLIED: 0.5,
    MatchStatus.MISSING: 0.0,
}

DISCLAIMER = (
    "JD keyword coverage is a proxy, not an ATS score. It measures how much of "
    "this posting's weighted terminology appears in your resume. Real applicant "
    "tracking systems are proprietary and vary by vendor."
)


@dataclass
class CategoryCoverage:
    category: str
    covered_weight: float
    total_weight: float
    term_count: int

    @property
    def percent(self) -> float:
        return 100.0 * self.covered_weight / self.total_weight if self.total_weight else 0.0


@dataclass
class Coverage:
    percent: float
    covered_weight: float
    total_weight: float
    by_category: list[CategoryCoverage] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    credit_scheme: dict[str, float] = field(default_factory=dict)
    disclaimer: str = DISCLAIMER

    def as_dict(self) -> dict:
        data = asdict(self)
        data["by_category"] = [
            {**asdict(c), "percent": round(c.percent, 1)} for c in self.by_category
        ]
        return data


def compute(
    matches: list[Match], weights: dict[str, float], categories: dict[str, str]
) -> Coverage:
    """Weighted coverage overall and per category.

    Only dictionary terms count. Discovered phrases are shown to the user as
    suggestions for skills.yaml, but scoring against phrases the system does
    not understand would make the number noise.
    """
    by_category: dict[str, CategoryCoverage] = {}
    counts = {status.value: 0 for status in MatchStatus}
    covered = total = 0.0

    for match in matches:
        weight = weights.get(match.term, 0.0)
        if weight <= 0:
            continue
        credit = CREDIT[match.status]
        counts[match.status.value] += 1

        total += weight
        covered += weight * credit

        category = categories.get(match.term, "unknown")
        bucket = by_category.setdefault(category, CategoryCoverage(category, 0.0, 0.0, 0))
        bucket.total_weight += weight
        bucket.covered_weight += weight * credit
        bucket.term_count += 1

    return Coverage(
        percent=round(100.0 * covered / total, 1) if total else 0.0,
        covered_weight=round(covered, 3),
        total_weight=round(total, 3),
        by_category=sorted(by_category.values(), key=lambda c: -c.total_weight),
        counts=counts,
        credit_scheme={status.value: credit for status, credit in CREDIT.items()},
    )
