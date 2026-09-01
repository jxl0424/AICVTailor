"""Matching JD terms against the resume.

Deterministic. No model call happens here -- a term is present because it is
in the document, or implied because a named bullet is similar enough to say so,
or it is missing. `implied` always carries the bullet id that justified it, so
nothing downstream can claim experience without pointing at its source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..latex.ir import Document
from .semantic import SimilarityIndex
from .terms import SkillDictionary, Term

# Above this, a bullet is close enough to say the term is implied. Deliberately
# high: a false `implied` understates a real gap, which is the failure mode
# that costs an interview.
IMPLIED_THRESHOLD = 0.62


class MatchStatus(str, Enum):
    PRESENT_EXACT = "present_exact"
    PRESENT_AS_SYNONYM = "present_as_synonym"
    IMPLIED = "implied"
    MISSING = "missing"


class ResumeLocation(str, Enum):
    BULLET = "bullet"
    SKILLS = "skills"
    OTHER = "other"  # summary, coursework, headings


@dataclass
class Match:
    term: str
    status: MatchStatus
    location: ResumeLocation | None = None
    evidence: str = ""
    bullet_id: str | None = None
    score: float = 0.0

    @property
    def present(self) -> bool:
        return self.status in (MatchStatus.PRESENT_EXACT, MatchStatus.PRESENT_AS_SYNONYM)


@dataclass(frozen=True, slots=True)
class ResumeIndex:
    """Everything in the resume that a term could match against."""

    bullets: tuple[tuple[str, str], ...]  # (bullet_id, text)
    skills: tuple[tuple[str, str], ...]  # (skill_line_id, values text)
    other: tuple[str, ...]

    @classmethod
    def from_document(cls, doc: Document) -> ResumeIndex:
        source = doc.source
        skills: list[tuple[str, str]] = []
        for line in doc.skill_lines():
            skills.append((line.id, line.values_span.text(source)))

        # Section text that is neither a bullet nor a skills line: the summary
        # and the coursework list. Both count as present for keyword coverage.
        other: list[str] = []
        for section in doc.sections:
            if section.entries or section.skill_lines:
                continue
            other.append(section.span.text(source))

        return cls(
            bullets=tuple((b.id, b.text) for b in doc.bullets()),
            skills=tuple(skills),
            other=tuple(other),
        )

    def all_text(self) -> str:
        parts = [t for _, t in self.bullets] + [t for _, t in self.skills] + list(self.other)
        return "\n".join(parts)


def _contains(text: str, phrase: str) -> bool:
    """Whole-phrase, case-insensitive. Word boundaries that tolerate `C++`."""
    return (
        re.search(
            r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", text, re.I
        )
        is not None
    )


def _locate(index: ResumeIndex, phrase: str) -> tuple[ResumeLocation, str] | None:
    """Where in the resume a phrase appears. Bullets outrank the skills list.

    The distinction drives RELOCATE later: a skill listed only in the skills
    block is present for keyword purposes but not evidenced by any experience.
    """
    for _, text in index.bullets:
        if _contains(text, phrase):
            return ResumeLocation.BULLET, text
    for _, text in index.skills:
        if _contains(text, phrase):
            return ResumeLocation.SKILLS, text
    for text in index.other:
        if _contains(text, phrase):
            return ResumeLocation.OTHER, text
    return None


def match_term(
    term: Term,
    index: ResumeIndex,
    dictionary: SkillDictionary,
    similarity: SimilarityIndex,
) -> Match:
    """Classify one term against the resume."""
    hit = _locate(index, term.canonical)
    if hit is not None:
        location, evidence = hit
        return Match(
            term=term.canonical,
            status=MatchStatus.PRESENT_EXACT,
            location=location,
            evidence=evidence.strip()[:200],
            score=1.0,
        )

    for synonym in dictionary.synonyms_of.get(term.canonical, []):
        hit = _locate(index, synonym)
        if hit is not None:
            location, evidence = hit
            return Match(
                term=term.canonical,
                status=MatchStatus.PRESENT_AS_SYNONYM,
                location=location,
                evidence=f"matched '{synonym}' in: {evidence.strip()[:180]}",
                score=1.0,
            )

    # Nothing literal. Ask whether a specific bullet implies it -- and name
    # which one, so the claim is auditable.
    best_id, best_text, best_score = None, "", 0.0
    for bullet_id, text in index.bullets:
        score = similarity.similarity(term.canonical, text)
        if score > best_score:
            best_id, best_text, best_score = bullet_id, text, score

    if best_id is not None and best_score >= IMPLIED_THRESHOLD:
        return Match(
            term=term.canonical,
            status=MatchStatus.IMPLIED,
            location=ResumeLocation.BULLET,
            evidence=best_text.strip()[:200],
            bullet_id=best_id,
            score=round(best_score, 3),
        )

    return Match(
        term=term.canonical,
        status=MatchStatus.MISSING,
        score=round(best_score, 3),
    )


def match_all(
    terms: list[Term],
    index: ResumeIndex,
    dictionary: SkillDictionary,
    similarity: SimilarityIndex,
) -> list[Match]:
    return [match_term(term, index, dictionary, similarity) for term in terms]
