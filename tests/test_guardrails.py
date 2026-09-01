"""Guardrail enforcement.

Every test here proves the CODE catches a violation. Where a model is involved
the prompt is replaced with one that says nothing about the rules -- and in
several cases actively encourages breaking them -- so a pass cannot be
credited to the model behaving itself.
"""

from __future__ import annotations

import pytest

from aicvtailor.guardrails import (
    GuardrailReport,
    check_forbidden_claims,
    check_length,
    check_never_reword,
    check_new_entities,
    check_protected_tokens,
    enforce,
    enforce_document,
)

RAILS = {
    "forbidden_claims": [],
    "never_reword": [],
    "max_bullet_length": 240,
    "forbid_new_entities": True,
    "entity_allowlist": [],
}


def rails(**overrides):
    return {**RAILS, **overrides}


class TestNewEntities:
    """The main anti-fabrication check."""

    def test_an_invented_metric_is_caught(self):
        violations = check_new_entities(
            "Built a retrieval pipeline over research papers",
            "Built a retrieval pipeline that improved recall by 40%",
            rails(),
        )
        assert any("40" in v.offending for v in violations)

    def test_a_changed_metric_is_caught(self):
        """Turning 26 tests into 60 is fabrication, not rewording."""
        violations = check_new_entities(
            "Wrote 26 automated tests", "Wrote 60 automated tests", rails()
        )
        assert any(v.offending == "60" for v in violations)

    def test_an_invented_employer_is_caught(self):
        violations = check_new_entities(
            "Taught an AI curriculum to a student",
            "Taught an AI curriculum at Deepmind",
            rails(),
        )
        assert any("Deepmind" in v.offending for v in violations)

    def test_an_invented_date_is_caught(self):
        violations = check_new_entities(
            "Led the migration", "Led the migration in 2019", rails()
        )
        assert any(v.offending == "2019" for v in violations)

    def test_an_invented_tool_is_caught(self):
        violations = check_new_entities(
            "Deployed the service in containers",
            "Deployed the service on Kubernetes",
            rails(),
        )
        assert any("Kubernetes" in v.offending for v in violations)

    def test_an_invented_team_size_is_caught(self):
        violations = check_new_entities(
            "Led the project", "Led a team of 8 on the project", rails()
        )
        assert any(v.offending == "8" for v in violations)

    def test_genuine_rewording_passes(self):
        assert (
            check_new_entities(
                "Built a hybrid retrieval pipeline combining vector search and graphs",
                "Designed a hybrid retrieval pipeline that combines graphs with vector search",
                rails(),
            )
            == []
        )

    def test_dropping_a_figure_is_allowed(self):
        """Only new entities are violations. Losing one is the user's call."""
        assert check_new_entities("Improved recall by 26%", "Improved recall", rails()) == []

    def test_the_target_term_may_be_introduced(self):
        """Surfacing a term a named bullet already evidences is the point of a
        REWORD, so the term itself is permitted."""
        assert (
            check_new_entities(
                "Combined dense vector search with graph retrieval",
                "Combined a vector database with graph retrieval",
                rails(),
                allowed={"vector database"},
            )
            == []
        )

    def test_a_target_term_does_not_license_other_inventions(self):
        violations = check_new_entities(
            "Combined dense vector search with graphs",
            "Combined a vector database with graphs at Acme, cutting latency 30%",
            rails(),
            allowed={"vector database"},
        )
        offending = {v.offending for v in violations}
        assert "Acme" in offending
        assert "30%" in offending or "30" in offending

    def test_sentence_initial_capitals_are_not_treated_as_names(self):
        assert check_new_entities("built the thing", "Built the thing", rails()) == []

    def test_the_allowlist_is_respected(self):
        assert (
            check_new_entities(
                "Ran the pipeline", "Ran the pipeline 3 times", rails(entity_allowlist=["3"])
            )
            == []
        )

    def test_the_check_can_be_disabled_deliberately(self):
        assert (
            check_new_entities(
                "Ran it", "Ran it at Acme in 2019", rails(forbid_new_entities=False)
            )
            == []
        )


