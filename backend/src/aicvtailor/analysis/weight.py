"""Term weighting.

Every factor is recorded alongside the result, because the UI has to show the
maths. A coverage number nobody can audit is a number nobody should trust.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .sections import SectionKind
from .terms import Term

# How much a mention counts for, by where it appears. A skill named under
# Requirements is the point of the posting; the same word in the benefits
# blurb usually is not.
SECTION_MULTIPLIER: dict[SectionKind, float] = {
    SectionKind.REQUIREMENTS: 1.6,
    SectionKind.RESPONSIBILITIES: 1.2,
    SectionKind.OTHER: 1.0,
    SectionKind.NICE_TO_HAVE: 0.5,
    SectionKind.BOILERPLATE: 0.3,
}

REQUIRED_MULTIPLIER = 1.3
OPTIONAL_MULTIPLIER = 0.7
SPREAD_BONUS_PER_SECTION = 0.15
UNKNOWN_TERM_PENALTY = 0.6


@dataclass(frozen=True, slots=True)
class WeightBreakdown:
    """The full derivation of one term's weight, for display."""

    frequency: int
    frequency_factor: float
    section: str
    section_factor: float
    requirement: str
    requirement_factor: float
    distinct_sections: int
    spread_factor: float
    dictionary_factor: float
    weight: float

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def formula(self) -> str:
        return (
            f"{self.frequency_factor:.2f} (freq {self.frequency}) x "
            f"{self.section_factor:.2f} ({self.section}) x "
            f"{self.requirement_factor:.2f} ({self.requirement}) x "
            f"{self.spread_factor:.2f} (spread {self.distinct_sections}) x "
            f"{self.dictionary_factor:.2f} = {self.weight:.3f}"
        )


def _requirement_state(term: Term) -> str:
    """Required beats optional: an explicit must-have anywhere wins."""
    cues = [m.required for m in term.mentions if m.required is not None]
    if True in cues:
        return "required"
    if cues and all(cue is False for cue in cues):
        return "optional"
    if term.sections == {SectionKind.NICE_TO_HAVE}:
        return "optional"
    if SectionKind.REQUIREMENTS in term.sections:
        return "required"
    return "unstated"


def score(term: Term) -> WeightBreakdown:
    """Weight one term.

    Frequency is log-scaled so a word repeated eight times does not outrank
    eight distinct requirements. The strongest section a term appears in wins,
    so burying a required skill in the benefits text cannot dilute it.
    """
    frequency_factor = 1.0 + math.log1p(term.frequency)

    best_section = max(
        term.sections, key=lambda s: SECTION_MULTIPLIER.get(s, 1.0), default=SectionKind.OTHER
    )
    section_factor = SECTION_MULTIPLIER.get(best_section, 1.0)

    requirement = _requirement_state(term)
    requirement_factor = {
        "required": REQUIRED_MULTIPLIER,
        "optional": OPTIONAL_MULTIPLIER,
        "unstated": 1.0,
    }[requirement]

    distinct = len(term.sections)
    spread_factor = 1.0 + SPREAD_BONUS_PER_SECTION * (distinct - 1)

    dictionary_factor = 1.0 if term.in_dictionary else UNKNOWN_TERM_PENALTY

    weight = (
        frequency_factor
        * section_factor
        * requirement_factor
        * spread_factor
        * dictionary_factor
    )

    return WeightBreakdown(
        frequency=term.frequency,
        frequency_factor=round(frequency_factor, 3),
        section=best_section.value,
        section_factor=section_factor,
        requirement=requirement,
        requirement_factor=requirement_factor,
        distinct_sections=distinct,
        spread_factor=round(spread_factor, 3),
        dictionary_factor=dictionary_factor,
        weight=round(weight, 4),
    )
