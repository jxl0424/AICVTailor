"""Dictionary matching, synonym collapse, and discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicvtailor.analysis.sections import split_sections
from aicvtailor.analysis.terms import (
    SkillDictionary,
    discover_unknown_terms,
    extract_dictionary_terms,
)

JD = Path(__file__).parent / "fixtures" / "jd_ai_engineer.txt"


@pytest.fixture
def dictionary() -> SkillDictionary:
    return SkillDictionary()


@pytest.fixture
def sections():
    return split_sections(JD.read_text(encoding="utf-8"))


class TestNormalisation:
    def test_synonyms_collapse_to_one_canonical_term(self, dictionary):
        for surface in ("PyTorch", "torch", "Py Torch", "PYTORCH"):
            assert dictionary.normalise(surface) == "PyTorch"

    def test_multi_word_synonyms_collapse(self, dictionary):
        assert dictionary.normalise("model evals") == "LLM evaluation"
        assert dictionary.normalise("LLM evaluation") == "LLM evaluation"

    def test_unknown_phrases_normalise_to_nothing(self, dictionary):
        assert dictionary.normalise("interpretive dance") is None

    def test_longest_match_wins(self, dictionary):
        """'LLM evaluation' must not be shredded into a shorter entry."""
        found = list(dictionary.find("we do LLM evaluation daily"))
        assert [c for c, _, _ in found] == ["LLM evaluation"]

    def test_matching_respects_word_boundaries(self, dictionary):
        """'React' must not match inside 'reactive'."""
        assert not list(dictionary.find("a reactive system"))

    def test_technical_punctuation_survives(self, dictionary):
        assert [c for c, _, _ in dictionary.find("we use CI/CD here")] == ["CI/CD"]


class TestExtraction:
    def test_finds_the_terms_a_real_posting_states(self, sections, dictionary):
        found = set(extract_dictionary_terms(sections, dictionary))
        for expected in {"Python", "Docker", "Retrieval-Augmented Generation"}:
            assert expected in found

    def test_records_which_section_each_mention_came_from(self, sections, dictionary):
        terms = extract_dictionary_terms(sections, dictionary)
        python = terms["Python"]
        assert python.frequency >= 1
        assert python.sections

    def test_qdrant_collapses_into_vector_database(self, sections, dictionary):
        """The posting names three vector stores; they are one requirement."""
        terms = extract_dictionary_terms(sections, dictionary)
        assert "Vector database" in terms
        surfaces = terms["Vector database"].surfaces
        assert "qdrant" in surfaces


class TestDiscovery:
    def test_surfaces_repeated_terms_the_dictionary_lacks(self, sections, dictionary):
        discovered = {t.canonical for t in discover_unknown_terms(sections, dictionary)}
        assert "llm" in discovered or "nlp" in discovered

    def test_generic_posting_filler_is_not_suggested(self, sections, dictionary):
        """Regression: the first run suggested 'systems', 'days' and
        'including', which buried the two or three real misses."""
        discovered = {t.canonical for t in discover_unknown_terms(sections, dictionary)}
        for noise in ("systems", "days", "including", "built", "end", "team"):
            assert noise not in discovered

    def test_terms_already_in_the_dictionary_are_not_suggested(self, sections, dictionary):
        discovered = {t.canonical for t in discover_unknown_terms(sections, dictionary)}
        assert "python" not in discovered
        assert "docker" not in discovered

    def test_a_one_off_mention_is_not_suggested(self, dictionary):
        sections = split_sections("We use Frobnicator once.")
        assert discover_unknown_terms(sections, dictionary, min_frequency=2) == []


class TestShippedDictionary:
    def test_no_synonym_is_claimed_by_two_canonical_terms(self):
        """Ambiguity would make normalisation depend on iteration order."""
        dictionary = SkillDictionary()
        owner: dict[str, str] = {}
        for canonical, synonyms in dictionary.synonyms_of.items():
            for synonym in synonyms:
                key = synonym.lower()
                assert key not in owner, f"'{synonym}': {owner[key]} vs {canonical}"
                owner[key] = canonical

    def test_every_canonical_term_has_a_known_category(self):
        allowed = {"hard_skill", "tool", "method", "domain", "soft_skill"}
        assert set(SkillDictionary().categories.values()) <= allowed
