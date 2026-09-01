"""Suggestion generation.

Three actions, and the type system decides which can touch the resume:

  REWORD    a named bullet already supports the term; surface it in words.
            Carries `source_bullet_id`, so every rewrite is traceable.
  RELOCATE  the skill is in the resume but buried in the skills block; promote
            it. Deterministic reordering, no model call.
  GAP       the resume does not support this. Advisory only.

`GapSuggestion` has no target id and no proposed text. It is not that applying
one is forbidden -- there is nothing to apply. `applicable()` returns only the
two types that carry a target, so the tailoring stage cannot receive a GAP even
by mistake.

The rule that satisfies "a term marked missing never silently appears as
experience": REWORD is only ever generated for a term whose match status is
IMPLIED, meaning a specific bullet already evidences it. A MISSING term can
only become a GAP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, Field

from .analysis.match import MatchStatus, ResumeLocation
from .analysis.pipeline import RankedTerm
from .config import get_guardrails
from .guardrails import GuardrailReport, Violation, enforce
from .latex.ir import Document
from .latex.sanitize import protected_tokens, sanitize
from .llm.base import Role
from .llm.runlog import RunLog

log = logging.getLogger(__name__)

# Below this weight a term is not worth spending a rewriter call on.
MIN_WEIGHT_FOR_REWORD = 1.0
# A skills-block entry this far down the list counts as buried.
BURIED_POSITION = 3


class RewriteResponse(BaseModel):
    rewritten: str = Field(description="The rewritten bullet, LaTeX-safe, one line")


@dataclass(frozen=True, slots=True)
class _Base:
    term: str
    category: str
    weight: float
    status: str
    rationale: str


@dataclass(frozen=True, slots=True)
class RewordSuggestion(_Base):
    """Rewrite one bullet so it states a term it already evidences."""

    source_bullet_id: str
    original_text: str
    proposed_text: str
    target_id: str
    guardrails: GuardrailReport = field(default_factory=GuardrailReport)

    action = "REWORD"


@dataclass(frozen=True, slots=True)
class RelocateSuggestion(_Base):
    """Promote an existing skill within the skills block. No model involved."""

    source_line_id: str
    original_text: str
    proposed_text: str
    target_id: str

    action = "RELOCATE"


@dataclass(frozen=True, slots=True)
class GapSuggestion(_Base):
    """Something the resume does not support. Carries no target and no text."""

    what_it_would_take: str

    action = "GAP"


Suggestion = RewordSuggestion | RelocateSuggestion | GapSuggestion


def applicable(suggestions: Iterable[Suggestion]) -> list[RewordSuggestion | RelocateSuggestion]:
    """The suggestions that can be applied to the document.

    A GAP has no target id, so it cannot appear here. This is the boundary the
    tailoring stage consumes.
    """
    return [s for s in suggestions if not isinstance(s, GapSuggestion)]


def as_dict(suggestion: Suggestion) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "term": suggestion.term,
        "category": suggestion.category,
        "weight": suggestion.weight,
        "status": suggestion.status,
        "action": suggestion.action,
        "rationale": suggestion.rationale,
        "source_bullet_id": getattr(suggestion, "source_bullet_id", None),
        "target_id": getattr(suggestion, "target_id", None),
        "original_text": getattr(suggestion, "original_text", ""),
        "proposed_text": getattr(suggestion, "proposed_text", ""),
    }
    if isinstance(suggestion, GapSuggestion):
        payload["what_it_would_take"] = suggestion.what_it_would_take
    if isinstance(suggestion, RewordSuggestion):
        payload["guardrails"] = suggestion.guardrails.as_dict()
    return payload


# --- REWORD ----------------------------------------------------------------

SYSTEM_PROMPT = """You rewrite a single resume bullet.

You may reword, reorder, re-emphasise and re-scope what the bullet already
says. You may NOT introduce any employer, date, tool, metric, team size or
outcome that is not in the source bullet. If the bullet does not support a
target term, leave the term out and return the bullet essentially unchanged.

