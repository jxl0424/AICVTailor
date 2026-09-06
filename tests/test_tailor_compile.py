"""Compilation, the compile gate, and PDF verification.

These run against a real LaTeX engine when one is installed, and skip when it
is not -- the same degradation the app itself does. A tailored .tex that does
not compile is a bug, so the gate is the thing under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.latex import parse
from aicvtailor.latex.regenerate import Edit
from aicvtailor.tailor.compile import compile_tex, compile_with_gate, detect_engine
from aicvtailor.tailor.verify import extract_text, verify

FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"
MASTER = Path(__file__).parents[1] / "data" / "master" / "master.tex"

needs_latex = pytest.mark.skipif(
    detect_engine() is None, reason="no LaTeX engine installed"
)


@pytest.fixture
def source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def doc(source: str):
    return parse(source)


class TestEngineDetection:
    def test_reports_an_engine_or_none_without_raising(self):
        engine = detect_engine()
        assert engine is None or engine in {"tectonic", "latexmk", "pdflatex"}

    def test_configured_none_disables_compilation(self, monkeypatch):
        from aicvtailor.config import reload_config

        monkeypatch.setenv("LATEX_ENGINE", "none")
        reload_config()
        assert detect_engine() is None
        reload_config()

    def test_compiling_without_an_engine_is_skipped_not_failed(self):
        result = compile_tex("anything", engine=None)
        if result.skipped:
            assert not result.ok
            assert "skipped" in result.detail


@needs_latex
class TestCompilation:
    def test_the_fixture_compiles(self, source):
        result = compile_tex(source)
        assert result.ok, result.error
        assert result.pdf_bytes
        assert result.pages >= 1

    def test_broken_latex_fails_with_a_usable_message(self):
        result = compile_tex(r"\documentclass{article}\begin{document}\undefinedmacro\end{document}")
        assert not result.ok
        assert "Undefined control sequence" in result.error

    def test_an_unbalanced_brace_is_reported(self):
        result = compile_tex(r"\documentclass{article}\begin{document}\textbf{unclosed\end{document}")
        assert not result.ok
        assert result.error


@needs_latex
class TestCompileGate:
    def test_clean_edits_compile_and_are_all_applied(self, doc, source):
        edits = [doc.edit(b.id, b.text + " and more detail") for b in list(doc.bullets())[:2]]
        gated = compile_with_gate(source, edits)

        assert gated.result.ok
        assert len(gated.applied) == 2
        assert gated.reverted == []

    def test_a_broken_edit_is_reverted_individually(self, doc, source):
        """The whole point of the gate: one bad bullet must not cost the run.

        The edit bypasses the sanitizer deliberately -- this is the last line
        of defence for anything that gets past it.
        """
        bullets = list(doc.bullets())
        good = doc.edit(bullets[0].id, bullets[0].text + " and more detail")
        bad = Edit(
            start=doc.editable_spans()[bullets[1].id].start,
            end=doc.editable_spans()[bullets[1].id].end,
            new_text=r"broken \undefinedmacro here",
            target_id=bullets[1].id,
        )

        gated = compile_with_gate(source, [good, bad])

        assert gated.result.ok, "the surviving document must still compile"
        assert [e.target_id for e in gated.applied] == [bullets[0].id]
        assert [e.target_id for e, _ in gated.reverted] == [bullets[1].id]
        assert "undefined" in gated.reverted[0][1].lower()

    def test_the_reverted_bullet_keeps_its_original_text(self, doc, source):
        bullets = list(doc.bullets())
        bad = Edit(
            start=doc.editable_spans()[bullets[0].id].start,
            end=doc.editable_spans()[bullets[0].id].end,
            new_text=r"\undefinedmacro",
            target_id=bullets[0].id,
        )
        gated = compile_with_gate(source, [bad])

        assert gated.tex == source
        assert r"\undefinedmacro" not in gated.tex

    def test_no_edits_leaves_the_document_byte_identical(self, source):
        gated = compile_with_gate(source, [])
        assert gated.tex == source
        assert gated.result.ok


class TestVerification:
    @needs_latex
    def test_terms_in_the_tex_are_confirmed_in_the_extracted_text(self, source):
        result = compile_tex(source)
        report = verify(result.pdf_bytes, target_terms=["FrameworkB", "LangA"])

        assert report.surviving_terms
        assert report.lost_terms == []

    @needs_latex
    def test_a_term_absent_from_the_pdf_is_reported_lost(self, source):
        result = compile_tex(source)
        report = verify(result.pdf_bytes, target_terms=["Kubernetes"])

        assert report.lost_terms == ["Kubernetes"]
        assert not report.ok
        assert any("not in the extracted PDF text" in n for n in report.notes)

    @needs_latex
    def test_the_page_limit_is_enforced(self, source):
        result = compile_tex(source)
        report = verify(result.pdf_bytes, max_pages=0)

        assert not report.page_limit_ok
        assert any("over the configured limit" in n for n in report.notes)

    @needs_latex
    def test_growing_past_the_baseline_is_reported(self, source):
        result = compile_tex(source)
        report = verify(result.pdf_bytes, baseline_pages=1)
        if result.pages > 1:
            assert report.grew

    @needs_latex
    def test_forbidden_claims_are_rechecked_on_the_final_pdf_text(self, source):
        """This is the text that actually leaves the machine."""
        result = compile_tex(source)
        report = verify(result.pdf_bytes, forbidden_claims=["Placeholder Project One"])

        assert report.forbidden_hits
        assert not report.ok

    def test_no_pdf_means_verification_is_skipped_not_failed(self):
        report = verify(None, target_terms=["anything"])
        assert report.ok and report.skipped

    def test_extraction_normalises_whitespace(self):
        """A term split across a line break must still be findable."""
        assert extract_text(b"not a pdf") == ""


@needs_latex
@pytest.mark.skipif(not MASTER.exists(), reason="no master.tex in data/master/")
class TestAgainstTheRealCV:
    def test_the_real_master_compiles(self):
        result = compile_tex(MASTER.read_text(encoding="utf-8"))
        assert result.ok, result.error
        assert result.pages >= 1

    def test_every_bullet_can_be_rewritten_and_still_compile(self):
        """The strongest end-to-end statement Phase 1 and Phase 5 make together."""
        source = MASTER.read_text(encoding="utf-8")
        document = parse(source)
        edits = [document.edit(b.id, b.text) for b in document.bullets()]

        gated = compile_with_gate(source, edits)
        assert gated.result.ok
        assert gated.tex == source
        assert gated.reverted == []
