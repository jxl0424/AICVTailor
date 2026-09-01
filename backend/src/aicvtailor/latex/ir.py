"""Structured intermediate representation of a LaTeX resume.

Every node records the byte span it came from, which is what makes surgical
regeneration possible. The important invariant is that entry header fields --
employers, roles, dates, institutions, degrees -- carry no way to be edited.
`Document.edit()` is the only constructor of an Edit bound to this document,
and it refuses any id that is not a bullet or a skills line. Rewording a job
title is therefore not a rule the code checks, it is a thing the API cannot
express.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterator, Literal

from .regenerate import Edit

# Loose date detection, used only to label header fields for display. Getting
# this wrong costs a mislabelled column in the UI and nothing else, because
# header fields cannot be edited either way.
_DATE_RE = re.compile(
    r"(?:\b(19|20)\d{2}\b|\bpresent\b|\bcurrent\b|"
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b)",
    re.IGNORECASE,
)


def fingerprint(text: str) -> str:
    """Content hash used to re-link ids after the master file is edited.

    Ids encode structural position, so inserting a bullet shifts every id below
    it. The fingerprint survives that, letting a stored `source_bullet_id` be
    resolved against a newer parse.
    """
    normalised = " ".join(text.split()).casefold()
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:10]


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int

    def text(self, source: str) -> str:
        return source[self.start : self.end]

    def __len__(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class HeaderField:
    """One positional argument of an entry heading.

    Deliberately positional. In this template the argument order is not
    consistent between sections -- education uses
    (institution, location, degree, dates) while experience uses
    (role, dates, employer, empty) -- so any parser that assumed
    "argument 1 is the company" would mislabel real documents.
    """

    index: int
    span: Span
    text: str
    role_guess: str = "unknown"


@dataclass(frozen=True, slots=True)
class Bullet:
    id: str
    span: Span
    text: str
    fingerprint: str
    protected: tuple[str, ...] = ()


@dataclass
class Entry:
    id: str
    kind: Literal["subheading", "project"]
    span: Span
    fields: tuple[HeaderField, ...]
    bullets: list[Bullet] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.fields[0].text if self.fields else ""

    @property
    def dates(self) -> str:
        for f in self.fields:
            if f.role_guess == "dates":
                return f.text
        return ""


@dataclass
class SkillLine:
    """One `\\textbf{Label}{: a, b, c}` row of the skills block.

    The values are editable because RELOCATE needs to reorder and promote
    existing skills. The label is not.
    """

    id: str
    label: str
    label_span: Span
    values_span: Span
    values: tuple[str, ...]


@dataclass
class Section:
    id: str
    title: str
    title_span: Span
    span: Span
    entries: list[Entry] = field(default_factory=list)
    skill_lines: list[SkillLine] = field(default_factory=list)


class ImmutableTarget(KeyError):
    """The requested id is not editable, or does not exist."""


@dataclass
class Document:
    source: str
    sections: list[Section] = field(default_factory=list)

    # -- traversal ---------------------------------------------------------
    def bullets(self) -> Iterator[Bullet]:
        for section in self.sections:
            for entry in section.entries:
                yield from entry.bullets

    def skill_lines(self) -> Iterator[SkillLine]:
        for section in self.sections:
            yield from section.skill_lines

    def entries(self) -> Iterator[Entry]:
        for section in self.sections:
            yield from section.entries

    def bullet(self, bullet_id: str) -> Bullet | None:
        return next((b for b in self.bullets() if b.id == bullet_id), None)

    def find_by_fingerprint(self, value: str) -> Bullet | None:
        """Re-link an id recorded against an older parse of the master file."""
        return next((b for b in self.bullets() if b.fingerprint == value), None)

    # -- editing -----------------------------------------------------------
    def editable_spans(self) -> dict[str, Span]:
        """Every span the tailoring pipeline is allowed to touch.

        Bullets and skills values. Nothing else: not headings, not the
        preamble, not section titles, not the contact block.
        """
        spans: dict[str, Span] = {b.id: b.span for b in self.bullets()}
        spans.update({s.id: s.values_span for s in self.skill_lines()})
        return spans

    def edit(self, target_id: str, new_text: str) -> Edit:
        """Build an Edit against this document.

        Raises ImmutableTarget for anything that is not an editable span, which
        is what stops a job title or a date from being rewritten by any code
        path, including a buggy one.
        """
        span = self.editable_spans().get(target_id)
        if span is None:
            raise ImmutableTarget(
                f"'{target_id}' is not an editable span "
                "(entry headers, section titles and the preamble are immutable)"
            )
        return Edit(start=span.start, end=span.end, new_text=new_text, target_id=target_id)

    def to_source(self, edits: list[Edit] | None = None) -> str:
        from .regenerate import regenerate

        return regenerate(self.source, edits)


def guess_field_role(text: str, index: int, total: int) -> str:
    """Best-effort label for a positional header field. Display only."""
    stripped = text.strip()
    if not stripped:
        return "empty"
    if _DATE_RE.search(stripped) and len(stripped) < 40:
        return "dates"
    if index == 0:
        return "title"
    if total >= 4 and index == 2:
        return "subtitle"
    return "detail"
