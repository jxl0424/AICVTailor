"""The tailoring run: diff, traceability, coverage, and what must not happen."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.latex import parse
from aicvtailor.tailor.compile import detect_engine
from aicvtailor.tailor.diff import word_diff
from aicvtailor.tailor.pipeline import recompute_coverage, tailor

FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"
needs_latex = pytest.mark.skipif(detect_engine() is None, reason="no LaTeX engine installed")


@pytest.fixture
def source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def doc(source: str):
    return parse(source)


def accept(target_id: str, text: str, term: str, source_bullet_id: str | None = None) -> dict:
    return {
        "target_id": target_id,
        "proposed_text": text,
        "term": term,
        "source_bullet_id": source_bullet_id or target_id,
    }


class TestWordDiff:
    def test_marks_only_what_changed(self):
        pieces = word_diff("Built a retrieval pipeline", "Designed a retrieval pipeline")
        assert [p.kind for p in pieces if p.kind != "equal"] == ["delete", "insert"]

    def test_identical_text_produces_no_changes(self):
        assert all(p.kind == "equal" for p in word_diff("same text", "same text"))

    def test_whitespace_is_preserved_so_rendering_is_faithful(self):
        assert "".join(p.text for p in word_diff("a b", "a b") if p.kind != "delete") == "a b"

    def test_an_insertion_is_attributed_to_the_new_text(self):
        pieces = word_diff("vector search", "Qdrant vector search")
        assert any(p.kind == "insert" and "Qdrant" in p.text for p in pieces)


class TestApplication:
    def test_a_row_with_no_target_is_dropped_not_applied(self, doc):
        """A GAP cannot reach here through the API. If it does, it is skipped."""
        result = tailor(doc, [{"term": "Kubernetes", "target_id": None, "proposed_text": None}])

        assert result.changes == []
        assert any("nothing to apply" in w for w in result.warnings)
        assert "Kubernetes" not in result.tex

    def test_a_target_outside_the_editable_spans_is_refused(self, doc):
        result = tailor(doc, [accept("s2.e0", "Fabricated University", "X")])

        assert result.changes == []
        assert any("not an editable span" in w for w in result.warnings)

    def test_header_text_is_never_altered(self, doc, source):
        """Belt and braces with the Phase 1 structural guarantee."""
        result = tailor(doc, [accept("s2.e0", "Somewhere Else", "X")])
        for entry in doc.entries():
            for field in entry.fields:
                if field.text.strip():
                    assert field.text in result.tex

    @needs_latex
    def test_every_change_is_traceable_to_a_source_bullet(self, doc):
        bullets = list(doc.bullets())[:2]
        result = tailor(
            doc, [accept(b.id, b.text + " and more", "retrieval") for b in bullets]
        )

        assert len(result.changes) == 2
        for change in result.changes:
            assert change.source_bullet_id
            assert change.target_terms == ["retrieval"]

    @needs_latex
    def test_changes_carry_their_section_and_entry(self, doc):
        bullet = next(iter(doc.bullets()))
        result = tailor(doc, [accept(bullet.id, bullet.text + " extended", "X")])

        change = result.changes[0]
        assert change.section
        assert change.entry
        assert "\\textbf" not in change.entry, "entry label should be display text"

    @needs_latex
    def test_an_uncompilable_edit_is_reverted_and_reported(self, doc):
        bullet = next(iter(doc.bullets()))
        result = tailor(doc, [accept(bullet.id, r"\undefinedmacro", "X")])

        assert result.compiled
        assert result.reverted
        assert any("reverted the edit" in w for w in result.warnings)
        assert r"\undefinedmacro" not in result.tex


class TestCoverage:
    def test_a_term_written_into_the_tex_gains_full_credit(self):
        terms = [{"term": "Kubernetes", "weight": 1.0, "status": "missing"}]
        before, after = recompute_coverage(terms, "we now mention Kubernetes here")

        assert before == 0.0
        assert after == 100.0

    def test_a_term_still_absent_keeps_its_old_credit(self):
        terms = [{"term": "Kubernetes", "weight": 1.0, "status": "missing"}]
        before, after = recompute_coverage(terms, "nothing relevant here")
        assert before == after == 0.0

    def test_a_reverted_edit_does_not_improve_the_number(self):
        """Coverage is recomputed from the final text, not from what was
        accepted, so a compile-gate revert cannot inflate it."""
        terms = [{"term": "Kubernetes", "weight": 1.0, "status": "missing"}]
        _, after = recompute_coverage(terms, "the edit was reverted, so no mention")
        assert after == 0.0

    def test_implied_terms_start_at_half_credit(self):
        terms = [{"term": "Kubernetes", "weight": 1.0, "status": "implied"}]
        before, _ = recompute_coverage(terms, "")
        assert before == 50.0

    def test_no_terms_does_not_divide_by_zero(self):
        assert recompute_coverage([], "text") == (0.0, 0.0)


@needs_latex
class TestVerificationIntegration:
    def test_the_run_confirms_terms_reached_the_pdf_text(self, doc):
        bullet = next(iter(doc.bullets()))
        result = tailor(
            doc, [accept(bullet.id, bullet.text + " using Qdrant", "Qdrant")]
        )

        assert result.compiled
        assert "Qdrant" in result.verification.surviving_terms

    def test_the_page_limit_is_surfaced_as_a_warning(self, doc):
        bullet = next(iter(doc.bullets()))
        result = tailor(
            doc,
            [accept(bullet.id, bullet.text, "X")],
            rails={"max_pages": 0, "forbidden_claims": [], "never_reword": []},
        )
        assert any("over the configured limit" in w for w in result.warnings)

    def test_a_forbidden_claim_in_the_output_is_caught_at_document_level(self, doc):
        bullet = next(iter(doc.bullets()))
        result = tailor(
            doc,
            [accept(bullet.id, "Delivered Project Halberd end to end", "X")],
            rails={
                "forbidden_claims": ["Project Halberd"],
                "never_reword": [],
                "max_pages": None,
            },
        )
        assert not result.document_guardrails["ok"]
        assert any("forbidden claim" in w for w in result.warnings)
