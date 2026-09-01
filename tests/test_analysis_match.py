"""Matching JD terms to the resume.

The rule that matters: a term the resume does not support must come back
`missing`. A false `implied` understates a real gap, which is the failure mode
that costs an interview.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.analysis.match import (
    IMPLIED_THRESHOLD,
    MatchStatus,
    ResumeIndex,
    ResumeLocation,
    match_term,
)
from aicvtailor.analysis.semantic import LexicalIndex
from aicvtailor.analysis.terms import Mention, SkillDictionary, Term
from aicvtailor.analysis.sections import SectionKind
from aicvtailor.latex import parse

MASTER = Path(__file__).parents[1] / "data" / "master" / "master.tex"
FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"


def make_term(canonical: str, category: str = "tool") -> Term:
    return Term(
        canonical=canonical,
        category=category,
        mentions=[Mention(surface=canonical, section=SectionKind.REQUIREMENTS, required=True, start=0)],
    )


@pytest.fixture
def dictionary() -> SkillDictionary:
    return SkillDictionary()


@pytest.fixture
def similarity() -> LexicalIndex:
    return LexicalIndex()


@pytest.fixture
def index():
    return ResumeIndex.from_document(parse(FIXTURE.read_text(encoding="utf-8")))


class TestLexicalSimilarity:
    def test_related_wording_scores_high(self, similarity):
        assert similarity.similarity(
            "vector search", "Semantic search over a dense vector index"
        ) > IMPLIED_THRESHOLD

    def test_unrelated_text_scores_zero(self, similarity):
        assert similarity.similarity("Kubernetes", "Taught a Python course") == 0.0

    def test_similar_looking_but_different_tools_do_not_match(self, similarity):
        """Kubernetes and Kubeflow share a prefix and nothing else. A fuzzy
        ratio alone would call them close and invent an implied skill."""
        assert similarity.similarity("Kubernetes", "Deployed models with Kubeflow") < IMPLIED_THRESHOLD

    def test_empty_input_is_safe(self, similarity):
        assert similarity.similarity("", "text") == 0.0
        assert similarity.similarity("term", "") == 0.0


class TestMatching:
    def test_a_term_in_a_bullet_is_present_exact(self, index, dictionary, similarity):
        match = match_term(make_term("LangA"), index, dictionary, similarity)
        assert match.status is MatchStatus.PRESENT_EXACT

    def test_a_term_only_in_the_skills_block_is_flagged_as_such(
        self, index, dictionary, similarity
    ):
        """Present for keyword purposes, but not evidenced by experience.
        This distinction is what RELOCATE acts on later."""
        match = match_term(make_term("FrameworkB"), index, dictionary, similarity)
        assert match.status is MatchStatus.PRESENT_EXACT
        assert match.location is ResumeLocation.SKILLS

    def test_a_synonym_hit_is_reported_as_a_synonym(self, dictionary, similarity):
        index = ResumeIndex(bullets=(("b0", "Built pipelines with torch"),), skills=(), other=())
        match = match_term(make_term("PyTorch"), index, dictionary, similarity)

        assert match.status is MatchStatus.PRESENT_AS_SYNONYM
        assert "torch" in match.evidence

    def test_an_absent_term_is_missing(self, index, dictionary, similarity):
        match = match_term(make_term("Kubernetes"), index, dictionary, similarity)
        assert match.status is MatchStatus.MISSING
        assert match.bullet_id is None

    def test_implied_always_names_the_bullet_that_justified_it(self, dictionary, similarity):
        """Traceability: nothing may claim experience without pointing at its
        source bullet."""
        # The bullet must not contain the phrase literally, or this is just an
        # exact match dressed up as a semantic one.
        bullet = "Semantic search over a dense vector index of papers"
        assert "vector search" not in bullet
        index = ResumeIndex(bullets=(("s1.e0.b0", bullet),), skills=(), other=())
        match = match_term(make_term("vector search"), index, dictionary, similarity)

        assert match.status is MatchStatus.IMPLIED
        assert match.bullet_id == "s1.e0.b0"
        assert match.score >= IMPLIED_THRESHOLD

    def test_word_boundaries_prevent_a_substring_false_positive(self, dictionary, similarity):
        index = ResumeIndex(bullets=(("b0", "Wrote reactive UI code"),), skills=(), other=())
        assert match_term(make_term("React"), index, dictionary, similarity).status is (
            MatchStatus.MISSING
        )

    def test_technical_punctuation_matches(self, dictionary, similarity):
        index = ResumeIndex(bullets=(("b0", "Wrote C/C++ firmware"),), skills=(), other=())
        assert match_term(make_term("C/C++"), index, dictionary, similarity).status is (
            MatchStatus.PRESENT_EXACT
        )


@pytest.mark.skipif(not MASTER.exists(), reason="no master.tex in data/master/")
class TestAgainstTheRealResume:
    def test_a_skill_the_cv_does_not_mention_stays_missing(self, dictionary, similarity):
        """The anti-fabrication guarantee, checked on the real document."""
        index = ResumeIndex.from_document(parse(MASTER.read_text(encoding="utf-8")))

        for absent in ("Kubernetes", "Terraform", "Rust", "Salesforce"):
            match = match_term(make_term(absent), index, dictionary, similarity)
            assert match.status is MatchStatus.MISSING, (
                f"{absent} is not in the CV but was reported {match.status.value}"
            )

    def test_indexing_covers_bullets_and_skills(self, dictionary):
        index = ResumeIndex.from_document(parse(MASTER.read_text(encoding="utf-8")))
        assert len(index.bullets) == 16
        assert len(index.skills) == 4
