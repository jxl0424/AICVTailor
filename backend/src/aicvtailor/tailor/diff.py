"""Word-level diff, grouped by section and entry.

Every change carries the JD terms it was targeting and the source bullet id it
came from, which is what makes the acceptance criterion -- every rewritten
bullet traceable to a source bullet -- checkable in the UI rather than only in
the code.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field

from ..latex.ir import Document

_TOKEN_RE = re.compile(r"\s+|[^\s]+")


@dataclass(frozen=True, slots=True)
class Piece:
    kind: str  # equal | insert | delete
    text: str


@dataclass
class ChangedSpan:
    target_id: str
    kind: str  # bullet | skills
    section: str
    entry: str
    source_bullet_id: str | None
    target_terms: list[str]
    before: str
    after: str
    pieces: list[Piece] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["pieces"] = [asdict(p) for p in self.pieces]
        return data


def word_diff(before: str, after: str) -> list[Piece]:
    """Token-level diff that keeps whitespace, so the rendering is faithful."""
    a = _TOKEN_RE.findall(before)
    b = _TOKEN_RE.findall(after)
    pieces: list[Piece] = []

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            pieces.append(Piece("equal", "".join(a[i1:i2])))
        elif tag == "delete":
            pieces.append(Piece("delete", "".join(a[i1:i2])))
        elif tag == "insert":
            pieces.append(Piece("insert", "".join(b[j1:j2])))
        else:  # replace
            pieces.append(Piece("delete", "".join(a[i1:i2])))
            pieces.append(Piece("insert", "".join(b[j1:j2])))

    return [p for p in pieces if p.text]


def build(
    document: Document,
    changes: list[tuple[str, str, str, list[str], str | None]],
) -> list[ChangedSpan]:
    """Assemble the diff view.

    `changes` is (target_id, before, after, target_terms, source_bullet_id).
    Section and entry labels are read from the IR so the view groups the way
    the resume reads.
    """
    location: dict[str, tuple[str, str, str]] = {}
    for section in document.sections:
        for entry in section.entries:
            for bullet in entry.bullets:
                location[bullet.id] = (section.title, entry.display_title, "bullet")
        for line in section.skill_lines:
            location[line.id] = (section.title, line.label, "skills")

    spans: list[ChangedSpan] = []
    for target_id, before, after, terms, source_bullet_id in changes:
        section_title, entry_title, kind = location.get(target_id, ("", "", "bullet"))
        spans.append(
            ChangedSpan(
                target_id=target_id,
                kind=kind,
                section=section_title,
                entry=entry_title,
                source_bullet_id=source_bullet_id,
                target_terms=terms,
                before=before,
                after=after,
                pieces=word_diff(before, after),
            )
        )
    return spans
