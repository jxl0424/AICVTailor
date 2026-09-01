"""Recognisers for Jake's Resume template.

Builds the IR by locating known macros and assigning each bullet to the entry
and section it falls inside, purely by byte position. Anything unrecognised is
simply not represented in the IR, which means it is never edited and passes
through regeneration untouched.
"""

from __future__ import annotations

import re

from .ir import (
    Bullet,
    Document,
    Entry,
    HeaderField,
    Section,
    SkillLine,
    Span,
    fingerprint,
    guess_field_role,
)
from .sanitize import protected_tokens
from .scanner import comment_mask, find_calls

# `\textbf{Languages}{: Python, C/C++}` -- the skills block is a plain itemize,
# not a resume macro, so it needs its own recogniser.
_SKILL_LINE_RE = re.compile(
    r"\\textbf\{(?P<label>[^{}]*)\}\s*\{(?P<values>[^{}]*)\}",
)

_BULLET_MACROS = (("resumeItem", 1), ("resumeSubItem", 1))
_ENTRY_MACROS = (("resumeSubheading", 4, "subheading"), ("resumeProjectHeading", 2, "project"))


def _document_body(source: str) -> tuple[int, int]:
    """Byte range of the document body.

    The preamble is out of bounds for the IR entirely -- nothing that is not
    inside \\begin{document} can ever be reached by an edit.
    """
    start = source.find(r"\begin{document}")
    end = source.find(r"\end{document}")
    return (
        start + len(r"\begin{document}") if start != -1 else 0,
        end if end != -1 else len(source),
    )


def parse(source: str) -> Document:
    """Parse LaTeX into the editable IR."""
    mask = comment_mask(source)
    body_start, body_end = _document_body(source)
    doc = Document(source=source)

    def in_body(pos: int) -> bool:
        return body_start <= pos < body_end

    # -- sections ----------------------------------------------------------
    section_calls = [c for c in find_calls(source, "section", 1, mask) if in_body(c.start)]
    for index, call in enumerate(section_calls):
        end = section_calls[index + 1].start if index + 1 < len(section_calls) else body_end
        doc.sections.append(
            Section(
                id=f"s{index}",
                title=call.arg_text(source, 0).strip(),
                title_span=Span(*call.args[0]),
                span=Span(call.start, end),
            )
        )

    def section_for(pos: int) -> Section | None:
        return next((s for s in doc.sections if s.span.start <= pos < s.span.end), None)

    # -- entries -----------------------------------------------------------
    entry_calls = []
    for macro, arity, kind in _ENTRY_MACROS:
        entry_calls.extend(
            (c, kind) for c in find_calls(source, macro, arity, mask) if in_body(c.start)
        )
    entry_calls.sort(key=lambda pair: pair[0].start)

    counters: dict[str, int] = {}
    for call, kind in entry_calls:
        section = section_for(call.start)
        if section is None:
            continue
        index = counters.get(section.id, 0)
        counters[section.id] = index + 1

        fields = tuple(
            HeaderField(
                index=i,
                span=Span(lo, hi),
                text=source[lo:hi],
                role_guess=guess_field_role(source[lo:hi], i, len(call.args)),
            )
            for i, (lo, hi) in enumerate(call.args)
        )
        section.entries.append(
            Entry(
                id=f"{section.id}.e{index}",
                kind=kind,  # type: ignore[arg-type]
                span=Span(call.start, call.end),
                fields=fields,
            )
        )

    all_entries = [e for s in doc.sections for e in s.entries]
    all_entries.sort(key=lambda e: e.span.start)

    def entry_for(pos: int) -> Entry | None:
        """The nearest entry heading above this position.

        Bullets follow their heading in source order, so the last heading that
        starts before the bullet owns it.
        """
        candidate = None
        for entry in all_entries:
            if entry.span.start < pos:
                candidate = entry
            else:
                break
        return candidate

    # -- bullets -----------------------------------------------------------
    bullet_calls = []
    for macro, arity in _BULLET_MACROS:
        bullet_calls.extend(c for c in find_calls(source, macro, arity, mask) if in_body(c.start))
    bullet_calls.sort(key=lambda c: c.start)

    bullet_counters: dict[str, int] = {}
    for call in bullet_calls:
        entry = entry_for(call.start)
        if entry is None:
            continue
        index = bullet_counters.get(entry.id, 0)
        bullet_counters[entry.id] = index + 1

        lo, hi = call.args[0]
        text = source[lo:hi]
        entry.bullets.append(
            Bullet(
                id=f"{entry.id}.b{index}",
                span=Span(lo, hi),
                text=text,
                fingerprint=fingerprint(text),
                protected=protected_tokens(text),
            )
        )

    # -- skills ------------------------------------------------------------
    for section in doc.sections:
        if "skill" not in section.title.lower():
            continue
        region = source[section.span.start : section.span.end]
        for index, match in enumerate(_SKILL_LINE_RE.finditer(region)):
            offset = section.span.start
            values = match.group("values")
            section.skill_lines.append(
                SkillLine(
                    id=f"{section.id}.k{index}",
                    label=match.group("label"),
                    label_span=Span(offset + match.start("label"), offset + match.end("label")),
                    values_span=Span(offset + match.start("values"), offset + match.end("values")),
                    values=tuple(
                        v.strip() for v in values.lstrip(":").split(",") if v.strip()
                    ),
                )
            )

    return doc
