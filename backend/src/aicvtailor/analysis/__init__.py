"""JD analysis: ingest, parse, extract terms, weight, match, report coverage."""

from .coverage import Coverage, compute
from .ingest import IngestError, Ingested, from_text, from_url
from .jd_parse import ParsedJD, parse_regex
from .match import Match, MatchStatus, ResumeIndex, ResumeLocation, match_all
from .pipeline import AnalysisResult, RankedTerm, analyse
from .sections import Section, SectionKind, split_sections
from .semantic import LexicalIndex, SimilarityIndex, build_index
from .terms import SkillDictionary, Term, discover_unknown_terms, extract_dictionary_terms
from .weight import WeightBreakdown, score

__all__ = [
    "AnalysisResult",
    "Coverage",
    "IngestError",
    "Ingested",
    "LexicalIndex",
    "Match",
    "MatchStatus",
    "ParsedJD",
    "RankedTerm",
    "ResumeIndex",
    "ResumeLocation",
    "Section",
    "SectionKind",
    "SimilarityIndex",
    "SkillDictionary",
    "Term",
    "WeightBreakdown",
    "analyse",
    "build_index",
    "compute",
    "discover_unknown_terms",
    "extract_dictionary_terms",
    "from_text",
    "from_url",
    "match_all",
    "parse_regex",
    "score",
    "split_sections",
]
