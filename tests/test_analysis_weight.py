"""Term weighting.

Every factor is asserted separately, because the UI shows this derivation and
a coverage number nobody can audit is a number nobody should trust.
"""

from __future__ import annotations

import pytest

from aicvtailor.analysis.sections import SectionKind
from aicvtailor.analysis.terms import Mention, Term
from aicvtailor.analysis.weight import score


def term(*mentions: tuple[SectionKind, bool | None], in_dictionary: bool = True) -> Term:
    return Term(
        canonical="X",
        category="tool",
        in_dictionary=in_dictionary,
        mentions=[
            Mention(surface="X", section=section, required=required, start=i)
            for i, (section, required) in enumerate(mentions)
        ],
    )


def test_requirements_outweigh_boilerplate():
    required = score(term((SectionKind.REQUIREMENTS, None)))
    boilerplate = score(term((SectionKind.BOILERPLATE, None)))
    assert required.weight > boilerplate.weight * 4


def test_nice_to_have_is_discounted_against_requirements():
    assert score(term((SectionKind.NICE_TO_HAVE, None))).weight < score(
        term((SectionKind.REQUIREMENTS, None))
    ).weight


def test_the_strongest_section_wins():
    """Burying a required skill in the benefits blurb must not dilute it."""
    mixed = score(term((SectionKind.BOILERPLATE, None), (SectionKind.REQUIREMENTS, None)))
    assert mixed.section == SectionKind.REQUIREMENTS.value


def test_repetition_increases_weight_but_sublinearly():
    once = score(term((SectionKind.REQUIREMENTS, None)))
    eight = score(term(*[(SectionKind.REQUIREMENTS, None)] * 8))

    assert eight.weight > once.weight
    # Log scaling: eight mentions of one word must not outrank eight distinct
    # requirements.
    assert eight.weight < once.weight * 3


def test_an_explicit_must_have_cue_raises_the_weight():
    """Outside the requirements section, an inline cue is what decides it.
    Inside it, membership already implies required."""
    assert score(term((SectionKind.OTHER, True))).weight > score(
        term((SectionKind.OTHER, None))
    ).weight


def test_requirements_membership_implies_required_without_a_cue():
    assert score(term((SectionKind.REQUIREMENTS, None))).requirement == "required"


def test_an_optional_cue_lowers_it():
    assert score(term((SectionKind.REQUIREMENTS, False))).weight < score(
        term((SectionKind.REQUIREMENTS, None))
    ).weight


def test_required_beats_optional_when_both_cues_appear():
    breakdown = score(term((SectionKind.REQUIREMENTS, True), (SectionKind.OTHER, False)))
    assert breakdown.requirement == "required"


def test_appearing_in_several_sections_adds_a_spread_bonus():
    spread = score(term((SectionKind.REQUIREMENTS, None), (SectionKind.RESPONSIBILITIES, None)))
    assert spread.spread_factor > 1.0
    assert spread.distinct_sections == 2


def test_terms_outside_the_dictionary_are_penalised():
    assert score(term((SectionKind.REQUIREMENTS, None), in_dictionary=False)).weight < score(
        term((SectionKind.REQUIREMENTS, None))
    ).weight


def test_the_breakdown_multiplies_out_to_the_weight():
    """The formula shown in the UI must be the one that was used."""
    breakdown = score(term((SectionKind.REQUIREMENTS, True), (SectionKind.NICE_TO_HAVE, None)))
    product = (
        breakdown.frequency_factor
        * breakdown.section_factor
        * breakdown.requirement_factor
        * breakdown.spread_factor
        * breakdown.dictionary_factor
    )
    assert product == pytest.approx(breakdown.weight, rel=0.01)
    assert f"{breakdown.weight:.3f}" in breakdown.formula
