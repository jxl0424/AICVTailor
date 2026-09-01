"""The Phase 3 analysis run: JD text in, ranked terms and coverage out.

Deterministic end to end apart from one optional extractor call for JD fields.
Every stage writes to the run log so a bad result can be traced to the stage
that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..latex.ir import Document
from ..llm.runlog import RunLog
from . import coverage as coverage_mod
from . import jd_parse
from .match import Match, ResumeIndex, match_all
from .sections import Section, split_sections
from .semantic import SimilarityIndex, build_index
from .terms import SkillDictionary, Term, discover_unknown_terms, extract_dictionary_terms
from .weight import WeightBreakdown, score


@dataclass
class RankedTerm:
    term: Term
    weight: WeightBreakdown
    match: Match

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term.canonical,
            "category": self.term.category,
            "in_dictionary": self.term.in_dictionary,
            "frequency": self.term.frequency,
            "sections": sorted(s.value for s in self.term.sections),
            "surfaces": sorted(self.term.surfaces),
            "weight": self.weight.weight,
            "weight_breakdown": self.weight.as_dict(),
            "weight_formula": self.weight.formula,
            "status": self.match.status.value,
            "location": self.match.location.value if self.match.location else None,
            "evidence": self.match.evidence,
            "bullet_id": self.match.bullet_id,
            "match_score": self.match.score,
        }


@dataclass
class AnalysisResult:
    parsed: jd_parse.ParsedJD
    sections: list[Section]
    ranked: list[RankedTerm]
    unknown: list[Term]
    coverage: coverage_mod.Coverage
    similarity_backend: str
    run_id: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parsed": self.parsed.as_dict(),
            "sections": [
                {
                    "kind": s.kind.value,
                    "heading": s.heading,
                    "chars": len(s.text),
                }
                for s in self.sections
            ],
            "terms": [r.as_dict() for r in self.ranked],
            "unknown_terms": [
                {
                    "term": t.canonical,
                    "frequency": t.frequency,
                    "sections": sorted(s.value for s in t.sections),
                }
                for t in self.unknown
            ],
            "coverage": self.coverage.as_dict(),
            "similarity_backend": self.similarity_backend,
            "warnings": self.warnings,
        }


def analyse(
    jd_text: str,
    resume: Document,
    *,
    provider=None,
    dictionary: SkillDictionary | None = None,
    similarity: SimilarityIndex | None = None,
    runlog: RunLog | None = None,
) -> AnalysisResult:
    """Run the full analysis. No rewriting, no resume mutation."""
    runlog = runlog or RunLog()
    dictionary = dictionary or SkillDictionary()
    similarity = similarity or build_index()
    warnings: list[str] = []

    sections = split_sections(jd_text)
    runlog.write(
        "sections",
        count=len(sections),
        kinds=[s.kind.value for s in sections],
    )

    parsed = jd_parse.parse(jd_text, provider, runlog=runlog)
    runlog.write("jd_parse", **parsed.as_dict())

    terms_by_name = extract_dictionary_terms(sections, dictionary)
    unknown = discover_unknown_terms(sections, dictionary)

    # The employer's own name is the most repeated phrase in most postings and
    # is never a skill worth adding to the dictionary.
    noise = {w.lower() for w in (parsed.company or "").split() if len(w) > 2}
    if noise:
        unknown = [
            t for t in unknown if not noise & set(t.canonical.lower().split())
        ]
    runlog.write("terms", known=len(terms_by_name), unknown=len(unknown))

    if not terms_by_name:
        warnings.append(
            "No terms from config/skills.yaml were found in this posting. The "
            "dictionary may need entries for this field -- see the unrecognised "
            "terms list."
        )

    index = ResumeIndex.from_document(resume)
    weights = {name: score(term) for name, term in terms_by_name.items()}
    matches = {
        m.term: m
        for m in match_all(list(terms_by_name.values()), index, dictionary, similarity)
    }
    runlog.write(
        "match",
        backend=similarity.name,
        counts={
            status: sum(1 for m in matches.values() if m.status.value == status)
            for status in {m.status.value for m in matches.values()}
        },
    )

    ranked = [
        RankedTerm(term=term, weight=weights[name], match=matches[name])
        for name, term in terms_by_name.items()
    ]
    ranked.sort(key=lambda r: (-r.weight.weight, r.term.canonical))

    report = coverage_mod.compute(
        list(matches.values()),
        {name: w.weight for name, w in weights.items()},
        {name: t.category for name, t in terms_by_name.items()},
    )
    runlog.write("coverage", percent=report.percent, backend=similarity.name)

    if similarity.name == "lexical":
        warnings.append(
            "Semantic matching is using the lexical fallback. Terms your resume "
            "covers only in meaning, without sharing a word, will read as missing."
        )

    return AnalysisResult(
        parsed=parsed,
        sections=sections,
        ranked=ranked,
        unknown=unknown,
        coverage=report,
        similarity_backend=similarity.name,
        run_id=runlog.run_id,
        warnings=warnings,
    )
