"""Truth guardrails, enforced on the model's output.

Nothing here trusts the prompt. Every check runs on what came back, so the
tests can strip the guardrail text out of the prompt entirely and still prove
the system refuses to fabricate. Prompt instructions are a courtesy to the
model; this file is the rule.

The checks, in the order they tend to fire:

  new entities   a number, date or name in the rewrite that is not in the
                 source bullet. This is the main anti-fabrication check.
  forbidden      a claim the user has said must never appear, even though it
                 is true and in their notes.
  never_reword   a string that must be copied verbatim if it appears at all.
  protected      a macro or math run from the source that the rewrite dropped.
  length         a bullet that would overflow the layout.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Any

from .config import get_guardrails
from .latex.sanitize import missing_tokens

# A rewrite may introduce the JD terms it was asked to surface, plus anything
# the source bullet already said. Everything else new is suspect.
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]*(?:[.+#][A-Za-z0-9]+)*\b")
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?;:]\s+|\n\s*)([A-Z][a-zA-Z0-9]*)")

# Words that are capitalised for grammar, not because they name anything.
_NEUTRAL_CAPITALS = frozenset(
    """
    a an the and or but if then i we you they it this that these those
    built build designed developed created delivered led ran shipped used
    applied improved reduced increased implemented integrated managed
    """.split()
)


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    detail: str
    offending: str = ""

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


@dataclass
class GuardrailReport:
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violations": [
                {"rule": v.rule, "detail": v.detail, "offending": v.offending}
                for v in self.violations
            ],
        }

    def feedback(self) -> str:
        """What gets quoted back to the model on the single repair attempt."""
        lines = ["Your rewrite was rejected. Fix every point and return only the bullet."]
        for violation in self.violations:
            lines.append(f"- {violation.detail}")
        return "\n".join(lines)


def _normalise(text: str) -> str:
    """Casefold, strip punctuation, collapse whitespace.

    So a forbidden claim is still caught when the model reformats it -- naive
    substring matching misses "Project  Halberd." and "project halberd".
    """
    lowered = text.casefold()
    stripped = "".join(" " if ch in string.punctuation else ch for ch in lowered)
    return " ".join(stripped.split())


def check_forbidden_claims(text: str, rails: dict[str, Any]) -> list[Violation]:
    """Claims that must never appear, even though they are true.

    Entries prefixed `re:` are regexes. Everything else is matched on the
    normalised form so punctuation and casing cannot smuggle it through.
    """
    violations: list[Violation] = []
    haystack = _normalise(text)

    for claim in rails.get("forbidden_claims") or []:
        claim = str(claim)
        if claim.startswith("re:"):
            pattern = claim[3:]
            try:
                match = re.search(pattern, text, re.I)
            except re.error:
                continue
            if match:
                violations.append(
                    Violation(
                        "forbidden_claim",
                        f"output matches the forbidden pattern {pattern!r}; remove it",
                        match.group(),
                    )
                )
        elif _normalise(claim) and _normalise(claim) in haystack:
            violations.append(
                Violation(
                    "forbidden_claim",
                    f"output mentions {claim!r}, which must never appear; remove it",
                    claim,
                )
            )
    return violations


def check_never_reword(original: str, rewritten: str, rails: dict[str, Any]) -> list[Violation]:
    """Strings that must survive verbatim wherever they appear.

    Job titles, employers, degrees and dates do not need listing: they live in
    entry headers, which the IR gives no editable span at all. This covers
    strings inside bullets -- certification names, exact framings.
    """
    violations: list[Violation] = []
    for phrase in rails.get("never_reword") or []:
        phrase = str(phrase)
        if phrase and phrase in original and phrase not in rewritten:
            violations.append(
                Violation(
                    "never_reword",
                    f"{phrase!r} must be copied verbatim but was altered or removed",
                    phrase,
                )
            )
    return violations


def check_length(text: str, rails: dict[str, Any]) -> list[Violation]:
    limit = rails.get("max_bullet_length")
    if not limit or len(text) <= int(limit):
        return []
    return [
        Violation(
            "max_bullet_length",
            f"rewrite is {len(text)} characters, over the {limit} limit; shorten it",
            text[-40:],
        )
    ]


def check_protected_tokens(original: str, rewritten: str) -> list[Violation]:
    """Macros and math from the source bullet must survive the rewrite.

    A dropped `\\textbf{}` loses formatting; a dropped `\\href{}{}` loses a
    link. Position is not checked -- a rewrite may move them, not lose them.
    """
    return [
        Violation(
            "protected_token",
            f"LaTeX construct {token!r} from the source bullet is missing; keep it",
            token,
        )
        for token in missing_tokens(original, rewritten)
    ]


def _entities(text: str) -> dict[str, set[str]]:
    """Numbers, years, acronyms and proper nouns in a piece of text."""
    sentence_starts = {m.group(1).casefold() for m in _SENTENCE_START_RE.finditer(text)}

    proper: set[str] = set()
    for match in _PROPER_NOUN_RE.finditer(text):
        word = match.group()
        folded = word.casefold()
        if folded in _NEUTRAL_CAPITALS:
            continue
        # A capitalised word that only ever starts a sentence is grammar.
        if folded in sentence_starts and not word.isupper():
            continue
        proper.add(word)

    return {
        "number": set(_NUMBER_RE.findall(text)),
        "year": set(_YEAR_RE.findall(text)),
        "acronym": set(_ACRONYM_RE.findall(text)),
        "proper_noun": proper,
    }


def check_new_entities(
    original: str,
    rewritten: str,
    rails: dict[str, Any],
    allowed: set[str] | None = None,
) -> list[Violation]:
    """The main anti-fabrication check.

    A rewrite may reword, reorder, re-emphasise and re-scope what the source
    bullet says. It may not introduce a metric, a date, a tool or an
    organisation the source did not contain. `allowed` carries the JD terms the
    rewrite was explicitly asked to surface -- those are the point of the
    exercise, and the suggestion layer only asks for terms a named bullet
    already supports.
    """
    if not rails.get("forbid_new_entities", True):
        return []

    permitted = {a.casefold() for a in (allowed or set())}
    permitted |= {str(a).casefold() for a in (rails.get("entity_allowlist") or [])}
    # Multi-word allowances also permit their parts, so "vector database"
    # licenses "database".
    for phrase in list(permitted):
        permitted.update(phrase.split())

    source = _entities(original)
    source_words = {w.casefold() for w in re.findall(r"[A-Za-z0-9.+#]+", original)}
    candidate = _entities(rewritten)

    violations: list[Violation] = []
    label = {
        "number": "a figure",
        "year": "a date",
        "acronym": "an acronym",
        "proper_noun": "a name",
    }

    for kind, values in candidate.items():
        for value in sorted(values - source[kind]):
            folded = value.casefold()
            if folded in permitted or folded in source_words:
                continue
            violations.append(
                Violation(
                    "new_entity",
                    f"{label[kind]} {value!r} appears in the rewrite but not in the "
                    "source bullet; it cannot be introduced",
                    value,
                )
            )
    return violations


def enforce(
    original: str,
    rewritten: str,
    *,
    target_terms: set[str] | None = None,
    rails: dict[str, Any] | None = None,
) -> GuardrailReport:
    """Run every check against one rewritten bullet."""
    rails = rails if rails is not None else get_guardrails()

    violations: list[Violation] = []
    violations += check_new_entities(original, rewritten, rails, target_terms)
    violations += check_forbidden_claims(rewritten, rails)
    violations += check_never_reword(original, rewritten, rails)
    violations += check_protected_tokens(original, rewritten)
    violations += check_length(rewritten, rails)

    return GuardrailReport(violations=violations)


def enforce_document(text: str, rails: dict[str, Any] | None = None) -> GuardrailReport:
    """Whole-document check, run on the final .tex and on extracted PDF text.

    Forbidden claims are checked here as well as per bullet, because a claim
    can be assembled across edits that each looked innocent alone.
    """
    rails = rails if rails is not None else get_guardrails()
    return GuardrailReport(violations=check_forbidden_claims(text, rails))