class TestForbiddenClaims:
    def test_a_forbidden_claim_is_caught(self):
        violations = check_forbidden_claims(
            "Worked on Project Halberd", rails(forbidden_claims=["Project Halberd"])
        )
        assert violations and violations[0].rule == "forbidden_claim"

    def test_punctuation_and_casing_cannot_smuggle_it_through(self):
        """Naive substring matching misses 'project  halberd.'"""
        for variant in ("project halberd", "Project  Halberd.", "PROJECT HALBERD!"):
            assert check_forbidden_claims(
                f"Worked on {variant}", rails(forbidden_claims=["Project Halberd"])
            )

    def test_regex_entries_are_supported(self):
        assert check_forbidden_claims(
            "Holds SC cleared status",
            rails(forbidden_claims=[r"re:\b(security clearance|SC cleared)\b"]),
        )

    def test_a_broken_regex_does_not_crash_the_run(self):
        assert check_forbidden_claims("anything", rails(forbidden_claims=["re:[unclosed"])) == []

    def test_unrelated_text_passes(self):
        assert check_forbidden_claims(
            "Built a search system", rails(forbidden_claims=["Project Halberd"])
        ) == []

    def test_the_document_level_check_catches_it_too(self):
        """A claim can be assembled across edits that each looked innocent."""
        report = enforce_document(
            r"\resumeItem{Project Halberd delivery}", rails(forbidden_claims=["Project Halberd"])
        )
        assert not report.ok


class TestNeverReword:
    def test_altering_a_protected_string_is_caught(self):
        violations = check_never_reword(
            "Achieved ISO 27001 certification",
            "Achieved ISO27001 certification",
            rails(never_reword=["ISO 27001"]),
        )
        assert violations and violations[0].rule == "never_reword"

    def test_removing_it_is_caught(self):
        assert check_never_reword(
            "Achieved ISO 27001 certification",
            "Achieved certification",
            rails(never_reword=["ISO 27001"]),
        )

    def test_keeping_it_verbatim_passes(self):
        assert (
            check_never_reword(
                "Achieved ISO 27001 certification",
                "Holds ISO 27001 certification",
                rails(never_reword=["ISO 27001"]),
            )
            == []
        )

    def test_a_string_absent_from_the_source_is_not_required(self):
        assert check_never_reword("Built a thing", "Built a thing", rails(never_reword=["ISO 27001"])) == []


class TestProtectedTokens:
    def test_a_dropped_macro_is_caught(self):
        violations = check_protected_tokens(r"Built \textbf{PlaceholderSys} quickly", "Built it quickly")
        assert violations and violations[0].rule == "protected_token"

    def test_a_dropped_link_is_caught(self):
        assert check_protected_tokens(r"See \href{https://x.test}{\underline{demo}}", "See the demo")

    def test_a_moved_macro_is_fine(self):
        assert check_protected_tokens(r"\textbf{X} first", r"Later, \textbf{X}") == []


class TestLength:
    def test_an_overlong_rewrite_is_caught(self):
        violations = check_length("x" * 300, rails(max_bullet_length=240))
        assert violations and "300" in violations[0].detail

    def test_a_short_rewrite_passes(self):
        assert check_length("x" * 100, rails(max_bullet_length=240)) == []


class TestEnforce:
    def test_reports_every_violation_at_once(self):
        report = enforce(
            r"Built \textbf{a system}",
            "Built a system at Acme with 40% uplift",
            rails=rails(),
        )
        assert not report.ok
        assert {v.rule for v in report.violations} >= {"new_entity", "protected_token"}

    def test_a_clean_rewrite_passes(self):
        report = enforce("Built a hybrid retrieval pipeline", "Designed a hybrid retrieval pipeline")
        assert report.ok

    def test_feedback_quotes_every_violation_for_the_repair_attempt(self):
        report = enforce("Ran it", "Ran it at Acme in 2019", rails=rails())
        feedback = report.feedback()
        assert "Acme" in feedback and "2019" in feedback

    def test_an_empty_report_is_ok(self):
        assert GuardrailReport().ok
