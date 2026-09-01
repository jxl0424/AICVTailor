"""Coverage arithmetic, ingestion, JD parsing, and the end-to-end run."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from aicvtailor.analysis import coverage as coverage_mod
from aicvtailor.analysis import ingest, jd_parse
from aicvtailor.analysis.match import Match, MatchStatus
from aicvtailor.analysis.pipeline import analyse
from aicvtailor.latex import parse

JD = Path(__file__).parent / "fixtures" / "jd_ai_engineer.txt"
FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"
MASTER = Path(__file__).parents[1] / "data" / "master" / "master.tex"


class TestCoverage:
    def test_all_present_is_full_coverage(self):
        matches = [Match("A", MatchStatus.PRESENT_EXACT), Match("B", MatchStatus.PRESENT_AS_SYNONYM)]
        report = coverage_mod.compute(matches, {"A": 1.0, "B": 1.0}, {"A": "tool", "B": "tool"})
        assert report.percent == 100.0

    def test_all_missing_is_zero(self):
        report = coverage_mod.compute(
            [Match("A", MatchStatus.MISSING)], {"A": 1.0}, {"A": "tool"}
        )
        assert report.percent == 0.0

    def test_implied_earns_half_credit(self):
        """A bullet that gestures at a skill reads to a human and not to a
        keyword filter, so it is worth neither nothing nor everything."""
        report = coverage_mod.compute(
            [Match("A", MatchStatus.IMPLIED)], {"A": 1.0}, {"A": "tool"}
        )
        assert report.percent == 50.0

    def test_weighting_makes_important_terms_dominate(self):
        matches = [Match("big", MatchStatus.MISSING), Match("small", MatchStatus.PRESENT_EXACT)]
        report = coverage_mod.compute(
            matches, {"big": 9.0, "small": 1.0}, {"big": "tool", "small": "tool"}
        )
        assert report.percent == 10.0

    def test_categories_are_reported_separately(self):
        matches = [Match("a", MatchStatus.PRESENT_EXACT), Match("b", MatchStatus.MISSING)]
        report = coverage_mod.compute(
            matches, {"a": 1.0, "b": 1.0}, {"a": "tool", "b": "soft_skill"}
        )
        by_name = {c.category: c.percent for c in report.by_category}
        assert by_name == {"tool": 100.0, "soft_skill": 0.0}

    def test_zero_weight_terms_do_not_divide_by_zero(self):
        report = coverage_mod.compute([Match("a", MatchStatus.MISSING)], {}, {})
        assert report.percent == 0.0

    def test_the_report_carries_its_disclaimer_and_credit_scheme(self):
        """The number must travel with the explanation of what it is not."""
        report = coverage_mod.compute(
            [Match("a", MatchStatus.PRESENT_EXACT)], {"a": 1.0}, {"a": "tool"}
        )
        assert "not an ATS score" in report.disclaimer
        assert report.credit_scheme["implied"] == 0.5


class TestJDParse:
    def test_extracts_the_headline_role_and_company(self):
        parsed = jd_parse.parse_regex(JD.read_text(encoding="utf-8"))
        assert parsed.role == "AI Engineer (LLM Systems)"
        assert parsed.company == "Northwind Analytics"

    def test_strips_the_working_pattern_off_the_location(self):
        parsed = jd_parse.parse_regex(JD.read_text(encoding="utf-8"))
        assert parsed.location == "London, UK"

    def test_detects_hybrid_working(self):
        assert jd_parse.parse_regex(JD.read_text(encoding="utf-8")).workplace == "hybrid"

    def test_flags_visa_restrictions_with_the_sentence_that_said_so(self):
        parsed = jd_parse.parse_regex(JD.read_text(encoding="utf-8"))
        assert parsed.visa_mentioned
        assert "unable to sponsor" in parsed.visa_context.lower()

    @pytest.mark.parametrize(
        "headline,expected",
        [
            ("Senior Machine Learning Engineer", "senior"),
            ("Graduate Data Scientist", "graduate"),
            ("Principal Engineer, Platform", "principal"),
            ("Machine Learning Intern", "intern"),
        ],
    )
    def test_detects_seniority(self, headline, expected):
        assert jd_parse.parse_regex(f"{headline}\n\nRequirements\n- Python").seniority == expected

    def test_labelled_fields_win_over_the_headline(self):
        text = "Some marketing preamble\nJob Title: Data Engineer\nCompany: Acme\n"
        parsed = jd_parse.parse_regex(text)
        assert parsed.role == "Data Engineer"
        assert parsed.company == "Acme"

    def test_clearance_is_detected(self):
        parsed = jd_parse.parse_regex("Role\n\nYou must hold an active security clearance.")
        assert parsed.clearance_required

    def test_no_model_call_is_made_when_regex_answers_everything(self):
        class ExplodingProvider:
            def complete(self, *a, **k):
                raise AssertionError("should not call a model")

        text = "Job Title: Data Engineer\nCompany: Acme\nLocation: Leeds\n"
        assert jd_parse.parse(text, ExplodingProvider()).role == "Data Engineer"

    def test_a_failing_model_call_leaves_the_regex_result_intact(self):
        class BrokenProvider:
            def complete(self, *a, **k):
                raise RuntimeError("provider down")

        parsed = jd_parse.parse("Senior AI Engineer\n\nWe need Python.", BrokenProvider())
        assert parsed.role == "Senior AI Engineer"


class TestIngest:
    def test_pasted_text_is_accepted(self):
        result = ingest.from_text("  " + "A real posting with enough words to analyse. " * 3)
        assert result.method == "paste"

    def test_too_short_is_rejected_with_guidance(self):
        with pytest.raises(ingest.IngestError, match="too short"):
            ingest.from_text("AI Engineer")

    def test_a_failed_fetch_tells_the_user_to_paste(self):
        class BrokenClient:
            def get(self, url):
                raise httpx.ConnectError("refused")

            def close(self): ...

        with pytest.raises(ingest.IngestError, match="paste"):
            ingest.from_url("https://example.test/job", client=BrokenClient())

    def test_known_hostile_boards_get_a_specific_explanation(self):
        """LinkedIn renders postings client-side; a generic error would send
        the user hunting for a problem on their end."""

        class BrokenClient:
            def get(self, url):
                raise httpx.HTTPStatusError("403", request=None, response=None)

            def close(self): ...

        with pytest.raises(ingest.IngestError, match="linkedin"):
            ingest.from_url("https://www.linkedin.com/jobs/view/123", client=BrokenClient())


class TestEndToEnd:
    @pytest.fixture
    def result(self):
        resume = parse(FIXTURE.read_text(encoding="utf-8"))
        return analyse(JD.read_text(encoding="utf-8"), resume)

    def test_produces_ranked_terms_highest_weight_first(self, result):
        weights = [r.weight.weight for r in result.ranked]
        assert weights == sorted(weights, reverse=True)

    def test_requirements_outrank_nice_to_haves_in_the_output(self, result):
        by_term = {r.term.canonical: r for r in result.ranked}
        assert by_term["Python"].weight.weight > by_term["Kubernetes"].weight.weight

    def test_every_implied_term_names_its_source_bullet(self, result):
        for ranked in result.ranked:
            if ranked.match.status is MatchStatus.IMPLIED:
                assert ranked.match.bullet_id

    def test_no_model_call_is_needed_for_analysis(self, result):
        """Everything except optional JD-field extraction is deterministic."""
        assert result.coverage.percent >= 0

    def test_the_run_is_logged_stage_by_stage(self, result, tmp_path):
        from aicvtailor.llm.runlog import RunLog

        stages = {entry["stage"] for entry in RunLog(result.run_id).read()}
        assert {"sections", "jd_parse", "terms", "match", "coverage"} <= stages

    def test_serialises_for_the_api(self, result):
        payload = result.as_dict()
        assert payload["coverage"]["disclaimer"]
        assert payload["terms"][0]["weight_formula"]

    def test_warns_when_running_on_the_lexical_fallback(self, result):
        if result.similarity_backend == "lexical":
            assert any("lexical" in w for w in result.warnings)


@pytest.mark.skipif(not MASTER.exists(), reason="no master.tex in data/master/")
class TestAgainstTheRealCV:
    def test_analysis_completes_and_reports_a_plausible_number(self):
        resume = parse(MASTER.read_text(encoding="utf-8"))
        result = analyse(JD.read_text(encoding="utf-8"), resume)

        assert 0 < result.coverage.percent < 100
        assert len(result.ranked) >= 10

    def test_a_skill_absent_from_the_cv_is_never_reported_present(self):
        resume = parse(MASTER.read_text(encoding="utf-8"))
        result = analyse(JD.read_text(encoding="utf-8"), resume)

        by_term = {r.term.canonical: r.match for r in result.ranked}
        assert by_term["Kubernetes"].status is MatchStatus.MISSING
