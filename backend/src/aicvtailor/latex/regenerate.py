"""Span-preserving regeneration.

The whole output strategy: never re-render the document. Take the original
bytes and splice replacements into recorded spans. The preamble, custom macros,
indentation, trailing whitespace and comments are therefore preserved by
construction rather than by effort.
"""

from __future__ import annotations

from dataclasses import dataclass


class OverlappingEdits(ValueError):
    """Two edits touch the same bytes, so the result would depend on order."""


@dataclass(frozen=True, slots=True)
class Edit:
    """Replace `source[start:end]` with `new_text`."""

    start: int
    end: int
    new_text: str
    target_id: str = ""

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"inverted span {self.start}:{self.end}")


def regenerate(source: str, edits: list[Edit] | None = None) -> str:
    """Apply edits to `source`.

    With no edits this returns `source` itself, so a parse/regenerate round
    trip is byte-identical for free. That property is necessary but nowhere
    near sufficient -- the tests that matter apply real edits and assert that
    only the targeted spans moved.
    """
    if not edits:
        return source

    ordered = sorted(edits, key=lambda e: (e.start, e.end))
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start < prev.end:
            raise OverlappingEdits(
                f"edit {prev.target_id or prev.start} overlaps "
                f"{nxt.target_id or nxt.start}"
            )

    out = []
    cursor = 0
    for edit in ordered:
        out.append(source[cursor : edit.start])
        out.append(edit.new_text)
        cursor = edit.end
    out.append(source[cursor:])
    return "".join(out)
