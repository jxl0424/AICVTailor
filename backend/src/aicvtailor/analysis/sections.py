"""Splitting a job description into weighted regions.

A term under "Requirements" matters more than the same term in the benefits
blurb. Rather than treating the JD as a bag of words, it is cut into labelled
sections so weighting has something honest to work with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SectionKind(str, Enum):
    REQUIREMENTS = "requirements"
    RESPONSIBILITIES = "responsibilities"
    NICE_TO_HAVE = "nice_to_have"
    BOILERPLATE = "boilerplate"
    OTHER = "other"


# Matched against a heading line, longest-intent first. Order matters:
# "preferred qualifications" is a nice-to-have, not a requirement, even though
# it contains "qualifications".
_HEADING_PATTERNS: tuple[tuple[SectionKind, re.Pattern[str]], ...] = (
    (
        SectionKind.NICE_TO_HAVE,
        re.compile(
            r"\b(nice[-\s]to[-\s]have|preferred|desirable|bonus|"
            r"a plus|advantageous|would be great|icing)\b",
            re.I,
        ),
    ),
    (
        SectionKind.REQUIREMENTS,
        re.compile(
            r"\b(requirements?|qualifications?|must[-\s]have|what you.{0,3}ll need|"
            r"what we.{0,3}re looking for|who you are|essential|skills? (and|&) "
            r"experience|your (background|profile)|about you)\b",
            re.I,
        ),
    ),
    (
        SectionKind.RESPONSIBILITIES,
        re.compile(
            r"\b(responsibilities|what you.{0,3}ll do|the role|your role|"
            r"day[-\s]to[-\s]day|duties|in this role|you will)\b",
            re.I,
        ),
    ),
    (
        SectionKind.BOILERPLATE,
        re.compile(
            r"\b(about (us|the company|our)|who we are|our (mission|values|story)|"
            r"benefits|perks|what we offer|compensation|equal opportunit|"
            r"diversity|how to apply|application process)\b",
            re.I,
        ),
    ),
)

# Inline cues that override the surrounding section for a single line.
REQUIRED_CUE = re.compile(
    r"\b(must have|required|is required|essential|you have|proven|"
    r"demonstrable|minimum of|at least \d)\b",
    re.I,
)
OPTIONAL_CUE = re.compile(
    r"\b(nice to have|preferred|bonus|a plus|desirable|ideally|"
    r"familiarity with|exposure to|would be)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class Section:
    kind: SectionKind
    heading: str
    text: str
    start: int
    end: int


def _looks_like_heading(line: str) -> bool:
    """Headings are short, rarely end in a full stop, and often end in a colon."""
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if stripped.endswith(":"):
        return True
    if stripped.endswith((".", ",", ";")):
        return False
    words = stripped.split()
    if len(words) > 9:
        return False
    # A bullet is not a heading even when it is short.
    if stripped[0] in "-*•·>":
        return False
    return stripped.isupper() or stripped == stripped.title() or len(words) <= 5


def classify_heading(line: str) -> SectionKind | None:
    for kind, pattern in _HEADING_PATTERNS:
        if pattern.search(line):
            return kind
    return None


def split_sections(text: str) -> list[Section]:
    """Cut the JD at recognised headings.

    Text before the first heading is OTHER rather than dropped -- plenty of
    postings open with the most important sentence in the whole document.
    """
    lines = text.splitlines(keepends=True)
    boundaries: list[tuple[int, int, SectionKind, str]] = []

    offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped and _looks_like_heading(line):
            # An unrecognised heading still ends the previous section. Without
            # this, content under a heading like "Tech you'll touch" inherits
            # whatever came before it -- which weighted required tooling as
            # nice-to-have on the first real posting this was run against.
            kind = classify_heading(stripped) or SectionKind.OTHER
            boundaries.append((offset, offset + len(line), kind, stripped))
        offset += len(line)

    if not boundaries:
        return [Section(SectionKind.OTHER, "", text, 0, len(text))]

    sections: list[Section] = []
    first_start = boundaries[0][0]
    if first_start > 0:
        sections.append(Section(SectionKind.OTHER, "", text[:first_start], 0, first_start))

    for index, (start, _body_start, kind, heading) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        # The heading line stays inside the section text. A posting whose first
        # line is "Senior AI Engineer (LLM Systems)" carries real terms there,
        # and excluding heading lines silently dropped them.
        sections.append(Section(kind, heading, text[start:end], start, end))

    return sections