Keep any LaTeX macros from the source, such as \\textbf{...}. Return one line
of plain text. Do not add a leading bullet character."""


def _build_user_prompt(bullet_text: str, terms: Sequence[str], rails: dict[str, Any]) -> str:
    lines = [
        "Source bullet:",
        bullet_text.strip(),
        "",
        f"Target terms to surface if the bullet genuinely supports them: {', '.join(terms)}",
    ]
    if limit := rails.get("max_bullet_length"):
        lines.append(f"Maximum length: {limit} characters.")
    if never := rails.get("never_reword"):
        present = [p for p in never if p in bullet_text]
        if present:
            lines.append(f"Copy these strings verbatim: {'; '.join(present)}")
    return "\n".join(lines)


def rewrite_bullet(
    provider,
    bullet_text: str,
    terms: Sequence[str],
    *,
    rails: dict[str, Any] | None = None,
    runlog: RunLog | None = None,
    system_prompt: str = SYSTEM_PROMPT,
) -> tuple[str, GuardrailReport]:
    """One rewrite, guardrail-checked, with a single repair attempt.

    `system_prompt` is a parameter so the adversarial tests can strip the rules
    out of the prompt entirely and prove the enforcement below still holds.
    """
    rails = rails if rails is not None else get_guardrails()
    allowed = set(terms)
    keep = protected_tokens(bullet_text)

    user_prompt = _build_user_prompt(bullet_text, terms, rails)
    report = GuardrailReport()
    candidate = bullet_text

    for attempt in range(2):
        response = provider.complete(
            system=system_prompt,
            user=user_prompt,
            schema=RewriteResponse,
            role=Role.REWRITER,
        )
        raw = (response or {}).get("rewritten", "") if isinstance(response, dict) else str(response)
        candidate = sanitize(raw.strip(), keep)

        report = enforce(bullet_text, candidate, target_terms=allowed, rails=rails)
        if runlog is not None:
            runlog.write(
                "rewrite",
                attempt=attempt + 1,
                ok=report.ok,
                violations=[v.rule for v in report.violations],
                terms=list(terms),
            )
        if report.ok:
            return candidate, report

        log.warning(
            "rewrite rejected (%s)", ", ".join(v.rule for v in report.violations)
        )
        user_prompt = f"{user_prompt}\n\n{report.feedback()}"

    # Two failures. The source bullet stands; the suggestion is unusable and
    # says why rather than being quietly downgraded to a pass.
    return candidate, report


def _reword_for(
    ranked: RankedTerm, document: Document, provider, rails: dict[str, Any], runlog: RunLog | None
) -> RewordSuggestion | GapSuggestion:
    bullet = document.bullet(ranked.match.bullet_id or "")
    if bullet is None:
        return _gap_for(ranked, "the bullet that implied this term is no longer in the resume")

    proposed, report = rewrite_bullet(
        provider, bullet.text, [ranked.canonical], rails=rails, runlog=runlog
    )

    if not report.ok:
        return GapSuggestion(
            term=ranked.canonical,
            category=ranked.term_category,
            weight=ranked.weight.weight,
            status=ranked.match.status.value,
            rationale=(
                "A rewrite was attempted but rejected by the guardrails: "
                + "; ".join(v.detail for v in report.violations)
            ),
            what_it_would_take=(
                f"Edit {bullet.id} by hand if you want this term surfaced."
            ),
        )

    return RewordSuggestion(
        term=ranked.canonical,
        category=ranked.term_category,
        weight=ranked.weight.weight,
        status=ranked.match.status.value,
        rationale=(
            f"{bullet.id} already evidences this (similarity {ranked.match.score}); "
            "the rewrite states it explicitly."
        ),
        source_bullet_id=bullet.id,
        original_text=bullet.text,
        proposed_text=proposed,
        target_id=bullet.id,
        guardrails=report,
    )


def _relocate_for(ranked: RankedTerm, document: Document) -> RelocateSuggestion | None:
    """Promote a buried skill. Deterministic reordering, no model call."""
    for line in document.skill_lines():
        matches = [
            index
            for index, value in enumerate(line.values)
            if ranked.canonical.casefold() in value.casefold()
            or value.casefold() in ranked.canonical.casefold()
        ]
        if not matches or matches[0] < BURIED_POSITION:
            continue

        position = matches[0]
        reordered = [line.values[position], *[v for i, v in enumerate(line.values) if i != position]]
        original = line.values_span.text(document.source)
        prefix = ": " if original.lstrip().startswith(":") else ""

        return RelocateSuggestion(
            term=ranked.canonical,
            category=ranked.term_category,
            weight=ranked.weight.weight,
            status=ranked.match.status.value,
            rationale=(
                f"Listed {position + 1}th under '{line.label}'. This posting weights it "
                f"at {ranked.weight.weight:.2f}, so it should lead the line."
            ),
            source_line_id=line.id,
            original_text=original,
            proposed_text=prefix + ", ".join(reordered),
            target_id=line.id,
        )
    return None


def _gap_for(ranked: RankedTerm, reason: str = "") -> GapSuggestion:
    return GapSuggestion(
        term=ranked.canonical,
        category=ranked.term_category,
        weight=ranked.weight.weight,
        status=ranked.match.status.value,
        rationale=reason
        or f"Nothing in the resume evidences {ranked.canonical}. Claiming it would be fabrication.",
        what_it_would_take=(
            f"To claim {ranked.canonical} honestly you would need to actually do it: a "
            "project, a role, or a course that produces something you can point at."
        ),
    )


def generate(
    ranked_terms: Sequence[RankedTerm],
    document: Document,
    *,
    provider=None,
    rails: dict[str, Any] | None = None,
    runlog: RunLog | None = None,
    limit: int = 12,
) -> list[Suggestion]:
    """Turn ranked terms into suggestions, highest weight first."""
    rails = rails if rails is not None else get_guardrails()
    runlog = runlog or RunLog()
    suggestions: list[Suggestion] = []

    for ranked in sorted(ranked_terms, key=lambda r: -r.weight.weight):
        if len(suggestions) >= limit:
            break

        status = ranked.match.status

        if status is MatchStatus.IMPLIED and ranked.weight.weight >= MIN_WEIGHT_FOR_REWORD:
            if provider is None:
                suggestions.append(
                    _gap_for(
                        ranked,
                        f"{ranked.match.bullet_id} implies this, but no LLM provider is "
                        "available to draft a rewrite.",
                    )
                )
            else:
                suggestions.append(_reword_for(ranked, document, provider, rails, runlog))
            continue

        if status is MatchStatus.MISSING:
            suggestions.append(_gap_for(ranked))
            continue

        if ranked.match.location is ResumeLocation.SKILLS:
            if relocate := _relocate_for(ranked, document):
                suggestions.append(relocate)
            continue

        # Present in a bullet already: nothing to do.

    runlog.write(
        "suggest",
        total=len(suggestions),
        by_action={
            action: sum(1 for s in suggestions if s.action == action)
            for action in {s.action for s in suggestions}
        },
    )
    return suggestions
