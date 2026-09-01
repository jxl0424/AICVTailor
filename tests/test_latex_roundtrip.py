"""Round-trip gate for the LaTeX parser and regenerator.

The brief's acceptance criterion is "parse, change nothing, regenerate,
byte-identical". That test alone is nearly vacuous under span replacement --
it passes if regenerate simply returns its input, which proves nothing about
whether the recorded spans are correct. The tests that actually gate this phase
are the restore and mutation round trips below, which apply real edits and
assert that only the targeted bytes moved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.latex import parse
from aicvtailor.latex.ir import ImmutableTarget
from aicvtailor.latex.regenerate import Edit, OverlappingEdits, regenerate

FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"
REAL_MASTER = Path(__file__).parents[1] / "data" / "master" / "master.tex"


@pytest.fixture
def source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def doc(source: str):
    return parse(source)


# Runs against the user's real CV too when present, since that is the document
# that actually has to survive. Skipped in a fresh clone, where data/ is empty.
ALL_SOURCES = [
    pytest.param(FIXTURE, id="fixture"),
    pytest.param(
        REAL_MASTER,
        id="real-master",
        marks=pytest.mark.skipif(
            not REAL_MASTER.exists(), reason="no master.tex in data/master/"
        ),
    ),
]


@pytest.mark.parametrize("path", ALL_SOURCES)
def test_identity_roundtrip_is_byte_identical(path: Path):
    text = path.read_text(encoding="utf-8")
    assert parse(text).to_source() == text


@pytest.mark.parametrize("path", ALL_SOURCES)
def test_restore_roundtrip_is_byte_identical(path: Path):
    """Rewrite every editable span with its own exact source text.

    Unlike the identity test this actually exercises the splice path, so a
    span that is off by one byte shows up as a corrupted document.
    """
    text = path.read_text(encoding="utf-8")
    document = parse(text)

    edits = [document.edit(b.id, b.text) for b in document.bullets()]
    edits += [
        document.edit(k.id, k.values_span.text(text)) for k in document.skill_lines()
    ]
    assert edits, "fixture should expose editable spans"
    assert document.to_source(edits) == text


@pytest.mark.parametrize("path", ALL_SOURCES)
def test_mutation_touches_only_the_targeted_span(path: Path):
    """The real proof that the spans are right."""
    text = path.read_text(encoding="utf-8")
    document = parse(text)

    for bullet in document.bullets():
        out = document.to_source([document.edit(bullet.id, bullet.text + " SENTINEL")])
        assert out[: bullet.span.start] == text[: bullet.span.start]
        assert out[bullet.span.end + 9 :] == text[bullet.span.end :]
        assert len(out) == len(text) + 9


def test_every_bullet_can_be_rewritten_at_once(doc, source):
    edits = [doc.edit(b.id, "REPLACED") for b in doc.bullets()]
    out = doc.to_source(edits)

    assert out.count("REPLACED") == len(edits)
    # Structure survives: the same number of macro calls, none orphaned.
    for macro in (r"\resumeItem", r"\resumeSubheading", r"\resumeProjectHeading"):
        assert out.count(macro) == source.count(macro)


@pytest.mark.parametrize("path", ALL_SOURCES)
def test_preamble_is_never_touched(path: Path):
    """Everything above \\begin{document} is out of the IR's reach."""
    text = path.read_text(encoding="utf-8")
    document = parse(text)
    boundary = text.index(r"\begin{document}")

    out = document.to_source([document.edit(b.id, "X") for b in document.bullets()])
    assert out[:boundary] == text[:boundary]


def test_trailing_whitespace_and_indentation_survive(doc, source):
    """A full re-render would silently normalise these away."""
    assert " \n" in source, "fixture must contain trailing whitespace to be meaningful"
    out = doc.to_source([doc.edit(b.id, b.text) for b in doc.bullets()])
    assert out == source


def test_commented_out_bullet_is_not_parsed(doc):
    """A `% \\resumeItem{...}` line is inert text, not a bullet."""
    assert any("commented-out" in b.text for b in doc.bullets()) is False


def test_overlapping_edits_are_rejected():
    with pytest.raises(OverlappingEdits):
        regenerate("abcdef", [Edit(0, 3, "X"), Edit(2, 5, "Y")])


def test_adjacent_edits_are_allowed():
    assert regenerate("abcdef", [Edit(0, 3, "X"), Edit(3, 6, "Y")]) == "XY"
