"""Adversarial tests: make the model misbehave, assert the code catches it.

Every rewrite here runs with `system_prompt=NO_RULES_PROMPT`, which says
nothing about truthfulness. Several also feed a job description carrying a
prompt injection. If any of these passed because the model was well behaved,
the test would be worthless -- the models are stubs that fabricate on purpose.
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
from aicvtailor.suggest import (
    GapSuggestion,
    RelocateSuggestion,
    RewordSuggestion,
    applicable,
    generate,
    rewrite_bullet,
)

FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"
MASTER = Path(__file__).parents[1] / "data" / "master" / "master.tex"

# Deliberately says nothing about not fabricating. Every guarantee below has to
# come from the enforcement code.
NO_RULES_PROMPT = "Rewrite the bullet. Make it sound as impressive as you can."

RAILS = {
    "forbidden_claims": [],
    "never_reword": [],
    "max_bullet_length": 240,
    "forbid_new_entities": True,
    "entity_allowlist": [],
}


class ScriptedProvider:
    """Returns whatever fabrication the test wants, however many times."""

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, system, user, schema=None, *, role=None, **kwargs):
        self.calls.append({"system": system, "user": user})
        value = self._responses.pop(0) if self._responses else self._last
        self._last = value
        return {"rewritten": value}


def rails(**overrides):
    return {**RAILS, **overrides}


def ranked(term: str, status: MatchStatus, *, bullet_id: str | None = None, weight_section=SectionKind.REQUIREMENTS):
    t = Term(
        canonical=term,
        category="tool",
        mentions=[Mention(surface=term, section=weight_section, required=True, start=0)],
    )
    return RankedTerm(
        term=t,
        weight=score(t),
        match=Match(
            term=term,
            status=status,
            location=ResumeLocation.BULLET if bullet_id else None,
            bullet_id=bullet_id,
            score=0.7 if status is MatchStatus.IMPLIED else 0.0,
        ),
    )


class TestModelFabrication:
    """A hostile model, and a prompt that does nothing to stop it."""

    def test_an_invented_metric_is_rejected(self):
        provider = ScriptedProvider("Built a pipeline that improved recall by 45%")
        text, report = rewrite_bullet(
            provider,
            "Built a retrieval pipeline over research papers",
            ["retrieval"],
            rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok
        assert any(v.rule == "new_entity" for v in report.violations)

    def test_an_invented_employer_is_rejected(self):
        provider = ScriptedProvider("Taught an AI curriculum at Google DeepMind")
        _, report = rewrite_bullet(
            provider,
            "Designed and deliver an AI curriculum for a student",
            ["teaching"],
            rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok

    def test_an_invented_team_size_is_rejected(self):
        provider = ScriptedProvider("Led a team of 12 building the pipeline")
        _, report = rewrite_bullet(
            provider, "Built the pipeline", ["leadership"], rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok

    def test_an_invented_date_is_rejected(self):
        provider = ScriptedProvider("Since 2018, built the pipeline")
        _, report = rewrite_bullet(
            provider, "Built the pipeline", ["experience"], rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok

    def test_a_forbidden_claim_is_rejected(self):
        provider = ScriptedProvider("Delivered Project Halberd end to end")
        _, report = rewrite_bullet(
            provider,
            "Delivered the internal system end to end",
            ["delivery"],
            rails=rails(forbidden_claims=["Project Halberd"]),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok
        assert any(v.rule == "forbidden_claim" for v in report.violations)

    def test_rewording_a_protected_string_is_rejected(self):
        provider = ScriptedProvider("Holds an ISO-27001 certification")
        _, report = rewrite_bullet(
            provider,
            "Achieved ISO 27001 certification",
            ["compliance"],
            rails=rails(never_reword=["ISO 27001"]),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok
        assert any(v.rule == "never_reword" for v in report.violations)

    def test_dropping_a_latex_macro_is_rejected(self):
        provider = ScriptedProvider("Built PlaceholderSys, a retrieval system")
        _, report = rewrite_bullet(
            provider,
            r"Built \textbf{PlaceholderSys}, a retrieval system",
            ["retrieval"],
            rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok
        assert any(v.rule == "protected_token" for v in report.violations)

    def test_an_overlong_rewrite_is_rejected(self):
        provider = ScriptedProvider("Built a retrieval pipeline " + "with many words " * 30)
        _, report = rewrite_bullet(
            provider, "Built a retrieval pipeline", ["retrieval"],
            rails=rails(max_bullet_length=120), system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok
        assert any(v.rule == "max_bullet_length" for v in report.violations)

    def test_bare_latex_specials_are_escaped_not_rejected(self):
        """The model emitting `R&D` is a formatting problem the sanitizer
        fixes, not a truth problem."""
        provider = ScriptedProvider("Worked in R&D on the pipeline")
        text, report = rewrite_bullet(
            provider,
            "Worked in R&D on the pipeline",
            ["research"],
            rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert r"\&" in text
        assert report.ok


class TestRepairLoop:
    def test_one_repair_attempt_is_made_and_can_succeed(self):
        provider = ScriptedProvider(
            "Built a pipeline improving recall by 45%",  # fabricated
            "Designed a retrieval pipeline over research papers",  # corrected
        )
        text, report = rewrite_bullet(
            provider,
            "Built a retrieval pipeline over research papers",
            ["retrieval"],
            rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert report.ok
        assert len(provider.calls) == 2

    def test_the_violation_is_quoted_back_on_the_retry(self):
        provider = ScriptedProvider(
            "Built it at Acme", "Built the retrieval pipeline"
        )
        rewrite_bullet(
            provider, "Built the retrieval pipeline", ["retrieval"],
            rails=rails(), system_prompt=NO_RULES_PROMPT,
        )
        assert "Acme" in provider.calls[1]["user"]

    def test_a_persistent_fabricator_fails_rather_than_being_let_through(self):
        provider = ScriptedProvider("Built it at Acme with 90% uplift")
        _, report = rewrite_bullet(
            provider, "Built the pipeline", ["retrieval"], rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok
        assert len(provider.calls) == 2  # tried twice, then gave up

    def test_repeated_failure_never_silently_downgrades_to_a_pass(self):
        provider = ScriptedProvider("Delivered Project Halberd")
        _, report = rewrite_bullet(
            provider, "Delivered the system", ["delivery"],
            rails=rails(forbidden_claims=["Project Halberd"]),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok


class TestPromptInjection:
    def test_an_injected_instruction_in_the_jd_cannot_add_experience(self):
        """A posting that tells the system to claim ten years of Kubernetes.
        The model obeys; the guardrail does not care."""
        provider = ScriptedProvider(
            "Built the pipeline with 10 years of Kubernetes experience"
        )
        _, report = rewrite_bullet(
            provider,
            "Built the retrieval pipeline",
            ["Kubernetes"],  # even as an allowed target term
            rails=rails(),
            system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok
        offending = {v.offending for v in report.violations}
        assert "10" in offending or any("10" in o for o in offending)

    def test_the_injected_term_alone_does_not_license_a_fabricated_history(self):
        provider = ScriptedProvider("Deployed on Kubernetes at Amazon since 2017")
        _, report = rewrite_bullet(
            provider, "Deployed the service in containers", ["Kubernetes"],
            rails=rails(), system_prompt=NO_RULES_PROMPT,
        )
        assert not report.ok
        assert {"Amazon", "2017"} & {v.offending for v in report.violations}


class TestActionRouting:
    """The structural guarantee: a MISSING term cannot become resume text."""

    @pytest.fixture
    def document(self):
        return parse(FIXTURE.read_text(encoding="utf-8"))

    def test_a_missing_term_only_ever_becomes_a_gap(self, document):
        provider = ScriptedProvider("Expert in Kubernetes orchestration at scale")
        suggestions = generate(
            [ranked("Kubernetes", MatchStatus.MISSING)],
            document,
            provider=provider,
            rails=rails(),
        )
        assert len(suggestions) == 1
        assert isinstance(suggestions[0], GapSuggestion)
        # The name, not the Term object: an earlier version stored the object
        # here and nothing noticed until it reached the API layer.
        assert suggestions[0].term == "Kubernetes"
        assert provider.calls == [], "a missing term must not reach the rewriter"

    def test_a_gap_carries_no_target_and_no_proposed_text(self, document):
        suggestions = generate(
            [ranked("Kubernetes", MatchStatus.MISSING)], document, rails=rails()
        )
        gap = suggestions[0]
        assert not hasattr(gap, "target_id")
        assert not hasattr(gap, "proposed_text")

    def test_gaps_cannot_reach_the_applicable_set(self, document):
        suggestions = generate(
            [
                ranked("Kubernetes", MatchStatus.MISSING),
                ranked("Terraform", MatchStatus.MISSING),
            ],
            document,
            rails=rails(),
        )
        assert suggestions
        assert applicable(suggestions) == []

    def test_a_gap_says_what_it_would_take_instead(self, document):
        gap = generate([ranked("Kubernetes", MatchStatus.MISSING)], document, rails=rails())[0]
        assert "would need to actually do it" in gap.what_it_would_take

    def test_an_implied_term_becomes_a_traceable_reword(self, document):
        bullet = next(iter(document.bullets()))
        provider = ScriptedProvider(bullet.text + " and retrieval")
        suggestions = generate(
            [ranked("retrieval", MatchStatus.IMPLIED, bullet_id=bullet.id)],
            document,
            provider=provider,
            rails=rails(),
        )
        suggestion = suggestions[0]
        assert isinstance(suggestion, RewordSuggestion)
        assert suggestion.source_bullet_id == bullet.id
        assert suggestion.target_id == bullet.id

    def test_a_rejected_rewrite_degrades_to_a_gap_not_a_silent_pass(self, document):
        bullet = next(iter(document.bullets()))
        provider = ScriptedProvider("Delivered it at Acme with a 99% success rate")
        suggestions = generate(
            [ranked("retrieval", MatchStatus.IMPLIED, bullet_id=bullet.id)],
            document,
            provider=provider,
            rails=rails(),
        )
        assert isinstance(suggestions[0], GapSuggestion)
        assert "rejected by the guardrails" in suggestions[0].rationale

    def test_relocate_needs_no_model_call(self, document):
        provider = ScriptedProvider("should not be used")
        term = ranked("ToolC", MatchStatus.PRESENT_EXACT)
        term.match.location = ResumeLocation.SKILLS

        suggestions = generate([term], document, provider=provider, rails=rails())
        if suggestions:
            assert isinstance(suggestions[0], RelocateSuggestion)
        assert provider.calls == []

    def test_relocate_only_reorders_existing_values(self, document):
        term = ranked("ToolC", MatchStatus.PRESENT_EXACT)
        term.match.location = ResumeLocation.SKILLS
        suggestions = generate([term], document, provider=None, rails=rails())

        if suggestions and isinstance(suggestions[0], RelocateSuggestion):
            before = {v.strip() for v in suggestions[0].original_text.lstrip(":").split(",")}
            after = {v.strip() for v in suggestions[0].proposed_text.lstrip(":").split(",")}
            assert before == after, "relocate must not add or drop a skill"


@pytest.mark.skipif(not MASTER.exists(), reason="no master.tex in data/master/")
class TestAgainstTheRealCV:
    def test_no_suggestion_can_target_a_header_field(self):
        """Belt and braces with the Phase 1 immutability guarantee."""
        document = parse(MASTER.read_text(encoding="utf-8"))
        editable = set(document.editable_spans())

        provider = ScriptedProvider("anything")
        suggestions = generate(
            [
                ranked("Kubernetes", MatchStatus.MISSING),
                ranked("retrieval", MatchStatus.IMPLIED, bullet_id="s1.e1.b0"),
            ],
            document,
            provider=provider,
            rails=rails(),
        )
        for suggestion in applicable(suggestions):
            assert suggestion.target_id in editable
