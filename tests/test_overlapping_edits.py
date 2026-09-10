"""Two accepted suggestions must never write the same span.

Relocating a skill emits an edit against its whole skills line. Once the
relocation rule loosened, a posting naming two skills from the same line
produced two edits against that one span, and accepting both raised
OverlappingEdits -- a 500 the moment the user pressed Tailor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.analysis.match import Match, MatchStatus, ResumeLocation
from aicvtailor.analysis.pipeline import RankedTerm
from aicvtailor.analysis.sections import SectionKind
from aicvtailor.analysis.terms import Mention, Term
from aicvtailor.analysis.weight import score
from aicvtailor.latex import parse
from aicvtailor.suggest import applicable, generate
from aicvtailor.tailor.pipeline import tailor

FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"
RAILS = {"forbidden_claims": [], "never_reword": [], "max_pages": None,
         "max_bullet_length": 240, "forbid_new_entities": True, "entity_allowlist": []}


@pytest.fixture
def document():
    return parse(FIXTURE.read_text(encoding="utf-8"))


def skills_term(name: str) -> RankedTerm:
    t = Term(
        canonical=name,
        category="tool",
        mentions=[Mention(surface=name, section=SectionKind.REQUIREMENTS, required=True, start=0)],
    )
    return RankedTerm(
        term=t,
        weight=score(t),
        match=Match(
            term=name,
            status=MatchStatus.PRESENT_EXACT,
            location=ResumeLocation.SKILLS,
        ),
    )


class TestRelocationsAreMerged:
    def test_two_skills_from_one_line_produce_one_suggestion(self, document):
        line = next(iter(document.skill_lines()))
        assert len(line.values) >= 3

        suggestions = applicable(
            generate(
                [skills_term(line.values[2]), skills_term(line.values[1])],
                document,
                provider=None,
                rails=RAILS,
            )
        )
        assert len(suggestions) == 1, "one span, one edit"
        assert len({s.target_id for s in suggestions}) == 1

    def test_the_merged_suggestion_promotes_both(self, document):
        line = next(iter(document.skill_lines()))
        wanted = [line.values[2], line.values[1]]

        suggestion = applicable(
            generate([skills_term(w) for w in wanted], document, provider=None, rails=RAILS)
        )[0]
        promoted = [v.strip() for v in suggestion.proposed_text.lstrip(":").split(",")]

        assert promoted[: len(wanted)] == wanted

    def test_nothing_is_added_or_dropped(self, document):
        line = next(iter(document.skill_lines()))
        suggestion = applicable(
            generate(
                [skills_term(line.values[-1])], document, provider=None, rails=RAILS
            )
        )[0]

        before = {v.strip() for v in suggestion.original_text.lstrip(":").split(",")}
        after = {v.strip() for v in suggestion.proposed_text.lstrip(":").split(",")}
        assert before == after

    def test_accepting_every_suggestion_never_overlaps(self, document):
        line = next(iter(document.skill_lines()))
        suggestions = applicable(
            generate(
                [skills_term(v) for v in line.values], document, provider=None, rails=RAILS
            )
        )
        targets = [s.target_id for s in suggestions]
        assert len(targets) == len(set(targets))


class TestTailorGuardsAgainstDuplicates:
    def test_a_duplicate_span_is_dropped_not_raised(self, document):
        """Defence in depth: even if two suggestions somehow target one span,
        the run must degrade rather than 500."""
        line = next(iter(document.skill_lines()))
        original = line.values_span.text(document.source)

        result = tailor(
            document,
            [
                {"target_id": line.id, "term": "A", "source_bullet_id": None,
                 "proposed_text": ": first"},
                {"target_id": line.id, "term": "B", "source_bullet_id": None,
                 "proposed_text": ": second"},
            ],
            rails=RAILS,
        )

        assert len(result.changes) == 1
        assert any("only be written once" in w for w in result.warnings)
        assert ": first" in result.tex

    def test_no_latex_engine_still_produces_a_tailored_tex(self, document, monkeypatch):
        """The user hitting this had no LaTeX installed."""
        from aicvtailor.config import reload_config

        monkeypatch.setenv("LATEX_ENGINE", "none")
        reload_config()

        bullet = next(iter(document.bullets()))
        result = tailor(
            document,
            [{"target_id": bullet.id, "term": "x", "source_bullet_id": bullet.id,
              "proposed_text": bullet.text + " extended"}],
            rails=RAILS,
        )

        assert result.changes
        assert "extended" in result.tex
        assert not result.compiled
        assert any("No LaTeX engine" in w for w in result.warnings)
        reload_config()
