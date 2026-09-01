"""Parser and scanner behaviour on the real template's quirks."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.latex import parse
from aicvtailor.latex.scanner import comment_mask, find_calls, read_group

FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"
REAL_MASTER = Path(__file__).parents[1] / "data" / "master" / "master.tex"


@pytest.fixture
def source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def doc(source: str):
    return parse(source)


class TestScanner:
    def test_comment_mask_marks_only_comment_text(self):
        src = "a % hidden\nb"
        mask = comment_mask(src)
        assert mask[0] == 0
        assert mask[src.index("hidden")] == 1
        assert mask[src.index("b")] == 0

    def test_escaped_percent_does_not_start_a_comment(self):
        src = r"gained 26\% recall"
        assert sum(comment_mask(src)) == 0

    def test_nested_braces_are_balanced(self):
        src = r"\textbf{outer {inner} rest}"
        lo, hi, after = read_group(src, src.index("{"), comment_mask(src))
        assert src[lo:hi] == "outer {inner} rest"
        assert after == len(src)

    def test_escaped_braces_do_not_affect_depth(self):
        src = r"\textbf{a \{ b}"
        lo, hi, _ = read_group(src, src.index("{"), comment_mask(src))
        assert src[lo:hi] == r"a \{ b"

    def test_unbalanced_group_returns_none(self):
        src = r"\textbf{never closed"
        assert read_group(src, src.index("{"), comment_mask(src)) is None

    def test_macro_names_are_not_prefix_matched(self):
        """`\\resumeItem` must not match inside `\\resumeItemListStart`."""
        src = r"\resumeItemListStart \resumeItem{real}"
        calls = find_calls(src, "resumeItem", 1)
        assert len(calls) == 1
        assert calls[0].arg_text(src, 0) == "real"

    def test_arguments_may_be_separated_by_newlines(self):
        """The real CV puts the heading macro and its arguments on separate
        lines throughout."""
        src = "\\resumeSubheading\n  {A}{B}\n  {C}{D}"
        calls = find_calls(src, "resumeSubheading", 4)
        assert [calls[0].arg_text(src, i) for i in range(4)] == ["A", "B", "C", "D"]

    def test_empty_arguments_are_valid(self):
        """The Experience entry ends with a genuinely empty `{}`."""
        src = r"\resumeSubheading{A}{B}{C}{}"
        calls = find_calls(src, "resumeSubheading", 4)
        assert calls[0].arg_text(src, 3) == ""


class TestStructure:
    def test_finds_every_section(self, doc):
        titles = [s.title for s in doc.sections]
        assert titles == [
            "Professional Summary",
            "Projects",
            "Education",
            "Experience",
            "Relevant Coursework",
            "Technical Skills",
        ]

    def test_bullets_attach_to_the_entry_above_them(self, doc):
        for entry in doc.entries():
            for bullet in entry.bullets:
                assert bullet.span.start > entry.span.start
                assert bullet.id.startswith(entry.id + ".b")

    def test_bullet_ids_are_unique(self, doc):
        ids = [b.id for b in doc.bullets()]
        assert len(ids) == len(set(ids))

    def test_leading_whitespace_inside_an_argument_is_kept(self, doc):
        """One bullet in the real CV opens with a space. Normalising it would
        break the byte-identical round trip."""
        assert any(b.text.startswith(" ") for b in doc.bullets())

    def test_protected_tokens_are_captured_on_bullets(self, doc):
        marked = [b for b in doc.bullets() if b.protected]
        assert marked, "fixture has a bullet with \\textbf and $|$"
        assert any(r"\textbf{bold text}" in b.protected for b in marked)

    def test_skill_lines_split_into_values(self, doc):
        lines = {k.label: k.values for k in doc.skill_lines()}
        assert lines["Languages"] == ("LangA", "LangB", "LangC")

    def test_escaped_ampersand_in_a_skill_label_survives(self, doc):
        assert any(r"Data \& Storage" == k.label for k in doc.skill_lines())


class TestHeaderFieldOrdering:
    """The template's four-argument heading is used with different meanings in
    different sections, so field roles are positional and only guessed."""

    def test_education_and_experience_disagree_on_argument_order(self, doc):
        education = next(s for s in doc.sections if s.title == "Education")
        experience = next(s for s in doc.sections if s.title == "Experience")

        edu_roles = [f.role_guess for f in education.entries[0].fields]
        exp_roles = [f.role_guess for f in experience.entries[0].fields]

        assert edu_roles[3] == "dates"  # institution, location, degree, dates
        assert exp_roles[1] == "dates"  # role, dates, employer, empty
        assert exp_roles[3] == "empty"
        assert edu_roles != exp_roles


@pytest.mark.skipif(not REAL_MASTER.exists(), reason="no master.tex in data/master/")
class TestRealMaster:
    def test_parses_the_expected_shape(self):
        doc = parse(REAL_MASTER.read_text(encoding="utf-8"))
        assert [s.title for s in doc.sections] == [
            "Professional Summary",
            "Projects",
            "Education",
            "Experience",
            "Relevant Coursework",
            "Technical Skills",
        ]
        assert len(list(doc.entries())) == 7
        assert len(list(doc.bullets())) == 16
        assert len(list(doc.skill_lines())) == 4

    def test_no_bullet_text_is_empty(self):
        doc = parse(REAL_MASTER.read_text(encoding="utf-8"))
        for bullet in doc.bullets():
            assert bullet.text.strip()
