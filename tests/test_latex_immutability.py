"""Entry headers must be structurally unreachable, not merely rule-checked.

`never_reword` in guardrails.yaml is a string check that runs after the model
returns. This is the layer underneath it: there is no id that addresses a job
title, an employer, a degree or a date, so no code path -- including a buggy or
model-driven one -- can construct an Edit that touches them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.latex import parse
from aicvtailor.latex.ir import ImmutableTarget

FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"
REAL_MASTER = Path(__file__).parents[1] / "data" / "master" / "master.tex"


@pytest.fixture
def source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def doc(source: str):
    return parse(source)


def _overlaps(a, b) -> bool:
    return a.start < b.end and b.start < a.end


def test_no_editable_span_touches_an_entry_header(doc):
    """The load-bearing assertion of the whole anti-fabrication design."""
    editable = list(doc.editable_spans().values())
    for entry in doc.entries():
        for field in entry.fields:
            for span in editable:
                assert not _overlaps(span, field.span), (
                    f"editable span {span} overlaps header field "
                    f"{field.index} of {entry.id}: {field.text!r}"
                )


def test_no_editable_span_touches_a_section_title(doc):
    editable = list(doc.editable_spans().values())
    for section in doc.sections:
        for span in editable:
            assert not _overlaps(span, section.title_span)


def test_editing_an_entry_or_section_id_is_refused(doc):
    for bad_id in ("s2", "s2.e0", "s3.e0", "preamble", "", "s2.e0.field0"):
        with pytest.raises(ImmutableTarget):
            doc.edit(bad_id, "anything")


@pytest.mark.skipif(not REAL_MASTER.exists(), reason="no master.tex in data/master/")
def test_real_master_headers_are_all_immutable():
    """Same guarantee, checked against the actual CV.

    The protected strings are read out of the parsed document rather than
    written down here: this file is committed, and the real employers, degrees
    and dates are exactly what should not be in it.
    """
    text = REAL_MASTER.read_text(encoding="utf-8")
    document = parse(text)
    spans = list(document.editable_spans().values())

    header_fields = [f for e in document.entries() for f in e.fields]
    assert header_fields, "real master.tex parsed with no entry headers"

    for field in header_fields:
        if not field.text.strip():
            continue
        for span in spans:
            assert not _overlaps(span, field.span), (
                f"a header field of {field.index} sits inside an editable span"
            )
        # And the text itself is not reachable anywhere else in the document.
        at = text.find(field.text)
        assert at != -1
        for span in spans:
            assert not (span.start < at + len(field.text) and at < span.end)


def test_skill_values_are_editable_but_labels_are_not(doc):
    """RELOCATE needs to reorder skills, so values must be writable. The
    category labels are not up for rewriting."""
    lines = list(doc.skill_lines())
    assert lines

    editable = doc.editable_spans()
    for line in lines:
        assert line.id in editable
        for span in editable.values():
            assert not _overlaps(span, line.label_span)


def test_bullet_ids_relink_by_fingerprint_after_the_file_shifts(doc, source):
    """Ids encode structural position, so inserting content above a bullet
    renumbers it. A stored source_bullet_id must still resolve."""
    target = doc.bullet("s1.e0.b1")
    original_fingerprint = target.fingerprint

    # Simulate the user adding a bullet earlier in the document. Search from
    # \begin{document}: the marker also appears in the preamble as a
    # \newcommand definition, and inserting there would prove nothing.
    body_at = source.index(r"\begin{document}")
    marker = r"\resumeItemListStart"
    at = source.index(marker, body_at) + len(marker)
    shifted = source[:at] + "\n      \\resumeItem{A newly added first bullet}" + source[at:]

    reparsed = parse(shifted)
    relinked = reparsed.find_by_fingerprint(original_fingerprint)

    assert relinked is not None, "fingerprint failed to survive a structural shift"
    assert relinked.text == target.text
    assert relinked.id != target.id  # position changed, content did not


def test_macro_definitions_in_the_preamble_are_not_parsed_as_content(source):
    """`\\newcommand{\\resumeItem}[1]{...}` defines the macro, it is not a bullet.

    Found by a test that searched for its marker from byte zero and hit the
    preamble definition instead of the document body.
    """
    document = parse(source)
    body_at = source.index(r"\begin{document}")

    for bullet in document.bullets():
        assert bullet.span.start > body_at
    for entry in document.entries():
        assert entry.span.start > body_at
