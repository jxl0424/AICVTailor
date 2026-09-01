"""JD section splitting and classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.analysis.sections import SectionKind, classify_heading, split_sections

JD = Path(__file__).parent / "fixtures" / "jd_ai_engineer.txt"


@pytest.fixture
def jd() -> str:
    return JD.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "heading,expected",
    [
        ("Requirements", SectionKind.REQUIREMENTS),
        ("What we're looking for", SectionKind.REQUIREMENTS),
        ("Must have", SectionKind.REQUIREMENTS),
        ("About you", SectionKind.REQUIREMENTS),
        ("Responsibilities", SectionKind.RESPONSIBILITIES),
        ("What you'll do", SectionKind.RESPONSIBILITIES),
        ("The role", SectionKind.RESPONSIBILITIES),
        ("Nice to have", SectionKind.NICE_TO_HAVE),
        ("Bonus points", SectionKind.NICE_TO_HAVE),
        ("About us", SectionKind.BOILERPLATE),
        ("Benefits", SectionKind.BOILERPLATE),
        ("How to apply", SectionKind.BOILERPLATE),
    ],
)
def test_headings_classify(heading, expected):
    assert classify_heading(heading) is expected


def test_preferred_qualifications_is_optional_not_required():
    """It contains 'qualifications', but it means nice-to-have."""
    assert classify_heading("Preferred qualifications") is SectionKind.NICE_TO_HAVE


def test_unrecognised_heading_still_ends_the_previous_section():
    """Regression: content under an unknown heading used to inherit the
    previous section's weight. On a real posting that scored the required
    tech stack under "Tech you'll touch" as nice-to-have."""
    text = (
        "Nice to have\n"
        "- Kubernetes\n"
        "\n"
        "Tech you'll touch\n"
        "Python, PyTorch, Docker\n"
    )
    sections = split_sections(text)
    kinds = {s.heading: s.kind for s in sections}

    assert kinds["Nice to have"] is SectionKind.NICE_TO_HAVE
    assert kinds["Tech you'll touch"] is SectionKind.OTHER
    assert "PyTorch" not in next(s.text for s in sections if s.kind is SectionKind.NICE_TO_HAVE)


def test_text_before_the_first_heading_is_kept():
    """Postings often open with the most important sentence in the document."""
    sections = split_sections("Senior AI Engineer, London\n\nRequirements\n- Python\n")
    assert sections[0].kind is SectionKind.OTHER
    assert "Senior AI Engineer" in sections[0].text


def test_a_document_with_no_headings_is_one_section():
    sections = split_sections("We want someone who knows Python and Docker.")
    assert len(sections) == 1
    assert sections[0].kind is SectionKind.OTHER


def test_bullets_are_not_mistaken_for_headings():
    sections = split_sections("Requirements\n- Python\n- Docker\n")
    assert len(sections) == 1
    assert "- Python" in sections[0].text


def test_real_fixture_splits_as_expected(jd):
    kinds = [s.kind for s in split_sections(jd)]
    assert SectionKind.REQUIREMENTS in kinds
    assert SectionKind.RESPONSIBILITIES in kinds
    assert SectionKind.NICE_TO_HAVE in kinds
    assert SectionKind.BOILERPLATE in kinds


def test_sections_cover_the_document_without_overlap(jd):
    sections = split_sections(jd)
    for previous, following in zip(sections, sections[1:]):
        assert previous.end <= following.start
